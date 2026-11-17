"""API REST (FastAPI — schéma OpenAPI 3.1 généré automatiquement sur /openapi.json, documentation /docs).

Authentification : ``Authorization: Bearer sov_…`` (ou ``X-API-Key``), clés à portées read / write / admin /
webhook créées par ``sovereign apikey create``. Les webhooks entrants sont authentifiés par signature HMAC
(pas par clé). Le serveur MCP HTTP Streamable est monté sur ``/mcp`` derrière la même authentification.
"""

import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import sovereign
from sovereign.automation.webhooks import WebhookError, handle_webhook
from sovereign.commands.router import CommandError, CommandResult
from sovereign.core.finance import FinanceError
from sovereign.core.security import ApiPrincipal
from sovereign.skills.base import SkillError
from sovereign.workspace import import_workspace

if TYPE_CHECKING:
    from sovereign.app import Sovereign


class CommandRequest(BaseModel):
    args: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    text: str


class RevenueRequest(BaseModel):
    amount: str
    date: str | None = None
    counterparty: str | None = None
    reference: str | None = None
    description: str | None = None


class ExpenseRequest(BaseModel):
    amount: str
    category: str
    vendor: str | None = None
    description: str | None = None
    recurring: Literal["none", "monthly", "yearly"] = "none"
    reference: str | None = None


class DecisionRequest(BaseModel):
    reason: str | None = None


