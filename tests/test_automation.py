from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import pytest

from sovereign.app import Sovereign
from sovereign.automation.webhooks import WebhookError, handle_webhook
from sovereign.core.clock import FixedClock
from sovereign.core.security import sign_payload


@pytest.fixture
def app(clock):
    application = Sovereign.ephemeral(clock=clock)
    yield application
    application.close()


# -- file durable -----------------------------------------------------------------------------------
def test_queue_idempotency_and_priority(app):
    a = app.queue.enqueue("skill.run", {"n": 1}, idempotency_key="k")
    b = app.queue.enqueue("skill.run", {"n": 2}, idempotency_key="k")
    assert a.id == b.id
    high = app.queue.enqueue("skill.run", {"n": 3}, priority=9)
    assert app.queue.claim().id == high.id


def test_queue_retry_backoff_then_dead(app, clock):
    task = app.queue.enqueue("x", {}, max_attempts=2)
    claimed = app.queue.claim()
    failed = app.queue.fail(claimed.id, "boom")
    assert failed.status == "queued" and failed.run_after > clock.now()
    assert app.queue.claim() is None  # pas avant la fin de l'attente
    clock.advance(seconds=60)
    app.queue.claim()
    assert app.queue.fail(task.id, "boom").status == "dead"
    assert app.queue.retry_dead(task.id).status == "queued"


def test_expired_lease_is_recovered(app, clock):
    app.queue.enqueue("x", {})
    first = app.queue.claim()
    assert app.queue.claim() is None
    clock.advance(seconds=app.queue.lease_seconds + 1)  # le worker a « planté »
    again = app.queue.claim()
    assert again.id == first.id and again.attempts == 2


def test_steps_are_checkpointed(app):
    calls = []
    task = app.queue.enqueue("x", {})
    assert app.queue.step(task.id, "a", lambda: calls.append(1) or {"v": 1}) == {"v": 1}
    assert app.queue.step(task.id, "a", lambda: calls.append(2) or {"v": 2}) == {"v": 1}
    assert calls == [1] and app.queue.steps(task.id) == ["a"]


def test_worker_handles_unknown_and_failing_tasks(app):
    app.queue.enqueue("unknown.kind", {}, max_attempts=1)
    app.queue.enqueue("skill.run", {"skill": "finance-budget", "action": "nope"}, max_attempts=1)
    dead_events = []
    app.kernel.events.subscribe("task.dead", lambda e: dead_events.append(e))
    assert app.worker.run_once() == 3  # 2 tâches + 1 alerte « task.dead » générée par la règle
    assert app.queue.stats()["dead"] == 2
    assert len(dead_events) == 1  # le type inconnu échoue avant exécution
    assert any(n["level"] == "critical" for n in app.notifier.sent)


# -- cycles et planification ---------------------------------------------------------------------------
def test_cycles_are_idempotent_per_period(app):
    app.kernel.finance.record_collection("5000", "2026-11-01")
    first = app.cycles.run("daily")
    assert first["status"] == "succeeded" and first["report_id"]
    assert app.cycles.run("daily")["status"] == "skipped"
    assert app.cycles.run("daily", force=True)["status"] == "succeeded"


def test_scheduler_due_and_tick(app, clock):
    clock.set(datetime(2026, 11, 2, 6, 0, tzinfo=UTC))  # lundi 6 h : quotidien pas encore dû (7 h)
    assert "daily" not in app.scheduler.due()
    clock.set(datetime(2026, 11, 2, 8, 0, tzinfo=UTC))
    assert set(app.scheduler.due()) == {"daily", "weekly", "monthly"}
    enqueued = app.scheduler.tick()
    assert len(enqueued) == 3 and len(app.scheduler.tick()) == 3  # idempotent : mêmes tâches
    assert app.queue.stats()["queued"] == 3
    app.worker.run_once()
    assert app.scheduler.due() == []
    assert {c["cycle"] for c in app.cycles.history()} == {"daily", "weekly", "monthly"}


def test_monthly_cycle_renews_recurring_through_budget_gate(app, clock):
    app.kernel.finance.record_collection("10000", "2026-10-01")
    clock.set(datetime(2026, 10, 15, 9, tzinfo=UTC))
    assert app.kernel.finance.request_expense("100", "saas", vendor="CRM", recurring="monthly").approved
    clock.set(datetime(2026, 11, 3, 9, tzinfo=UTC))
    app.cycles.run("monthly")
    renewals = [e for e in app.kernel.finance.expenses() if (e.reference or "").startswith("renew:")]
    assert len(renewals) == 1 and renewals[0].status == "approved"


