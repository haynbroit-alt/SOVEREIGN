"""Cycles proactifs : QUOTIDIEN, HEBDOMADAIRE, MENSUEL.

Chaque cycle est un workflow durable : ses étapes sont journalisées (``TaskQueue.step``) — un cycle interrompu
reprend là où il s'était arrêté — et idempotent par période (un seul cycle quotidien par jour, etc.).
Aucun appel IA n'est fait par les cycles, sauf autorisation explicite (``llm.allow_in_cycles``).

QUOTIDIEN     analyser la situation, préparer les priorités du jour
HEBDOMADAIRE  évaluer les résultats, réviser les stratégies, auto-audit (dreaming)
MENSUEL       performance commerciale, dépenses, opportunités, renouvellement des dépenses récurrentes
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sovereign.core.clock import iso, period_key
from sovereign.core.ids import new_id

if TYPE_CHECKING:
    from sovereign.app import Sovereign

CYCLES = ("daily", "weekly", "monthly")
LABELS = {"daily": "Cycle quotidien", "weekly": "Cycle hebdomadaire", "monthly": "Cycle mensuel"}
MISSIONS = {
    "daily": "Analyser la situation du jour et préparer les priorités",
    "weekly": "Évaluer les résultats de la semaine et réviser les stratégies",
    "monthly": "Examiner la performance commerciale, les dépenses et les opportunités du mois",
}
# Skills de chaque cycle : bornées et choisies, pas « toutes les Skills à chaque fois ».
CYCLE_SKILLS = {
    "daily": ["finance-budget", "analysis-optimization", "intelligent-management", "watch-anticipation"],
    "weekly": ["memory-learning", "analysis-optimization", "marketing-strategy", "watch-anticipation"],
    "monthly": [
        "finance-budget",
        "business-intelligence",
        "marketing-strategy",
        "business-development",
        "innovation-creativity",
    ],
}


class CycleRunner:
    def __init__(self, app: Sovereign) -> None:
        self.app = app

    def _skills(self, cycle: str) -> list[str]:
        known = set(self.app.kernel.registry.names)
        available = self.app.kernel.available_data()
        selected = []
        for name in CYCLE_SKILLS[cycle]:
            if name not in known:
                continue
            manifest = self.app.kernel.registry.manifest(name)
            # Une Skill sans aucune donnée à analyser n'est pas appelée (sauf finance et mémoire, toujours utiles).
            if name in ("finance-budget", "memory-learning") or available.intersection(manifest.data):
                selected.append(name)
        return selected or ["finance-budget"]

    def run(self, cycle: str, *, task_id: str | None = None, force: bool = False) -> dict[str, Any]:
        if cycle not in CYCLES:
            raise ValueError(f"cycle inconnu : {cycle} (attendus : {', '.join(CYCLES)})")
        kernel = self.app.kernel
        now = kernel.clock.now()
        period = period_key(cycle, now)
        existing = kernel.db.one("SELECT * FROM cycle_runs WHERE cycle = ? AND period = ?", (cycle, period))
        if existing is not None and existing["status"] == "succeeded" and not force:
            return {
                "cycle": cycle,
                "period": period,
                "status": "skipped",
                "reason": "déjà exécuté pour la période",
                "report_id": existing["report_id"],
            }
        step_owner = task_id or new_id("cyc")
        kernel.db.execute(
            """INSERT INTO cycle_runs(cycle, period, status, started_at, task_id) VALUES (?, ?, 'running', ?, ?)
               ON CONFLICT(cycle, period) DO UPDATE SET status = 'running', started_at = excluded.started_at,
                    task_id = excluded.task_id""",
            (cycle, period, iso(now), step_owner),
        )
        queue = self.app.queue
        try:
            if cycle == "monthly":
                queue.step(step_owner, "renew_recurring", self._renew_recurring)
            if cycle == "weekly":
                queue.step(step_owner, "assign_missions", self._assign_missions)
            summary = queue.step(step_owner, "director", lambda: self._director(cycle))
            queue.step(
                step_owner,
                "notify",
                lambda: {
                    "sent": self.app.notifier.send(
                        f"{LABELS[cycle]} {period}", summary["summary"], {"report_id": summary["report_id"]}
                    )
                },
            )
        except Exception:
            kernel.db.execute(
                "UPDATE cycle_runs SET status = 'failed', finished_at = ? WHERE cycle = ? AND period = ?",
                (iso(kernel.clock.now()), cycle, period),
            )
            raise
        kernel.db.execute(
            "UPDATE cycle_runs SET status = 'succeeded', finished_at = ?, report_id = ? WHERE cycle = ? AND period = ?",
            (iso(kernel.clock.now()), summary["report_id"], cycle, period),
        )
        kernel.events.publish(
            f"cycle.{cycle}",
            {"period": period, "report_id": summary["report_id"]},
            source="scheduler",
            dedup_key=f"cycle:{cycle}:{period}:{summary['report_id']}",
        )
        return {
            "cycle": cycle,
            "period": period,
            "status": "succeeded",
            "report_id": summary["report_id"],
            "summary": summary["summary"],
        }

    def _director(self, cycle: str) -> dict[str, Any]:
        report = self.app.director.run(
            MISSIONS[cycle],
            skills=self._skills(cycle),
            mode=cycle,
            deep=cycle == "weekly",
            use_llm=self.app.kernel.settings.llm.allow_in_cycles,
        )
        path = self._write_report_file(cycle, report.report_id)
        return {"report_id": report.report_id, "summary": report.summary, "path": path}

    def _write_report_file(self, cycle: str, report_id: str | None) -> str | None:
        """Les rapports de cycle sont aussi écrits en Markdown (consultables hors base, artefacts CI)."""

        kernel = self.app.kernel
        if kernel.db.path == ":memory:" or report_id is None:
            return None
        row = kernel.db.one("SELECT content_md FROM reports WHERE id = ?", (report_id,))
        if row is None:
            return None
        directory = kernel.settings.reports_path
        directory.mkdir(parents=True, exist_ok=True)
        file = directory / f"{kernel.clock.now():%Y-%m-%d}-cycle-{cycle}-{report_id[-8:]}.md"
        file.write_text(row["content_md"], encoding="utf-8")
        return str(file)

    def _renew_recurring(self) -> dict[str, Any]:
        """Les dépenses récurrentes repassent par le garde-fou chaque mois (jamais d'engagement implicite)."""

        finance = self.app.kernel.finance
        month = self.app.kernel.clock.now().strftime("%Y-%m")
        renewed = []
        seen: set[tuple[str, str | None]] = set()
        for expense in finance.expenses():
            key = (expense.category, expense.vendor)
            if expense.recurring != "monthly" or expense.status not in ("approved", "spent") or key in seen:
                continue
            seen.add(key)
            if expense.created_at.strftime("%Y-%m") == month:
                continue
            decision = finance.request_expense(
                expense.actual or expense.amount,
                expense.category,
                vendor=expense.vendor,
                description=f"Renouvellement {month} : {expense.description or expense.category}",
                recurring="monthly",
                requested_by="cycle.monthly",
                reference=f"renew:{expense.category}:{expense.vendor}:{month}",
            )
            renewed.append({"category": expense.category, "status": decision.expense.status})
        return {"renewed": renewed}

    def _assign_missions(self) -> dict[str, Any]:
        kernel = self.app.kernel
        if "intelligent-management" not in kernel.registry.names or not kernel.store.list("agent"):
            return {"skipped": "aucune équipe d'agents déclarée"}
        out = kernel.run_skill("intelligent-management", "assign_missions", {"apply": True}, caller="cycle.weekly")
        return {"summary": out.summary}

    def history(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.app.kernel.db.query("SELECT * FROM cycle_runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