class EventRequest(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str | None = None


REVENUE_ACTIONS = frozenset({("finance-budget", "record_revenue"), ("finance-budget", "collect_invoice")})
"""Actions qui ouvrent du budget : réservées à la portée admin (même niveau qu'une validation humaine)."""


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


def create_api(app: "Sovereign", *, mount_mcp: bool = True, mcp_host: str = "127.0.0.1") -> FastAPI:
    settings = app.kernel.settings
    mcp_server = None
    if mount_mcp:
        from sovereign.interfaces.mcp_server import build_mcp

        mcp_server = build_mcp(app)
        mcp_asgi = mcp_server.streamable_http_app(streamable_http_path="/", stateless_http=True, host=mcp_host)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield

    api = FastAPI(
        title="SOVEREIGN API",
        version=sovereign.__version__,
        lifespan=lifespan,
        description=f"Autonomous Business Intelligence Director — {sovereign.MOTTO}",
    )

    def principal(request: Request) -> ApiPrincipal:
        found = app.kernel.api_keys.verify(_extract_key(request))
        if found is not None:
            return found
        if not settings.security.require_api_key:
            return ApiPrincipal("local", "local", frozenset({"read", "write", "admin", "webhook"}))
        raise HTTPException(status_code=401, detail="clé d'API manquante ou invalide (sovereign apikey create)")

    def need(scope: str) -> Any:
        def check(p: Annotated[ApiPrincipal, Depends(principal)]) -> ApiPrincipal:
            if not p.allows(scope):
                raise HTTPException(status_code=403, detail=f"portée « {scope} » requise")
            return p

        return check

    Reader = Annotated[ApiPrincipal, Depends(need("read"))]
    Writer = Annotated[ApiPrincipal, Depends(need("write"))]
    Admin = Annotated[ApiPrincipal, Depends(need("admin"))]

    @api.exception_handler(FinanceError)
    @api.exception_handler(SkillError)
    @api.exception_handler(CommandError)
    @api.exception_handler(ValueError)
    async def business_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @api.get("/health", tags=["système"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": sovereign.__version__}

    @api.get("/v1/commands", tags=["commandes"])
    def list_commands(_: Reader) -> list[dict[str, str]]:
        return [{"name": s.name, "summary": s.summary, "usage": s.usage, "scope": s.scope} for s in app.commands.list()]

    @api.post("/v1/commands/{name}", tags=["commandes"])
    def run_command(name: str, body: CommandRequest, who: Reader) -> CommandResult:
        spec = app.commands.specs.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"commande inconnue : {name}")
        if spec.scope == "write" and not who.allows("write"):
            raise HTTPException(status_code=403, detail="portée « write » requise")
        return app.commands.execute(name, body.args, source="api", **body.options)

    @api.post("/v1/ask", tags=["commandes"])
    def ask(body: AskRequest, who: Writer) -> CommandResult:
        return app.commands.run(body.text, source="api")

    @api.get("/v1/status", tags=["système"])
    def status(_: Reader) -> dict[str, Any]:
        return app.commands.execute("status", source="api").data

    @api.get("/v1/skills", tags=["skills"])
    def skills(_: Reader) -> dict[str, Any]:
        return app.commands.execute("skills", source="api").data

    @api.get("/v1/skills/{name}", tags=["skills"])
    def skill_detail(name: str, _: Reader) -> dict[str, Any]:
        return app.commands.execute("skills", name, source="api").data

    @api.post("/v1/skills/{name}/{action}", tags=["skills"])
    def skill_action(name: str, action: str, payload: dict[str, Any], who: Reader) -> dict[str, Any]:
        spec = app.kernel.registry.get(name).spec(action)
        if spec.risk != "read_only" and not who.allows("write"):
            raise HTTPException(status_code=403, detail="portée « write » requise pour cette action")
        if (name, action) in REVENUE_ACTIONS and not who.allows("admin"):
            raise HTTPException(status_code=403, detail="portée « admin » requise : un encaissement ouvre du budget")
        return app.kernel.run_skill(name, action, payload, caller=f"api:{who.name}").model_dump(mode="json")

    @api.get("/v1/budget", tags=["finances"])
    def budget(_: Reader) -> dict[str, Any]:
        return app.kernel.finance.status().model_dump(mode="json")

    @api.post("/v1/finance/revenues", tags=["finances"])
    def add_revenue(body: RevenueRequest, who: Admin) -> dict[str, Any]:
        """Déclarer un encaissement réel : portée admin (il ouvre du budget, comme une validation humaine)."""

        entry = app.kernel.finance.record_collection(
            body.amount,
            body.date,
            counterparty=body.counterparty,
            reference=body.reference,
            description=body.description,
            source="api",
            actor=f"api:{who.name}",
        )
        return entry.model_dump(mode="json")

    @api.post("/v1/finance/expenses", tags=["finances"])
    def add_expense(body: ExpenseRequest, who: Writer) -> dict[str, Any]:
        decision = app.kernel.finance.request_expense(
            body.amount,
            body.category,
            vendor=body.vendor,
            description=body.description,
            recurring=body.recurring,
            reference=body.reference,
            requested_by=f"api:{who.name}",
        )
        return decision.model_dump(mode="json")

    @api.post("/v1/finance/expenses/{expense_id}/{decision}", tags=["finances"])
    def review_expense(expense_id: str, decision: str, body: DecisionRequest, who: Admin) -> dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=404, detail="décision : approve | reject")
        expense = app.kernel.finance.decide_review(expense_id, decision == "approve", f"api:{who.name}", body.reason)
        return expense.model_dump(mode="json")

    @api.get("/v1/approvals", tags=["gouvernance"])
    def approvals(_: Reader, status: str = "pending") -> list[dict[str, Any]]:
        return [a.model_dump(mode="json") for a in app.approvals.list(status if status != "all" else None)]

    @api.post("/v1/approvals/{approval_id}/{decision}", tags=["gouvernance"])
    def decide(approval_id: str, decision: str, body: DecisionRequest, who: Admin) -> dict[str, Any]:
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=404, detail="décision : approve | reject")
        return app.approvals.decide(approval_id, decision == "approve", f"api:{who.name}", body.reason)

    @api.get("/v1/reports", tags=["rapports"])
    def reports(_: Reader, limit: int = 20) -> list[dict[str, Any]]:
        rows = app.kernel.db.query(
            "SELECT id, kind, title, created_at FROM reports ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    @api.get("/v1/reports/{report_id}", tags=["rapports"], response_class=PlainTextResponse)
    def report(report_id: str, _: Reader) -> str:
        row = app.kernel.db.one("SELECT content_md FROM reports WHERE id = ?", (report_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="rapport inconnu")
        return str(row["content_md"])

    @api.post("/v1/events", tags=["automatisation"])
    def publish(body: EventRequest, who: Writer) -> dict[str, Any]:
        event = app.kernel.events.publish(body.type, body.payload, source=f"api:{who.name}", dedup_key=body.dedup_key)
        return {"accepted": True, "duplicate": event is None, "id": event.id if event else None}

    @api.post("/v1/automation/tick", tags=["automatisation"])
    def tick(_: Writer, run: bool = True) -> dict[str, Any]:
        enqueued = app.scheduler.tick()
        processed = app.worker.run_once() if run else 0
        return {"enqueued": enqueued, "processed": processed, "queue": app.queue.stats()}

    @api.post("/v1/webhooks/{source}", tags=["automatisation"])
    async def webhook(source: str, request: Request) -> dict[str, Any]:
        body = await request.body()
        try:
            return handle_webhook(app, source, body, dict(request.headers))
        except WebhookError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @api.post("/v1/workspace/import-csv/{kind}", tags=["données"])
    async def workspace_import_csv(kind: str, request: Request, who: Admin) -> dict[str, Any]:
        from sovereign.importers import import_csv

        body = await request.body()
        mapping = {k.removeprefix("map."): v for k, v in request.query_params.items() if k.startswith("map.")}
        options: dict[str, Any] = {"decimal": request.query_params.get("decimal", "auto")}
        if kind in ("collections", "campaigns"):
            options["mapping"] = mapping
        return import_csv(app, kind, body, **options).as_dict()

    @api.post("/v1/workspace/import", tags=["données"])
    def workspace_import(data: dict[str, Any], who: Admin) -> dict[str, Any]:
        return import_workspace(app, data, actor=f"api:{who.name}")

    if mcp_server is not None:
        api.mount("/mcp", cast(Any, _Authenticated(mcp_asgi, app)))
    return api


class _Authenticated:
    """Enveloppe ASGI : le transport MCP HTTP exige la même clé d'API (portée write)."""

    def __init__(self, inner: Any, app: "Sovereign") -> None:
        self.inner = inner
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            raw = headers.get("authorization", "")
            key = raw[7:].strip() if raw.lower().startswith("bearer ") else headers.get("x-api-key")
            who = self.app.kernel.api_keys.verify(key)
            required = self.app.kernel.settings.security.require_api_key
            if (who is None and required) or (who is not None and not who.allows("write")):
                response = JSONResponse(status_code=401, content={"detail": "clé d'API (portée write) requise"})
                await response(scope, receive, send)
                return
        await self.inner(scope, receive, send)
