from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from sovereign.app import Sovereign
from sovereign.interfaces.api import create_api
from sovereign.interfaces.cli import cli
from sovereign.interfaces.mcp_server import build_mcp


@pytest.fixture
def app(clock):
    application = Sovereign.ephemeral(clock=clock)
    application.kernel.finance.record_collection("8000", "2026-11-01", reference="api-seed")
    yield application
    application.close()


# -- API REST ---------------------------------------------------------------------------------------
@pytest.fixture
def api(app):
    with TestClient(create_api(app, mcp_host="testserver")) as client:
        yield client


def _key(app, *scopes):
    return app.kernel.api_keys.create("test", list(scopes))[1]


def test_health_is_public_but_v1_requires_key(api):
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/v1/status").status_code == 401
    assert api.get("/v1/status", headers={"Authorization": "Bearer sov_faux"}).status_code == 401


def test_scopes_are_enforced(app, api):
    reader = {"Authorization": f"Bearer {_key(app, 'read')}"}
    writer = {"X-API-Key": _key(app, "write")}
    assert api.get("/v1/budget", headers=reader).json()["envelope"] == "800.00"
    assert api.post("/v1/finance/expenses", headers=reader, json={"amount": "10", "category": "x"}).status_code == 403
    decision = api.post("/v1/finance/expenses", headers=writer, json={"amount": "500", "category": "ads"}).json()
    assert decision["needs_review"]
    review = api.post(f"/v1/finance/expenses/{decision['expense']['id']}/approve", headers=writer, json={})
    assert review.status_code == 403  # la revue humaine exige la portée admin


def test_commands_and_skills_over_http(app, api):
    headers = {"Authorization": f"Bearer {_key(app, 'write')}"}
    names = {c["name"] for c in api.get("/v1/commands", headers=headers).json()}
    assert len(names) == 15
    result = api.post("/v1/commands/budget", headers=headers, json={}).json()
    assert result["command"] == "budget" and "Disponible" in result["summary"]
    out = api.post("/v1/skills/finance-budget/check_spend", headers=headers, json={"amount": "100"}).json()
    assert out["allowed"] is True
    bad = api.post("/v1/skills/finance-budget/check_spend", headers=headers, json={"amount": "-1"})
    assert bad.status_code == 422
    ask = api.post("/v1/ask", headers=headers, json={"text": "/status"}).json()
    assert ask["command"] == "status"


def test_openapi_is_published(api):
    schema = api.get("/openapi.json").json()
    assert schema["info"]["title"] == "SOVEREIGN API"
    assert "/v1/commands/{name}" in schema["paths"] and "/v1/webhooks/{source}" in schema["paths"]


def test_approval_requires_admin_and_works(app, api):
    from sovereign.skills.base import Recommendation

    rec = Recommendation(
        title="Abonnement outil", rationale="r", estimated_cost="100", estimated_value="900", tags=["outils"]
    ).finalize("finance-budget")
    approval = app.approvals.create(
        "recommendation", rec.title, "s", {"recommendation": rec.model_dump(mode="json")}, {"class": "high"}
    )
    admin = {"Authorization": f"Bearer {_key(app, 'admin')}"}
    assert len(api.get("/v1/approvals", headers=admin).json()) == 1
    effects = api.post(f"/v1/approvals/{approval.id}/approve", headers=admin, json={"reason": "ok"}).json()
    assert effects["expense"]["status"] == "approved"


def test_mcp_mount_requires_key(app, api):
    assert api.post("/mcp/", json={}).status_code == 401


def test_webhook_unconfigured_source(api):
    assert api.post("/v1/webhooks/stripe", content=b"{}").status_code == 404


# -- MCP ------------------------------------------------------------------------------------------
def test_mcp_tools_resources_prompts(app):
    from mcp import Client

    server = build_mcp(app)

    async def scenario():
        async with Client(server) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"director", "budget", "council", "skill_run", "status", "report"} <= tools
            assert not any("approve" in t for t in tools)  # jamais d'auto-validation par une IA
            budget = await client.call_tool("budget", {})
            text = budget.content[0].text
            assert "Disponible" in text
            run = await client.call_tool(
                "skill_run", {"skill": "finance-budget", "action": "check_spend", "input": {"amount": "50"}}
            )
            assert run.structured_content["allowed"] is True
            resources = {str(r.uri) for r in (await client.list_resources()).resources}
            assert "sovereign://skills" in resources
            catalog = await client.read_resource("sovereign://skills")
            assert "finance-budget" in json.loads(catalog.contents[0].text)
            doc = await client.read_resource("sovereign://skills/finance-budget")
            assert "## Sécurité" in doc.contents[0].text
            prompts = {p.name for p in (await client.list_prompts()).prompts}
            assert {"director_briefing", "council_meeting", "weekly_review"} <= prompts

    asyncio.run(scenario())


# -- CLI ------------------------------------------------------------------------------------------
def test_cli_end_to_end(tmp_path):
    runner = CliRunner()
    env = {"SOVEREIGN_HOME": str(tmp_path)}
    assert runner.invoke(cli, ["init", "--demo"], env=env).exit_code == 0
    result = runner.invoke(cli, ["budget", "--json"], env=env)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["command"] == "budget"
    council = runner.invoke(cli, ["council", "Ouvrir", "une", "agence", "--cost=900000", "--json"], env=env)
    assert json.loads(council.output)["data"]["verdict"]["vetoes"]
    expense = runner.invoke(cli, ["finance", "expense", "50", "outils"], env=env)
    assert '"approved": true' in expense.output
    assert runner.invoke(cli, ["cycle", "daily"], env=env).exit_code == 0
    assert runner.invoke(cli, ["report", "weekly"], env=env).exit_code == 0
    assert list((tmp_path / "reports").glob("*weekly*.md"))
    assert runner.invoke(cli, ["ask", "/nope"], env=env).exit_code == 2


def test_csv_import_over_api_and_mcp(app, api):
    admin = {"Authorization": f"Bearer {_key(app, 'admin')}"}
    body = "date;montant;client\n03/11/2026;1 500,00;Client API\n"
    result = api.post("/v1/workspace/import-csv/collections", headers=admin, content=body.encode()).json()
    assert result["imported"] == 1
    writer = {"Authorization": f"Bearer {_key(app, 'write')}"}
    assert api.post("/v1/workspace/import-csv/collections", headers=writer, content=b"x").status_code == 403

    from mcp import Client

    async def scenario():
        async with Client(build_mcp(app)) as client:
            res = await client.call_tool(
                "import_csv", {"kind": "collections", "csv_text": "date,amount\n2026-11-04,200\n"}
            )
            assert res.structured_content["imported"] == 1

    asyncio.run(scenario())
    assert str(app.kernel.finance.collected_total()) == "9700.00"
