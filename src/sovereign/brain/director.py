"""Le DIRECTEUR : boucle cognitive complète.

    OBSERVER → COMPRENDRE → CONFRONTER → (HYPOTHÈSES) → ANTICIPER → PRIORISER → DÉCIDER (CEO + Risque)
    → SIMULER → EXÉCUTER (risque faible) | REVUE HUMAINE (risque élevé) → MESURER → APPRENDRE → RÉVISER
    → DREAMING (auto-audit, anticipation) → retour

Chaque phase est tracée dans le rapport. Les Skills sont sélectionnées selon la mission (mots-clés, événements,
données disponibles, utilité apprise) : seules les pertinentes sont chargées et exécutées.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from sovereign.brain.council import CouncilFacts, CouncilVerdict
from sovereign.brain.hypotheses import Hypothesis, explain
from sovereign.brain.prioritize import Priority, prioritize
from sovereign.brain.simulation import SimulationResult, simulate
from sovereign.brain.world_model import Confrontation
from sovereign.core.clock import iso, parse_dt, period_key
from sovereign.core.db import dumps
from sovereign.core.ids import new_id
from sovereign.core.stats import BetaPosterior, linear_fit
from sovereign.core.telemetry import span
from sovereign.skills.base import Insight, Recommendation
from sovereign.skills.registry import stems

if TYPE_CHECKING:
    from sovereign.app import Sovereign

Route = Literal["execute", "mission", "human_review", "none", "already_handled"]


class Phase(BaseModel):
    name: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionRecord(BaseModel):
    priority: Priority
    verdict: CouncilVerdict | None
    simulation: SimulationResult | None
    route: Route
    task_id: str | None = None
    approval_id: str | None = None
    mission_ref: str | None = None
    decision_id: str | None = None
    note: str = ""


class Forecast(BaseModel):
    metric: str
    current: float
    slope_per_day: float
    horizon_days: int
    projected: float
    points: int
    r2: float


class DirectorReport(BaseModel):
    mission_id: str
    mission: str
    mode: str
    at: datetime
    selected: list[dict[str, Any]]
    phases: list[Phase]
    metrics: dict[str, float]
    insights: list[Insight]
    confrontations: list[Confrontation]
    hypotheses: list[Hypothesis]
    forecasts: list[Forecast]
    priorities: list[Priority]
    decisions: list[DecisionRecord]
    measured: list[dict[str, Any]]
    learned: list[str]
    audit: list[str]
    skill_errors: list[str]
    summary: str
    report_id: str | None = None


DEFAULT_SITUATION = ("finance-budget", "memory-learning")


class Director:
    def __init__(self, app: Sovereign) -> None:
        self.app = app
        self.kernel = app.kernel

    # -- utilitaires -------------------------------------------------------------------------
    def _choose_skills(self, mission: str, events: list[str], forced: list[str] | None,
                       limit: int) -> list[dict[str, Any]]:
        registry = self.kernel.registry
        if forced:
            return [{"name": n, "score": None, "reasons": ["imposée par la commande"]} for n in forced
                    if n in registry.manifests]
        available = self.kernel.available_data()
        picks = registry.select(mission, events=events, available_data=available,
                                priors=self.kernel.skill_priors(), limit=limit)
        chosen = [{"name": p.name, "score": p.score, "reasons": p.reasons} for p in picks]
        if not chosen:
            ranked = sorted(
                (m for m in registry.manifests.values() if available.intersection(m.data)),
                key=lambda m: -len(available.intersection(m.data)),
            )
            names = [m.name for m in ranked][:limit]
            for default in DEFAULT_SITUATION:
                if default not in names and len(names) < limit + 1 and default in registry.manifests:
                    names.insert(0, default)
            chosen = [{"name": n, "score": None, "reasons": ["vue d'ensemble (aucun mot-clé spécifique)"]}
                      for n in names[: limit + 1]]
        return chosen

    def _history(self, metric: str, limit: int = 30) -> list[tuple[datetime, float]]:
        rows = self.kernel.db.query(
            "SELECT at, value FROM observations WHERE metric = ? ORDER BY seq DESC LIMIT ?", (metric, limit))
        return [(parse_dt(r["at"]), float(r["value"])) for r in reversed(rows)]

    def _forecast(self, metrics: dict[str, float], horizon: int = 30) -> list[Forecast]:
        forecasts: list[Forecast] = []
        for metric in sorted(metrics):
            history = self._history(metric)
            if len({t for t, _ in history}) < 3:
                continue
            origin = history[0][0]
            xs = [(t - origin).total_seconds() / 86400 for t, _ in history]
            ys = [v for _, v in history]
            fit = linear_fit(xs, ys)
            projected = fit.predict(xs[-1] + horizon)
            current = ys[-1]
            change = abs(projected - current) / (abs(current) or 1.0)
            if change >= 0.1 or fit.r2 >= 0.6:
                forecasts.append(Forecast(metric=metric, current=round(current, 4),
                                          slope_per_day=round(fit.slope, 6), horizon_days=horizon,
                                          projected=round(projected, 4), points=len(history), r2=round(fit.r2, 3)))
        return forecasts

    def _team_load(self) -> float | None:
        agents = self.kernel.store.list("agent")
        if not agents:
            return None
        capacity = sum(int(a.get("capacity", 1) or 1) for a in agents)
        active = sum(1 for m in self.kernel.store.list("mission") if m.get("status") in ("assigned", "in_progress"))
        return active / capacity if capacity else None

    def _strategic_fit(self, rec: Recommendation) -> float:
        objectives = self.kernel.store.list("objective")
        if not objectives:
            return 0.5
        rec_stems = stems(" ".join([rec.title, rec.rationale, *rec.tags]))
        best = 0.0
        for objective in objectives:
            obj_stems = stems(" ".join([str(objective.get("title", "")), *objective.get("tags", [])]))
            if obj_stems:
                best = max(best, len(rec_stems & obj_stems) / len(obj_stems))
        return round(min(1.0, 0.3 + best), 3)

    def _past(self, rec: Recommendation) -> tuple[float | None, int]:
        category = rec.tags[0] if rec.tags else None
        if not category:
            return None, 0
        rows = self.kernel.db.query(
            "SELECT status FROM decisions WHERE status IN ('succeeded', 'partial', 'failed') "
            "AND (category = ? OR (' ' || tags || ' ') LIKE ?)",
            (category, f"% {category} %"),
        )
        if not rows:
            return None, 0
        succeeded = sum(1.0 if r["status"] == "succeeded" else 0.5 if r["status"] == "partial" else 0.0
                        for r in rows)
        return round(BetaPosterior(succeeded, len(rows) - succeeded).mean, 3), len(rows)

    def _already_handled(self, rec: Recommendation) -> str | None:
        if self.kernel.db.one(
            "SELECT 1 FROM recommendation_log WHERE id = ? AND status IN ('adopted', 'executed')", (rec.id,)):
            return "déjà adoptée"
        if self.kernel.db.one(
            "SELECT 1 FROM approvals WHERE status = 'pending' AND json_extract(payload, '$.recommendation.id') = ?",
            (rec.id,)):
            return "en attente de validation humaine"
        return None

    # -- boucle ------------------------------------------------------------------------------
    def run(
        self,
        mission: str = "Analyse de situation",
        *,
        events: list[str] | None = None,
        skills: list[str] | None = None,
        mode: str = "mission",
        deep: bool = False,
        decide_top: int = 3,
        execute: bool = True,
        use_llm: bool = False,
        skill_limit: int = 4,
    ) -> DirectorReport:
        kernel = self.kernel
        now = kernel.clock.now()
        mission_id = new_id("mis")
        phases: list[Phase] = []
        errors: list[str] = []
        with span("director.run", mission=mission[:80], mode=mode):
            # 1. OBSERVER --------------------------------------------------------------------
            selected = self._choose_skills(mission, events or [], skills, skill_limit)
            outputs = {}
            for choice in selected:
                try:
                    outputs[choice["name"]] = kernel.run_skill(choice["name"], "assess", mission_id=mission_id,
                                                               caller="director")
                except Exception as exc:  # une Skill défaillante ne doit pas aveugler le directeur
                    errors.append(f"{choice['name']}: {type(exc).__name__}: {exc}")
            metrics: dict[str, float] = {}
            insights: list[Insight] = []
            recommendations: list[Recommendation] = []
            for out in outputs.values():
                metrics.update(out.metrics)
                insights.extend(out.insights)
                recommendations.extend(out.recommendations)
            phases.append(Phase(name="OBSERVER", summary=(
                f"{len(outputs)} Skill(s) mobilisée(s) sur {len(kernel.registry.names)} : "
                + ", ".join(outputs) + f" — {len(metrics)} métriques, {len(insights)} constats."),
                details={"selected": selected, "errors": errors}))

            # 2. COMPRENDRE ------------------------------------------------------------------
            beliefs = self.app.world.list()
            relevant = [b for b in beliefs if (b.metric and b.metric in metrics) or not b.testable]
            phases.append(Phase(name="COMPRENDRE", summary=(
                f"Modèle du monde : {len(beliefs)} croyance(s), {len(relevant)} en lien avec la mission."),
                details={"beliefs": [{"statement": b.statement, "confidence": b.confidence} for b in relevant[:10]]}))

            # 3. CONFRONTER ------------------------------------------------------------------
            confrontations = self.app.world.confront(metrics)
            contradictions = [c for c in confrontations if c.outcome == "contradiction"]
            phases.append(Phase(name="CONFRONTER", summary=(
                f"{len(confrontations) - len(contradictions)} confirmation(s), {len(contradictions)} contradiction(s)."
            )))

            # 4. HYPOTHÈSES (uniquement sur contradiction) ----------------------------------------
            hypotheses = explain(contradictions)
            investigated: list[str] = []
            if deep and hypotheses:
                for hypothesis in hypotheses[:2]:
                    if hypothesis.test_skill not in kernel.registry.manifests or hypothesis.test_skill in outputs:
                        continue
                    try:
                        extra = kernel.run_skill(hypothesis.test_skill, hypothesis.test_action, {},
                                                 mission_id=mission_id, caller="director")
                        insights.extend(extra.insights)
                        recommendations.extend(extra.recommendations)
                        investigated.append(f"{hypothesis.test_skill}.{hypothesis.test_action}")
                    except Exception as exc:
                        errors.append(f"test d'hypothèse {hypothesis.test_skill}: {exc}")
            if contradictions:
                phases.append(Phase(name="HYPOTHÈSES", summary=(
                    f"{len(hypotheses)} explication(s) candidate(s)"
                    + (f", {len(investigated)} vérifiée(s) : {', '.join(investigated)}" if investigated else "")),
                    details={"hypotheses": [h.statement for h in hypotheses]}))

            # 5. ANTICIPER -------------------------------------------------------------------
            forecasts = self._forecast(metrics)
            ahead = [i for i in insights if i.kind in ("opportunity", "risk", "trend", "anomaly")]
            phases.append(Phase(name="ANTICIPER", summary=(
                f"{len(forecasts)} prévision(s) à 30 jours, "
                f"{sum(1 for i in ahead if i.kind == 'opportunity')} opportunité(s), "
                f"{sum(1 for i in ahead if i.kind in ('risk', 'anomaly'))} risque(s)/anomalie(s).")))

            # 6. PRIORISER -------------------------------------------------------------------
            priorities = prioritize(recommendations)
            phases.append(Phase(name="PRIORISER", summary=(
                f"{len(priorities)} recommandation(s) classée(s) par impact × confiance × urgence / effort, ROI."
                + (f" N°1 : {priorities[0].recommendation.title}." if priorities else ""))))

            # 7-10. DÉCIDER → SIMULER → EXÉCUTER | REVUE HUMAINE -------------------------------
            decisions = self._decide(priorities, decide_top, mission_id, execute, use_llm, now)
            adopted = [d for d in decisions if d.verdict and d.verdict.verdict in ("adopter", "adopter_sous_conditions")]
            phases.append(Phase(name="DÉCIDER", summary=(
                f"Conseil (CEO + Risque + CFO + CMO + COO + Mémoire) : {len(adopted)} adoptée(s) sur "
                f"{sum(1 for d in decisions if d.verdict)} examinée(s)."
                + (f" {sum(1 for d in decisions if d.route == 'already_handled')} déjà traitée(s)."
                   if any(d.route == "already_handled" for d in decisions) else ""))))
            phases.append(Phase(name="SIMULER", summary=(
                f"{sum(1 for d in decisions if d.simulation)} scénario(s) Monte-Carlo : "
                f"{sum(1 for d in decisions if d.simulation and d.simulation.risk_class == 'low')} risque faible, "
                f"{sum(1 for d in decisions if d.simulation and d.simulation.risk_class == 'high')} risque élevé.")))
            phases.append(Phase(name="EXÉCUTER / REVUE HUMAINE", summary=(
                f"{sum(1 for d in decisions if d.route in ('execute', 'mission'))} action(s) lancée(s), "
                f"{sum(1 for d in decisions if d.route == 'human_review')} en attente de validation humaine.")))

            # 11. MESURER --------------------------------------------------------------------
            measured = self._measure()
            phases.append(Phase(name="MESURER", summary=(
                f"{len(measured)} résultat(s) de décision mesuré(s) automatiquement ; "
                f"{len(kernel.memory.due_for_review())} revue(s) attendant une mesure humaine."),
                details={"measured": measured}))

            # 12-13. APPRENDRE → RÉVISER -------------------------------------------------------
            learned = self._learn()
            phases.append(Phase(name="APPRENDRE / RÉVISER", summary=(
                f"{len(learned)} croyance(s) révisée(s) à partir des résultats mesurés." if learned
                else "Aucun nouveau résultat mesuré : rien à réviser.")))

            # 14. DREAMING -------------------------------------------------------------------
            audit: list[str] = []
            if deep or mode in ("weekly", "audit", "dreaming"):
                audit = self.dream()
                phases.append(Phase(name="DREAMING", summary=f"Auto-audit : {len(audit)} point(s) d'attention.",
                                    details={"findings": audit}))

        report = DirectorReport(
            mission_id=mission_id, mission=mission, mode=mode, at=now, selected=selected, phases=phases,
            metrics=metrics, insights=insights, confrontations=confrontations, hypotheses=hypotheses,
            forecasts=forecasts, priorities=priorities, decisions=decisions, measured=measured, learned=learned,
            audit=audit, skill_errors=errors, summary="",
        )
        report.summary = self._summarize(report)
        report.report_id = self._persist(report)
        kernel.store.put("director_mission", mission_id, {
            "id": mission_id, "mission": mission, "mode": mode, "at": iso(now), "report_id": report.report_id,
            "skills": [s["name"] for s in selected],
        })
        kernel.events.publish("director.completed", {"mission_id": mission_id, "mode": mode,
                                                     "report_id": report.report_id}, source="director")
        return report

    def _facts(self, rec: Recommendation, quick: SimulationResult) -> CouncilFacts:
        finance = self.kernel.finance
        status = finance.status()
        past_rate, past_n = self._past(rec)
        return CouncilFacts(
            expected_value=quick.expected_value, p_success=quick.p_success, p_loss=quick.p_loss,
            unit=finance.currency if quick.unit == "money" else "points d'impact",
            budget_available=status.available, budget_utilization=status.utilization,
            past_success_rate=past_rate, past_samples=past_n, team_load=self._team_load(),
            strategic_fit=self._strategic_fit(rec),
        )

    def _decide(self, priorities: list[Priority], top: int, mission_id: str, execute: bool, use_llm: bool,
                now: datetime) -> list[DecisionRecord]:
        records: list[DecisionRecord] = []
        council = self.app.council_for(use_llm)
        available = self.kernel.finance.available()
        examined = 0
        for priority in priorities:
            if examined >= top:
                break
            rec = priority.recommendation
            handled = self._already_handled(rec)
            if handled:
                records.append(DecisionRecord(priority=priority, verdict=None, simulation=None,
                                              route="already_handled", note=handled))
                continue
            examined += 1
            quick = simulate(rec, available_budget=available, runs=500)
            verdict = council.deliberate(rec, self._facts(rec, quick), use_llm=use_llm)
            if verdict.verdict not in ("adopter", "adopter_sous_conditions"):
                records.append(DecisionRecord(priority=priority, verdict=verdict, simulation=None, route="none",
                                              note="Non adoptée par le conseil."))
                continue
            sim = simulate(rec, available_budget=available, runs=2000)
            record = DecisionRecord(priority=priority, verdict=verdict, simulation=sim, route="none")
            if not execute:
                record.note = "Mode consultatif : aucune exécution."
            elif sim.risk_class == "high":
                approval = self.app.approvals.create(
                    kind="recommendation", title=rec.title,
                    summary=f"{rec.rationale} — Conseil : {verdict.verdict} ({verdict.score:+.2f}). "
                            f"Risque élevé : {', '.join(sim.reasons)}.",
                    payload={"recommendation": rec.model_dump(mode="json"), "verdict": verdict.verdict,
                             "simulation": sim.model_dump(mode="json")},
                    risk={"class": sim.risk_class, "reasons": sim.reasons, "p_loss": sim.p_loss},
                    mission_id=mission_id,
                )
                record.route, record.approval_id = "human_review", approval.id
                record.note = "Revue humaine requise : " + "; ".join(sim.reasons)
            else:
                record.decision_id = self.kernel.memory.record(
                    title=rec.title, decision=f"Exécuter : {rec.title}", context=rec.rationale,
                    rationale="; ".join(verdict.minutes[-1:]), category=(rec.tags or [rec.skill])[0],
                    tags=rec.tags, skills=[rec.skill], recommendation_ids=[rec.id], confidence=verdict.confidence,
                    review_at=now + timedelta(days=rec.horizon_days or 14), source="director",
                ).id
                if rec.follow_up is not None:
                    task = self.app.queue.enqueue(
                        "skill.run", {"skill": rec.follow_up.skill, "action": rec.follow_up.action,
                                      "input": rec.follow_up.payload, "mission_id": mission_id},
                        idempotency_key=f"{rec.id}:{period_key('daily', now)}",
                    )
                    record.route, record.task_id = "execute", task.id
                    record.note = f"Suivi automatique : {rec.follow_up.skill}.{rec.follow_up.action}."
                else:
                    ref = f"mission_{rec.id.removeprefix('rec_')}"
                    self.kernel.store.put("mission", ref, {
                        "id": ref, "title": rec.title, "required_capabilities": rec.tags[:2] or [rec.skill],
                        "priority": max(1, min(10, round(priority.score))) if priority.score >= 1 else 1,
                        "effort": rec.effort, "status": "todo", "source": "director", "mission_id": mission_id,
                        "due": iso(now + timedelta(days=rec.horizon_days or 14)),
                    })
                    record.route, record.mission_ref = "mission", ref
                    record.note = "Mission créée pour les équipes d'agents (assignation par intelligent-management)."
                self.kernel.db.execute(
                    "INSERT OR IGNORE INTO recommendation_log(id, skill, title, mission_id, status, at) "
                    "VALUES (?, ?, ?, ?, 'executed', ?)", (rec.id, rec.skill, rec.title, mission_id, iso(now)))
            records.append(record)
        return records

    def _measure(self) -> list[dict[str, Any]]:
        """MESURER : clôture automatique des décisions dont la métrique attendue est observée."""

        measured: list[dict[str, Any]] = []
        for decision in self.kernel.memory.due_for_review():
            if decision.expected is None:
                continue
            history = self._history(decision.expected.metric, limit=1)
            if not history or history[-1][0] < decision.created_at:
                continue
            closed = self.kernel.memory.record_outcome(
                decision.id, actual=history[-1][1], notes="Mesure automatique à l'échéance de revue.")
            measured.append({"decision_id": closed.id, "title": closed.title, "status": closed.status,
                             "score": closed.score})
            self.kernel.events.publish("decision.outcome_recorded", {
                "decision_id": closed.id, "status": closed.status, "score": closed.score}, source="director")
        return measured

    def _learn(self) -> list[str]:
        """APPRENDRE puis RÉVISER : résultats mesurés → croyances apprises par catégorie."""

        rows = self.kernel.db.query(
            "SELECT category, status FROM decisions WHERE status IN ('succeeded', 'partial', 'failed')")
        groups: dict[str, list[str]] = {}
        for row in rows:
            groups.setdefault(row["category"], []).append(row["status"])
        learned: list[str] = []
        for category, statuses in groups.items():
            if len(statuses) < 2:
                continue
            success = sum(1.0 if s == "succeeded" else 0.5 if s == "partial" else 0.0 for s in statuses)
            posterior = BetaPosterior(success, len(statuses) - success)
            belief = self.app.world.learn(
                f"strategy:{category}",
                f"Les initiatives « {category} » réussissent ({len(statuses)} résultats mesurés).",
                posterior.mean, len(statuses),
            )
            learned.append(f"{belief.statement} → confiance {belief.confidence:.0%}")
        return learned

    def dream(self) -> list[str]:
        """DREAMING : auto-audit du système et anticipation des prochaines décisions."""

        kernel = self.kernel
        findings: list[str] = []
        stale = self.app.world.stale(30)
        if stale:
            findings.append(f"{len(stale)} croyance(s) testable(s) non confrontée(s) depuis 30 jours : "
                            + "; ".join(b.statement for b in stale[:3]))
        for a, b in self.app.world.inconsistencies():
            findings.append(f"Croyances incompatibles sur {a.metric} : « {a.statement} » vs « {b.statement} ».")
        dead = self.app.queue.list("dead", limit=20)
        if dead:
            findings.append(f"{len(dead)} tâche(s) en échec définitif : " + ", ".join(t.kind for t in dead[:5]))
        week_ago = kernel.clock.now() - timedelta(days=7)
        old = [a for a in self.app.approvals.list("pending") if a.created_at < week_ago]
        if old:
            findings.append(f"{len(old)} validation(s) humaine(s) en attente depuis plus de 7 jours.")
        utility = kernel.skill_utility()
        unused = [n for n, u in utility.items() if u["runs"] == 0]
        if unused:
            findings.append("Skills jamais utilisées : " + ", ".join(unused) + " (utilité non démontrée).")
        unreliable = [n for n, u in utility.items() if u["reliability"] is not None and u["reliability"] < 0.9]
        if unreliable:
            findings.append("Skills peu fiables (< 90 % de succès d'exécution) : " + ", ".join(unreliable))
        available = kernel.available_data()
        gaps = sorted({kind for m in kernel.registry.manifests.values() for kind in m.data} - available)
        if gaps:
            findings.append("Données absentes (analyses limitées) : " + ", ".join(gaps[:12]))
        horizon = kernel.clock.now() + timedelta(days=14)
        upcoming = [d for d in kernel.memory.list(status="open", limit=500)
                    if d.review_at and d.review_at <= horizon]
        if upcoming:
            findings.append(f"Anticipation : {len(upcoming)} décision(s) à évaluer d'ici 14 jours.")
        for objective in kernel.store.list("objective"):
            deadline = objective.get("deadline")
            if deadline and objective.get("status", "active") == "active":
                due = parse_dt(deadline)
                if due <= kernel.clock.now() + timedelta(days=30):
                    findings.append(f"Anticipation : objectif « {objective.get('title')} » à échéance le "
                                    f"{due.date().isoformat()}.")
        status = kernel.finance.status()
        if status.envelope > 0 and status.utilization >= 0.8:
            findings.append(f"Budget consommé à {status.utilization:.0%} : préparer les arbitrages du mois.")
        return findings

    @staticmethod
    def _summarize(report: DirectorReport) -> str:
        critical = sum(1 for i in report.insights if i.severity == "critical")
        adopted = [d for d in report.decisions if d.verdict and d.verdict.verdict in ("adopter",
                                                                                      "adopter_sous_conditions")]
        rejected = [d for d in report.decisions if d.verdict and d.verdict.verdict in ("rejeter", "reporter")]
        launched = sum(1 for d in report.decisions if d.route in ("execute", "mission"))
        review = sum(1 for d in report.decisions if d.route == "human_review")
        parts = [
            f"Mission « {report.mission} » : {len(report.selected)} Skill(s) mobilisée(s) "
            f"({', '.join(s['name'] for s in report.selected)}).",
            f"{len(report.insights)} constat(s) dont {critical} critique(s) ; {len(report.priorities)} "
            "recommandation(s) priorisée(s).",
        ]
        if report.decisions:
            parts.append(f"Conseil : {len(adopted)} adoptée(s), {len(rejected)} rejetée(s) ou reportée(s) ; "
                         f"{launched} action(s) lancée(s), {review} en attente de validation humaine.")
        if report.priorities:
            parts.append(f"Priorité n°1 : {report.priorities[0].recommendation.title}.")
        if report.skill_errors:
            parts.append(f"⚠ {len(report.skill_errors)} Skill(s) en erreur.")
        return " ".join(parts)

    def _persist(self, report: DirectorReport) -> str:
        from sovereign.commands.render import render_director

        report_id = new_id("rpt")
        self.kernel.db.execute(
            "INSERT INTO reports(id, kind, title, content_md, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, f"director.{report.mode}", report.mission[:200], render_director(report),
             dumps({"summary": report.summary, "mission_id": report.mission_id}), iso(report.at)),
        )
        return report_id

