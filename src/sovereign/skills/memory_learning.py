"""SKILL 10 — MÉMOIRE ET APPRENTISSAGE.

Conserve les décisions importantes, compare les résultats passés, identifie les erreurs répétitives,
capitalise les expériences et adapte les stratégies selon les résultats mesurés (a posteriori bêta).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from sovereign.core.memory import Decision, DecisionStatus, Expectation
from sovereign.core.stats import BetaPosterior
from sovereign.skills.base import (
    EmptyInput,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillOutput,
    action,
)


class DecisionInput(BaseModel):
    title: str
    decision: str
    context: str = ""
    rationale: str = ""
    options: list[str] = Field(default_factory=list)
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    recommendation_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0, le=1)
    expected: Expectation | None = None
    review_in_days: int | None = Field(None, ge=0, description="Date de revue = maintenant + N jours")


class DecisionOutput(SkillOutput):
    decision: Decision


class OutcomeInput(BaseModel):
    decision_id: str
    actual: float | None = None
    status: DecisionStatus | None = None
    notes: str = ""
    lesson: str | None = None


class SearchInput(BaseModel):
    query: str
    limit: int = Field(10, ge=1, le=50)


class SearchHit(BaseModel):
    id: str
    title: str
    decision: str
    status: str
    score: float | None
    relevance: float
    lesson: str | None
    created_at: datetime


class SearchOutput(SkillOutput):
    hits: list[SearchHit]


class CompareInput(BaseModel):
    group_by: Literal["category", "tag"] = "category"
    filter: str | None = Field(None, description="Restreindre à une catégorie / un tag")


class GroupStats(BaseModel):
    group: str
    closed: int
    succeeded: int
    partial: int
    failed: int
    average_score: float | None
    success_probability: float
    uncertainty: float
    best: str | None
    worst: str | None


class CompareOutput(SkillOutput):
    groups: list[GroupStats]


class RepeatedErrorsInput(BaseModel):
    min_occurrences: int = Field(2, ge=2)


class ErrorPattern(BaseModel):
    pattern: str
    occurrences: int
    decisions: list[str]
    lessons: list[str]


class RepeatedErrorsOutput(SkillOutput):
    patterns: list[ErrorPattern]


class LessonsInput(BaseModel):
    category: str | None = None
    limit: int = Field(20, ge=1, le=200)


class Lesson(BaseModel):
    decision_id: str
    category: str
    status: str
    score: float | None
    lesson: str


class LessonsOutput(SkillOutput):
    lessons: list[Lesson]


class AdaptInput(BaseModel):
    group_by: Literal["category", "tag"] = "category"
    min_samples: int = Field(2, ge=1)


class StrategyAdvice(BaseModel):
    group: str
    samples: int
    success_probability: float
    uncertainty: float
    advice: Literal["favoriser", "éviter", "explorer", "maintenir"]
    reason: str


class AdaptOutput(SkillOutput):
    strategies: list[StrategyAdvice]


class AssessOutput(SkillOutput):
    open_decisions: int
    due_reviews: list[str]
    closed_decisions: int
    success_rate: float | None


def _groups(decision: Decision, group_by: str) -> list[str]:
    if group_by == "category":
        return [decision.category]
    return decision.tags or ["(sans tag)"]


class MemoryLearningSkill(Skill):
    name = "memory-learning"
    version = "1.0.0"

    @action("État de la mémoire : décisions ouvertes, revues dues, erreurs répétées, taux de succès")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        memory = ctx.memory
        decisions = memory.list(limit=1000)
        open_ = [d for d in decisions if d.status == "open"]
        closed = [d for d in decisions if d.status in ("succeeded", "partial", "failed")]
        due = memory.due_for_review()
        success = sum(1 for d in closed if d.status == "succeeded") + 0.5 * sum(
            1 for d in closed if d.status == "partial")
        rate = success / len(closed) if closed else None
        out = AssessOutput(
            open_decisions=len(open_), due_reviews=[d.id for d in due], closed_decisions=len(closed),
            success_rate=round(rate, 3) if rate is not None else None,
            summary=(f"{len(decisions)} décision(s) mémorisée(s), {len(open_)} ouverte(s), {len(due)} revue(s) due(s)"
                     + (f", taux de succès {rate:.0%}." if rate is not None else ".")),
        )
        if rate is not None:
            out.metrics["memory.success_rate"] = rate
        out.metrics["memory.open_decisions"] = float(len(open_))
        if due:
            out.recommendations.append(Recommendation(
                title=f"Mesurer le résultat de {len(due)} décision(s) arrivée(s) à échéance",
                rationale="Sans mesure, le système ne peut ni apprendre ni réviser ses croyances.",
                impact=6, effort=1.5, confidence=0.9, urgency=7, risk=0.0, tags=["mémoire", "apprentissage"],
                evidence=[f"{d.id} : {d.title}" for d in due[:5]],
            ))
        errors = self.detect_repeated_errors(ctx, RepeatedErrorsInput())
        out.insights.extend(errors.insights)
        out.recommendations.extend(errors.recommendations)
        return out

    @action("Mémoriser une décision importante (contexte, options, choix, attendu chiffré)", risk="write")
    def record_decision(self, ctx: SkillContext, data: DecisionInput) -> DecisionOutput:
        review_at = ctx.clock.now() + timedelta(days=data.review_in_days) if data.review_in_days is not None else None
        decision = ctx.memory.record(
            data.title, data.decision, context=data.context, rationale=data.rationale, options=data.options,
            category=data.category, tags=data.tags, skills=data.skills,
            recommendation_ids=data.recommendation_ids, confidence=data.confidence, expected=data.expected,
            review_at=review_at, source=ctx.caller,
        )
        similar = [hit for hit, _ in ctx.memory.search(f"{data.title} {data.category}", limit=5)
                   if hit.id != decision.id and hit.status == "failed"]
        out = DecisionOutput(decision=decision, summary=f"Décision {decision.id} mémorisée.")
        if similar:
            out.insights.append(Insight(
                kind="risk", severity="warning", title="Décision proche d'un échec passé",
                detail="; ".join(f"{d.title} → {d.lesson or 'échec'}" for d in similar[:3]),
            ))
        return out

    @action("Enregistrer le résultat réel d'une décision (score et leçon calculés)", risk="write")
    def record_outcome(self, ctx: SkillContext, data: OutcomeInput) -> DecisionOutput:
        decision = ctx.memory.record_outcome(
            data.decision_id, actual=data.actual, status=data.status, notes=data.notes, lesson=data.lesson,
        )
        ctx.events.publish("decision.outcome_recorded", {
            "decision_id": decision.id, "status": decision.status, "score": decision.score,
            "category": decision.category, "skills": decision.skills,
        }, source="memory")
        return DecisionOutput(decision=decision, summary=f"Résultat : {decision.status} (score {decision.score}).")

    @action("Rechercher dans les décisions et leçons (plein texte, BM25)")
    def search(self, ctx: SkillContext, data: SearchInput) -> SearchOutput:
        hits = [
            SearchHit(id=d.id, title=d.title, decision=d.decision, status=d.status, score=d.score,
                      relevance=round(rank, 4), lesson=d.lesson, created_at=d.created_at)
            for d, rank in ctx.memory.search(data.query, data.limit)
        ]
        return SearchOutput(hits=hits, summary=f"{len(hits)} résultat(s) pour « {data.query} ».")

    @action("Comparer les résultats passés par catégorie ou par tag")
    def compare_results(self, ctx: SkillContext, data: CompareInput) -> CompareOutput:
        buckets: dict[str, list[Decision]] = defaultdict(list)
        for decision in ctx.memory.list(limit=5000):
            if decision.status not in ("succeeded", "partial", "failed"):
                continue
            for group in _groups(decision, data.group_by):
                if data.filter is None or group == data.filter:
                    buckets[group].append(decision)
        groups: list[GroupStats] = []
        for group, items in sorted(buckets.items()):
            succeeded = sum(1 for d in items if d.status == "succeeded")
            partial = sum(1 for d in items if d.status == "partial")
            failed = sum(1 for d in items if d.status == "failed")
            posterior = BetaPosterior(succeeded + 0.5 * partial, failed + 0.5 * partial)
            scored = [d for d in items if d.score is not None]
            best = max(scored, key=lambda d: d.score or 0, default=None)
            worst = min(scored, key=lambda d: d.score or 0, default=None)
            groups.append(GroupStats(
                group=group, closed=len(items), succeeded=succeeded, partial=partial, failed=failed,
                average_score=round(sum(d.score or 0 for d in scored) / len(scored), 3) if scored else None,
                success_probability=round(posterior.mean, 3), uncertainty=round(posterior.stdev, 3),
                best=best.title if best else None, worst=worst.title if worst else None,
            ))
        groups.sort(key=lambda g: -g.success_probability)
        return CompareOutput(groups=groups, summary=f"{len(groups)} groupe(s) comparé(s).")

    @action("Identifier les erreurs répétitives (échecs récurrents par catégorie / tag)")
    def detect_repeated_errors(self, ctx: SkillContext, data: RepeatedErrorsInput) -> RepeatedErrorsOutput:
        failures = [d for d in ctx.memory.list(status="failed", limit=5000)]
        index: dict[str, list[Decision]] = defaultdict(list)
        for decision in failures:
            index[f"catégorie:{decision.category}"].append(decision)
            for tag in decision.tags:
                index[f"tag:{tag}"].append(decision)
        patterns = [
            ErrorPattern(pattern=key, occurrences=len(items), decisions=[d.id for d in items],
                         lessons=[d.lesson for d in items if d.lesson][:3])
            for key, items in index.items() if len(items) >= data.min_occurrences
        ]
        patterns.sort(key=lambda p: -p.occurrences)
        out = RepeatedErrorsOutput(patterns=patterns, summary=f"{len(patterns)} schéma(s) d'échec récurrent.")
        for pattern in patterns[:5]:
            out.insights.append(Insight(
                kind="weakness", severity="critical" if pattern.occurrences >= 3 else "warning",
                title=f"Erreur répétée : {pattern.pattern} ({pattern.occurrences} échecs)",
                detail=" | ".join(pattern.lessons),
            ))
            out.recommendations.append(Recommendation(
                title=f"Revoir l'approche « {pattern.pattern.split(':', 1)[1]} » avant toute nouvelle décision",
                rationale=f"{pattern.occurrences} échecs mesurés partagent ce point commun.",
                impact=7, effort=3, confidence=min(0.95, 0.5 + 0.1 * pattern.occurrences), urgency=6, risk=0.05,
                tags=["apprentissage", "erreurs"], evidence=pattern.decisions[:5],
            ))
        return out

    @action("Capitaliser : leçons tirées des décisions clôturées")
    def lessons(self, ctx: SkillContext, data: LessonsInput) -> LessonsOutput:
        items = [
            Lesson(decision_id=d.id, category=d.category, status=d.status, score=d.score, lesson=d.lesson)
            for d in ctx.memory.list(category=data.category, limit=5000)
            if d.lesson and d.status != "open"
        ][: data.limit]
        return LessonsOutput(lessons=items, summary=f"{len(items)} leçon(s).")

    @action("Adapter les stratégies selon les résultats mesurés (favoriser / éviter / explorer)")
    def adapt_strategies(self, ctx: SkillContext, data: AdaptInput) -> AdaptOutput:
        compared = self.compare_results(ctx, CompareInput(group_by=data.group_by))
        strategies: list[StrategyAdvice] = []
        for group in compared.groups:
            p, u = group.success_probability, group.uncertainty
            if group.closed < data.min_samples or u > 0.2:
                advice, reason = "explorer", f"Trop peu de résultats ({group.closed}) : incertitude ±{u:.2f}."
            elif p >= 0.65:
                advice, reason = "favoriser", f"Succès probable {p:.0%} sur {group.closed} décisions mesurées."
            elif p <= 0.35:
                advice, reason = "éviter", f"Succès probable seulement {p:.0%} sur {group.closed} décisions."
            else:
                advice, reason = "maintenir", f"Résultats mitigés ({p:.0%}) : améliorer l'exécution."
            strategies.append(StrategyAdvice(
                group=group.group, samples=group.closed, success_probability=p, uncertainty=u, advice=advice,
                reason=reason,
            ))
        out = AdaptOutput(strategies=strategies, summary=f"{len(strategies)} stratégie(s) évaluée(s).")
        for strategy in strategies:
            if strategy.advice == "éviter":
                out.recommendations.append(Recommendation(
                    title=f"Réduire les initiatives « {strategy.group} »",
                    rationale=strategy.reason, impact=6, effort=2, confidence=1 - strategy.uncertainty,
                    urgency=5, risk=0.1, tags=["stratégie", "apprentissage"],
                ))
            elif strategy.advice == "favoriser":
                out.insights.append(Insight(kind="strength", title=f"Stratégie efficace : {strategy.group}",
                                            detail=strategy.reason, value=strategy.success_probability))
        return out


SKILL = MemoryLearningSkill
