"""SKILL 2 — MARKETING STRATÉGIQUE.

Identifie et classe les publics cibles, conçoit des campagnes dont le budget est borné par l'enveloppe
autorisée, mesure leur efficacité (CTR, CVR, CPA, ROAS, ROI, tests A/B), analyse les tendances et construit
une stratégie marketing (positionnement, mix de canaux, messages, plan 30/60/90 jours).
"""

from __future__ import annotations

import math
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sovereign.core.money import to_decimal
from sovereign.core.stats import clamp, linear_fit, mean, median, two_proportion_z_test
from sovereign.skills.base import (
    EmptyInput,
    FollowUp,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillError,
    SkillOutput,
    action,
)

Objective = Literal["leads", "sales", "awareness"]
TrendClass = Literal["rising", "stable", "declining", "insufficient_data"]
Verdict = Literal["scale", "keep", "pause", "insufficient_data"]

# Hypothèses de départ lorsqu'aucune campagne n'a encore été mesurée (à valider par l'expérience).
DEFAULT_CHANNEL_MIX: list[tuple[str, float]] = [
    ("contenu / SEO", 0.35),
    ("email / CRM", 0.25),
    ("réseaux sociaux", 0.25),
    ("partenariats", 0.15),
]


def _norm(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _minmax(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    if high == low:
        return [1.0 for _ in values]
    return [(v - low) / (high - low) for v in values]


def _dec(value: float) -> Decimal:
    return to_decimal(Decimal(str(value)))


# -- Données de l'espace de travail --------------------------------------------------------------
class Segment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    size: int = Field(ge=0)
    pain: float = Field(ge=1, le=10)
    willingness_to_pay: Decimal = Field(ge=0, description="Par client et par an")
    reachability: float = Field(ge=0, le=1)
    competition: float = Field(ge=0, le=1)

    @property
    def key(self) -> str:
        return self.id or _norm(self.name)


class Campaign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    channel: str
    segment_id: str | None = None
    spend: Decimal = Field(ge=0)
    impressions: int = Field(0, ge=0)
    clicks: int = Field(0, ge=0)
    conversions: int = Field(0, ge=0)
    revenue: Decimal = Field(Decimal("0"), ge=0)
    start: date | None = None
    end: date | None = None
    variant_of: str | None = None
    status: Literal["active", "paused", "done"] | None = None

    @property
    def key(self) -> str:
        return self.id or _norm(self.name)


class TrendPoint(BaseModel):
    period: str
    value: float


class TrendSeries(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    topic: str
    points: list[TrendPoint] = Field(default_factory=list)


# -- Segmentation ---------------------------------------------------------------------------------
class SegmentsInput(BaseModel):
    segments: list[Segment] = Field(default_factory=list, description="Vide ⇒ segments du store")


class SegmentScore(BaseModel):
    id: str
    name: str
    rank: int
    attractiveness: float
    potential_revenue: Decimal
    components: dict[str, float]


class SegmentsOutput(SkillOutput):
    segments: list[SegmentScore]
    icp: str | None


# -- Plan de campagne -----------------------------------------------------------------------------
class ChannelAssumption(BaseModel):
    name: str
    cpc: Decimal = Field(gt=0, description="Coût par clic attendu")
    cvr: float = Field(gt=0, le=1, description="Taux de conversion clic → objectif")
    cpm: Decimal | None = Field(None, gt=0, description="Coût pour mille impressions")
    average_order_value: Decimal | None = Field(None, ge=0)


class CampaignPlanInput(BaseModel):
    objective: Objective = "leads"
    budget: Decimal = Field(gt=0, description="Budget souhaité (sera borné par le disponible)")
    channels: list[ChannelAssumption] = Field(min_length=1)
    duration_days: int = Field(30, ge=1, le=365)
    segment_id: str | None = None


class Allocation(BaseModel):
    channel: str
    budget: Decimal
    share: float
    expected_impressions: float
    expected_clicks: float
    expected_conversions: float
    expected_revenue: Decimal


class CampaignPlanOutput(SkillOutput):
    objective: Objective
    requested_budget: Decimal
    available_budget: Decimal
    planned_budget: Decimal
    capped: bool
    allocations: list[Allocation]
    expected_conversions: float
    expected_revenue: Decimal
    expected_roas: float | None
    kpi_targets: dict[str, float]


# -- Mesure ----------------------------------------------------------------------------------------
class CampaignResultsInput(BaseModel):
    campaigns: list[Campaign] = Field(default_factory=list, description="Vide ⇒ campagnes du store")


class CampaignMetrics(BaseModel):
    id: str
    name: str
    channel: str
    spend: Decimal
    revenue: Decimal
    conversions: int
    ctr: float | None
    cvr: float | None
    cpa: Decimal | None
    roas: float | None
    roi: float | None
    verdict: Verdict


class ABTest(BaseModel):
    control: str
    variant: str
    control_rate: float
    variant_rate: float
    relative_lift: float | None
    p_value: float
    significant: bool
    winner: str | None


class CampaignResultsOutput(SkillOutput):
    campaigns: list[CampaignMetrics]
    ab_tests: list[ABTest]
    total_spend: Decimal
    total_revenue: Decimal
    overall_roas: float | None
    overall_cac: Decimal | None


# -- Tendances --------------------------------------------------------------------------------------
class TrendInput(BaseModel):
    series: list[TrendSeries] = Field(default_factory=list, description="Vide ⇒ séries « trend_series » du store")
    threshold: float = Field(0.05, gt=0, le=1, description="Pente relative minimale par période")


class TrendResult(BaseModel):
    id: str
    topic: str
    points_count: int
    last_value: float | None
    slope: float | None
    relative_slope: float | None
    growth: float | None
    acceleration: float | None
    classification: TrendClass


class TrendOutput(SkillOutput):
    trends: list[TrendResult]


# -- Stratégie ----------------------------------------------------------------------------------------
class StrategyInput(BaseModel):
    offer_name: str | None = None
    objective: str = "croissance"
    segments: list[Segment] = Field(default_factory=list)
    campaigns: list[Campaign] = Field(default_factory=list)


class ChannelShare(BaseModel):
    channel: str
    share: float
    roas: float | None
    basis: Literal["observé", "hypothèse"]


class PlanPhase(BaseModel):
    window: str
    focus: str
    actions: list[str]
    kpi: str


class StrategyOutput(SkillOutput):
    priority_segment: str
    positioning: str
    channel_mix: list[ChannelShare]
    messages: list[str]
    plan: list[PlanPhase]
    kpis: list[str]


class AssessOutput(SkillOutput):
    segments: int
    campaigns: int
    trend_series: int


def _merge(target: SkillOutput, part: SkillOutput) -> None:
    target.insights.extend(part.insights)
    target.recommendations.extend(part.recommendations)
    target.metrics.update(part.metrics)


class MarketingStrategySkill(Skill):
    name = "marketing-strategy"
    version = "1.0.0"

    @staticmethod
    def _segments(ctx: SkillContext, given: list[Segment]) -> list[Segment]:
        return given or [Segment.model_validate(s) for s in ctx.store.list("segment")]

    @staticmethod
    def _campaigns(ctx: SkillContext, given: list[Campaign]) -> list[Campaign]:
        return given or [Campaign.model_validate(c) for c in ctx.store.list("campaign")]

    @staticmethod
    def _series(ctx: SkillContext, given: list[TrendSeries]) -> list[TrendSeries]:
        return given or [TrendSeries.model_validate(t) for t in ctx.store.list("trend_series")]

    @action("Synthèse marketing de l'espace de travail : cibles, campagnes, tendances (pour le directeur)")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        segments = self._segments(ctx, [])
        campaigns = self._campaigns(ctx, [])
        series = self._series(ctx, [])
        out = AssessOutput(segments=len(segments), campaigns=len(campaigns), trend_series=len(series))
        if not (segments or campaigns or series):
            out.summary = (
                "Aucune donnée marketing : importez des « segment » (taille, douleur, disposition à payer, "
                "accessibilité, concurrence), des « campaign » (dépense, impressions, clics, conversions, "
                "revenu) "
                "et des « trend_series » pour activer l'analyse."
            )
            return out
        parts: list[str] = []
        if segments:
            seg = self.segment_audiences(ctx, SegmentsInput(segments=segments))
            _merge(out, seg)
            parts.append(seg.summary)
        if campaigns:
            res = self.measure_campaigns(ctx, CampaignResultsInput(campaigns=campaigns))
            _merge(out, res)
            parts.append(res.summary)
        if series:
            trends = self.analyze_trends(ctx, TrendInput(series=series))
            _merge(out, trends)
            parts.append(trends.summary)
        out.summary = " ".join(parts)
        return out

    @action("Identifier et classer les publics cibles (attractivité multicritère, ICP)")
    def segment_audiences(self, ctx: SkillContext, data: SegmentsInput) -> SegmentsOutput:
        segments = self._segments(ctx, data.segments)
        if not segments:
            return SegmentsOutput(
                segments=[], icp=None, summary="Aucun segment : renseignez des enregistrements « segment »."
            )
        size_n = _minmax([float(s.size) for s in segments])
        wtp_n = _minmax([float(s.willingness_to_pay) for s in segments])
        scored: list[tuple[Segment, float, dict[str, float]]] = []
        for seg, size_score, wtp_score in zip(segments, size_n, wtp_n, strict=True):
            components = {
                "taille": round(size_score * 0.25, 4),
                "douleur": round(seg.pain / 10 * 0.25, 4),
                "disposition_a_payer": round(wtp_score * 0.25, 4),
                "accessibilite": round(seg.reachability * 0.15, 4),
                "faible_concurrence": round((1 - seg.competition) * 0.10, 4),
            }
            scored.append((seg, round(sum(components.values()), 4), components))
        scored.sort(key=lambda item: (-item[1], item[0].name))
        results = [
            SegmentScore(
                id=seg.key,
                name=seg.name,
                rank=index + 1,
                attractiveness=score,
                components=components,
                potential_revenue=to_decimal(
                    Decimal(seg.size) * seg.willingness_to_pay * Decimal(str(seg.reachability))
                ),
            )
            for index, (seg, score, components) in enumerate(scored)
        ]
        icp = results[0]
        out = SegmentsOutput(
            segments=results,
            icp=icp.name,
            summary=(
                f"{len(results)} segment(s) classé(s) ; cible prioritaire (ICP) : {icp.name} "
                f"(attractivité {icp.attractiveness:.2f}, potentiel {icp.potential_revenue}/an)."
            ),
        )
        out.metrics = {"marketing.icp_attractiveness": icp.attractiveness}
        out.insights.append(
            Insight(
                kind="strength",
                title=f"Cible prioritaire : {icp.name}",
                detail=", ".join(f"{k} {v:.2f}" for k, v in icp.components.items()),
                value=icp.attractiveness,
            )
        )
        runner_up = results[1].attractiveness if len(results) > 1 else 0.0
        out.recommendations.append(
            Recommendation(
                title=f"Concentrer l'acquisition sur le segment « {icp.name} »",
                rationale=(
                    f"Meilleure attractivité ({icp.attractiveness:.2f}"
                    + (f" contre {runner_up:.2f} pour le suivant" if len(results) > 1 else "")
                    + f") et potentiel de {icp.potential_revenue}/an."
                ),
                impact=clamp(4 + 6 * icp.attractiveness, 0, 10),
                effort=3,
                confidence=clamp(0.5 + (icp.attractiveness - runner_up), 0.3, 0.9),
                urgency=5,
                risk=0.15,
                estimated_value=icp.potential_revenue,
                tags=["marketing", "ciblage"],
                evidence=[f"{r.name}: {r.attractiveness:.2f}" for r in results[:4]],
                follow_up=FollowUp(skill=self.name, action="design_strategy", payload={}),
            )
        )
        for seg, score, _ in scored:
            if seg.competition >= 0.8 and seg.reachability <= 0.3:
                out.insights.append(
                    Insight(
                        kind="weakness",
                        title=f"Segment difficile : {seg.name}",
                        detail=f"Concurrence {seg.competition:.0%} et accessibilité {seg.reachability:.0%}.",
                        value=score,
                    )
                )
        return out

    @action("Concevoir une campagne : allocation par canal bornée par le budget autorisé, prévisions, KPI")
    def plan_campaign(self, ctx: SkillContext, data: CampaignPlanInput) -> CampaignPlanOutput:
        channels = data.channels
        if data.objective == "sales" and not any(c.average_order_value for c in channels):
            raise SkillError("objectif « sales » : indiquez au moins un panier moyen (average_order_value)")
        efficiencies: list[float] = []
        for ch in channels:
            per_euro = ch.cvr / float(ch.cpc)
            if data.objective == "sales":
                efficiencies.append(per_euro * float(ch.average_order_value or 0))
            elif data.objective == "awareness":
                efficiencies.append(1000 / float(ch.cpm) if ch.cpm else 100 / float(ch.cpc))
            else:
                efficiencies.append(per_euro)
        if sum(efficiencies) <= 0:
            raise SkillError("aucun canal n'a d'efficacité positive pour cet objectif")
        available = ctx.finance.available()
        planned = to_decimal(max(Decimal("0"), min(data.budget, available)))
        capped = planned < data.budget
        # Modèle de réponse concave r_i(b) = e_i·√(b·b₀) : l'allocation optimale (Lagrange) est b_i ∝ e_i².
        weights = [e * e for e in efficiencies]
        total_weight = sum(weights)
        b0 = float(planned) / len(channels) if planned > 0 else 0.0
        allocations: list[Allocation] = []
        for ch, weight in zip(channels, weights, strict=True):
            share = weight / total_weight
            budget = _dec(float(planned) * share)
            effective = math.sqrt(float(budget) * b0) if b0 > 0 else 0.0
            conversions = ch.cvr / float(ch.cpc) * effective
            clicks = conversions / ch.cvr
            impressions = (1000 / float(ch.cpm) * effective) if ch.cpm else clicks * 100
            revenue = _dec(conversions * float(ch.average_order_value or 0))
            allocations.append(
                Allocation(
                    channel=ch.name,
                    budget=budget,
                    share=round(share, 4),
                    expected_impressions=round(impressions, 1),
                    expected_clicks=round(clicks, 1),
                    expected_conversions=round(conversions, 2),
                    expected_revenue=revenue,
                )
            )
        conversions_total = round(sum(a.expected_conversions for a in allocations), 2)
        revenue_total = to_decimal(sum((a.expected_revenue for a in allocations), Decimal("0")))
        roas = round(float(revenue_total / planned), 3) if planned > 0 and revenue_total > 0 else None
        kpis: dict[str, float] = {"conversions": conversions_total}
        if conversions_total > 0:
            kpis["cpa_max"] = round(float(planned) / conversions_total, 2)
        if roas is not None:
            kpis["roas_min"] = roas
        if data.objective == "awareness":
            kpis["impressions"] = round(sum(a.expected_impressions for a in allocations), 0)
        out = CampaignPlanOutput(
            objective=data.objective,
            requested_budget=to_decimal(data.budget),
            available_budget=available,
            planned_budget=planned,
            capped=capped,
            allocations=allocations,
            expected_conversions=conversions_total,
            expected_revenue=revenue_total,
            expected_roas=roas,
            kpi_targets=kpis,
        )
        if planned <= 0:
            out.summary = (
                "Campagne non finançable : aucun budget disponible (10 % des encaissements). "
                "Aucune dépense ne sera engagée."
            )
            out.insights.append(
                Insight(kind="risk", severity="critical", title="Aucun budget disponible", detail=out.summary)
            )
            return out
        out.summary = (
            f"Campagne « {data.objective} » : {planned} répartis sur {len(allocations)} canal(aux), "
            f"{conversions_total:g} conversion(s) attendue(s)"
            + (f", ROAS prévu {roas:.2f}" if roas is not None else "")
            + "."
        )
        if capped:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Budget réduit au disponible",
                    detail=f"{data.budget} demandés, {available} disponibles dans l'enveloppe de 10 %.",
                )
            )
        out.metrics = {
            "marketing.plan_budget": float(planned),
            "marketing.plan_conversions": conversions_total,
        }
        if roas is not None:
            out.metrics["marketing.plan_expected_roas"] = roas
        out.recommendations.append(
            Recommendation(
                title=f"Valider puis lancer la campagne « {data.objective} » ({planned})",
                rationale=(
                    "Allocation optimisée par canal sous rendements décroissants ; dépense à soumettre au "
                    "garde-fou financier, paiement manuel (aucun accès bancaire)."
                ),
                impact=clamp(3 + 2 * (roas or 1.0), 0, 10),
                effort=4,
                confidence=0.45,
                urgency=4,
                risk=0.3 if roas is None or roas < 1 else 0.2,
                estimated_value=revenue_total if revenue_total else None,
                estimated_cost=planned,
                external=True,
                horizon_days=data.duration_days,
                tags=["marketing", "campagne"],
                evidence=[f"{a.channel}: {a.budget} ({a.share:.0%})" for a in allocations],
                follow_up=FollowUp(skill="finance-budget", action="check_spend", payload={"amount": str(planned)}),
            )
        )
        return out

    @action("Mesurer l'efficacité des campagnes : CTR, CVR, CPA, ROAS, ROI, tests A/B")
    def measure_campaigns(self, ctx: SkillContext, data: CampaignResultsInput) -> CampaignResultsOutput:
        campaigns = self._campaigns(ctx, data.campaigns)
        if not campaigns:
            return CampaignResultsOutput(
                campaigns=[],
                ab_tests=[],
                total_spend=Decimal("0.00"),
                total_revenue=Decimal("0.00"),
                overall_roas=None,
                overall_cac=None,
                summary="Aucune campagne mesurée : renseignez des enregistrements « campaign ».",
            )
        roas_values = [float(c.revenue / c.spend) for c in campaigns if c.spend > 0]
        reference = median(roas_values) if roas_values else 0.0
        metrics: list[CampaignMetrics] = []
        for camp in campaigns:
            ctr = camp.clicks / camp.impressions if camp.impressions else None
            cvr = camp.conversions / camp.clicks if camp.clicks else None
            cpa = to_decimal(camp.spend / camp.conversions) if camp.conversions else None
            roas = float(camp.revenue / camp.spend) if camp.spend > 0 else None
            roi = float((camp.revenue - camp.spend) / camp.spend) if camp.spend > 0 else None
            verdict: Verdict
            if roas is None:
                verdict = "insufficient_data"
            elif roas < 0.5 or (roas < 1 and roas < 0.7 * reference):
                verdict = "pause"
            elif len(roas_values) >= 2 and roas > 1 and roas >= 1.3 * reference:
                verdict = "scale"
            else:
                verdict = "keep"
            metrics.append(
                CampaignMetrics(
                    id=camp.key,
                    name=camp.name,
                    channel=camp.channel,
                    spend=to_decimal(camp.spend),
                    revenue=to_decimal(camp.revenue),
                    conversions=camp.conversions,
                    ctr=round(ctr, 5) if ctr is not None else None,
                    cvr=round(cvr, 5) if cvr is not None else None,
                    cpa=cpa,
                    roas=round(roas, 4) if roas is not None else None,
                    roi=round(roi, 4) if roi is not None else None,
                    verdict=verdict,
                )
            )
        by_key = {c.key: c for c in campaigns}
        tests: list[ABTest] = []
        for variant in campaigns:
            control = by_key.get(variant.variant_of or "")
            if control is None or not control.clicks or not variant.clicks:
                continue
            result = two_proportion_z_test(control.conversions, control.clicks, variant.conversions, variant.clicks)
            winner = None
            if result.significant:
                winner = variant.name if result.effect > 0 else control.name
            tests.append(
                ABTest(
                    control=control.name,
                    variant=variant.name,
                    control_rate=round(control.conversions / control.clicks, 5),
                    variant_rate=round(variant.conversions / variant.clicks, 5),
                    relative_lift=round(result.relative_effect, 4) if result.relative_effect is not None else None,
                    p_value=round(result.p_value, 5),
                    significant=result.significant,
                    winner=winner,
                )
            )
        total_spend = to_decimal(sum((c.spend for c in campaigns), Decimal("0")))
        total_revenue = to_decimal(sum((c.revenue for c in campaigns), Decimal("0")))
        total_conversions = sum(c.conversions for c in campaigns)
        total_clicks = sum(c.clicks for c in campaigns)
        total_impressions = sum(c.impressions for c in campaigns)
        overall_roas = round(float(total_revenue / total_spend), 4) if total_spend > 0 else None
        overall_cac = to_decimal(total_spend / total_conversions) if total_conversions else None
        out = CampaignResultsOutput(
            campaigns=metrics,
            ab_tests=tests,
            total_spend=total_spend,
            total_revenue=total_revenue,
            overall_roas=overall_roas,
            overall_cac=overall_cac,
            summary=(
                f"{len(metrics)} campagne(s) : dépense {total_spend}, revenu {total_revenue}"
                + (f", ROAS global {overall_roas:.2f}" if overall_roas is not None else "")
                + (f", CAC {overall_cac}" if overall_cac is not None else "")
                + "."
            ),
        )
        if overall_roas is not None:
            out.metrics["marketing.roas"] = overall_roas
        if overall_cac is not None:
            out.metrics["marketing.cac"] = float(overall_cac)
        if total_impressions:
            out.metrics["marketing.ctr"] = round(total_clicks / total_impressions, 5)
        if overall_roas is not None and overall_roas < 1:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Le marketing détruit de la valeur",
                    detail=f"ROAS global {overall_roas:.2f} < 1 : chaque euro investi rapporte moins d'un euro.",
                    metric="marketing.roas",
                    value=overall_roas,
                )
            )
        for item in metrics:
            weight = float(item.spend / total_spend) if total_spend > 0 else 0.0
            if item.verdict == "pause":
                out.insights.append(
                    Insight(
                        kind="weakness",
                        severity="warning",
                        title=f"Campagne sous-performante : {item.name}",
                        detail=f"ROAS {item.roas:.2f} (médiane {reference:.2f}).",
                        value=item.roas,
                    )
                )
                out.recommendations.append(
                    Recommendation(
                        title=f"Mettre en pause la campagne « {item.name} »",
                        rationale=f"ROAS {item.roas:.2f} contre une médiane de {reference:.2f} ; "
                        f"{item.spend} dépensés.",
                        impact=clamp(3 + 7 * weight, 0, 10),
                        effort=1,
                        confidence=0.7,
                        urgency=7,
                        risk=0.1,
                        estimated_value=to_decimal(item.spend - item.revenue) if item.spend > item.revenue else None,
                        external=True,
                        tags=["marketing", "optimisation"],
                        evidence=[f"dépense={item.spend}", f"revenu={item.revenue}", f"ROAS={item.roas}"],
                    )
                )
            elif item.verdict == "scale":
                increase = to_decimal(item.spend * Decimal("0.2"))
                out.insights.append(
                    Insight(
                        kind="strength",
                        title=f"Campagne performante : {item.name}",
                        detail=f"ROAS {item.roas:.2f} (médiane {reference:.2f}).",
                        value=item.roas,
                    )
                )
                out.recommendations.append(
                    Recommendation(
                        title=f"Augmenter de 20 % le budget de « {item.name} »",
                        rationale=f"ROAS {item.roas:.2f}, au moins 30 % au-dessus de la médiane ({reference:.2f}).",
                        # Impact : poids de la campagne dans le mix + force du ROAS (plafonnée).
                        impact=clamp(2 + 6 * weight + min(2.0, (item.roas or 0.0) / 3), 0, 10),
                        effort=1.5,
                        confidence=0.6,
                        urgency=5,
                        risk=0.2,
                        estimated_cost=increase,
                        # Rendements décroissants : le ROAS marginal est supposé à 70 % du ROAS moyen.
                        estimated_value=to_decimal(increase * Decimal(str(item.roas)) * Decimal("0.7")),
                        external=True,
                        tags=["marketing", "croissance"],
                        evidence=[f"ROAS={item.roas}", f"CPA={item.cpa}"],
                        follow_up=FollowUp(
                            skill="finance-budget", action="check_spend", payload={"amount": str(increase)}
                        ),
                    )
                )
        for test in tests:
            if test.winner:
                out.insights.append(
                    Insight(
                        kind="observation",
                        title=f"Test A/B concluant : {test.winner} l'emporte",
                        detail=f"{test.control_rate:.2%} → {test.variant_rate:.2%} (p = {test.p_value:.4f}).",
                        value=test.p_value,
                    )
                )
                out.recommendations.append(
                    Recommendation(
                        title=f"Généraliser la version gagnante « {test.winner} »",
                        rationale=(
                            f"Écart de conversion significatif (p = {test.p_value:.4f}, effet relatif "
                            f"{(test.relative_lift or 0):+.0%})."
                        ),
                        impact=6,
                        effort=1.5,
                        confidence=clamp(1 - test.p_value, 0, 0.95),
                        urgency=5,
                        risk=0.1,
                        external=True,
                        tags=["marketing", "a/b"],
                    )
                )
        return out

    @action("Analyser les tendances : pente, croissance, accélération, classement")
    def analyze_trends(self, ctx: SkillContext, data: TrendInput) -> TrendOutput:
        series = self._series(ctx, data.series)
        results: list[TrendResult] = []
        for item in series:
            values = [p.value for p in item.points]
            key = item.id or _norm(item.topic)
            if len(values) < 3:
                results.append(
                    TrendResult(
                        id=key,
                        topic=item.topic,
                        points_count=len(values),
                        last_value=values[-1] if values else None,
                        slope=None,
                        relative_slope=None,
                        growth=None,
                        acceleration=None,
                        classification="insufficient_data",
                    )
                )
                continue
            fit = linear_fit(list(range(len(values))), values)
            avg = mean(values)
            relative = fit.slope / abs(avg) if avg else 0.0
            growth = (values[-1] - values[0]) / abs(values[0]) if values[0] else None
            acceleration = None
            if len(values) >= 4:
                half = len(values) // 2
                first = linear_fit(list(range(half)), values[:half]).slope
                second = linear_fit(list(range(len(values) - half)), values[half:]).slope
                acceleration = round(second - first, 6)
            classification: TrendClass = (
                "rising" if relative >= data.threshold else "declining" if relative <= -data.threshold else "stable"
            )
            results.append(
                TrendResult(
                    id=key,
                    topic=item.topic,
                    points_count=len(values),
                    last_value=values[-1],
                    slope=round(fit.slope, 6),
                    relative_slope=round(relative, 6),
                    growth=round(growth, 6) if growth is not None else None,
                    acceleration=acceleration,
                    classification=classification,
                )
            )
        rising = [r for r in results if r.classification == "rising"]
        declining = [r for r in results if r.classification == "declining"]
        out = TrendOutput(trends=results)
        if not results:
            out.summary = "Aucune série de tendance : renseignez des enregistrements « trend_series »."
            return out
        out.summary = f"{len(results)} tendance(s) : {len(rising)} en hausse, {len(declining)} en baisse."
        out.metrics = {
            "marketing.trends_rising": float(len(rising)),
            "marketing.trends_declining": float(len(declining)),
        }
        for trend in sorted(rising, key=lambda r: -(r.relative_slope or 0))[:3]:
            accelerating = trend.acceleration is not None and trend.acceleration > 0
            out.insights.append(
                Insight(
                    kind="trend",
                    title=f"Tendance montante : {trend.topic}",
                    detail=f"+{trend.relative_slope:.0%} de la moyenne par période"
                    + (" et en accélération" if accelerating else "")
                    + ".",
                    value=trend.relative_slope,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Exploiter la tendance « {trend.topic} » dans les contenus et campagnes",
                    rationale=f"Pente relative {trend.relative_slope:+.0%} par période sur "
                    f"{trend.points_count} points.",
                    impact=clamp(4 + 20 * (trend.relative_slope or 0), 0, 9),
                    effort=3,
                    confidence=0.6 if accelerating else 0.5,
                    urgency=7 if accelerating else 5,
                    risk=0.15,
                    tags=["marketing", "tendance"],
                )
            )
        for trend in declining:
            out.insights.append(
                Insight(
                    kind="trend",
                    severity="warning",
                    title=f"Tendance en déclin : {trend.topic}",
                    detail=f"{trend.relative_slope:+.0%} de la moyenne par période.",
                    value=trend.relative_slope,
                )
            )
        return out

    @action("Construire la stratégie marketing : positionnement, cible, mix de canaux, messages, plan 30/60/90")
    def design_strategy(self, ctx: SkillContext, data: StrategyInput) -> StrategyOutput:
        segments = self._segments(ctx, data.segments)
        if not segments:
            raise SkillError("aucun segment : renseignez des « segment » avant de construire une stratégie")
        ranking = self.segment_audiences(ctx, SegmentsInput(segments=segments))
        icp_score = ranking.segments[0]
        icp = next(s for s in segments if s.key == icp_score.id)
        campaigns = self._campaigns(ctx, data.campaigns)
        by_channel: dict[str, tuple[Decimal, Decimal]] = {}
        for camp in campaigns:
            spend, revenue = by_channel.get(camp.channel, (Decimal("0"), Decimal("0")))
            by_channel[camp.channel] = (spend + camp.spend, revenue + camp.revenue)
        observed = {ch: float(rev / sp) for ch, (sp, rev) in by_channel.items() if sp > 0 and rev > 0}
        mix: list[ChannelShare]
        if observed:
            total = sum(observed.values())
            mix = [
                ChannelShare(channel=ch, share=round(r / total, 4), roas=round(r, 3), basis="observé")
                for ch, r in sorted(observed.items(), key=lambda kv: -kv[1])
            ]
        else:
            mix = [
                ChannelShare(channel=ch, share=share, roas=None, basis="hypothèse") for ch, share in DEFAULT_CHANNEL_MIX
            ]
        icp_names = {_norm(icp.name), _norm(icp.id)}
        needs = [n for n in ctx.store.list("need") if _norm(n.get("segment")) in icp_names]
        needs.sort(key=lambda n: -(float(n.get("importance", 0)) - float(n.get("satisfaction", 0))))
        offer = data.offer_name or "Notre offre"
        if needs:
            messages = [f"« {n['statement']} » — enfin résolu pour {icp.name}." for n in needs[:3]]
            core_need = needs[0]["statement"]
        else:
            messages = [
                f"Une réponse directe à une douleur de niveau {icp.pain:g}/10 pour {icp.name}.",
                "Résultat mesurable dès le premier mois.",
                "Mise en route simple, accompagnement inclus.",
            ]
            core_need = "sa douleur principale"
        wtp_rank = sorted(segments, key=lambda s: s.willingness_to_pay).index(icp) / max(1, len(segments) - 1)
        stance = "positionnement premium" if wtp_rank >= 0.5 and len(segments) > 1 else "positionnement accessible"
        positioning = f"{offer} : la réponse à « {core_need} » pour {icp.name} ({stance})."
        top_channel = mix[0].channel
        plan = [
            PlanPhase(
                window="J+30",
                focus="Validation du message",
                actions=[
                    f"Tester 2 à 3 messages auprès de {icp.name}",
                    f"Démarrer {top_channel} à petit budget",
                    "Mettre en place le suivi des conversions",
                ],
                kpi="Taux de conversion par message",
            ),
            PlanPhase(
                window="J+60",
                focus="Optimisation",
                actions=[
                    "Couper les messages et canaux sous la médiane de ROAS",
                    "Lancer un test A/B sur la meilleure page",
                    "Activer le 2e canal du mix",
                ],
                kpi="CAC et ROAS par canal",
            ),
            PlanPhase(
                window="J+90",
                focus="Accélération",
                actions=[
                    "Augmenter le budget des canaux gagnants dans la limite de l'enveloppe",
                    "Programmes de recommandation clients",
                    "Préparer le segment suivant",
                ],
                kpi="Croissance des conversions mois sur mois",
            ),
        ]
        kpis = [
            "CAC",
            "ROAS",
            "Taux de conversion",
            "Part de revenus du segment prioritaire",
            "Churn à 90 jours",
        ]
        out = StrategyOutput(
            priority_segment=icp.name,
            positioning=positioning,
            channel_mix=mix,
            messages=messages,
            plan=plan,
            kpis=kpis,
            summary=(
                f"Stratégie « {data.objective} » centrée sur {icp.name} ; canal principal {top_channel} "
                f"({'observé' if observed else 'hypothèse à valider'})."
            ),
        )
        out.recommendations.append(
            Recommendation(
                title=f"Adopter la stratégie marketing centrée sur « {icp.name} »",
                rationale=positioning,
                impact=7,
                effort=4,
                confidence=0.6 if observed else 0.45,
                urgency=5,
                risk=0.2,
                estimated_value=icp_score.potential_revenue,
                tags=["marketing", "stratégie"],
                evidence=[f"{c.channel}: {c.share:.0%} ({c.basis})" for c in mix],
            )
        )
        if not observed:
            out.insights.append(
                Insight(
                    kind="observation",
                    title="Mix de canaux non encore validé par des mesures",
                    detail="Aucune campagne mesurée : le mix proposé est une hypothèse de départ.",
                )
            )
        return out


SKILL = MarketingStrategySkill
