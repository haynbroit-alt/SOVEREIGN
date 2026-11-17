"""Webhooks entrants : vérification de signature puis normalisation en événements internes.

Sources reconnues :
- ``stripe``  : ``payment_intent.succeeded`` / ``invoice.paid`` / ``charge.refunded`` → encaissement / remboursement
                RÉEL (idempotent sur l'identifiant Stripe). C'est du suivi des encaissements, pas un accès bancaire.
- ``github``  : ``release`` publiée → ``github.release`` (veille), ``workflow_run`` échoué → ``github.ci_failed``.
- ``generic`` : ``{"type": "...", "payload": {...}, "id": "..."}`` signé selon le schéma SOVEREIGN.

Un webhook dont la signature est invalide est rejeté (401) et n'atteint jamais le bus d'événements.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sovereign.core.security import SignatureError, verify_webhook

if TYPE_CHECKING:
    from sovereign.app import Sovereign


class WebhookError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _cents_to_decimal(value: Any) -> Decimal:
    return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))


def handle_webhook(app: Sovereign, source: str, body: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
    settings = app.kernel.settings.security
    secret = settings.webhook_secrets.get(source)
    if not secret:
        raise WebhookError(404, f"source de webhook non configurée : {source}")
    try:
        verify_webhook(source, secret, body, headers, tolerance=settings.webhook_tolerance_seconds)
    except SignatureError as exc:
        raise WebhookError(401, str(exc)) from exc
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise WebhookError(400, "corps JSON invalide") from exc
    if source == "stripe":
        return _stripe(app, data)
    if source == "github":
        lower = {k.lower(): v for k, v in headers.items()}
        return _github(app, lower.get("x-github-event", ""), lower.get("x-github-delivery"), data)
    event_type = str(data.get("type", "")).strip()
    if not event_type or not all(part.isidentifier() or part.replace("-", "_").isidentifier()
                                 for part in event_type.split(".")):
        raise WebhookError(400, "champ 'type' manquant ou invalide")
    event = app.kernel.events.publish(event_type, data.get("payload") or {}, source=f"webhook:{source}",
                                      dedup_key=f"{source}:{data['id']}" if data.get("id") else None)
    return {"accepted": True, "duplicate": event is None, "event": event_type}


def _stripe(app: Sovereign, data: dict[str, Any]) -> dict[str, Any]:
    event_type = data.get("type", "")
    obj = (data.get("data") or {}).get("object") or {}
    finance = app.kernel.finance
    currency = str(obj.get("currency", finance.currency)).upper()
    today = app.kernel.clock.now().date()
    stamp = int(obj.get("created") or data.get("created") or 0)
    created = min(datetime.fromtimestamp(stamp, tz=UTC).date(), today) if stamp else today
    if event_type in ("payment_intent.succeeded", "invoice.paid"):
        amount = obj.get("amount_received", obj.get("amount_paid", obj.get("amount")))
        if amount is None:
            raise WebhookError(400, "montant absent")
        entry = finance.record_collection(
            _cents_to_decimal(amount), created, counterparty=obj.get("customer"),
            description=f"Stripe {event_type}", reference=f"stripe:{obj.get('id')}", currency=currency,
            source="stripe", actor="webhook:stripe",
        )
        return {"accepted": True, "ledger_entry": entry.id, "duplicate": entry.duplicate}
    if event_type == "charge.refunded":
        amount = obj.get("amount_refunded")
        if not amount:
            return {"accepted": True, "ignored": "remboursement nul"}
        entry = finance.record_refund(
            _cents_to_decimal(amount), created, counterparty=obj.get("customer"),
            description="Stripe charge.refunded", reference=f"stripe-refund:{obj.get('id')}", currency=currency,
            source="stripe", actor="webhook:stripe",
        )
        return {"accepted": True, "ledger_entry": entry.id, "duplicate": entry.duplicate}
    return {"accepted": True, "ignored": f"type Stripe non suivi : {event_type}"}


def _github(app: Sovereign, kind: str, delivery: str | None, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "release" and data.get("action") == "published":
        release = data.get("release") or {}
        event = app.kernel.events.publish("github.release", {
            "repository": (data.get("repository") or {}).get("full_name"), "tag": release.get("tag_name"),
            "url": release.get("html_url")}, source="webhook:github", dedup_key=f"github:{delivery}")
        return {"accepted": True, "event": "github.release", "duplicate": event is None}
    if kind == "workflow_run" and (data.get("workflow_run") or {}).get("conclusion") == "failure":
        run = data["workflow_run"]
        event = app.kernel.events.publish("github.ci_failed", {"name": run.get("name"), "url": run.get("html_url")},
                                          source="webhook:github", dedup_key=f"github:{delivery}")
        return {"accepted": True, "event": "github.ci_failed", "duplicate": event is None}
    return {"accepted": True, "ignored": f"événement GitHub non suivi : {kind}"}
