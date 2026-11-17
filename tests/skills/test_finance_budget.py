from __future__ import annotations

import sqlite3
import threading
from decimal import Decimal

import pytest

from sovereign.core.finance import FinanceError
from sovereign.kernel import Kernel


def test_envelope_is_ten_percent_of_collected(funded):
    status = funded.run_skill("finance-budget", "status").status
    assert status.collected_total == Decimal("10000.00")
    assert status.envelope == Decimal("1000.00")
    assert status.available == Decimal("1000.00")


def test_invoices_open_no_budget_until_collected(kernel):
    invoice = kernel.run_skill(
        "finance-budget", "issue_invoice", {"client": "ACME", "amount": "5000", "due_on": "2026-11-10"}
    ).invoice
    status = kernel.finance.status()
    assert status.envelope == 0 and status.receivables == Decimal("5000.00")
    assert any("échue" in a for a in status.alerts)
    kernel.run_skill("finance-budget", "collect_invoice", {"invoice_id": invoice.id})
    assert kernel.finance.envelope() == Decimal("500.00")
    with pytest.raises(FinanceError):
        kernel.finance.collect_invoice(invoice.id)


def test_overspending_is_refused(funded):
    out = funded.run_skill("finance-budget", "request_expense", {"amount": "1000.01", "category": "ads"})
    assert out.decision.expense.status == "rejected"
    assert funded.finance.available() == Decimal("1000.00")


def test_large_expense_goes_to_human_review(funded):
    out = funded.run_skill("finance-budget", "request_expense", {"amount": "600", "category": "ads"})
    assert out.decision.needs_review and out.decision.expense.status == "review"
    assert funded.finance.available() == Decimal("1000.00")  # rien n'est réservé tant que l'humain n'a pas validé
    expense = funded.finance.decide_review(out.decision.expense.id, approve=True, actor="ceo")
    assert expense.status == "approved"
    assert funded.finance.available() == Decimal("400.00")


def test_review_approval_rechecks_availability(funded):
    review = funded.finance.request_expense("600", "ads").expense  # en revue, non réservée
    assert funded.finance.request_expense("450", "tools").approved  # le disponible tombe à 550
    expense = funded.finance.decide_review(review.id, approve=True, actor="ceo")
    assert expense.status == "rejected"


def test_small_expense_approved_and_spent(funded):
    decision = funded.finance.request_expense("100", "tools", vendor="SaaS")
    assert decision.approved and "manuellement" in decision.message
    with pytest.raises(FinanceError):
        funded.finance.confirm_spent(decision.expense.id, "150")
    spent = funded.run_skill(
        "finance-budget", "confirm_expense", {"expense_id": decision.expense.id, "actual": "90"}
    ).expense
    assert spent.status == "spent" and spent.actual == Decimal("90.00")
    assert funded.finance.available() == Decimal("910.00")


def test_no_collection_means_no_spending(kernel):
    decision = kernel.finance.request_expense("1", "tools")
    assert decision.expense.status == "rejected"


def test_future_collection_rejected(kernel):
    with pytest.raises(FinanceError):
        kernel.finance.record_collection("100", "2027-01-01")


def test_collection_idempotent_on_reference(funded):
    again = funded.finance.record_collection("10000", "2026-11-01", reference="seed-1")
    assert again.duplicate
    assert funded.finance.collected_total() == Decimal("10000.00")


def test_refund_shrinks_envelope_and_blocks(funded):
    funded.finance.request_expense("400", "tools")
    funded.finance.record_refund("8000", "2026-11-10", reference="refund-1")
    status = funded.finance.status()
    assert status.envelope == Decimal("200.00")
    assert status.overdrawn and status.available == Decimal("-200.00")
    assert funded.finance.request_expense("1", "tools").expense.status == "rejected"


def test_ledger_and_amounts_are_immutable_in_sql(funded):
    with pytest.raises(sqlite3.IntegrityError):
        funded.db.execute("UPDATE ledger SET amount_cents = 1")
    with pytest.raises(sqlite3.IntegrityError):
        funded.db.execute("DELETE FROM ledger")
    expense = funded.finance.request_expense("50", "tools").expense
    with pytest.raises(sqlite3.IntegrityError):
        funded.db.execute("UPDATE expenses SET amount_cents = 1 WHERE id = ?", (expense.id,))
    with pytest.raises(sqlite3.IntegrityError):
        funded.db.execute("DELETE FROM expenses")


def test_foreign_currency_refused(funded):
    with pytest.raises(FinanceError):
        funded.finance.record_collection("10", currency="USD")


def test_concurrent_requests_never_exceed_envelope(tmp_path, clock):
    kernel = Kernel(Kernel.ephemeral().settings, clock=clock, db_path=tmp_path / "c.db")
    kernel.finance.record_collection("10000", "2026-11-01")
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        kernel.finance.request_expense("100", "tools")

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    approved = [e for e in kernel.finance.expenses() if e.status == "approved"]
    total = sum((e.amount for e in approved), Decimal("0"))
    assert total <= Decimal("1000.00")
    assert kernel.finance.available() >= 0
    kernel.close()


def test_threshold_event_emitted_once(funded):
    seen = []
    funded.events.subscribe("budget.threshold_crossed", lambda e: seen.append(e.payload["threshold"]))
    funded.finance.request_expense("450", "a")  # 45 % (≤ 50 % du disponible → approuvée)
    funded.finance.request_expense("250", "b")  # 70 %
    funded.finance.request_expense("120", "c")  # 82 % → franchit le seuil de 80 %
    funded.finance.request_expense("10", "d")  # 83 % → pas de second événement
    assert seen == [0.8]


def test_forecast_detects_exhaustion(kernel, clock):
    for month, amount in [
        ("2026-06", 3000),
        ("2026-07", 2500),
        ("2026-08", 2000),
        ("2026-09", 1500),
        ("2026-10", 1000),
        ("2026-11", 500),
    ]:
        kernel.finance.record_collection(amount, f"{month}-05")
    decision = kernel.finance.request_expense("200", "saas", recurring="monthly")
    assert decision.approved
    out = kernel.run_skill("finance-budget", "forecast", {"months": 6})
    assert out.exhaustion_month is not None
    assert out.monthly_trend is not None and out.monthly_trend < 0
    assert out.recommendations


def test_assess_produces_metrics_and_recommendations(kernel):
    kernel.finance.issue_invoice("Late Corp", "2000", "2026-09-01", "2026-10-01")
    for month, amount in [("2026-08", 3000), ("2026-09", 2000), ("2026-10", 1000)]:
        kernel.finance.record_collection(amount, f"{month}-10")
    out = kernel.run_skill("finance-budget", "assess")
    assert out.metrics["finance.envelope"] == 600.0
    titles = [r.title for r in out.recommendations]
    assert any("Relancer" in t for t in titles)
    assert any("baisse" in t for t in titles)
    assert all(r.id.startswith("rec_") and r.skill == "finance-budget" for r in out.recommendations)
    # Les métriques alimentent le modèle du monde
    assert kernel.db.scalar("SELECT COUNT(*) FROM observations WHERE metric = 'finance.envelope'") == 1


def test_check_spend(funded):
    out = funded.run_skill("finance-budget", "check_spend", {"amount": "1500"})
    assert not out.allowed and out.shortfall == Decimal("500.00")