# -- règles (mode continu) -----------------------------------------------------------------------------
def test_budget_threshold_rule_notifies_and_diagnoses(app):
    app.kernel.finance.record_collection("10000", "2026-11-01")
    for amount in ("450", "250", "120"):
        app.kernel.finance.request_expense(amount, "x")
    kinds = sorted(t.kind for t in app.queue.list())
    assert "notify" in kinds and "skill.run" in kinds
    app.worker.run_once()
    assert any(n["title"] == "budget.threshold_crossed" for n in app.notifier.sent)


def test_rules_ignore_insignificant_events(app):
    app.kernel.events.publish("metric.anomaly", {"z": 1.0})
    assert app.queue.stats()["queued"] == 0
    app.kernel.events.publish("metric.anomaly", {"z": 5.0, "metric": "x"})
    assert app.queue.list()[0].kind == "director.mission"


def test_outcome_triggers_learning(app):
    d = app.kernel.memory.record("x", "y", category="seo")
    app.kernel.run_skill("memory-learning", "record_outcome", {"decision_id": d.id, "status": "succeeded"})
    assert any(t.payload.get("action") == "adapt_strategies" for t in app.queue.list())


# -- webhooks --------------------------------------------------------------------------------------------
@pytest.fixture
def hooked(clock):
    application = Sovereign.ephemeral(clock=FixedClock(datetime.now(UTC)), security={
        "webhook_secrets": {"stripe": "whsec_test", "github": "gh_secret", "generic": "gen_secret"}})
    yield application
    application.close()


def _stripe_headers(body: bytes, secret: str = "whsec_test") -> dict[str, str]:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}"}


def test_stripe_payment_becomes_real_collection(hooked):
    body = json.dumps({"type": "payment_intent.succeeded", "data": {"object": {
        "id": "pi_1", "amount_received": 125000, "currency": "eur", "customer": "cus_1"}}}).encode()
    result = handle_webhook(hooked, "stripe", body, _stripe_headers(body))
    assert result["accepted"] and not result["duplicate"]
    assert str(hooked.kernel.finance.collected_total()) == "1250.00"
    again = handle_webhook(hooked, "stripe", body, _stripe_headers(body))
    assert again["duplicate"] and str(hooked.kernel.finance.collected_total()) == "1250.00"


def test_stripe_refund_and_bad_signature(hooked):
    body = json.dumps({"type": "charge.refunded", "data": {"object": {
        "id": "ch_1", "amount_refunded": 5000, "currency": "eur"}}}).encode()
    with pytest.raises(WebhookError) as err:
        handle_webhook(hooked, "stripe", body, _stripe_headers(body, "wrong"))
    assert err.value.status == 401
    handle_webhook(hooked, "stripe", body, _stripe_headers(body))
    assert str(hooked.kernel.finance.collected_total()) == "-50.00"


def test_github_release_and_generic_event(hooked):
    body = json.dumps({"action": "published", "release": {"tag_name": "v2"},
                       "repository": {"full_name": "o/r"}}).encode()
    sig = "sha256=" + hmac.new(b"gh_secret", body, hashlib.sha256).hexdigest()
    out = handle_webhook(hooked, "github", body, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "release",
                                                  "X-GitHub-Delivery": "d1"})
    assert out["event"] == "github.release"
    generic = json.dumps({"type": "metric.anomaly", "id": "e1", "payload": {"z": 6, "metric": "x"}}).encode()
    result = handle_webhook(hooked, "generic", generic, sign_payload("gen_secret", generic))
    assert result["accepted"] and not result["duplicate"]
    assert handle_webhook(hooked, "generic", generic, sign_payload("gen_secret", generic))["duplicate"]
    with pytest.raises(WebhookError):
        handle_webhook(hooked, "unknown", generic, {})


def test_notifier_signs_outgoing_payloads():
    from sovereign.automation.notify import Notifier
    from sovereign.config import AutomationSettings
    from sovereign.core.security import verify_webhook

    captured = {}

    def transport(url, body, headers):
        captured.update(url=url, body=body, headers=headers)

    notifier = Notifier(AutomationSettings(notify_webhook_url="https://hooks.example.test/x",
                                           notify_webhook_secret="s"), transport=transport)
    assert notifier.send("Titre", "Texte")
    verify_webhook("generic", "s", captured["body"], captured["headers"])
    assert json.loads(captured["body"])["text"].startswith("*SOVEREIGN — Titre*")
