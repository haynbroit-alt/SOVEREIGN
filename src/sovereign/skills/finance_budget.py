"""SKILL 7 — FINANCE ET BUDGET.

Suit les revenus réellement encaissés, calcule automatiquement l'enveloppe de 10 %, contrôle chaque dépense,
prévoit les coûts et empêche tout dépassement. Aucun accès bancaire : SOVEREIGN autorise ou refuse, l'humain paie.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from sovereign.core.finance import Expense, ExpenseDecision, FinanceStatus, Invoice, LedgerEntry
from sovereign.core.money import to_decimal
from sovereign.core.stats import linear_fit
from sovereign.skills.base import (
    EmptyInput,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillOutput,
    TargetMetric,
    action,
)


class RevenueInput(BaseModel):
    amount: Decimal = Field(gt=0)
    occurred_on: date | None = None
    counterparty: str | None = None
    description: str | None = None
    reference: str | None = Field(None, description="Identifiant externe (idempotence), ex. id de paiement")
    currency: str | None = None


class RevenueOutput(SkillOutput):
    entry: LedgerEntry
    duplicate: bool
    status: FinanceStatus


class InvoiceInput(BaseModel):
    client: str
    amount: Decimal = Field(gt=0)
    issued_on: date | None = None
    due_on: date | None = None
    description: str | None = None


class InvoiceOutput(SkillOutput):
    invoice: Invoice


class CollectInvoiceInput(BaseModel):
    invoice_id: str
    amount: Decimal | None = Field(None, gt=0)
    occurred_on: date | None = None


class ExpenseRequestInput(BaseModel):
    amount: Decimal = Field(gt=0)
    category: str
    vendor: str | None = None
    description: str | None = None
    recurring: Literal["none", "monthly", "yearly"] = "none"
    reference: str | None = None
    currency: str | None = None


class ExpenseDecisionOutput(SkillOutput):
    decision: ExpenseDecision


class ConfirmExpenseInput(BaseModel):
    expense_id: str
    actual: Decimal | None = Field(None, ge=0)


class ExpenseOutput(SkillOutput):
    expense: Expense


class CheckSpendInput(BaseModel):
    amount: Decimal = Field(gt=0)


class CheckSpendOutput(SkillOutput):
    allowed: bool
    available: Decimal
    shortfall: Decimal


class StatusOutput(SkillOutput):
    status: FinanceStatus


class PlannedExpense(BaseModel):
    label: str
    amount: Decimal = Field(gt=0)
    month_offset: int = Field(0, ge=0, description="0 = mois courant")


class ForecastInput(BaseModel):
    months: int = Field(6, ge=1, le=36)
    planned_expenses: list[PlannedExpense] = Field(default_factory=list)


class ForecastMonth(BaseModel):
    month: str
    projected_collections: Decimal
    projected_envelope_increase: Decimal
    projected_costs: Decimal
    projected_available_end: Decimal


class ForecastOutput(SkillOutput):
    method: str
    monthly_trend: float | None
    months: list[ForecastMonth]
    exhaustion_month: str | None


def _months_between(start: str, end: str) -> int:
    y1, m1 = (int(x) for x in start.split("-"))
    y2, m2 = (int(x) for x in end.split("-"))
    return (y2 * 12 + m2) - (y1 * 12 + m1)


def _add_months(month: str, offset: int) -> str:
    year, mon = (int(x) for x in month.split("-"))
    total = year * 12 + (mon - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


class FinanceBudgetSkill(Skill):
    name = "finance-budget"
    version = "1.0.0"

    @action("Diagnostic financier complet de l'espace de travail (pour le directeur)")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> StatusOutput:
        status = ctx.finance.status()
        out = StatusOutput(status=status, summary=self._summary(status))
        out.metrics = {
            "finance.collected_total": float(status.collected_total),
            "finance.envelope": float(status.envelope),
            "finance.available": float(status.available),
            "finance.utilization": status.utilization,
            "finance.receivables": float(status.receivables),
            "finance.pending_review": float(status.pending_review),
        }
        for alert in status.alerts:
            out.insights.append(Insight(kind="risk", title=alert, severity="warning"))
        monthly = ctx.finance.monthly_collections()
        if len(monthly) >= 3:
            values = [float(v) for _, v in monthly[-6:]]
            fit = linear_fit(list(range(len(values))), values)
            base = sum(values) / len(values)
            if base > 0:
                trend = fit.slope / base
                out.metrics["finance.collections_trend"] = round(trend, 4)
                if trend < -0.05:
                    out.insights.append(
                        Insight(
                            kind="trend",
                            severity="warning",
                            title="Encaissements mensuels en baisse",
                            detail=f"Pente {fit.slope:.2f}/mois ({trend:.1%} de la moyenne) sur {len(values)} mois.",
                            metric="finance.collections_trend",
                            value=trend,
                        )
                    )
                    out.recommendations.append(
                        Recommendation(
                            title="Enrayer la baisse des encaissements",
                            expected_metric=TargetMetric(
                                metric="finance.collections_trend", direction="increase", target=0.0
                            ),
                            rationale="La tendance mensuelle est négative : prioriser relances, rétention et ventes.",
                            impact=8,
                            effort=5,
                            confidence=min(0.9, 0.5 + fit.r2 / 2),
                            urgency=8,
                            risk=0.1,
                            tags=["finance", "revenus"],
                            evidence=[f"pente={fit.slope:.2f}", f"r2={fit.r2:.2f}"],
                        )
                    )
                elif trend > 0.05:
                    out.insights.append(
                        Insight(
                            kind="strength",
                            title="Encaissements mensuels en hausse",
                            detail=f"+{trend:.1%} de la moyenne par mois.",
                            metric="finance.collections_trend",
                            value=trend,
                        )
                    )
        today = ctx.clock.now().date()
        overdue = [i for i in ctx.finance.invoices("open") if i.due_on and i.due_on < today]
        if overdue:
            total = sum((i.amount for i in overdue), Decimal("0"))
            weight = float(total / status.collected_total) if status.collected_total > 0 else 1.0
            out.recommendations.append(
                Recommendation(
                    title=f"Relancer {len(overdue)} facture(s) échue(s) ({total})",
                    expected_metric=TargetMetric(
                        metric="finance.receivables", direction="decrease", target=float(status.receivables - total)
                    ),
                    rationale="Une créance n'ouvre aucun budget tant qu'elle n'est pas encaissée.",
                    impact=min(10, 4 + 10 * weight),
                    effort=1.5,
                    confidence=0.8,
                    urgency=8,
                    risk=0.05,
                    estimated_value=to_decimal(total),
                    external=True,
                    tags=["finance", "trésorerie"],
                    evidence=[f"{i.client}: {i.amount} échue le {i.due_on}" for i in overdue[:5]],
                )
            )
        if status.pending_review:
            out.recommendations.append(
                Recommendation(
                    title=f"Statuer sur {status.pending_review} dépense(s) en revue",
                    expected_metric=TargetMetric(metric="finance.pending_review", direction="decrease", target=0.0),
                    rationale="Des dépenses importantes attendent une validation humaine.",
                    impact=4,
                    effort=1,
                    confidence=0.9,
                    urgency=6,
                    risk=0.05,
                    tags=["finance", "gouvernance"],
                )
            )
        if status.envelope > 0 and status.utilization >= 0.8:
            out.recommendations.append(
                Recommendation(
                    title="Geler les dépenses non essentielles",
                    rationale=f"{status.utilization:.0%} de l'enveloppe est consommée.",
                    impact=6,
                    effort=1,
                    confidence=0.85,
                    urgency=9,
                    risk=0.05,
                    tags=["finance", "budget"],
                )
            )
        return out

    @staticmethod
    def _summary(status: FinanceStatus) -> str:
        return (
            f"Encaissé {status.collected_total} {status.currency} → enveloppe {status.envelope} "
            f"({status.spend_ratio:.0%}), engagé {status.committed}, dépensé {status.spent}, "
            f"disponible {status.available} ({status.utilization:.0%} utilisé)."
        )

    @action("État du budget : encaissé, enveloppe 10 %, engagé, dépensé, disponible, alertes")
    def status(self, ctx: SkillContext, data: EmptyInput) -> StatusOutput:
        status = ctx.finance.status()
        return StatusOutput(status=status, summary=self._summary(status))

    @action("Enregistrer un encaissement réel (idempotent sur la référence)", risk="write")
    def record_revenue(self, ctx: SkillContext, data: RevenueInput) -> RevenueOutput:
        entry = ctx.finance.record_collection(
            data.amount,
            data.occurred_on,
            counterparty=data.counterparty,
            description=data.description,
            reference=data.reference,
            currency=data.currency,
            actor=ctx.caller,
        )
        status = ctx.finance.status()
        return RevenueOutput(entry=entry, duplicate=entry.duplicate, status=status, summary=self._summary(status))

    @action("Émettre une facture (créance : n'ouvre aucun budget avant encaissement)", risk="write")
    def issue_invoice(self, ctx: SkillContext, data: InvoiceInput) -> InvoiceOutput:
        invoice = ctx.finance.issue_invoice(data.client, data.amount, data.issued_on, data.due_on, data.description)
        return InvoiceOutput(invoice=invoice, summary=f"Facture {invoice.id} émise ({invoice.amount}).")

    @action("Déclarer l'encaissement d'une facture", risk="write")
    def collect_invoice(self, ctx: SkillContext, data: CollectInvoiceInput) -> RevenueOutput:
        entry = ctx.finance.collect_invoice(data.invoice_id, data.amount, data.occurred_on, actor=ctx.caller)
        status = ctx.finance.status()
        return RevenueOutput(entry=entry, duplicate=entry.duplicate, status=status, summary=self._summary(status))

    @action("Demander une dépense : approuvée, refusée ou envoyée en revue humaine", risk="write")
    def request_expense(self, ctx: SkillContext, data: ExpenseRequestInput) -> ExpenseDecisionOutput:
        decision = ctx.finance.request_expense(
            data.amount,
            data.category,
            vendor=data.vendor,
            description=data.description,
            recurring=data.recurring,
            requested_by=ctx.caller,
            reference=data.reference,
            currency=data.currency,
        )
        return ExpenseDecisionOutput(decision=decision, summary=decision.message)

    @action("Confirmer le règlement d'une dépense approuvée (montant réel ≤ approuvé)", risk="write")
    def confirm_expense(self, ctx: SkillContext, data: ConfirmExpenseInput) -> ExpenseOutput:
        expense = ctx.finance.confirm_spent(data.expense_id, data.actual, actor=ctx.caller)
        return ExpenseOutput(expense=expense, summary=f"Dépense {expense.id} réglée : {expense.actual}.")

    @action("Vérifier si un montant peut être dépensé, sans rien réserver")
    def check_spend(self, ctx: SkillContext, data: CheckSpendInput) -> CheckSpendOutput:
        available = ctx.finance.available()
        allowed = data.amount <= available
        shortfall = to_decimal(max(Decimal("0"), data.amount - available))
        return CheckSpendOutput(
            allowed=allowed,
            available=available,
            shortfall=shortfall,
            summary=("Autorisable." if allowed else f"Non autorisable : il manque {shortfall}."),
        )

    @action("Prévoir encaissements, enveloppe et coûts sur N mois")
    def forecast(self, ctx: SkillContext, data: ForecastInput) -> ForecastOutput:
        finance = ctx.finance
        ratio = finance.settings.spend_ratio
        monthly = finance.monthly_collections()
        current_month = ctx.clock.now().strftime("%Y-%m")
        values = [float(v) for _, v in monthly[-12:]]
        # Écart (en mois) entre le dernier mois encaissé et le mois courant : la projection part du mois courant.
        gap = _months_between(monthly[-1][0], current_month) if monthly else 0
        if len(values) >= 3:
            fit = linear_fit(list(range(len(values))), values)
            method = f"régression linéaire sur {len(values)} mois (r²={fit.r2:.2f})"
            trend: float | None = fit.slope

            def projection(step: int) -> Decimal:
                return to_decimal(max(0.0, fit.predict(len(values) - 1 + step)))
        elif values:
            avg = sum(values) / len(values)
            method = f"moyenne des {len(values)} mois disponibles (trop peu d'historique pour une tendance)"
            trend = None

            def projection(step: int) -> Decimal:
                return to_decimal(max(0.0, avg))
        else:
            method = "aucun historique d'encaissement : projection nulle"
            trend = None

            def projection(step: int) -> Decimal:
                return Decimal("0.00")

        recurring_monthly = sum(
            (
                e.actual or e.amount
                for e in finance.expenses()
                if e.recurring == "monthly" and e.status in ("approved", "spent")
            ),
            Decimal("0"),
        )
        recurring_yearly = sum(
            (
                e.actual or e.amount
                for e in finance.expenses()
                if e.recurring == "yearly" and e.status in ("approved", "spent")
            ),
            Decimal("0"),
        )
        available = finance.available()
        months: list[ForecastMonth] = []
        exhaustion: str | None = None
        for step in range(data.months):
            month = _add_months(current_month, step)
            collections = projection(gap + step)
            envelope_increase = to_decimal(collections * ratio)
            planned = sum((p.amount for p in data.planned_expenses if p.month_offset == step), Decimal("0"))
            costs = to_decimal(recurring_monthly + (recurring_yearly / 12) + planned)
            available = to_decimal(available + envelope_increase - costs)
            months.append(
                ForecastMonth(
                    month=month,
                    projected_collections=collections,
                    projected_envelope_increase=envelope_increase,
                    projected_costs=costs,
                    projected_available_end=available,
                )
            )
            if available < 0 and exhaustion is None:
                exhaustion = month
        out = ForecastOutput(method=method, monthly_trend=trend, months=months, exhaustion_month=exhaustion)
        if exhaustion:
            out.summary = f"Enveloppe épuisée prévue en {exhaustion} au rythme actuel des coûts."
            out.insights.append(
                Insight(kind="risk", severity="critical", title="Épuisement budgétaire prévu", detail=out.summary)
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Réduire les coûts récurrents avant {exhaustion}",
                    rationale="Les coûts projetés dépassent l'enveloppe générée par les encaissements prévus.",
                    impact=8,
                    effort=3,
                    confidence=0.6,
                    urgency=8,
                    risk=0.1,
                    tags=["finance", "coûts"],
                )
            )
        else:
            out.summary = f"Aucun épuisement prévu sur {data.months} mois ({method})."
        return out


SKILL = FinanceBudgetSkill
