"""SKILL 3 — BUSINESS DEVELOPMENT.

Imagine de nouvelles activités (matrice d'Ansoff), évalue les modèles économiques (économie unitaire :
LTV, CAC, délai de récupération, point mort), compare les sources de revenus, classe les opportunités
(RICE + cohérence stratégique) et prépare des plans de lancement par phases dont le budget est borné par
l'enveloppe autorisée.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sovereign.core.money import to_decimal
from sovereign.core.stats import clamp
from sovereign.skills.base import (
    EmptyInput,
    FollowUp,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillError,
    SkillOutput,
    TargetMetric,
    action,
)

StreamType = Literal["subscription", "one_off", "usage", "commission"]
Verdict = Literal["viable", "fragile", "non viable"]

# Prévisibilité des revenus par type de flux (récurrence contractuelle → ponctuel).
PREDICTABILITY: dict[str, float] = {"subscription": 1.0, "usage": 0.7, "commission": 0.6, "one_off": 0.4}
# Horizon retenu pour la LTV des flux récurrents sans attrition mesurée (usage, commission).
RECURRING_HORIZON_MONTHS = 12
PHASES: list[tuple[str, float]] = [
    ("validation", 0.10),
    ("MVP", 0.40),
    ("lancement", 0.30),
    ("croissance", 0.20),
]
_STOPWORDS = {"pour", "avec", "dans", "les", "des", "une", "sur", "par", "the", "and", "for", "with"}


def _norm(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stems(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", _norm(text))
    return {t[:6] for t in tokens if len(t) > 3 and t not in _STOPWORDS}


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


# -- Données de l'espace de travail --------------------------------------------------------------
class RevenueStream(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: StreamType
    price: Decimal = Field(gt=0)
    volume_per_month: float = Field(ge=0, description="Unités vendues ou clients actifs par mois")
    gross_margin: float = Field(ge=0, le=1)
    churn_monthly: float | None = Field(None, ge=0, le=1)


class BusinessModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    revenue_streams: list[RevenueStream] = Field(min_length=1)
    fixed_costs_monthly: Decimal = Field(Decimal("0"), ge=0)
    cac: Decimal = Field(Decimal("0"), ge=0)

    @property
    def key(self) -> str:
        return self.id or _norm(self.name)


class Opportunity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str
    description: str = ""
    source_skill: str = ""
    potential_value: Decimal = Field(Decimal("0"), ge=0)
    confidence: float = Field(0.5, ge=0, le=1)
    score: float = 0.0
    status: str = "new"
    tags: list[str] = Field(default_factory=list)
    reach: int | None = Field(None, ge=0)
    effort: float | None = Field(None, ge=0.5, le=10)

    @property
    def key(self) -> str:
        return self.id or _norm(self.title)


class Asset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    kind: Literal["product", "skill", "audience", "technology", "channel", "brand"]


# -- Évaluation des modèles économiques ------------------------------------------------------------
class ModelsInput(BaseModel):
    models: list[BusinessModel] = Field(default_factory=list, description="Vide ⇒ « business_model » du store")


class StreamEconomics(BaseModel):
    name: str
    type: StreamType
    monthly_revenue: Decimal
    monthly_contribution: Decimal
    ltv: Decimal
    ltv_basis: str


class ModelEvaluation(BaseModel):
    id: str
    name: str
    monthly_revenue: Decimal
    monthly_contribution: Decimal
    fixed_costs_monthly: Decimal
    operating_result_monthly: Decimal
    break_even_volume: float | None
    current_volume: float
    ltv: Decimal
    cac: Decimal
    ltv_cac: float | None
    payback_months: float | None
    verdict: Verdict
    streams: list[StreamEconomics]
    weaknesses: list[str]


class ModelsOutput(SkillOutput):
    models: list[ModelEvaluation]


class StreamScore(BaseModel):
    model: str
    name: str
    type: StreamType
    monthly_contribution: Decimal
    predictability: float
    scalability: float
    score: float
    rank: int


class StreamsOutput(SkillOutput):
    streams: list[StreamScore]


# -- Classement des opportunités ---------------------------------------------------------------------
class RankInput(BaseModel):
    opportunities: list[Opportunity] = Field(default_factory=list, description="Vide ⇒ « opportunity » du store")
    include_rejected: bool = False


class RankedOpportunity(BaseModel):
    id: str
    title: str
    rank: int
    reach: int
    reach_estimated: bool
    impact: float
    confidence: float
    effort: float
    rice: float
    strategic_fit: float
    score: float
    potential_value: Decimal


class RankOutput(SkillOutput):
    ranking: list[RankedOpportunity]


# -- Nouvelles activités -------------------------------------------------------------------------------
class ActivitiesInput(BaseModel):
    assets: list[Asset] = Field(default_factory=list, description="Vide ⇒ « asset » du store")
    current_markets: list[str] = Field(default_factory=list, description="Vide ⇒ marchés / segments du store")
    new_markets: list[str] = Field(default_factory=list, description="Vide ⇒ segments des besoins non couverts")
    per_quadrant: int = Field(4, ge=1, le=10)


class Activity(BaseModel):
    quadrant: Literal["pénétration", "développement produit", "développement marché", "diversification"]
    title: str
    rationale: str
    risk_level: int
    assets: list[str]


class ActivitiesOutput(SkillOutput):
    activities: list[Activity]


# -- Plan de lancement ---------------------------------------------------------------------------------
class LaunchInput(BaseModel):
    name: str = Field(min_length=1)
    potential_annual_revenue: Decimal | None = Field(None, ge=0)
    budget: Decimal | None = Field(None, gt=0, description="Budget souhaité ; défaut : 10 % du potentiel")
    target_customers: int = Field(100, ge=1)
    validation_weeks: int = Field(4, ge=1, le=52)
    mvp_weeks: int = Field(8, ge=1, le=104)
    launch_weeks: int = Field(4, ge=1, le=52)
    growth_weeks: int = Field(12, ge=1, le=104)


class LaunchPhase(BaseModel):
    name: str
    start_week: int
    end_week: int
    budget: Decimal
    funded: Decimal
    funding_status: Literal["financée", "partiellement financée", "à financer par les encaissements futurs"]
    objectives: list[str]
    go_no_go: list[str]


class LaunchOutput(SkillOutput):
    name: str
    requested_budget: Decimal
    available_budget: Decimal
    funded_budget: Decimal
    funding_gap: Decimal
    total_weeks: int
    phases: list[LaunchPhase]


class AssessOutput(SkillOutput):
    business_models: int
    opportunities: int
    assets: int
    projects: int = 0


def _merge(target: SkillOutput, part: SkillOutput) -> None:
    target.insights.extend(part.insights)
    target.recommendations.extend(part.recommendations)
    target.metrics.update(part.metrics)


def _stream_economics(stream: RevenueStream) -> StreamEconomics:
    monthly_revenue = to_decimal(stream.price * _dec(stream.volume_per_month))
    unit_margin = stream.price * _dec(stream.gross_margin)
    contribution = to_decimal(monthly_revenue * _dec(stream.gross_margin))
    if stream.type == "subscription":
        if stream.churn_monthly:
            ltv = to_decimal(unit_margin / _dec(stream.churn_monthly))
            basis = f"prix × marge / churn ({stream.churn_monthly:.1%})"
        else:
            ltv = to_decimal(unit_margin * 36)
            basis = "churn inconnu : plafonnée à 36 mois"
    elif stream.type == "one_off":
        ltv = to_decimal(unit_margin)
        basis = "achat unique : prix × marge"
    else:
        ltv = to_decimal(unit_margin * RECURRING_HORIZON_MONTHS)
        basis = f"flux récurrent sans churn mesuré : {RECURRING_HORIZON_MONTHS} mois"
    return StreamEconomics(
        name=stream.name,
        type=stream.type,
        monthly_revenue=monthly_revenue,
        monthly_contribution=contribution,
        ltv=ltv,
        ltv_basis=basis,
    )


class BusinessDevelopmentSkill(Skill):
    name = "business-development"
    version = "1.0.0"

    @staticmethod
    def _models(ctx: SkillContext, given: list[BusinessModel]) -> list[BusinessModel]:
        return given or [BusinessModel.model_validate(m) for m in ctx.store.list("business_model")]

    @staticmethod
    def _opportunities(ctx: SkillContext, given: list[Opportunity]) -> list[Opportunity]:
        return given or [Opportunity.model_validate(o) for o in ctx.store.list("opportunity")]

    @staticmethod
    def _assets(ctx: SkillContext, given: list[Asset]) -> list[Asset]:
        return given or [Asset.model_validate(a) for a in ctx.store.list("asset")]

    @action("Synthèse développement commercial : modèles économiques, opportunités, actifs (pour le directeur)")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        models = self._models(ctx, [])
        opportunities = self._opportunities(ctx, [])
        assets = self._assets(ctx, [])
        projects = ctx.store.count("project")
        out = AssessOutput(
            business_models=len(models), opportunities=len(opportunities), assets=len(assets), projects=projects
        )
        if not (models or opportunities or assets or projects >= 2):
            out.summary = (
                "Aucune donnée de développement : importez des « business_model » (flux de revenus, "
                "coûts fixes, CAC), des « opportunity » (ou lancez "
                "business-intelligence.detect_opportunities), des « asset » "
                "(produits, compétences, technologies) ou au moins deux « project » pour activer l'analyse."
            )
            return out
        parts: list[str] = []
        if models:
            evaluation = self.evaluate_business_model(ctx, ModelsInput(models=models))
            _merge(out, evaluation)
            parts.append(evaluation.summary)
        if opportunities:
            ranking = self.rank_opportunities(ctx, RankInput(opportunities=opportunities))
            _merge(out, ranking)
            parts.append(ranking.summary)
        if assets:
            activities = self.generate_activities(ctx, ActivitiesInput(assets=assets, per_quadrant=2))
            out.insights.extend(activities.insights)
            parts.append(activities.summary)
        if projects >= 2:
            review = self.portfolio_review(ctx, PortfolioInput())
            _merge(out, review)
            parts.append(review.summary)
        out.summary = " ".join(parts)
        return out

    @action("Évaluer un modèle économique : MRR, contribution, LTV/CAC, délai de récupération, point mort")
    def evaluate_business_model(self, ctx: SkillContext, data: ModelsInput) -> ModelsOutput:
        models = self._models(ctx, data.models)
        evaluations: list[ModelEvaluation] = []
        for model in models:
            streams = [_stream_economics(s) for s in model.revenue_streams]
            revenue = to_decimal(sum((s.monthly_revenue for s in streams), Decimal("0")))
            contribution = to_decimal(sum((s.monthly_contribution for s in streams), Decimal("0")))
            result = to_decimal(contribution - model.fixed_costs_monthly)
            volume = sum(s.volume_per_month for s in model.revenue_streams)
            per_unit = contribution / _dec(volume) if volume > 0 else Decimal("0")
            break_even = round(float(model.fixed_costs_monthly / per_unit), 1) if per_unit > 0 else None
            if revenue > 0:
                ltv = to_decimal(sum((s.ltv * s.monthly_revenue for s in streams), Decimal("0")) / revenue)
            else:
                ltv = to_decimal(sum((s.ltv for s in streams), Decimal("0")) / len(streams))
            ltv_cac = round(float(ltv / model.cac), 3) if model.cac > 0 else None
            recurring = [s for s in model.revenue_streams if s.type != "one_off"]
            payback: float | None
            if model.cac == 0:
                payback = 0.0
            elif recurring:
                weight = sum(float(s.price) * s.volume_per_month for s in recurring)
                if weight > 0:
                    unit_monthly = (
                        sum(float(s.price) * s.gross_margin * float(s.price) * s.volume_per_month for s in recurring)
                        / weight
                    )
                else:
                    unit_monthly = sum(float(s.price) * s.gross_margin for s in recurring) / len(recurring)
                payback = round(float(model.cac) / unit_monthly, 2) if unit_monthly > 0 else None
            else:
                unit = max(float(s.price) * s.gross_margin for s in model.revenue_streams)
                payback = 0.0 if unit >= float(model.cac) else None
            weaknesses: list[str] = []
            if ltv_cac is not None and ltv_cac < 3:
                weaknesses.append(f"LTV/CAC de {ltv_cac:.2f} (< 3)")
            if payback is None or payback > 12:
                weaknesses.append("CAC non récupéré en 12 mois" if payback else "CAC jamais récupéré")
            if result < 0:
                weaknesses.append(f"résultat mensuel négatif ({result})")
            verdict: Verdict
            if (ltv_cac is None or ltv_cac >= 3) and payback is not None and payback <= 12 and result >= 0:
                verdict = "viable"
            elif (ltv_cac is not None and ltv_cac < 1) or (
                result < 0 and (break_even is None or break_even > 3 * max(volume, 1))
            ):
                verdict = "non viable"
            else:
                verdict = "fragile"
            evaluations.append(
                ModelEvaluation(
                    id=model.key,
                    name=model.name,
                    monthly_revenue=revenue,
                    monthly_contribution=contribution,
                    fixed_costs_monthly=to_decimal(model.fixed_costs_monthly),
                    operating_result_monthly=result,
                    break_even_volume=break_even,
                    current_volume=volume,
                    ltv=ltv,
                    cac=to_decimal(model.cac),
                    ltv_cac=ltv_cac,
                    payback_months=payback,
                    verdict=verdict,
                    streams=streams,
                    weaknesses=weaknesses,
                )
            )
        out = ModelsOutput(models=evaluations)
        if not evaluations:
            out.summary = "Aucun modèle économique : renseignez des enregistrements « business_model »."
            return out
        out.summary = " ".join(
            f"« {e.name} » : {e.verdict} (MRR {e.monthly_revenue}, LTV/CAC "
            f"{e.ltv_cac if e.ltv_cac is not None else 'n/a'}, récupération "
            f"{e.payback_months if e.payback_months is not None else '∞'} mois)."
            for e in evaluations
        )
        out.metrics["bizdev.mrr"] = float(sum((e.monthly_revenue for e in evaluations), Decimal("0")))
        for e in evaluations:
            if e.ltv_cac is not None:
                out.metrics[f"bizdev.{e.id}.ltv_cac"] = e.ltv_cac
            if e.payback_months is not None:
                out.metrics[f"bizdev.{e.id}.payback_months"] = e.payback_months
            out.metrics[f"bizdev.{e.id}.operating_result"] = float(e.operating_result_monthly)
            if e.verdict == "viable":
                out.insights.append(
                    Insight(
                        kind="strength",
                        title=f"Modèle économique viable : {e.name}",
                        detail=f"LTV/CAC {e.ltv_cac}, récupération {e.payback_months} mois.",
                    )
                )
                continue
            out.insights.append(
                Insight(
                    kind="weakness",
                    severity="critical" if e.verdict == "non viable" else "warning",
                    title=f"Modèle économique {e.verdict} : {e.name}",
                    detail="; ".join(e.weaknesses),
                    value=e.ltv_cac,
                )
            )
            if e.ltv_cac is not None and e.ltv_cac < 3:
                target_cac = to_decimal(e.ltv / 3)
                out.recommendations.append(
                    Recommendation(
                        title=f"Ramener le CAC de « {e.name} » sous {target_cac} ou relever LTV",
                        rationale=(
                            f"LTV {e.ltv} pour un CAC {e.cac} : il faut LTV/CAC ≥ 3 pour une "
                            "croissance saine "
                            "(rétention, prix ou canaux moins chers)."
                        ),
                        impact=clamp(9 - 2 * e.ltv_cac, 3, 9),
                        effort=5,
                        confidence=0.7,
                        urgency=7,
                        risk=0.15,
                        tags=["modèle économique", "cac"],
                        evidence=[f"LTV={e.ltv}", f"CAC={e.cac}"],
                        follow_up=FollowUp(skill="marketing-strategy", action="measure_campaigns", payload={}),
                    )
                )
            if e.operating_result_monthly < 0 and e.break_even_volume is not None:
                gap = round(e.break_even_volume - e.current_volume, 1)
                out.recommendations.append(
                    Recommendation(
                        title=f"Atteindre le point mort de « {e.name} » (+{gap:g} unités/mois)",
                        rationale=(
                            f"Coûts fixes {e.fixed_costs_monthly} pour une contribution de "
                            f"{e.monthly_contribution} ; point mort à {e.break_even_volume:g} unités/mois."
                        ),
                        impact=8,
                        effort=6,
                        confidence=0.6,
                        urgency=8,
                        risk=0.2,
                        estimated_value=to_decimal(-e.operating_result_monthly * 12),
                        tags=["modèle économique"],
                    )
                )
        return out

    @action("Comparer et classer les sources de revenus (contribution, prévisibilité, scalabilité)")
    def compare_revenue_streams(self, ctx: SkillContext, data: ModelsInput) -> StreamsOutput:
        models = self._models(ctx, data.models)
        rows: list[tuple[str, RevenueStream, StreamEconomics]] = [
            (m.name, s, _stream_economics(s)) for m in models for s in m.revenue_streams
        ]
        if not rows:
            return StreamsOutput(streams=[], summary="Aucune source de revenus : renseignez des « business_model ».")
        best = max((float(e.monthly_contribution) for _, _, e in rows), default=0.0)
        scored: list[StreamScore] = []
        for model_name, stream, eco in rows:
            contribution_n = float(eco.monthly_contribution) / best if best > 0 else 0.0
            predictability = PREDICTABILITY[stream.type]
            score = round(0.5 * contribution_n + 0.3 * predictability + 0.2 * stream.gross_margin, 4)
            scored.append(
                StreamScore(
                    model=model_name,
                    name=stream.name,
                    type=stream.type,
                    monthly_contribution=eco.monthly_contribution,
                    predictability=predictability,
                    scalability=stream.gross_margin,
                    score=score,
                    rank=0,
                )
            )
        scored.sort(key=lambda s: (-s.score, s.name))
        for index, item in enumerate(scored, start=1):
            item.rank = index
        top = scored[0]
        total = sum((s.monthly_contribution for s in scored), Decimal("0"))
        out = StreamsOutput(
            streams=scored,
            summary=(f"{len(scored)} source(s) de revenus ; la plus solide : « {top.name} » (score {top.score:.2f})."),
        )
        share = float(top.monthly_contribution / total) if total > 0 else 0.0
        if len(scored) > 1 and share >= 0.7:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Dépendance à une seule source de revenus",
                    detail=f"« {top.name} » porte {share:.0%} de la contribution.",
                    value=share,
                )
            )
        weak = [s for s in scored if s.predictability <= 0.4 and s.scalability < 0.5]
        if weak:
            out.recommendations.append(
                Recommendation(
                    title=f"Transformer « {weak[0].name} » en revenu récurrent",
                    rationale=(
                        "Flux ponctuel à faible marge : un abonnement ou un forfait améliore prévisibilité et marge."
                    ),
                    impact=5,
                    effort=5,
                    confidence=0.5,
                    urgency=3,
                    risk=0.2,
                    tags=["revenus", "récurrence"],
                )
            )
        return out

    @action("Classer les opportunités (RICE pondéré par la cohérence stratégique avec nos actifs)")
    def rank_opportunities(self, ctx: SkillContext, data: RankInput) -> RankOutput:
        opportunities = [
            o for o in self._opportunities(ctx, data.opportunities) if data.include_rejected or o.status != "rejected"
        ]
        if not opportunities:
            return RankOutput(ranking=[], summary="Aucune opportunité à classer : lancez la détection d'opportunités.")
        assets = self._assets(ctx, [])
        asset_stems = [_stems(a.name) for a in assets]
        max_value = max((o.potential_value for o in opportunities), default=Decimal("0"))
        ranking: list[RankedOpportunity] = []
        for opp in opportunities:
            ratio = float(opp.potential_value / max_value) if max_value > 0 else 0.0
            impact = (
                3.0
                if ratio >= 0.75
                else 2.0
                if ratio >= 0.5
                else 1.0
                if ratio >= 0.25
                else 0.5
                if ratio >= 0.1
                else 0.25
            )
            reach = opp.reach if opp.reach else 100
            effort = opp.effort or 5.0
            rice = reach * impact * opp.confidence / effort
            opp_stems = _stems(" ".join([opp.title, opp.description, *opp.tags]))
            matches = sum(1 for stems in asset_stems if stems & opp_stems)
            fit = min(1.0, matches / min(3, len(assets))) if assets else 0.0
            ranking.append(
                RankedOpportunity(
                    id=opp.key,
                    title=opp.title,
                    rank=0,
                    reach=reach,
                    reach_estimated=not opp.reach,
                    impact=impact,
                    confidence=opp.confidence,
                    effort=effort,
                    rice=round(rice, 3),
                    strategic_fit=round(fit, 3),
                    score=round(rice * (1 + 0.5 * fit), 3),
                    potential_value=to_decimal(opp.potential_value),
                )
            )
        ranking.sort(key=lambda r: (-r.score, r.title))
        for index, item in enumerate(ranking, start=1):
            item.rank = index
        top = ranking[0]
        out = RankOutput(
            ranking=ranking,
            summary=(f"{len(ranking)} opportunité(s) classée(s) ; priorité : « {top.title} » (score {top.score:g})."),
        )
        out.metrics = {"bizdev.top_opportunity_score": top.score, "bizdev.opportunities": float(len(ranking))}
        estimated = sum(1 for r in ranking if r.reach_estimated)
        if estimated:
            out.insights.append(
                Insight(
                    kind="observation",
                    title=f"Portée estimée pour {estimated} opportunité(s)",
                    detail=("Portée par défaut de 100 clients : renseignez « reach » pour fiabiliser le classement."),
                )
            )
        out.recommendations.append(
            Recommendation(
                title=f"Prioriser l'opportunité « {top.title} »",
                rationale=(
                    f"Meilleur score RICE ({top.rice:g}) × cohérence stratégique ({top.strategic_fit:.0%}) ; "
                    f"potentiel {top.potential_value}/an."
                ),
                impact=clamp(3 + 2 * top.impact, 0, 10),
                effort=clamp(top.effort, 0.5, 10),
                confidence=top.confidence,
                urgency=5,
                risk=0.25,
                estimated_value=top.potential_value or None,
                tags=["opportunité", "priorisation"],
                evidence=[f"#{r.rank} {r.title}: {r.score:g}" for r in ranking[:4]],
                follow_up=FollowUp(
                    skill=self.name,
                    action="launch_plan",
                    payload={"name": top.title, "potential_annual_revenue": str(top.potential_value)},
                ),
            )
        )
        return out

    @action("Imaginer de nouvelles activités à partir de nos actifs (matrice d'Ansoff)")
    def generate_activities(self, ctx: SkillContext, data: ActivitiesInput) -> ActivitiesOutput:
        assets = self._assets(ctx, data.assets)
        if not assets:
            return ActivitiesOutput(
                activities=[],
                summary="Aucun actif : renseignez des « asset » (produits, compétences, technologies, audiences).",
            )
        current = data.current_markets or sorted(
            (
                {m.get("name", "") for m in ctx.store.list("market")}
                | {s.get("name", "") for s in ctx.store.list("segment")}
            )
            - {""}
        )
        known = {_norm(m) for m in current}
        new = data.new_markets or sorted(
            {str(n.get("segment")) for n in ctx.store.list("need") if n.get("segment")} - set(current)
        )
        new = [m for m in new if _norm(m) not in known] or ["un segment adjacent (à préciser)"]
        current = current or ["notre marché actuel"]
        products = [a for a in assets if a.kind in ("product", "brand")]
        capabilities = [a for a in assets if a.kind in ("skill", "technology", "audience", "channel")]
        limit = data.per_quadrant
        activities: list[Activity] = []

        def add(quadrant: str, title: str, rationale: str, risk: int, used: list[str]) -> None:
            if sum(1 for a in activities if a.quadrant == quadrant) < limit:
                activities.append(
                    Activity(
                        quadrant=quadrant,
                        title=title,
                        rationale=rationale,
                        risk_level=risk,
                        assets=used,
                    )
                )

        for product in products:
            for market in current:
                add(
                    "pénétration",
                    f"Accroître la part de « {product.name} » sur {market}",
                    "Produit et marché connus : fidélisation, montée en gamme, recommandation.",
                    1,
                    [product.name],
                )
        for capability in capabilities:
            for market in current:
                add(
                    "développement produit",
                    f"Nouvelle offre fondée sur « {capability.name} » pour {market}",
                    "Capitalise sur une compétence existante auprès de clients déjà connus.",
                    2,
                    [capability.name],
                )
        for product in products:
            for market in new:
                add(
                    "développement marché",
                    f"Porter « {product.name} » vers {market}",
                    "Produit éprouvé, nouveau marché : valider l'adéquation avant d'investir.",
                    3,
                    [product.name],
                )
        for capability in capabilities:
            for market in new:
                add(
                    "diversification",
                    f"Créer une activité « {capability.name} » pour {market}",
                    "Nouveau produit et nouveau marché : risque maximal, à réserver aux opportunités fortes.",
                    4,
                    [capability.name],
                )
        counts = {
            q: sum(1 for a in activities if a.quadrant == q)
            for q in ("pénétration", "développement produit", "développement marché", "diversification")
        }
        out = ActivitiesOutput(
            activities=activities,
            summary=(
                f"{len(activities)} piste(s) d'activité : "
                + ", ".join(f"{n} {q}" for q, n in counts.items() if n)
                + "."
            ),
        )
        safest = next((a for a in activities if a.risk_level <= 2), None)
        if safest:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title=f"Piste d'activité à faible risque : {safest.title}",
                    detail=safest.rationale,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Étudier la piste « {safest.title} »",
                    rationale=(
                        f"Quadrant « {safest.quadrant} » (risque {safest.risk_level}/4) : s'appuie sur nos actifs."
                    ),
                    impact=5,
                    effort=4,
                    confidence=0.45,
                    urgency=3,
                    risk=0.1 * safest.risk_level,
                    tags=["nouvelle activité", safest.quadrant],
                )
            )
        return out

    @action(
        "Revue de portefeuille : développer, valider le marché, maintenir, fusionner, geler, archiver", risk="write"
    )
    def portfolio_review(self, ctx: SkillContext, data: PortfolioInput) -> PortfolioOutput:
        raw = ctx.store.list("project")
        if not raw:
            return PortfolioOutput(
                decisions=[],
                focus=[],
                freed_weeks=0.0,
                divergences=[],
                summary="Aucun projet : lancez « sovereign ecosystem scan » ou importez des projets.",
            )
        out = review_portfolio(raw, data.focus_limit, ctx.clock.now().date())
        counts: dict[str, int] = {}
        for decision in out.decisions:
            counts[decision.verdict] = counts.get(decision.verdict, 0) + 1
        active = sum(
            1 for r in raw if isinstance(r.get("repo"), dict) and (r["repo"].get("days_since_last_commit") or 999) <= 14
        )
        out.summary = (
            f"{len(out.decisions)} projet(s) : "
            + ", ".join(f"{n} {v}" for v, n in counts.items())
            + (f". Focus : {', '.join(out.focus)}." if out.focus else ".")
            + f" Effort évité : {out.freed_weeks:g} semaines."
            + (f" {len(out.divergences)} divergence(s) avec les analystes IA." if out.divergences else "")
        )
        out.metrics = {
            "portfolio.projects": float(len(out.decisions)),
            "portfolio.active_projects": float(active),
            "portfolio.focus": float(len(out.focus)),
            "portfolio.freed_weeks": out.freed_weeks,
            "portfolio.divergences": float(len(out.divergences)),
        }
        if active > data.focus_limit:
            out.insights.append(
                Insight(
                    kind="weakness",
                    severity="warning",
                    metric="portfolio.active_projects",
                    value=float(active),
                    title=f"Dispersion : {active} projets actifs sur 14 jours pour {data.focus_limit} prioritaires",
                    detail="Chaque projet actif hors focus retarde le premier revenu des projets prioritaires.",
                )
            )
        if out.focus:
            out.recommendations.append(
                Recommendation(
                    title="Concentrer l'effort sur " + " et ".join(f"« {n} »" for n in out.focus),
                    rationale="Meilleurs rendements de l'effort du portefeuille ; tout le reste attend.",
                    impact=8,
                    effort=2,
                    # Confiance = celle des évaluations elles-mêmes, jamais une valeur inventée.
                    confidence=round(mean(d.confidence or 0.5 for d in out.decisions if d.verdict == "développer"), 2),
                    urgency=7,
                    risk=0.1,
                    tags=["portefeuille", "stratégie"],
                    evidence=[f"{d.name} : {', '.join(d.reasons)}" for d in out.decisions if d.verdict == "développer"],
                    expected_metric=TargetMetric(
                        metric="portfolio.active_projects", direction="decrease", target=float(data.focus_limit)
                    ),
                )
            )
        stopped = [d for d in out.decisions if d.verdict in ("geler", "archiver")]
        if stopped:
            out.recommendations.append(
                Recommendation(
                    title=f"Geler ou archiver {len(stopped)} projet(s) : " + ", ".join(d.name for d in stopped[:6]),
                    rationale=f"Doublons ou potentiel insuffisant ; libère {out.freed_weeks:g} semaines d'effort.",
                    impact=6,
                    effort=1,
                    confidence=0.7,
                    urgency=6,
                    risk=0.1,
                    tags=["portefeuille", "focus"],
                    evidence=[f"{d.name} : {d.reasons[0]}" for d in stopped[:8]],
                )
            )
        for d in (d for d in out.decisions if d.verdict == "fusionner"):
            out.recommendations.append(
                Recommendation(
                    title=f"Fusionner « {d.name} » dans « {d.merge_into} »",
                    rationale="; ".join(d.reasons),
                    impact=5,
                    effort=4,
                    confidence=0.6,
                    urgency=4,
                    risk=0.2,
                    tags=["portefeuille", "fusion"],
                )
            )
        for d in (d for d in out.decisions if d.verdict == "valider le marché"):
            closing = d.days_left is not None and 0 <= d.days_left <= 45
            out.recommendations.append(
                Recommendation(
                    title=f"Tester le marché de « {d.name} » avant tout développement"
                    + (f" (avant le {d.deadline:%d/%m})" if closing and d.deadline else ""),
                    rationale=d.next_action,
                    impact=7,
                    effort=1.5,
                    confidence=d.confidence or 0.5,
                    urgency=10 if closing else 7,
                    risk=0.15,
                    external=True,
                    tags=["portefeuille", "ventes"],
                    evidence=d.reasons,
                )
            )
        for d in (d for d in out.decisions if d.blocking):
            critical = any("secrets" in b for b in d.blocking)
            out.recommendations.append(
                Recommendation(
                    title=f"Corriger « {d.name} » : {', '.join(d.blocking)}",
                    rationale="Bloquant avant toute mise en avant.",
                    impact=9 if critical else 5,
                    effort=2,
                    confidence=0.9,
                    urgency=10 if critical else 6,
                    risk=0.05,
                    tags=["sécurité" if critical else "qualité", "portefeuille"],
                )
            )
        if out.divergences:
            out.insights.append(
                Insight(
                    kind="contradiction",
                    severity="warning",
                    title=f"{len(out.divergences)} désaccord(s) SOVEREIGN / analystes IA",
                    detail=" | ".join(out.divergences[:4]),
                )
            )
        if data.apply:
            for decision in out.decisions:
                record = ctx.store.get("project", decision.project_id)
                if record is not None:
                    record.update(portfolio_status=decision.verdict, portfolio_reason="; ".join(decision.reasons))
                    ctx.store.put("project", decision.project_id, record)
        return out

    @action("Préparer un plan de lancement par phases (jalons, go/no-go, budget borné par le disponible)")
    def launch_plan(self, ctx: SkillContext, data: LaunchInput) -> LaunchOutput:
        available = to_decimal(max(Decimal("0"), ctx.finance.available()))
        if data.budget is not None:
            requested = to_decimal(data.budget)
        elif data.potential_annual_revenue:
            requested = to_decimal(data.potential_annual_revenue * Decimal("0.10"))
        else:
            requested = available
        if requested <= 0:
            raise SkillError("budget nul : indiquez un budget ou un potentiel, ou enregistrez des encaissements")
        customers = data.target_customers
        weeks = [data.validation_weeks, data.mvp_weeks, data.launch_weeks, data.growth_weeks]
        criteria = {
            "validation": [
                "≥ 15 entretiens clients qualifiés",
                "≥ 40 % confirment le problème et l'urgence",
                f"≥ {max(5, customers // 10)} pré-inscriptions ou lettres d'intention",
            ],
            "MVP": [f"≥ {max(3, customers // 20)} clients pilotes payants", "rétention à 30 jours ≥ 60 %"],
            "lancement": [
                f"≥ {max(10, customers // 4)} clients",
                "CAC ≤ LTV / 3",
                "conversion essai → payant ≥ 15 %",
            ],
            "croissance": [f"≥ {customers} clients", "churn mensuel ≤ 5 %"],
        }
        if data.potential_annual_revenue:
            criteria["croissance"].append(
                f"revenu annualisé ≥ {to_decimal(data.potential_annual_revenue * Decimal('0.5'))}"
            )
        objectives = {
            "validation": ["Confirmer le problème", "Tester la proposition de valeur et le prix"],
            "MVP": ["Livrer le minimum utile", "Mesurer l'usage réel"],
            "lancement": ["Ouvrir les ventes", "Valider un canal d'acquisition rentable"],
            "croissance": ["Augmenter l'acquisition", "Industrialiser l'onboarding"],
        }
        remaining = available
        start = 1
        phases: list[LaunchPhase] = []
        for (phase_name, share), duration in zip(PHASES, weeks, strict=True):
            budget = to_decimal(requested * _dec(share))
            funded = to_decimal(min(budget, remaining))
            remaining = to_decimal(remaining - funded)
            status: Literal["financée", "partiellement financée", "à financer par les encaissements futurs"]
            if funded >= budget:
                status = "financée"
            elif funded > 0:
                status = "partiellement financée"
            else:
                status = "à financer par les encaissements futurs"
            phases.append(
                LaunchPhase(
                    name=phase_name,
                    start_week=start,
                    end_week=start + duration - 1,
                    budget=budget,
                    funded=funded,
                    funding_status=status,
                    objectives=objectives[phase_name],
                    go_no_go=criteria[phase_name],
                )
            )
            start += duration
        funded_total = to_decimal(sum((p.funded for p in phases), Decimal("0")))
        planned_total = to_decimal(sum((p.budget for p in phases), Decimal("0")))
        gap = to_decimal(planned_total - funded_total)
        out = LaunchOutput(
            name=data.name,
            requested_budget=planned_total,
            available_budget=available,
            funded_budget=funded_total,
            funding_gap=gap,
            total_weeks=start - 1,
            phases=phases,
            summary=(
                f"Plan de lancement « {data.name} » : {start - 1} semaines, budget {planned_total} dont "
                f"{funded_total} finançables aujourd'hui" + (f" (écart {gap})." if gap > 0 else ".")
            ),
        )
        out.metrics = {"bizdev.launch_budget": float(planned_total), "bizdev.launch_funding_gap": float(gap)}
        if gap > 0:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Plan partiellement financé",
                    detail=(
                        f"{gap} restent à financer par les encaissements futurs : chaque phase ne "
                        "démarre que si "
                        "l'enveloppe de 10 % le permet."
                    ),
                    value=float(gap),
                )
            )
        first = phases[0]
        if first.funded > 0:
            out.recommendations.append(
                Recommendation(
                    title=f"Démarrer la phase de validation de « {data.name} »",
                    rationale=(
                        f"{first.end_week} semaine(s), {first.funded} engagés au maximum ; "
                        "critères de passage : " + "; ".join(first.go_no_go) + "."
                    ),
                    impact=6,
                    effort=3,
                    confidence=0.55,
                    urgency=5,
                    risk=0.2,
                    estimated_cost=first.funded,
                    estimated_value=to_decimal(data.potential_annual_revenue)
                    if data.potential_annual_revenue
                    else None,
                    external=True,
                    horizon_days=7 * first.end_week,
                    tags=["lancement", "validation"],
                    follow_up=FollowUp(
                        skill="finance-budget", action="check_spend", payload={"amount": str(first.funded)}
                    ),
                )
            )
        return out


# -- Revue de portefeuille ---------------------------------------------------------------------------------
PortfolioVerdict = Literal["développer", "valider le marché", "maintenir", "fusionner", "geler", "archiver", "évaluer"]
_MATURITY = {"idea": 0, "prototype": 1, "mvp": 2, "product": 3}
_AI_FAMILY = {
    "develop": {"développer", "maintenir", "valider le marché"},
    "merge": {"fusionner"},
    "freeze": {"geler", "valider le marché", "maintenir"},
    "archive": {"archiver", "geler"},
}


class PortfolioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus_limit: int = Field(2, ge=1, le=10, description="Nombre de projets développés en parallèle (encours)")
    apply: bool = Field(False, description="Enregistrer le statut décidé dans chaque projet")


class PortfolioDecision(BaseModel):
    project_id: str
    name: str
    verdict: PortfolioVerdict
    reasons: list[str]
    next_action: str
    commercial_potential: float | None
    effort_weeks: float | None
    return_on_effort: float | None
    confidence: float | None = None
    health: float | None
    blocking: list[str]
    merge_into: str | None = None
    deadline: date | None = Field(None, description="Échéance : fenêtre commerciale, expiration de règles…")
    days_left: int | None = None
    ai_verdict: str | None = None
    agrees_with_ai: bool | None = None


class PortfolioOutput(SkillOutput):
    decisions: list[PortfolioDecision]
    focus: list[str]
    freed_weeks: float
    divergences: list[str]


def _health(kpis: list[dict[str, object]]) -> float | None:
    total = weight_sum = 0.0
    for kpi in kpis:
        try:
            value, target = float(kpi["value"]), float(kpi["target"])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            continue
        weight = float(kpi.get("weight", 1) or 1)  # type: ignore[arg-type]
        if kpi.get("direction") == "lower":
            attained = target / value if value > 0 else 1.0
        else:
            attained = value / target if target > 0 else 1.0
        total += weight * clamp(attained, 0.0, 1.0)
        weight_sum += weight
    return round(total / weight_sum, 3) if weight_sum else None


class _Project:
    def __init__(self, raw: dict[str, object]) -> None:
        self.raw = raw
        self.id = str(raw["id"])
        self.name = str(raw.get("name") or raw["id"])
        repo = raw.get("repo") if isinstance(raw.get("repo"), dict) else {}
        self.repo: dict[str, object] = repo  # type: ignore[assignment]
        self.repo_name = str(self.repo.get("name") or self.id.removeprefix("repo-"))
        assessment = raw.get("assessment") if isinstance(raw.get("assessment"), dict) else {}
        self.assessment: dict[str, object] = assessment  # type: ignore[assignment]
        potential = self.assessment.get("commercial_potential")
        self.potential = float(potential) if potential is not None else None  # type: ignore[arg-type]
        effort = self.assessment.get("effort_to_mvp_weeks")
        self.effort = float(effort) if effort is not None else None  # type: ignore[arg-type]
        self.confidence = float(self.assessment.get("confidence", 0.5) or 0.5)  # type: ignore[arg-type]
        self.maturity = _MATURITY.get(str(self.assessment.get("maturity", "")), 1)
        self.health = _health(raw.get("kpis") or [])  # type: ignore[arg-type]
        self.loc = int(self.repo.get("loc", 0) or 0)  # type: ignore[call-overload]
        self.secrets = list(self.repo.get("secret_findings") or [])  # type: ignore[call-overload]
        self.tests_failed = int(self.repo.get("tests_failed") or 0)  # type: ignore[call-overload]
        days = self.repo.get("days_since_last_commit")
        self.inactive_days = int(days) if days is not None else None  # type: ignore[call-overload]
        self.complementary = str(
            self.assessment.get("complementary_to_sovereign") or self.assessment.get("complementary") or ""
        ).strip()
        self.internal = str(self.assessment.get("role", "")).lower() in ("internal", "interne")
        try:
            self.deadline: date | None = date.fromisoformat(str(self.assessment["deadline"]))
        except (KeyError, ValueError):
            self.deadline = None

    def rank_key(self) -> tuple[float, int, float, int]:
        return (self.potential if self.potential is not None else -1.0, self.maturity, self.health or 0.0, self.loc)

    def return_on_effort(self) -> float | None:
        if self.potential is None:
            return None
        effort = self.effort if self.effort is not None else 8.0
        return round(self.potential * (0.5 + self.confidence / 2) / (1 + effort / 8), 3)


def _strong_links(projects: list[_Project]) -> dict[str, set[str]]:
    """Recouvrements FORTS : paquet partagé / similarité ≥ 0,5, ou recouvrement constaté par un analyste."""

    def key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text.lower().split("/")[-1])

    keys = [(key(p.repo_name), p) for p in projects] + [(key(p.name), p) for p in projects]

    def resolve(name: str) -> _Project | None:
        wanted = key(name)
        exact = next((p for k, p in keys if k == wanted), None)
        if exact or len(wanted) < 4:
            return exact
        # « velyx » désigne le dépôt « velyx-prospection- » : préfixe non ambigu seulement.
        found = {p.id: p for k, p in keys if k.startswith(wanted)}
        return next(iter(found.values())) if len(found) == 1 else None

    links: dict[str, set[str]] = {p.id: set() for p in projects}
    for project in projects:
        for item in project.raw.get("overlaps") or []:  # type: ignore[attr-defined]
            other = resolve(str(item.get("repo", "")))
            if other and other.id != project.id and float(item.get("score", 0)) >= 0.5:
                links[project.id].add(other.id)
                links[other.id].add(project.id)
        for name in project.assessment.get("overlaps_with") or []:  # type: ignore[attr-defined]
            other = resolve(str(name))
            if other and other.id != project.id:
                links[project.id].add(other.id)
                links[other.id].add(project.id)
    return links


def review_portfolio(raw_projects: list[dict[str, object]], focus_limit: int, today: date) -> PortfolioOutput:
    projects = [_Project(r) for r in raw_projects]
    by_id = {p.id: p for p in projects}
    links = _strong_links(projects)
    decisions: dict[str, PortfolioDecision] = {}

    def decide(
        p: _Project, verdict: PortfolioVerdict, reasons: list[str], action: str, merge_into: str | None = None
    ) -> None:
        blocking = [f"secrets exposés ({len(p.secrets)})"] if p.secrets else []
        if p.tests_failed:
            blocking.append(f"{p.tests_failed} test(s) en échec")
        days_left = (p.deadline - today).days if p.deadline else None
        if p.deadline is not None and days_left is not None and verdict not in ("geler", "archiver"):
            if days_left >= 0:
                reasons = [*reasons, f"échéance le {p.deadline:%d/%m/%Y} ({days_left} j)"]
                action = f"{action} Échéance : {p.deadline:%d/%m/%Y}."
            else:
                reasons = [*reasons, f"échéance dépassée depuis le {p.deadline:%d/%m/%Y}"]
        decisions[p.id] = PortfolioDecision(
            project_id=p.id,
            name=p.name,
            verdict=verdict,
            reasons=reasons,
            next_action=action,
            commercial_potential=p.potential,
            effort_weeks=p.effort,
            return_on_effort=p.return_on_effort(),
            confidence=p.confidence if p.potential is not None else None,
            health=p.health,
            blocking=blocking,
            merge_into=merge_into,
            deadline=p.deadline,
            days_left=days_left,
        )

    def best_peer(p: _Project) -> _Project | None:
        """Pair évalué qui domine ``p`` ; un projet non évalué n'est jamais comparé (il est d'abord à évaluer)."""

        if p.potential is None or p.internal:
            return None
        better = [
            q
            for q in (by_id[i] for i in links[p.id])
            # Un outil interne absorbe les doublons sans cas commercial propre (potentiel < 4).
            if (q.internal and p.potential < 4) or (q.potential is not None and q.rank_key() > p.rank_key())
        ]
        return max(better, key=lambda q: (q.internal, q.rank_key())) if better else None

    # 0. Outils internes : jugés sur l'usage réel, jamais mis en concurrence commerciale ni gelés comme doublons.
    for p in projects:
        if p.internal:
            decide(
                p,
                "maintenir",
                ["outil interne : jugé sur l'usage réel, pas sur le potentiel commercial"],
                "Corriger les bloquants et l'utiliser sur des données réelles ; aucune nouvelle fonctionnalité sans "
                "besoin mesuré.",
            )

    # 1. Test de marché bon marché d'abord : l'information vaut plus que le code (doublon ou non).
    for p in projects:
        if p.id in decisions:
            continue
        if p.potential is not None and p.potential >= 3 and p.effort is not None and p.effort <= 3 and p.loc >= 50:
            best = best_peer(p)
            reasons = [f"potentiel {p.potential:g}/10, test bon marché ({p.effort:g} sem.)"]
            if best is not None:
                reasons.append(f"recouvre « {best.name} » : décider de la fusion après le test")
            decide(
                p,
                "valider le marché",
                reasons,
                "Aucun développement : vendre ou faire tester l'existant, décider sur la réponse du marché.",
                merge_into=best.name if best is not None else None,
            )
    # 2. Doublons : dans chaque recouvrement fort, seul le projet le plus prometteur reste candidat.
    for p in projects:
        best = best_peer(p) if p.id not in decisions else None
        if best is None:
            continue
        if best.internal:
            edge = "outil interne existant, sans cas commercial propre"
        elif (best.potential or 0) > (p.potential or 0):
            edge = "plus prometteur"
        else:
            edge = "même potentiel, plus abouti"
        if p.complementary and p.loc >= 200:
            decide(
                p,
                "fusionner",
                [f"recouvre « {best.name} » ({edge})", f"actif propre à récupérer : {p.complementary}"],
                f"Porter « {p.complementary} » dans « {best.name} », puis archiver.",
                merge_into=best.name,
            )
        else:
            decide(
                p,
                "geler",
                [f"recouvre « {best.name} » ({edge})"],
                f"Geler ; réutiliser au besoin depuis « {best.name} ».",
                merge_into=best.name,
            )
    # 3. Les autres selon le potentiel, l'effort et la limite d'encours.
    remaining = [p for p in projects if p.id not in decisions]
    for p in remaining:
        if p.potential is None:
            verdict: PortfolioVerdict = "évaluer"
            why = ["aucune évaluation commerciale"] + (["pas de code"] if p.loc < 50 else [])
            decide(p, verdict, why, "Évaluer le potentiel commercial (acheteur, prix, concurrents) avant d'investir.")
    candidates = sorted(
        (p for p in remaining if p.id not in decisions and p.potential is not None and p.potential >= 4),
        key=lambda p: -(p.return_on_effort() or 0),
    )
    for index, p in enumerate(candidates):
        roe = p.return_on_effort()
        if index < focus_limit:
            decide(
                p,
                "développer",
                [
                    f"potentiel {p.potential:g}/10, rendement de l'effort {roe}",
                    f"dans les {focus_limit} meilleurs paris du portefeuille",
                ],
                "Livrer le MVP vendable et obtenir un premier client payant.",
            )
        else:
            decide(
                p,
                "maintenir",
                [f"potentiel {p.potential:g}/10 mais hors limite d'encours ({focus_limit})"],
                "Ne rien développer ; reprendre quand un projet prioritaire est livré ou abandonné.",
            )
    for p in remaining:
        if p.id in decisions or p.potential is None:
            continue
        if p.potential <= 1.5:
            decide(p, "archiver", [f"potentiel {p.potential:g}/10"], "Archiver ; capitaliser les leçons.")
        else:
            decide(
                p,
                "geler",
                [f"potentiel {p.potential:g}/10 pour {p.effort or '?'} sem. d'effort"],
                "Geler en l'état ; ne plus y consacrer de temps sans signal de marché.",
            )
    # 4. Confrontation avec l'avis des analystes IA.
    divergences: list[str] = []
    for p in projects:
        decision = decisions[p.id]
        ai = str(p.assessment.get("verdict") or "") or None
        decision.ai_verdict = ai
        if ai in _AI_FAMILY:
            decision.agrees_with_ai = decision.verdict in _AI_FAMILY[ai]
            if not decision.agrees_with_ai:
                divergences.append(
                    f"{p.name} : SOVEREIGN « {decision.verdict} », analyste IA « {ai} »"
                    f" ({p.assessment.get('verdict_reason', '')})"
                )
    ordered = sorted(
        decisions.values(),
        key=lambda d: (
            ["développer", "valider le marché", "maintenir", "évaluer", "fusionner", "geler", "archiver"].index(
                d.verdict
            ),
            -(d.return_on_effort or 0),
        ),
    )
    focus = [d.name for d in ordered if d.verdict == "développer"]
    freed = sum(d.effort_weeks or 0 for d in ordered if d.verdict in ("geler", "archiver"))
    return PortfolioOutput(decisions=ordered, focus=focus, freed_weeks=round(freed, 1), divergences=divergences)


SKILL = BusinessDevelopmentSkill
