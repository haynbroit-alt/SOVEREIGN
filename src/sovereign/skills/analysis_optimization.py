"""SKILL 8 — ANALYSE ET OPTIMISATION.

Analyse les performances des projets, identifie les points faibles, détecte les fonctionnalités inutiles,
recommande des améliorations priorisées (ICE) et vérifie statistiquement les résultats après modification
(test t de Welch). S'applique aussi à SOVEREIGN lui-même : utilité réelle des Skills et des commandes.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sovereign.core.money import to_decimal
from sovereign.core.stats import linear_fit, mean, percentile, variance, welch_t_test
from sovereign.skills.base import (
    EmptyInput,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillError,
    SkillOutput,
    action,
)

# Les 15 commandes du directeur (utilisées pour repérer celles qui ne servent jamais).
KNOWN_COMMANDS = (
    "director",
    "strategy",
    "business",
    "marketing",
    "council",
    "opportunities",
    "projects",
    "audit",
    "budget",
    "skills",
    "memory",
    "roadmap",
    "status",
    "improve",
    "report",
)

ProjectStatus = Literal["on_track", "at_risk", "off_track", "no_kpi"]


# -- modèles de l'espace de travail ------------------------------------------------------------------
class KPI(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    value: float
    target: float
    direction: Literal["higher", "lower"] = "higher"
    weight: float = Field(1.0, gt=0)
    history: list[float] = Field(default_factory=list)


class Feature(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    users: int = Field(ge=0)
    uses: int = Field(ge=0)
    maintenance_cost_monthly: Decimal = Field(Decimal("0"), ge=0)
    released_on: date | None = None


class Project(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    status: str | None = None
    owner: str | None = None
    active_users: int | None = Field(None, ge=0)
    kpis: list[KPI] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)


class Change(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    project_id: str | None = None
    description: str = ""
    metric: str
    before: list[float]
    after: list[float]
    changed_on: date | None = None
    goal: Literal["increase", "decrease"] | None = None


# -- entrées / sorties ---------------------------------------------------------------------------------
class ProjectsInput(BaseModel):
    projects: list[Project] = Field(default_factory=list, description="Vide ⇒ projets de l'espace de travail")


class KPIResult(BaseModel):
    name: str
    value: float
    target: float
    direction: Literal["higher", "lower"]
    weight: float
    attainment: float
    trend_slope: float | None = None
    trend: Literal["improving", "stable", "degrading"] | None = None


class ProjectHealth(BaseModel):
    id: str
    name: str
    health: float | None
    status: ProjectStatus
    kpis: list[KPIResult]
    weak_points: list[str]


class ProjectsOutput(SkillOutput):
    projects: list[ProjectHealth]
    average_health: float | None


class UnusedFeaturesInput(BaseModel):
    projects: list[Project] = Field(default_factory=list, description="Vide ⇒ projets de l'espace de travail")
    share_threshold: float = Field(0.05, gt=0, lt=1, description="Part d'utilisateurs sous laquelle on alerte")
    recent_days: int = Field(30, ge=0, description="Fonctionnalités plus récentes ignorées")


class FeatureUsage(BaseModel):
    project_id: str
    project: str
    feature: str
    users: int
    uses: int
    user_share: float | None
    maintenance_cost_monthly: Decimal
    recent: bool
    in_pareto: bool
    unused: bool
    reason: str


class UnusedFeaturesOutput(SkillOutput):
    features: list[FeatureUsage]
    unused: list[FeatureUsage]
    wasted_monthly_cost: Decimal


class ImprovementsInput(BaseModel):
    projects: list[Project] = Field(default_factory=list, description="Vide ⇒ projets de l'espace de travail")
    max_items: int = Field(10, ge=1, le=100)


class BacklogItem(BaseModel):
    title: str
    project: str
    kind: Literal["kpi", "tendance", "fonctionnalité"]
    impact: float
    confidence: float
    ease: float
    ice: float
    rationale: str


class ImprovementsOutput(SkillOutput):
    backlog: list[BacklogItem]


class VerifyChangeInput(BaseModel):
    change_id: str | None = Field(None, description="Changement enregistré (kind 'change')")
    metric: str = ""
    description: str = ""
    before: list[float] = Field(default_factory=list)
    after: list[float] = Field(default_factory=list)
    goal: Literal["increase", "decrease"] = "increase"


class VerifyChangeOutput(SkillOutput):
    metric: str
    goal: Literal["increase", "decrease"]
    n_before: int
    n_after: int
    mean_before: float
    mean_after: float
    effect: float
    relative_effect: float | None
    p_value: float
    cohens_d: float | None
    verdict: Literal["amélioré", "dégradé", "non concluant"]
    sample_size_needed: int | None = Field(None, description="Taille par groupe pour 80 % de puissance (Lehr)")


class AuditSystemInput(BaseModel):
    min_recommendations: int = Field(5, ge=1, description="Propositions minimales avant de juger l'adoption")
    min_activity: int = Field(20, ge=0, description="Exécutions totales minimales avant de juger l'inutilisation")


class SkillAudit(BaseModel):
    skill: str
    runs: int
    reliability: float | None
    proposed: int
    adopted: int
    adoption_rate: float | None
    success_posterior: float
    flags: list[str]


class CommandUsage(BaseModel):
    command: str
    runs: int


class AuditSystemOutput(SkillOutput):
    total_skill_runs: int
    skills: list[SkillAudit]
    commands: list[CommandUsage]
    never_used_skills: list[str]
    unreliable_skills: list[str]
    unadopted_skills: list[str]
    never_used_commands: list[str]
    sufficient_history: bool


class AssessOutput(SkillOutput):
    projects_analyzed: int
    unused_features: int
    changes_verified: int
    backlog: list[BacklogItem]
    system_flags: list[str]


# -- calculs ---------------------------------------------------------------------------------------------
def attainment(kpi: KPI) -> float:
    """Atteinte d'un KPI : valeur/cible (plus haut = mieux) ou cible/valeur (plus bas = mieux).

    Bornée à [0 ; 1,5].
    """

    if kpi.target == 0:
        ok = kpi.value >= 0 if kpi.direction == "higher" else kpi.value <= 0
        return 1.0 if ok else 0.0
    if kpi.direction == "higher":
        ratio = kpi.value / kpi.target
    elif kpi.value <= 0:
        ratio = 1.5
    else:
        ratio = kpi.target / kpi.value
    return round(max(0.0, min(1.5, ratio)), 4)


def kpi_trend(kpi: KPI) -> tuple[float | None, str | None]:
    series = [*kpi.history, kpi.value] if kpi.history else []
    if len(series) < 3:
        return None, None
    fit = linear_fit(list(range(len(series))), series)
    scale = mean([abs(v) for v in series]) or 1.0
    relative = fit.slope / scale
    if abs(relative) <= 0.02:
        return round(fit.slope, 4), "stable"
    good = relative > 0 if kpi.direction == "higher" else relative < 0
    return round(fit.slope, 4), "improving" if good else "degrading"


def project_status(health: float | None) -> ProjectStatus:
    if health is None:
        return "no_kpi"
    if health >= 0.9:
        return "on_track"
    if health >= 0.7:
        return "at_risk"
    return "off_track"


def _load_projects(ctx: SkillContext, projects: list[Project]) -> list[Project]:
    if projects:
        return projects
    loaded: list[Project] = []
    for raw in ctx.store.list("project"):
        try:
            loaded.append(Project.model_validate(raw))
        except ValidationError as exc:
            raise SkillError(f"projet invalide dans l'espace de travail ({raw.get('id')}) : {exc}") from exc
    return loaded


class AnalysisOptimizationSkill(Skill):
    name = "analysis-optimization"
    version = "1.0.0"

    # -- diagnostic global ------------------------------------------------------------------------------
    @action("Diagnostic des projets, des changements et de l'utilité réelle de SOVEREIGN (pour le directeur)")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        projects = _load_projects(ctx, [])
        out = AssessOutput(
            projects_analyzed=len(projects),
            unused_features=0,
            changes_verified=0,
            backlog=[],
            system_flags=[],
        )
        parts: list[str] = []
        if projects:
            analysis = self.analyze_projects(ctx, ProjectsInput(projects=projects))
            unused = self.detect_unused_features(ctx, UnusedFeaturesInput(projects=projects))
            improvements = self.recommend_improvements(ctx, ImprovementsInput(projects=projects, max_items=10))
            out.unused_features = len(unused.unused)
            out.backlog = improvements.backlog
            out.insights.extend(analysis.insights + unused.insights)
            out.recommendations.extend(improvements.recommendations)
            out.metrics.update(analysis.metrics)
            out.metrics.update(unused.metrics)
            parts.append(analysis.summary)
            parts.append(unused.summary)
        for raw in ctx.store.list("change"):
            try:
                change = Change.model_validate(raw)
                verified = self.verify_change(ctx, VerifyChangeInput(change_id=change.id))
            except (ValidationError, SkillError) as exc:
                out.insights.append(
                    Insight(
                        kind="observation",
                        severity="info",
                        title=f"Changement {raw.get('id')} non vérifiable",
                        detail=str(exc),
                    )
                )
                continue
            out.changes_verified += 1
            out.insights.extend(verified.insights)
            out.recommendations.extend(verified.recommendations)
        if out.changes_verified:
            parts.append(f"{out.changes_verified} changement(s) vérifié(s) statistiquement.")
        audit = self.audit_system(ctx, AuditSystemInput())
        out.system_flags = [f"{s.skill}: {', '.join(s.flags)}" for s in audit.skills if s.flags]
        out.insights.extend(audit.insights)
        out.recommendations.extend(audit.recommendations)
        out.metrics.update(audit.metrics)
        if not projects:
            parts.insert(
                0,
                "Aucun projet dans l'espace de travail : importez des enregistrements « project » "
                "(kpis, features, active_users) et « change » (before/after) pour l'analyse.",
            )
        parts.append(audit.summary)
        out.summary = " ".join(p for p in parts if p)
        return out

    # -- performances des projets --------------------------------------------------------------------------
    @action("Santé des projets : atteinte des KPI, statut, tendances, points faibles")
    def analyze_projects(self, ctx: SkillContext, data: ProjectsInput) -> ProjectsOutput:
        projects = _load_projects(ctx, data.projects)
        results: list[ProjectHealth] = []
        out = ProjectsOutput(projects=[], average_health=None)
        for project in projects:
            kpis: list[KPIResult] = []
            for kpi in project.kpis:
                slope, trend = kpi_trend(kpi)
                kpis.append(
                    KPIResult(
                        name=kpi.name,
                        value=kpi.value,
                        target=kpi.target,
                        direction=kpi.direction,
                        weight=kpi.weight,
                        attainment=attainment(kpi),
                        trend_slope=slope,
                        trend=trend,
                    )
                )
            total_weight = sum(k.weight for k in kpis)
            health = round(sum(min(k.attainment, 1.0) * k.weight for k in kpis) / total_weight, 4) if kpis else None
            status = project_status(health)
            weak = sorted((k for k in kpis if k.attainment < 0.9), key=lambda k: k.attainment)
            weak_points = [f"{k.name} : {k.attainment:.0%} de la cible" for k in weak[:3]]
            weak_points += [
                f"{k.name} : tendance défavorable" for k in kpis if k.trend == "degrading" and k.attainment >= 0.9
            ]
            results.append(
                ProjectHealth(
                    id=project.id,
                    name=project.name,
                    health=health,
                    status=status,
                    kpis=kpis,
                    weak_points=weak_points,
                )
            )
            if health is not None:
                out.metrics[f"projects.{project.id}.health"] = health
            if status == "off_track":
                out.insights.append(
                    Insight(
                        kind="weakness",
                        severity="critical",
                        title=f"Projet « {project.name} » hors trajectoire",
                        detail="; ".join(weak_points),
                        metric=f"projects.{project.id}.health",
                        value=health,
                    )
                )
            elif status == "at_risk":
                out.insights.append(
                    Insight(
                        kind="weakness",
                        severity="warning",
                        title=f"Projet « {project.name} » à risque",
                        detail="; ".join(weak_points),
                        metric=f"projects.{project.id}.health",
                        value=health,
                    )
                )
            elif status == "on_track":
                out.insights.append(
                    Insight(
                        kind="strength",
                        title=f"Projet « {project.name} » sur la bonne voie",
                        metric=f"projects.{project.id}.health",
                        value=health,
                    )
                )
            for k in kpis:
                if k.trend == "degrading":
                    out.insights.append(
                        Insight(
                            kind="trend",
                            severity="warning",
                            title=f"KPI « {k.name} » ({project.name}) se dégrade",
                            detail=f"Pente {k.trend_slope} par période.",
                            value=k.trend_slope,
                        )
                    )
            if weak and status in ("at_risk", "off_track"):
                worst = weak[0]
                gap = 1 - min(worst.attainment, 1.0)
                share = worst.weight / total_weight if total_weight else 1.0
                out.recommendations.append(
                    Recommendation(
                        title=f"Redresser « {worst.name} » sur le projet « {project.name} »",
                        rationale=(
                            f"KPI le plus éloigné de sa cible ({worst.attainment:.0%}) ; "
                            f"il pèse {share:.0%} de la "
                            "santé du projet."
                        ),
                        impact=round(min(10.0, 3 + 7 * gap * (0.5 + share)), 2),
                        effort=5,
                        confidence=0.7 if worst.trend else 0.55,
                        urgency=8 if status == "off_track" else 6,
                        risk=0.15,
                        tags=["projets", "kpi"],
                        evidence=[f"{worst.name}={worst.value} / cible {worst.target}", f"santé={health}"],
                    )
                )
        scored = [r.health for r in results if r.health is not None]
        out.projects = results
        out.average_health = round(mean(scored), 4) if scored else None
        if out.average_health is not None:
            out.metrics["projects.health_avg"] = out.average_health
        out.metrics["projects.off_track"] = float(sum(1 for r in results if r.status == "off_track"))
        if not projects:
            out.summary = "Aucun projet à analyser."
        else:
            counts = {s: sum(1 for r in results if r.status == s) for s in ("on_track", "at_risk", "off_track")}
            out.summary = (
                f"{len(results)} projet(s) : {counts['on_track']} sur la bonne voie, {counts['at_risk']} à "
                f"risque, {counts['off_track']} hors trajectoire"
                + (f" (santé moyenne {out.average_health:.0%})." if out.average_health is not None else ".")
            )
        return out

    # -- fonctionnalités inutiles ----------------------------------------------------------------------------
    @action("Détecter les fonctionnalités inutilisées et leur coût de maintenance (Pareto des usages)")
    def detect_unused_features(self, ctx: SkillContext, data: UnusedFeaturesInput) -> UnusedFeaturesOutput:
        projects = _load_projects(ctx, data.projects)
        today = ctx.clock.now().date()
        rows: list[FeatureUsage] = []
        for project in projects:
            if not project.features:
                continue
            active = project.active_users or max((f.users for f in project.features), default=0)
            # Le quintile est calculé sur les fonctionnalités évaluables (les nouveautés récentes faussent la base).
            mature = [
                f for f in project.features if f.released_on is None or (today - f.released_on).days >= data.recent_days
            ]
            p20 = percentile([float(f.uses) for f in (mature or project.features)], 20)
            total_uses = sum(f.uses for f in project.features)
            pareto: set[str] = set()
            cumulative = 0
            for feature in sorted(project.features, key=lambda f: -f.uses):
                if total_uses == 0 or cumulative >= 0.8 * total_uses:
                    break
                pareto.add(feature.name)
                cumulative += feature.uses
            for feature in project.features:
                share = round(feature.users / active, 4) if active else None
                recent = feature.released_on is not None and (today - feature.released_on).days < data.recent_days
                low_share = share is not None and share < data.share_threshold
                # Usages faibles : quintile inférieur OU moins de 1 % des usages totaux (robuste aux petits effectifs).
                low_uses = feature.uses <= p20 or (total_uses > 0 and feature.uses / total_uses < 0.01)
                unused = low_share and low_uses and not recent
                if recent:
                    reason = f"Sortie depuis moins de {data.recent_days} jours : non évaluée."
                elif unused:
                    reason = (
                        f"{share:.1%} des utilisateurs actifs (< {data.share_threshold:.0%}) et usages "
                        f"({feature.uses}) dans le quintile inférieur ou < 1 % du total."
                    )
                elif share is None:
                    reason = "Nombre d'utilisateurs actifs inconnu."
                else:
                    reason = f"Utilisée par {share:.1%} des utilisateurs actifs."
                rows.append(
                    FeatureUsage(
                        project_id=project.id,
                        project=project.name,
                        feature=feature.name,
                        users=feature.users,
                        uses=feature.uses,
                        user_share=share,
                        maintenance_cost_monthly=to_decimal(feature.maintenance_cost_monthly),
                        recent=recent,
                        in_pareto=feature.name in pareto,
                        unused=unused,
                        reason=reason,
                    )
                )
        unused_rows = [r for r in rows if r.unused]
        wasted = to_decimal(sum((r.maintenance_cost_monthly for r in unused_rows), Decimal("0")))
        out = UnusedFeaturesOutput(features=rows, unused=unused_rows, wasted_monthly_cost=wasted)
        out.metrics["features.unused_count"] = float(len(unused_rows))
        out.metrics["features.wasted_monthly_cost"] = float(wasted)
        for row in unused_rows:
            annual = to_decimal(row.maintenance_cost_monthly * 12)
            out.insights.append(
                Insight(
                    kind="weakness",
                    severity="warning" if row.maintenance_cost_monthly > 0 else "info",
                    title=f"Fonctionnalité peu utilisée : « {row.feature} » ({row.project})",
                    detail=row.reason,
                    value=row.user_share,
                )
            )
            share_ratio = (row.user_share or 0) / data.share_threshold
            out.recommendations.append(
                Recommendation(
                    title=f"Retirer ou simplifier « {row.feature} » ({row.project})",
                    rationale=row.reason + (f" Économie : {annual} par an." if annual > 0 else ""),
                    impact=round(min(10.0, 2 + float(annual) / 1000), 2),
                    effort=3,
                    confidence=round(0.6 + 0.3 * (1 - min(1.0, share_ratio)), 3),
                    urgency=4,
                    risk=0.2,
                    estimated_value=annual if annual > 0 else None,
                    external=True,
                    tags=["produit", "simplification"],
                    evidence=[f"utilisateurs={row.users}", f"usages={row.uses}", f"part={row.user_share}"],
                )
            )
        if not rows:
            out.summary = "Aucune donnée d'usage de fonctionnalités (champ « features » des projets)."
        else:
            out.summary = (
                f"{len(unused_rows)} fonctionnalité(s) inutilisée(s) sur {len(rows)}, coût de maintenance "
                f"évitable {wasted} par mois."
            )
        return out

    # -- recommandations -------------------------------------------------------------------------------------
    @action("Backlog d'améliorations priorisé (ICE) : points faibles, tendances négatives, fonctionnalités inutiles")
    def recommend_improvements(self, ctx: SkillContext, data: ImprovementsInput) -> ImprovementsOutput:
        projects = _load_projects(ctx, data.projects)
        analysis = self.analyze_projects(ctx, ProjectsInput(projects=projects))
        unused = self.detect_unused_features(ctx, UnusedFeaturesInput(projects=projects))
        items: list[BacklogItem] = []
        for health in analysis.projects:
            total_weight = sum(k.weight for k in health.kpis) or 1.0
            for kpi in health.kpis:
                share = kpi.weight / total_weight
                if kpi.attainment < 0.9:
                    gap = 1 - min(kpi.attainment, 1.0)
                    impact = round(min(10.0, 2 + 8 * gap * (0.4 + share)), 2)
                    confidence = 0.7 if kpi.trend else 0.55
                    ease = 4.0 if gap > 0.5 else 5.5
                    items.append(
                        BacklogItem(
                            title=f"Améliorer « {kpi.name} »",
                            project=health.name,
                            kind="kpi",
                            impact=impact,
                            confidence=confidence,
                            ease=ease,
                            ice=round(impact * confidence * ease, 3),
                            rationale=(f"Atteinte {kpi.attainment:.0%} (valeur {kpi.value}, cible {kpi.target})."),
                        )
                    )
                elif kpi.trend == "degrading":
                    impact = round(min(10.0, 3 + 5 * share), 2)
                    items.append(
                        BacklogItem(
                            title=f"Stopper la dégradation de « {kpi.name} »",
                            project=health.name,
                            kind="tendance",
                            impact=impact,
                            confidence=0.6,
                            ease=6.0,
                            ice=round(impact * 0.6 * 6.0, 3),
                            rationale=(
                                f"Cible atteinte ({kpi.attainment:.0%}) mais pente défavorable ({kpi.trend_slope})."
                            ),
                        )
                    )
        for row in unused.unused:
            annual = float(row.maintenance_cost_monthly * 12)
            impact = round(min(10.0, 2 + annual / 1000), 2)
            items.append(
                BacklogItem(
                    title=f"Retirer « {row.feature} »",
                    project=row.project,
                    kind="fonctionnalité",
                    impact=impact,
                    confidence=0.7,
                    ease=7.0,
                    ice=round(impact * 0.7 * 7.0, 3),
                    rationale=row.reason,
                )
            )
        items.sort(key=lambda i: (-i.ice, i.title))
        backlog = items[: data.max_items]
        out = ImprovementsOutput(backlog=backlog)
        for item in backlog[:5]:
            out.recommendations.append(
                Recommendation(
                    title=f"{item.title} ({item.project})",
                    rationale=item.rationale,
                    impact=item.impact,
                    effort=round(max(0.5, 11 - item.ease), 2),
                    confidence=item.confidence,
                    urgency=7 if item.kind == "kpi" else 5,
                    risk=0.2 if item.kind == "fonctionnalité" else 0.1,
                    external=item.kind == "fonctionnalité",
                    tags=["amélioration", item.kind],
                    evidence=[f"ICE={item.ice}"],
                )
            )
        out.summary = (
            f"{len(backlog)} amélioration(s) priorisée(s) par ICE."
            if backlog
            else "Aucune amélioration prioritaire détectée."
        )
        return out

    # -- vérification après modification ---------------------------------------------------------------------
    @action("Vérifier l'effet réel d'un changement (avant/après, test t de Welch, d de Cohen)")
    def verify_change(self, ctx: SkillContext, data: VerifyChangeInput) -> VerifyChangeOutput:
        before, after, metric, goal, label = data.before, data.after, data.metric, data.goal, data.description
        if data.change_id:
            raw = ctx.store.get("change", data.change_id)
            if raw is None:
                raise SkillError(f"changement inconnu : {data.change_id}")
            change = Change.model_validate(raw)
            before, after, metric, label = change.before, change.after, change.metric, change.description
            goal = change.goal or self._goal_from_project(ctx, change) or data.goal
        if len(before) < 2 or len(after) < 2:
            raise SkillError("au moins deux mesures avant ET après sont nécessaires")
        result = welch_t_test(before, after)
        pooled = math.sqrt((variance(before) + variance(after)) / 2)
        cohens_d = round(result.effect / pooled, 4) if pooled > 0 else None
        if result.p_value < 0.05:
            good = result.effect > 0 if goal == "increase" else result.effect < 0
            verdict: Literal["amélioré", "dégradé", "non concluant"] = "amélioré" if good else "dégradé"
        else:
            verdict = "non concluant"
        needed: int | None = None
        if verdict == "non concluant" and result.effect != 0 and pooled > 0:
            needed = math.ceil(16 * pooled**2 / result.effect**2)
        metric_name = metric or "métrique"
        out = VerifyChangeOutput(
            metric=metric_name,
            goal=goal,
            n_before=len(before),
            n_after=len(after),
            mean_before=round(mean(before), 4),
            mean_after=round(mean(after), 4),
            effect=round(result.effect, 4),
            relative_effect=round(result.relative_effect, 4) if result.relative_effect is not None else None,
            p_value=round(result.p_value, 6),
            cohens_d=cohens_d,
            verdict=verdict,
            sample_size_needed=needed,
        )
        what = f"« {label} »" if label else "le changement"
        rel = f" ({result.relative_effect:+.1%})" if result.relative_effect is not None else ""
        out.summary = f"{metric_name} : {out.mean_before} → {out.mean_after}{rel}, p = {out.p_value:.4f} → {verdict}."
        if verdict == "dégradé":
            out.insights.append(
                Insight(
                    kind="anomaly",
                    severity="critical",
                    title=f"{what} dégrade {metric_name}",
                    detail=out.summary,
                    metric=metric_name,
                    value=result.effect,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Revenir sur {what} ou le corriger",
                    rationale=f"Dégradation statistiquement significative de {metric_name}.",
                    impact=7,
                    effort=3,
                    confidence=round(min(0.95, 1 - result.p_value), 3),
                    urgency=8,
                    risk=0.1,
                    tags=["vérification", "régression"],
                    evidence=[out.summary],
                )
            )
        elif verdict == "amélioré":
            out.insights.append(
                Insight(
                    kind="strength",
                    title=f"{what} améliore {metric_name}",
                    detail=out.summary,
                    metric=metric_name,
                    value=result.effect,
                )
            )
        else:
            out.insights.append(
                Insight(
                    kind="observation",
                    title=f"Effet de {what} non démontré",
                    detail=out.summary,
                    metric=metric_name,
                )
            )
            if needed:
                out.recommendations.append(
                    Recommendation(
                        title=f"Prolonger la mesure de {what} (≈ {needed} points par période)",
                        rationale="L'écart observé n'est pas distinguable du bruit avec l'échantillon actuel "
                        "(règle de Lehr, puissance 80 %).",
                        impact=3,
                        effort=1.5,
                        confidence=0.7,
                        urgency=3,
                        risk=0.0,
                        tags=["vérification", "mesure"],
                        evidence=[f"n avant={len(before)}", f"n après={len(after)}"],
                    )
                )
        return out

    @staticmethod
    def _goal_from_project(ctx: SkillContext, change: Change) -> Literal["increase", "decrease"] | None:
        if not change.project_id:
            return None
        raw = ctx.store.get("project", change.project_id)
        if raw is None:
            return None
        for kpi in raw.get("kpis", []):
            if kpi.get("name") == change.metric:
                return "decrease" if kpi.get("direction") == "lower" else "increase"
        return None

    # -- auto-analyse de SOVEREIGN ----------------------------------------------------------------------
    @action("Auditer l'utilité réelle des Skills et commandes de SOVEREIGN (usage, fiabilité, adoption)")
    def audit_system(self, ctx: SkillContext, data: AuditSystemInput) -> AuditSystemOutput:
        utility: dict[str, dict[str, Any]] = ctx.kernel.skill_utility()
        total_runs = sum(int(v["runs"]) for v in utility.values())
        sufficient = total_runs >= data.min_activity
        audits: list[SkillAudit] = []
        never, unreliable, unadopted = [], [], []
        for name, values in sorted(utility.items()):
            flags: list[str] = []
            runs = int(values["runs"])
            if runs == 0 and sufficient:
                flags.append("jamais utilisée")
                never.append(name)
            reliability = values.get("reliability")
            if reliability is not None and reliability < 0.9:
                flags.append(f"fiabilité {reliability:.0%}")
                unreliable.append(name)
            proposed, adopted = int(values["proposed"]), int(values["adopted"])
            if proposed >= data.min_recommendations and adopted == 0:
                flags.append(f"0 adoption sur {proposed} recommandations")
                unadopted.append(name)
            audits.append(
                SkillAudit(
                    skill=name,
                    runs=runs,
                    reliability=reliability,
                    proposed=proposed,
                    adopted=adopted,
                    adoption_rate=values.get("adoption_rate"),
                    success_posterior=float(values["success_posterior"]),
                    flags=flags,
                )
            )
        usage: dict[str, int] = {}
        for row in ctx.db.query("SELECT command, COUNT(*) AS n FROM command_runs GROUP BY command"):
            name = str(row["command"]).lstrip("/")
            usage[name] = usage.get(name, 0) + int(row["n"])
        commands = [CommandUsage(command=c, runs=usage.get(c, 0)) for c in KNOWN_COMMANDS]
        commands += [CommandUsage(command=c, runs=n) for c, n in sorted(usage.items()) if c not in KNOWN_COMMANDS]
        total_commands = sum(usage.values())
        never_commands = (
            [c.command for c in commands if c.runs == 0 and c.command in KNOWN_COMMANDS]
            if total_commands >= data.min_activity
            else []
        )
        out = AuditSystemOutput(
            total_skill_runs=total_runs,
            skills=audits,
            commands=commands,
            never_used_skills=never,
            unreliable_skills=unreliable,
            unadopted_skills=unadopted,
            never_used_commands=never_commands,
            sufficient_history=sufficient,
        )
        reliabilities = [a.reliability for a in audits if a.reliability is not None]
        out.metrics["system.skill_runs"] = float(total_runs)
        out.metrics["system.skills_never_used"] = float(len(never))
        out.metrics["system.skills_unreliable"] = float(len(unreliable))
        if reliabilities:
            out.metrics["system.avg_reliability"] = round(mean(reliabilities), 4)
        for name in unreliable:
            audit = next(a for a in audits if a.skill == name)
            out.insights.append(
                Insight(
                    kind="weakness",
                    severity="warning",
                    title=f"Skill peu fiable : {name}",
                    detail=", ".join(audit.flags),
                    value=audit.reliability,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Fiabiliser la Skill {name}",
                    rationale=f"{audit.runs} exécution(s), fiabilité {audit.reliability:.0%} : "
                    "examiner les erreurs "
                    "dans skill_runs.",
                    impact=6,
                    effort=4,
                    confidence=0.75,
                    urgency=6,
                    risk=0.05,
                    tags=["système", "fiabilité"],
                )
            )
        for name in unadopted:
            audit = next(a for a in audits if a.skill == name)
            out.recommendations.append(
                Recommendation(
                    title=f"Revoir la pertinence des recommandations de {name}",
                    rationale=f"Aucune des {audit.proposed} recommandations n'a été adoptée.",
                    impact=4,
                    effort=3,
                    confidence=0.6,
                    urgency=3,
                    risk=0.05,
                    tags=["système", "utilité"],
                )
            )
        for name in never:
            out.recommendations.append(
                Recommendation(
                    title=f"Candidat au retrait : Skill {name}",
                    rationale=(f"Jamais exécutée sur {total_runs} exécutions de Skills : utilité non démontrée."),
                    impact=2,
                    effort=2,
                    confidence=0.5,
                    urgency=2,
                    risk=0.1,
                    tags=["système", "simplification"],
                )
            )
        if never_commands:
            out.insights.append(
                Insight(
                    kind="observation",
                    title="Commandes jamais utilisées",
                    detail=", ".join(f"/{c}" for c in never_commands),
                )
            )
        if not sufficient:
            out.summary = (
                f"Auto-audit : historique insuffisant ({total_runs} exécution(s) < {data.min_activity}) "
                "pour juger l'inutilisation ; fiabilité et adoption suivies."
            )
        else:
            out.summary = (
                f"Auto-audit sur {total_runs} exécutions : {len(never)} Skill(s) jamais utilisée(s), "
                f"{len(unreliable)} peu fiable(s), {len(unadopted)} sans adoption."
            )
        return out


SKILL = AnalysisOptimizationSkill
