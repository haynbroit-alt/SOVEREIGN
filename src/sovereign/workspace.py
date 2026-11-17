"""Import / export de l'espace de travail (YAML ou JSON).

Format :

    records:              # données métier (kinds des Skills : market, competitor, need, segment, campaign, …)
      market: [{id: m1, name: …}, …]
    collections:          # encaissements RÉELS → registre (idempotents sur reference)
      - {amount: "1200", date: 2026-10-05, counterparty: "Client A", reference: "pay_1"}
    refunds: […]
    invoices: [{client, amount, issued_on, due_on, description}]
    expenses:             # passent par le garde-fou des 10 % comme toute dépense
      - {amount: "80", category: outils, vendor: X, recurring: monthly, spent: true}
    beliefs: [{statement, topic, metric, comparator, threshold, confidence}]
    decisions: [{title, decision, category, tags, expected: {…}, review_in_days, outcome: {actual|status}}]

L'import est idempotent pour les enregistrements (upsert par id) et les encaissements (référence) ; les
dépenses sont soumises au garde-fou et peuvent donc être refusées — c'est voulu.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from sovereign.core.ids import stable_id

if TYPE_CHECKING:
    from sovereign.app import Sovereign


def load_file(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("le fichier doit contenir un dictionnaire racine")
    return data


def import_workspace(app: Sovereign, data: dict[str, Any], actor: str = "import") -> dict[str, Any]:
    kernel = app.kernel
    report: dict[str, Any] = {
        "records": {},
        "collections": 0,
        "refunds": 0,
        "invoices": 0,
        "expenses": {},
        "beliefs": 0,
        "decisions": 0,
        "warnings": [],
    }
    for kind, items in (data.get("records") or {}).items():
        for item in items or []:
            record_id = str(item.get("id") or stable_id(kind, json.dumps(item, sort_keys=True, default=str)))
            kernel.store.put(kind, record_id, {**item, "id": record_id})
        report["records"][kind] = len(items or [])
    for item in data.get("collections") or []:
        entry = kernel.finance.record_collection(
            item["amount"],
            item.get("date"),
            counterparty=item.get("counterparty"),
            description=item.get("description"),
            reference=item.get("reference"),
            source="import",
            actor=actor,
        )
        report["collections"] += 0 if entry.duplicate else 1
    for item in data.get("refunds") or []:
        entry = kernel.finance.record_refund(
            item["amount"],
            item.get("date"),
            counterparty=item.get("counterparty"),
            reference=item.get("reference"),
            source="import",
            actor=actor,
        )
        report["refunds"] += 0 if entry.duplicate else 1
    existing_invoices = {(i.client, str(i.amount), i.issued_on.isoformat()) for i in kernel.finance.invoices()}
    for item in data.get("invoices") or []:
        key = (item["client"], str(item["amount"]), str(item.get("issued_on")))
        if key in existing_invoices:
            continue
        invoice = kernel.finance.issue_invoice(
            item["client"], item["amount"], item.get("issued_on"), item.get("due_on"), item.get("description")
        )
        if item.get("collected_on"):
            kernel.finance.collect_invoice(invoice.id, occurred_on=item["collected_on"], actor=actor)
        report["invoices"] += 1
    for index, item in enumerate(data.get("expenses") or []):
        expense_decision = kernel.finance.request_expense(
            item["amount"],
            item["category"],
            vendor=item.get("vendor"),
            description=item.get("description"),
            recurring=item.get("recurring", "none"),
            requested_by=actor,
            reference=item.get("reference") or f"import:{item['category']}:{item.get('vendor')}:{index}",
        )
        expense_status = expense_decision.expense.status
        if expense_status == "approved" and item.get("spent"):
            kernel.finance.confirm_spent(expense_decision.expense.id, item.get("actual"), actor=actor)
            expense_status = "spent"
        report["expenses"][expense_status] = report["expenses"].get(expense_status, 0) + 1
        if expense_status == "rejected":
            report["warnings"].append(f"dépense refusée par le garde-fou : {item['category']} {item['amount']}")
    known_beliefs = {b.statement for b in app.world.list()}
    for item in data.get("beliefs") or []:
        if item["statement"] in known_beliefs:
            continue
        app.world.add(
            item["statement"],
            topic=item.get("topic", "general"),
            metric=item.get("metric"),
            comparator=item.get("comparator"),
            threshold=item.get("threshold"),
            threshold_high=item.get("threshold_high"),
            confidence=item.get("confidence", 0.6),
            source=actor,
        )
        report["beliefs"] += 1
    known_decisions = {d.title for d in kernel.memory.list(limit=10000)}
    for item in data.get("decisions") or []:
        if item["title"] in known_decisions:
            continue
        review_in = item.get("review_in_days")
        review_at = kernel.clock.now() + timedelta(days=review_in) if review_in is not None else item.get("review_at")
        decision = kernel.memory.record(
            item["title"],
            item["decision"],
            context=item.get("context", ""),
            rationale=item.get("rationale", ""),
            category=item.get("category", "general"),
            tags=item.get("tags"),
            skills=item.get("skills"),
            confidence=item.get("confidence", 0.5),
            expected=item.get("expected"),
            review_at=review_at,
            source=item.get("source") or actor,
        )
        outcome = item.get("outcome")
        closed_status = item.get("status") if item.get("status") not in (None, "open") else None
        if outcome or closed_status:
            outcome = outcome or {}
            # Statut explicite si fourni (format d'export), sinon calculé depuis le réel et l'attendu.
            status = outcome.get("status") or (
                closed_status if outcome.get("actual") is None or not item.get("expected") else None
            )
            kernel.memory.record_outcome(
                decision.id,
                actual=outcome.get("actual"),
                status=status,
                notes=outcome.get("notes", ""),
                lesson=item.get("lesson"),
            )
        report["decisions"] += 1
    kernel.events.publish("workspace.imported", {k: v for k, v in report.items() if k != "warnings"}, source=actor)
    return report


def export_workspace(app: Sovereign) -> dict[str, Any]:
    kernel = app.kernel
    records = {kind: kernel.store.list(kind) for kind in kernel.store.kinds()}
    return {
        "records": records,
        "collections": [
            {
                "amount": str(e.amount),
                "date": e.occurred_on.isoformat(),
                "counterparty": e.counterparty,
                "reference": e.reference or e.id,
            }
            for e in kernel.finance.entries(100000)
            if e.entry_type == "collection"
        ],
        "refunds": [
            {"amount": str(e.amount), "date": e.occurred_on.isoformat(), "reference": e.reference or e.id}
            for e in kernel.finance.entries(100000)
            if e.entry_type == "refund"
        ],
        "beliefs": [
            b.model_dump(
                mode="json",
                include={
                    "statement",
                    "topic",
                    "metric",
                    "comparator",
                    "threshold",
                    "threshold_high",
                    "confidence",
                },
            )
            for b in app.world.list()
        ],
        "decisions": [d.model_dump(mode="json") for d in kernel.memory.list(limit=100000)],
    }


DEMO_PATH = Path(__file__).parent / "data" / "demo_workspace.yaml"
