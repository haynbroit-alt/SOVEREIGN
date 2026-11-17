"""Cœur financier : encaissements réels, enveloppe de 10 %, garde-fou des dépenses.

Principes (non négociables) :
- Aucun accès bancaire. SOVEREIGN n'émet jamais de paiement : il AUTORISE ou REFUSE une dépense, l'humain paie.
- Seuls les revenus RÉELLEMENT ENCAISSÉS comptent. Une facture émise n'ouvre aucun budget tant qu'elle n'est
  pas encaissée.
- Enveloppe autorisée = ratio (≤ 10 %) × encaissements nets cumulés (encaissements − remboursements).
- Disponible = enveloppe − (dépenses approuvées non encore réglées + dépenses réglées).
- Toute demande est évaluée dans une transaction ``BEGIN IMMEDIATE`` : deux demandes concurrentes ne
  peuvent pas consommer deux fois le même euro.
- Le registre des encaissements et le journal d'audit sont append-only (triggers SQL).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from sovereign.config import FinanceSettings
from sovereign.core.clock import Clock, iso, parse_dt
from sovereign.core.db import Database, dumps
from sovereign.core.events import EventBus
from sovereign.core.ids import new_id
from sovereign.core.money import from_cents, to_cents, to_decimal

ExpenseStatus = Literal["approved", "rejected", "review", "spent", "cancelled"]
Recurrence = Literal["none", "monthly", "yearly"]


class FinanceError(Exception):
    pass


AGENT_ACTORS = frozenset({"mcp", "director", "worker", "cycle", "agent"})
"""Acteurs automatiques : ils ne peuvent jamais déclarer un encaissement, sinon ils ouvriraient leur propre budget."""


def is_agent(actor: str) -> bool:
    return actor in AGENT_ACTORS or actor.split(":", 1)[0] in AGENT_ACTORS


class LedgerEntry(BaseModel):
    id: str
    entry_type: Literal["collection", "refund"]
    amount: Decimal
    currency: str
    occurred_on: date
    counterparty: str | None = None
    description: str | None = None
    reference: str | None = None
    invoice_id: str | None = None
    source: str = "manual"
    duplicate: bool = False


class Expense(BaseModel):
    id: str
    amount: Decimal
    actual: Decimal | None = None
    currency: str
    category: str
    vendor: str | None = None
    description: str | None = None
    recurring: Recurrence = "none"
    status: ExpenseStatus
    decision_reason: str | None = None
    requested_by: str | None = None
    reference: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    spent_at: datetime | None = None


class ExpenseDecision(BaseModel):
    expense: Expense
    approved: bool
    needs_review: bool
    available_before: Decimal
    available_after: Decimal
    message: str


class Invoice(BaseModel):
    id: str
    client: str
    amount: Decimal
    issued_on: date
    due_on: date | None = None
    description: str | None = None
    status: Literal["open", "collected", "cancelled"] = "open"
    collected_entry_id: str | None = None


class FinanceStatus(BaseModel):
    currency: str
    spend_ratio: Decimal
    collected_total: Decimal
    envelope: Decimal
    committed: Decimal = Field(description="Dépenses approuvées non encore réglées")
    spent: Decimal
    available: Decimal
    utilization: float
    overdrawn: bool
    pending_review: int
    receivables: Decimal
    receivables_count: int
    alerts: list[str]


class FinanceLedger:
    def __init__(self, db: Database, clock: Clock, settings: FinanceSettings, events: EventBus | None = None) -> None:
        self.db = db
        self.clock = clock
        self.settings = settings
        self.events = events

    # -- utilitaires --------------------------------------------------------------------
    @property
    def currency(self) -> str:
        return self.settings.currency

    def _check_currency(self, currency: str | None) -> None:
        if currency and currency.upper() != self.currency:
            raise FinanceError(
                f"devise {currency} refusée : le registre est tenu en {self.currency} (pas de conversion implicite)"
            )

    def _audit(self, actor: str, action: str, entity: str, entity_id: str | None, details: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO audit_log(at, actor, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?, ?)",
            (iso(self.clock.now()), actor, action, entity, entity_id, dumps(details)),
        )

    def _emit(self, event_type: str, payload: dict[str, Any], dedup_key: str | None = None) -> None:
        if self.events is not None:
            self.events.publish(event_type, payload, source="finance", dedup_key=dedup_key)

    # -- encaissements -------------------------------------------------------------------
    def record_collection(
        self,
        amount: Decimal | str | float,
        occurred_on: date | str | None = None,
        *,
        counterparty: str | None = None,
        description: str | None = None,
        reference: str | None = None,
        invoice_id: str | None = None,
        currency: str | None = None,
        source: str = "manual",
        actor: str = "human",
    ) -> LedgerEntry:
        """Enregistre un encaissement réel. Idempotent sur ``reference`` (ex. id de paiement Stripe)."""

        return self._record(
            "collection",
            amount,
            occurred_on,
            counterparty,
            description,
            reference,
            invoice_id,
            currency,
            source,
            actor,
        )

    def record_refund(
        self,
        amount: Decimal | str | float,
        occurred_on: date | str | None = None,
        *,
        counterparty: str | None = None,
        description: str | None = None,
        reference: str | None = None,
        currency: str | None = None,
        source: str = "manual",
        actor: str = "human",
    ) -> LedgerEntry:
        return self._record(
            "refund", amount, occurred_on, counterparty, description, reference, None, currency, source, actor
        )

    def _record(
        self,
        entry_type: Literal["collection", "refund"],
        amount: Decimal | str | float,
        occurred_on: date | str | None,
        counterparty: str | None,
        description: str | None,
        reference: str | None,
        invoice_id: str | None,
        currency: str | None,
        source: str,
        actor: str,
    ) -> LedgerEntry:
        if entry_type == "collection" and is_agent(actor):
            raise FinanceError(
                "un agent IA ne peut pas déclarer d'encaissement (il ouvrirait son propre budget) : passez par la "
                "CLI, l'API admin, un webhook Stripe signé ou l'import d'un fichier par un humain"
            )
        self._check_currency(currency)
        value = to_decimal(amount)
        if value <= 0:
            raise FinanceError("le montant doit être strictement positif")
        day = parse_dt(occurred_on).date() if occurred_on else self.clock.now().date()
        if day > self.clock.now().date():
            raise FinanceError("un encaissement ne peut pas être daté dans le futur : seul l'argent reçu compte")
        with self.db.transaction():
            if reference:
                existing = self.db.one("SELECT * FROM ledger WHERE reference = ?", (reference,))
                if existing is not None:
                    entry = self._entry(existing)
                    entry.duplicate = True
                    return entry
            before = self._utilization()
            entry_id = new_id("led")
            self.db.execute(
                """INSERT INTO ledger(id, entry_type, amount_cents, currency, occurred_on, counterparty, description,
                                      reference, invoice_id, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    entry_type,
                    to_cents(value),
                    self.currency,
                    day.isoformat(),
                    counterparty,
                    description,
                    reference,
                    invoice_id,
                    source,
                    iso(self.clock.now()),
                ),
            )
            self._audit(
                actor,
                f"ledger.{entry_type}",
                "ledger",
                entry_id,
                {"amount": str(value), "reference": reference, "source": source},
            )
            after = self._utilization()
        self._emit(f"finance.{entry_type}_recorded", {"entry_id": entry_id, "amount": str(value), "source": source})
        self._check_thresholds(before, after)
        row = self.db.one("SELECT * FROM ledger WHERE id = ?", (entry_id,))
        if row is None:  # pragma: no cover — impossible après un INSERT validé
            raise FinanceError(f"écriture introuvable : {entry_id}")
        return self._entry(row)

    def _entry(self, row: Any) -> LedgerEntry:
        return LedgerEntry(
            id=row["id"],
            entry_type=row["entry_type"],
            amount=from_cents(row["amount_cents"]),
            currency=row["currency"],
            occurred_on=date.fromisoformat(row["occurred_on"]),
            counterparty=row["counterparty"],
            description=row["description"],
            reference=row["reference"],
            invoice_id=row["invoice_id"],
            source=row["source"],
        )

    def entries(self, limit: int = 500) -> list[LedgerEntry]:
        rows = self.db.query("SELECT * FROM ledger ORDER BY occurred_on DESC, created_at DESC LIMIT ?", (limit,))
        return [self._entry(r) for r in rows]

    def collected_total(self, since: date | None = None, until: date | None = None) -> Decimal:
        clauses, params = [], []
        if since:
            clauses.append("occurred_on >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("occurred_on <= ?")
            params.append(until.isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cents = self.db.scalar(
            f"""SELECT COALESCE(SUM(CASE entry_type WHEN 'collection' THEN amount_cents ELSE -amount_cents END), 0)
                FROM ledger{where}""",
            params,
        )
        return from_cents(int(cents or 0))

    # -- factures (créances : n'ouvrent AUCUN budget) ------------------------------------------
    def issue_invoice(
        self,
        client: str,
        amount: Decimal | str | float,
        issued_on: date | str | None = None,
        due_on: date | str | None = None,
        description: str | None = None,
    ) -> Invoice:
        value = to_decimal(amount)
        if value <= 0:
            raise FinanceError("le montant doit être strictement positif")
        invoice = Invoice(
            id=new_id("inv"),
            client=client,
            amount=value,
            issued_on=parse_dt(issued_on).date() if issued_on else self.clock.now().date(),
            due_on=parse_dt(due_on).date() if due_on else None,
            description=description,
        )
        self._save_invoice(invoice)
        self._audit("human", "invoice.issued", "invoice", invoice.id, {"amount": str(value), "client": client})
        return invoice

    def _save_invoice(self, invoice: Invoice) -> None:
        now = iso(self.clock.now())
        self.db.execute(
            """INSERT INTO records(kind, id, data, created_at, updated_at) VALUES ('invoice', ?, ?, ?, ?)
               ON CONFLICT(kind, id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (invoice.id, invoice.model_dump_json(), now, now),
        )

    def invoices(self, status: str | None = None) -> list[Invoice]:
        rows = self.db.query("SELECT data FROM records WHERE kind = 'invoice' ORDER BY created_at")
        items = [Invoice.model_validate_json(r["data"]) for r in rows]
        return [i for i in items if status is None or i.status == status]

    def collect_invoice(
        self,
        invoice_id: str,
        amount: Decimal | str | float | None = None,
        occurred_on: date | str | None = None,
        actor: str = "human",
    ) -> LedgerEntry:
        invoice = next((i for i in self.invoices() if i.id == invoice_id), None)
        if invoice is None:
            raise FinanceError(f"facture inconnue : {invoice_id}")
        if invoice.status != "open":
            raise FinanceError(f"facture {invoice_id} déjà {invoice.status}")
        entry = self.record_collection(
            amount if amount is not None else invoice.amount,
            occurred_on,
            counterparty=invoice.client,
            description=f"Encaissement facture {invoice.id}",
            reference=f"invoice:{invoice.id}",
            invoice_id=invoice.id,
            actor=actor,
        )
        invoice.status = "collected"
        invoice.collected_entry_id = entry.id
        self._save_invoice(invoice)
        return entry

    # -- dépenses ----------------------------------------------------------------------------
    def _committed_cents(self) -> tuple[int, int]:
        row = self.db.one(
            """SELECT
                 COALESCE(SUM(CASE WHEN status = 'approved' THEN amount_cents END), 0) AS approved,
                 COALESCE(SUM(CASE WHEN status = 'spent' THEN COALESCE(actual_cents, amount_cents) END), 0) AS spent
               FROM expenses"""
        )
        if row is None:  # pragma: no cover — une agrégation SQL renvoie toujours une ligne
            raise FinanceError("agrégation des dépenses impossible")
        return int(row["approved"]), int(row["spent"])

    def envelope(self) -> Decimal:
        return to_decimal(self.collected_total() * self.settings.spend_ratio)

    def available(self) -> Decimal:
        approved, spent = self._committed_cents()
        return to_decimal(self.envelope() - from_cents(approved + spent))

    def can_spend(self, amount: Decimal | str | float) -> bool:
        value = to_decimal(amount)
        return value > 0 and value <= self.available()

    def _utilization(self) -> float:
        envelope = self.envelope()
        approved, spent = self._committed_cents()
        used = from_cents(approved + spent)
        if envelope <= 0:
            return 1.0 if used > 0 else 0.0
        return float(used / envelope)

    def _check_thresholds(self, before: float, after: float) -> None:
        for threshold in self.settings.alert_thresholds:
            level = float(threshold)
            if before < level <= after:
                envelope_cents = to_cents(self.envelope())
                self._emit(
                    "budget.threshold_crossed",
                    {"threshold": level, "utilization": round(after, 4), "available": str(self.available())},
                    dedup_key=f"budget-threshold:{level}:{envelope_cents}",
                )

    def request_expense(
        self,
        amount: Decimal | str | float,
        category: str,
        *,
        vendor: str | None = None,
        description: str | None = None,
        recurring: Recurrence = "none",
        requested_by: str = "human",
        reference: str | None = None,
        currency: str | None = None,
    ) -> ExpenseDecision:
        """Évalue une demande de dépense. N'effectue AUCUN paiement."""

        self._check_currency(currency)
        value = to_decimal(amount)
        if value <= 0:
            raise FinanceError("le montant doit être strictement positif")
        with self.db.transaction():
            if reference:
                existing = self.db.one("SELECT * FROM expenses WHERE reference = ?", (reference,))
                if existing is not None:
                    expense = self._expense(existing)
                    available = self.available()
                    return ExpenseDecision(
                        expense=expense,
                        approved=expense.status in ("approved", "spent"),
                        needs_review=expense.status == "review",
                        available_before=available,
                        available_after=available,
                        message="Demande déjà enregistrée (référence identique).",
                    )
            before_util = self._utilization()
            available = self.available()
            if available <= 0:
                status, reason = (
                    "rejected",
                    "Aucun budget disponible : l'enveloppe de 10 % des encaissements est épuisée.",
                )
            elif value > available:
                status, reason = (
                    "rejected",
                    (f"Dépassement refusé : {value} demandés pour {available} disponibles (10 % des encaissements)."),
                )
            elif value > available * self.settings.review_threshold_share:
                status, reason = (
                    "review",
                    (
                        f"Dépense importante ({value} > {int(self.settings.review_threshold_share * 100)} % du "
                        f"disponible {available}) : validation humaine requise."
                    ),
                )
            else:
                status, reason = "approved", "Dans l'enveloppe autorisée."
            expense_id = new_id("exp")
            now = iso(self.clock.now())
            self.db.execute(
                """INSERT INTO expenses(id, amount_cents, currency, category, vendor, description, recurring, status,
                                        decision_reason, requested_by, reference, created_at, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    expense_id,
                    to_cents(value),
                    self.currency,
                    category,
                    vendor,
                    description,
                    recurring,
                    status,
                    reason,
                    requested_by,
                    reference,
                    now,
                    now if status != "review" else None,
                ),
            )
            self._audit(
                requested_by,
                f"expense.{status}",
                "expense",
                expense_id,
                {"amount": str(value), "category": category, "reason": reason},
            )
            after_available = self.available()
            after_util = self._utilization()
        expense = self.get_expense(expense_id)
        self._emit(f"expense.{status}", {"expense_id": expense_id, "amount": str(value), "category": category})
        self._check_thresholds(before_util, after_util)
        return ExpenseDecision(
            expense=expense,
            approved=status == "approved",
            needs_review=status == "review",
            available_before=available,
            available_after=after_available,
            message=reason
            + (
                " Paiement à effectuer manuellement : SOVEREIGN n'a aucun accès bancaire."
                if status == "approved"
                else ""
            ),
        )

    def decide_review(self, expense_id: str, approve: bool, actor: str, reason: str | None = None) -> Expense:
        """Décision humaine sur une dépense en revue. L'approbation re-vérifie le disponible."""

        with self.db.transaction():
            expense = self.get_expense(expense_id)
            if expense.status != "review":
                raise FinanceError(f"la dépense {expense_id} n'est pas en revue ({expense.status})")
            before_util = self._utilization()
            if approve and expense.amount > self.available():
                status, why = "rejected", "Approbation impossible : le disponible a baissé entre-temps."
            else:
                status = "approved" if approve else "rejected"
                why = reason or ("Approuvée par validation humaine." if approve else "Refusée par validation humaine.")
            self.db.execute(
                "UPDATE expenses SET status = ?, decision_reason = ?, decided_at = ? WHERE id = ?",
                (status, why, iso(self.clock.now()), expense_id),
            )
            self._audit(actor, f"expense.review_{status}", "expense", expense_id, {"reason": why})
            after_util = self._utilization()
        self._emit(f"expense.{status}", {"expense_id": expense_id, "amount": str(expense.amount)})
        self._check_thresholds(before_util, after_util)
        return self.get_expense(expense_id)

    def confirm_spent(
        self, expense_id: str, actual: Decimal | str | float | None = None, actor: str = "human"
    ) -> Expense:
        """Déclare qu'une dépense approuvée a été réglée (par l'humain). Le réel ne peut excéder l'approuvé."""

        with self.db.transaction():
            expense = self.get_expense(expense_id)
            if expense.status != "approved":
                raise FinanceError(f"seule une dépense approuvée peut être réglée ({expense.status})")
            actual_value = to_decimal(actual) if actual is not None else expense.amount
            if actual_value < 0:
                raise FinanceError("montant réel négatif")
            if actual_value > expense.amount:
                raise FinanceError(
                    f"le montant réel {actual_value} dépasse l'approuvé {expense.amount} : "
                    "faites une nouvelle demande pour la différence"
                )
            self.db.execute(
                "UPDATE expenses SET status = 'spent', actual_cents = ?, spent_at = ? WHERE id = ?",
                (to_cents(actual_value), iso(self.clock.now()), expense_id),
            )
            self._audit(actor, "expense.spent", "expense", expense_id, {"actual": str(actual_value)})
        self._emit("expense.spent", {"expense_id": expense_id, "actual": str(actual_value)})
        return self.get_expense(expense_id)

    def cancel_expense(self, expense_id: str, actor: str = "human") -> Expense:
        with self.db.transaction():
            expense = self.get_expense(expense_id)
            if expense.status not in ("approved", "review"):
                raise FinanceError(f"impossible d'annuler une dépense {expense.status}")
            self.db.execute("UPDATE expenses SET status = 'cancelled' WHERE id = ?", (expense_id,))
            self._audit(actor, "expense.cancelled", "expense", expense_id, {})
        return self.get_expense(expense_id)

    def get_expense(self, expense_id: str) -> Expense:
        row = self.db.one("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        if row is None:
            raise FinanceError(f"dépense inconnue : {expense_id}")
        return self._expense(row)

    def _expense(self, row: Any) -> Expense:
        return Expense(
            id=row["id"],
            amount=from_cents(row["amount_cents"]),
            actual=from_cents(row["actual_cents"]) if row["actual_cents"] is not None else None,
            currency=row["currency"],
            category=row["category"],
            vendor=row["vendor"],
            description=row["description"],
            recurring=row["recurring"],
            status=row["status"],
            decision_reason=row["decision_reason"],
            requested_by=row["requested_by"],
            reference=row["reference"],
            created_at=parse_dt(row["created_at"]),
            decided_at=parse_dt(row["decided_at"]) if row["decided_at"] else None,
            spent_at=parse_dt(row["spent_at"]) if row["spent_at"] else None,
        )

    def expenses(self, status: str | None = None, limit: int = 500) -> list[Expense]:
        if status:
            rows = self.db.query(
                "SELECT * FROM expenses WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM expenses ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._expense(r) for r in rows]

    # -- synthèses ----------------------------------------------------------------------------
    def monthly_collections(self) -> list[tuple[str, Decimal]]:
        rows = self.db.query(
            """SELECT substr(occurred_on, 1, 7) AS month,
                      SUM(CASE entry_type WHEN 'collection' THEN amount_cents ELSE -amount_cents END) AS cents
               FROM ledger GROUP BY month ORDER BY month"""
        )
        return [(r["month"], from_cents(int(r["cents"]))) for r in rows]

    def monthly_spend(self) -> list[tuple[str, Decimal]]:
        rows = self.db.query(
            """SELECT substr(COALESCE(spent_at, decided_at, created_at), 1, 7) AS month,
                      SUM(COALESCE(actual_cents, amount_cents)) AS cents
               FROM expenses WHERE status IN ('approved', 'spent') GROUP BY month ORDER BY month"""
        )
        return [(r["month"], from_cents(int(r["cents"]))) for r in rows]

    def status(self) -> FinanceStatus:
        approved, spent = self._committed_cents()
        envelope = self.envelope()
        available = self.available()
        utilization = self._utilization()
        open_invoices = self.invoices("open")
        receivables = sum((i.amount for i in open_invoices), Decimal("0"))
        pending = int(self.db.scalar("SELECT COUNT(*) FROM expenses WHERE status = 'review'") or 0)
        alerts: list[str] = []
        if available < 0:
            alerts.append("Enveloppe dépassée (remboursements ultérieurs) : toute nouvelle dépense est bloquée.")
        elif envelope == 0:
            alerts.append("Aucun encaissement enregistré : aucune dépense ne peut être autorisée.")
        else:
            for threshold in sorted(self.settings.alert_thresholds, reverse=True):
                if utilization >= float(threshold):
                    alerts.append(f"Budget consommé à {utilization:.0%} (seuil {float(threshold):.0%}).")
                    break
        today = self.clock.now().date()
        overdue = [i for i in open_invoices if i.due_on and i.due_on < today]
        if overdue:
            total = sum((i.amount for i in overdue), Decimal("0"))
            alerts.append(f"{len(overdue)} facture(s) échue(s) non encaissée(s) pour {total} : relancer.")
        if pending:
            alerts.append(f"{pending} dépense(s) en attente de validation humaine.")
        return FinanceStatus(
            currency=self.currency,
            spend_ratio=self.settings.spend_ratio,
            collected_total=self.collected_total(),
            envelope=envelope,
            committed=from_cents(approved),
            spent=from_cents(spent),
            available=available,
            utilization=round(utilization, 4),
            overdrawn=available < 0,
            pending_review=pending,
            receivables=to_decimal(receivables),
            receivables_count=len(open_invoices),
            alerts=alerts,
        )
