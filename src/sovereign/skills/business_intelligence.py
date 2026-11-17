"""SKILL 1 — BUSINESS INTELLIGENCE.

Étudie les marchés (TAM / SAM / SOM), évalue la concurrence (indice HHI, positionnement prix/qualité,
fonctionnalités rares ou manquantes), identifie les besoins non satisfaits (score d'opportunité d'Ulwick),
détecte et chiffre les opportunités commerciales et estime le potentiel économique d'une offre.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sovereign.core.ids import stable_id
from sovereign.core.money import to_decimal
from sovereign.core.stats import clamp, median
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

Concentration = Literal["peu concentré", "modérément concentré", "très concentré"]
Quadrant = Literal["premium", "rapport qualité/prix", "low-cost", "surévalué"]
NeedClass = Literal["fortement sous-servi", "sous-servi", "servi", "sur-servi"]


def _norm(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _dec(value: float | Decimal) -> Decimal:
    return Decimal(str(value))


# -- Données de l'espace de travail --------------------------------------------------------------
class Market(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    total_customers: int = Field(ge=0)
    serviceable_ratio: float = Field(ge=0, le=1)
    obtainable_share: float = Field(ge=0, le=1)
    annual_value_per_customer: Decimal = Field(ge=0)
    growth_rate: float = Field(0.0, ge=-1, le=10)
    horizon_years: int = Field(3, ge=1, le=20)

    @property
    def key(self) -> str:
        return self.id or _norm(self.name)


class Competitor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    market_share: float = Field(ge=0, le=1)
    price: Decimal = Field(ge=0)
    quality: float = Field(ge=1, le=10)
    features: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class Need(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    statement: str
    segment: str | None = None
    importance: float = Field(ge=1, le=10)
    satisfaction: float = Field(ge=1, le=10)
    frequency: float | None = Field(None, ge=0, le=1)

    @property
    def key(self) -> str:
        return self.id or _norm(self.statement)


# -- Dimensionnement ------------------------------------------------------------------------------
class SizeMarketInput(BaseModel):
    markets: list[Market] = Field(default_factory=list, description="Vide ⇒ marchés de l'espace de travail")


class YearValue(BaseModel):
    year: int
    som: Decimal


class MarketSize(BaseModel):
    id: str
    name: str
    tam: Decimal
    sam: Decimal
    som: Decimal
    obtainable_customers: int
    growth_rate: float
    som_projection: list[YearValue]


class SizeMarketOutput(SkillOutput):
    markets: list[MarketSize]
    total_tam: Decimal
    total_sam: Decimal
    total_som: Decimal


# -- Concurrence ----------------------------------------------------------------------------------
class CompetitionInput(BaseModel):
    competitors: list[Competitor] = Field(default_factory=list, description="Vide ⇒ concurrents du store")
    our_features: list[str] = Field(default_factory=list, description="Nos fonctionnalités (détection des manques)")


class CompetitorPosition(BaseModel):
    name: str
    market_share: float
    price: Decimal
    quality: float
    quadrant: Quadrant


class CompetitionOutput(SkillOutput):
    competitors_count: int
    hhi: float
    concentration: Concentration | None
    uncovered_share: float
    intensity: float
    positions: list[CompetitorPosition]
    rare_features: list[str]
    table_stakes: list[str]
    missing_features: list[str]
    value_gap: bool


# -- Besoins ---------------------------------------------------------------------------------------
class NeedsInput(BaseModel):
    needs: list[Need] = Field(default_factory=list, description="Vide ⇒ besoins du store")


class NeedScore(BaseModel):
    id: str
    statement: str
    segment: str | None
    importance: float
    satisfaction: float
    opportunity: float
    classification: NeedClass


class NeedsOutput(SkillOutput):
    needs: list[NeedScore]
    underserved: int


# -- Opportunités ----------------------------------------------------------------------------------
class OpportunityScanInput(BaseModel):
    min_opportunity: float = Field(10.0, ge=0, le=20, description="Score d'Ulwick minimal d'un besoin")
    persist: bool = Field(True, description="Enregistrer les opportunités (kind 'opportunity')")
    needs: list[Need] = Field(default_factory=list)
    markets: list[Market] = Field(default_factory=list)
    competitors: list[Competitor] = Field(default_factory=list)


class DetectedOpportunity(BaseModel):
    id: str
    title: str
    description: str
    need_id: str
    market_id: str | None
    potential_value: Decimal
    reach: int
    confidence: float
    score: float
    status: str


class OpportunityScanOutput(SkillOutput):
    opportunities: list[DetectedOpportunity]
    persisted: int
    competition_intensity: float


# -- Potentiel -------------------------------------------------------------------------------------
class PotentialInput(BaseModel):
    name: str
    price: Decimal = Field(gt=0, description="Prix par client et par période de facturation")
    billing: Literal["monthly", "annual", "one_off"] = "monthly"
    target_customers: int = Field(ge=1, description="Clients cibles atteignables")
    conversion_rate: float = Field(gt=0, le=1)
    churn_monthly: float = Field(0.0, ge=0, le=1)
    gross_margin: float = Field(1.0, ge=0, le=1)


class Scenario(BaseModel):
    name: Literal["pessimiste", "central", "optimiste"]
    conversion_rate: float
    customers: float
    annual_revenue: Decimal


class PotentialOutput(SkillOutput):
    name: str
    customers: float
    annual_revenue_per_customer: Decimal
    annual_revenue: Decimal
    ltv: Decimal
    ltv_assumption: str
    scenarios: list[Scenario]


class AssessOutput(SkillOutput):
    markets: int
    competitors: int
    needs: int


def _merge(target: SkillOutput, part: SkillOutput) -> None:
    target.insights.extend(part.insights)
    target.recommendations.extend(part.recommendations)
    target.metrics.update(part.metrics)


class BusinessIntelligenceSkill(Skill):
    name = "business-intelligence"
    version = "1.0.0"

    # -- chargement depuis l'espace de travail --------------------------------------------------
    @staticmethod
    def _markets(ctx: SkillContext, given: list[Market]) -> list[Market]:
        return given or [Market.model_validate(m) for m in ctx.store.list("market")]

    @staticmethod
    def _competitors(ctx: SkillContext, given: list[Competitor]) -> list[Competitor]:
        return given or [Competitor.model_validate(c) for c in ctx.store.list("competitor")]

    @staticmethod
    def _needs(ctx: SkillContext, given: list[Need]) -> list[Need]:
        return given or [Need.model_validate(n) for n in ctx.store.list("need")]

    # -- actions ------------------------------------------------------------------------------
    @action("Synthèse marché / concurrence / besoins de l'espace de travail (pour le directeur)")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        markets = self._markets(ctx, [])
        competitors = self._competitors(ctx, [])
        needs = self._needs(ctx, [])
        out = AssessOutput(markets=len(markets), competitors=len(competitors), needs=len(needs))
        if not (markets or competitors or needs):
            out.summary = (
                "Aucune donnée de marché : importez des enregistrements « market » "
                "(taille, part atteignable, "
                "valeur client), « competitor » (part de marché, prix, qualité, fonctionnalités) et « need » "
                "(importance, satisfaction) pour activer l'analyse commerciale."
            )
            return out
        parts: list[str] = []
        if markets:
            sizing = self.size_market(ctx, SizeMarketInput(markets=markets))
            _merge(out, sizing)
            parts.append(sizing.summary)
        if competitors:
            competition = self.analyze_competition(ctx, CompetitionInput(competitors=competitors))
            _merge(out, competition)
            parts.append(competition.summary)
        if needs:
            unmet = self.find_unmet_needs(ctx, NeedsInput(needs=needs))
            _merge(out, unmet)
            parts.append(unmet.summary)
        out.summary = " ".join(parts)
        return out

    @action("Dimensionner les marchés : TAM, SAM, SOM et projection de croissance")
    def size_market(self, ctx: SkillContext, data: SizeMarketInput) -> SizeMarketOutput:
        markets = self._markets(ctx, data.markets)
        sizes: list[MarketSize] = []
        for market in markets:
            tam = to_decimal(Decimal(market.total_customers) * market.annual_value_per_customer)
            sam = to_decimal(tam * _dec(market.serviceable_ratio))
            som = to_decimal(sam * _dec(market.obtainable_share))
            projection = [
                YearValue(year=t, som=to_decimal(som * _dec((1 + market.growth_rate) ** t)))
                for t in range(1, market.horizon_years + 1)
            ]
            customers = int(market.total_customers * market.serviceable_ratio * market.obtainable_share)
            sizes.append(
                MarketSize(
                    id=market.key,
                    name=market.name,
                    tam=tam,
                    sam=sam,
                    som=som,
                    obtainable_customers=customers,
                    growth_rate=market.growth_rate,
                    som_projection=projection,
                )
            )
        total_tam = to_decimal(sum((s.tam for s in sizes), Decimal("0")))
        total_sam = to_decimal(sum((s.sam for s in sizes), Decimal("0")))
        total_som = to_decimal(sum((s.som for s in sizes), Decimal("0")))
        out = SizeMarketOutput(markets=sizes, total_tam=total_tam, total_sam=total_sam, total_som=total_som)
        if not sizes:
            out.summary = "Aucun marché à dimensionner : renseignez des enregistrements « market »."
            return out
        out.summary = f"{len(sizes)} marché(s) : TAM {total_tam}, SAM {total_sam}, SOM atteignable {total_som} par an."
        out.metrics = {
            "market.tam": float(total_tam),
            "market.sam": float(total_sam),
            "market.som": float(total_som),
        }
        for size in sizes:
            if size.growth_rate >= 0.15:
                out.insights.append(
                    Insight(
                        kind="opportunity",
                        title=f"Marché en forte croissance : {size.name}",
                        detail=f"Croissance annuelle {size.growth_rate:.0%} ; SOM {size.som} → "
                        f"{size.som_projection[-1].som} à {len(size.som_projection)} an(s).",
                        metric="market.growth_rate",
                        value=size.growth_rate,
                    )
                )
            elif size.growth_rate < 0:
                out.insights.append(
                    Insight(
                        kind="risk",
                        severity="warning",
                        title=f"Marché en contraction : {size.name}",
                        detail=f"Croissance annuelle {size.growth_rate:.0%}.",
                        metric="market.growth_rate",
                        value=size.growth_rate,
                    )
                )
        if len(sizes) >= 2 and total_som > 0:
            best = max(sizes, key=lambda s: s.som)
            share = float(best.som / total_som)
            out.insights.append(
                Insight(
                    kind="strength",
                    title=f"Marché le plus accessible : {best.name}",
                    detail=f"{share:.0%} du SOM total ({best.som}).",
                    value=share,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Concentrer l'effort commercial sur « {best.name} »",
                    rationale=f"Il représente {share:.0%} du chiffre d'affaires atteignable ({best.som}/an).",
                    impact=clamp(3 + 7 * share, 0, 10),
                    effort=4,
                    confidence=0.6,
                    urgency=5,
                    risk=0.15,
                    estimated_value=best.som,
                    tags=["marché", "focalisation"],
                    evidence=[f"{s.name}: SOM {s.som}" for s in sorted(sizes, key=lambda s: -s.som)[:4]],
                )
            )
        return out

    @action("Évaluer la concurrence : HHI, positionnement prix/qualité, fonctionnalités rares et manquantes")
    def analyze_competition(self, ctx: SkillContext, data: CompetitionInput) -> CompetitionOutput:
        competitors = self._competitors(ctx, data.competitors)
        n = len(competitors)
        if n == 0:
            return CompetitionOutput(
                competitors_count=0,
                hhi=0.0,
                concentration=None,
                uncovered_share=1.0,
                intensity=0.0,
                positions=[],
                rare_features=[],
                table_stakes=[],
                missing_features=[],
                value_gap=False,
                summary="Aucun concurrent renseigné : ajoutez des enregistrements « competitor ».",
            )
        total_share = sum(c.market_share for c in competitors)
        if total_share > 1.0 + 1e-6:
            raise SkillError(f"la somme des parts de marché dépasse 100 % ({total_share:.0%})")
        hhi = round(sum((c.market_share * 100) ** 2 for c in competitors), 1)
        concentration: Concentration = (
            "peu concentré" if hhi < 1500 else "modérément concentré" if hhi <= 2500 else "très concentré"
        )
        uncovered = round(max(0.0, 1.0 - total_share), 4)
        intensity = round(clamp(0.5 * (1 - hhi / 10000) + 0.3 * min(1.0, n / 10) + 0.2 * (1 - uncovered)), 4)
        price_median = median([float(c.price) for c in competitors])
        quality_median = median([c.quality for c in competitors])
        positions: list[CompetitorPosition] = []
        for comp in competitors:
            high_price = float(comp.price) >= price_median
            high_quality = comp.quality >= quality_median
            quadrant: Quadrant
            if high_price and high_quality:
                quadrant = "premium"
            elif not high_price and high_quality:
                quadrant = "rapport qualité/prix"
            elif not high_price:
                quadrant = "low-cost"
            else:
                quadrant = "surévalué"
            positions.append(
                CompetitorPosition(
                    name=comp.name,
                    market_share=comp.market_share,
                    price=to_decimal(comp.price),
                    quality=comp.quality,
                    quadrant=quadrant,
                )
            )
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}
        for comp in competitors:
            for feature in {_norm(f): f for f in comp.features}.items():
                counts[feature[0]] = counts.get(feature[0], 0) + 1
                labels.setdefault(feature[0], feature[1])
        rare = sorted(labels[k] for k, v in counts.items() if v / n <= 1 / 3)
        stakes = sorted(labels[k] for k, v in counts.items() if v / n >= 2 / 3)
        ours = {_norm(f) for f in data.our_features}
        missing = sorted(labels[k] for k, v in counts.items() if v / n >= 2 / 3 and k not in ours) if ours else []
        value_gap = n >= 2 and not any(p.quadrant == "rapport qualité/prix" for p in positions)
        out = CompetitionOutput(
            competitors_count=n,
            hhi=hhi,
            concentration=concentration,
            uncovered_share=uncovered,
            intensity=intensity,
            positions=positions,
            rare_features=rare,
            table_stakes=stakes,
            missing_features=missing,
            value_gap=value_gap,
            summary=(
                f"{n} concurrent(s), HHI {hhi:.0f} ({concentration}), part non couverte {uncovered:.0%}, "
                f"intensité concurrentielle {intensity:.2f}."
            ),
        )
        out.metrics = {
            "competition.hhi": hhi,
            "competition.intensity": intensity,
            "competition.uncovered_share": uncovered,
        }
        if concentration == "très concentré":
            leader = max(competitors, key=lambda c: c.market_share)
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Marché dominé par peu d'acteurs",
                    detail=f"HHI {hhi:.0f} ; leader {leader.name} à {leader.market_share:.0%}.",
                    metric="competition.hhi",
                    value=hhi,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title="Attaquer par une niche plutôt que frontalement",
                    rationale=f"Le marché est très concentré (HHI {hhi:.0f}) : un affrontement direct avec "
                    f"{leader.name} serait coûteux.",
                    impact=6,
                    effort=4,
                    confidence=0.6,
                    urgency=4,
                    risk=0.25,
                    tags=["concurrence", "positionnement"],
                    evidence=[f"HHI={hhi:.0f}", f"{leader.name}={leader.market_share:.0%}"],
                )
            )
        if uncovered >= 0.2:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title=f"{uncovered:.0%} du marché non capté par les concurrents identifiés",
                    detail="Clients non équipés, solutions artisanales ou acteurs non recensés.",
                    metric="competition.uncovered_share",
                    value=uncovered,
                )
            )
        if value_gap:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title="Aucun acteur sur le créneau rapport qualité/prix",
                    detail=f"Prix médian {price_median:.2f}, qualité médiane {quality_median:.1f} : "
                    "aucun concurrent "
                    "n'est à la fois sous le prix médian et au-dessus de la qualité médiane.",
                )
            )
            out.recommendations.append(
                Recommendation(
                    title="Se positionner sur le rapport qualité/prix",
                    rationale="Le quadrant « qualité ≥ médiane, prix < médiane » est vide.",
                    impact=7,
                    effort=5,
                    confidence=0.55,
                    urgency=5,
                    risk=0.2,
                    tags=["concurrence", "prix"],
                    evidence=[f"{p.name}: {p.quadrant}" for p in positions[:5]],
                )
            )
        if missing:
            out.insights.append(
                Insight(
                    kind="weakness",
                    severity="warning",
                    title="Fonctionnalités standard absentes de notre offre",
                    detail=", ".join(missing),
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Combler les fonctionnalités attendues : {', '.join(missing[:4])}",
                    rationale="Présentes chez au moins deux tiers des concurrents : leur absence freine la vente.",
                    impact=7,
                    effort=clamp(2 + len(missing), 0.5, 10),
                    confidence=0.7,
                    urgency=6,
                    risk=0.1,
                    tags=["produit", "concurrence"],
                )
            )
        if rare:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title="Leviers de différenciation peu exploités",
                    detail="Fonctionnalités rares chez les concurrents : " + ", ".join(rare[:6]),
                )
            )
        return out

    @action("Identifier les besoins non satisfaits (score d'opportunité d'Ulwick)")
    def find_unmet_needs(self, ctx: SkillContext, data: NeedsInput) -> NeedsOutput:
        needs = self._needs(ctx, data.needs)
        scored: list[NeedScore] = []
        for need in needs:
            opportunity = round(need.importance + max(need.importance - need.satisfaction, 0.0), 2)
            classification: NeedClass
            if need.satisfaction > need.importance:
                classification = "sur-servi"
            elif opportunity >= 12:
                classification = "fortement sous-servi"
            elif opportunity >= 10 and need.importance > need.satisfaction:
                classification = "sous-servi"
            else:
                classification = "servi"
            scored.append(
                NeedScore(
                    id=need.key,
                    statement=need.statement,
                    segment=need.segment,
                    importance=need.importance,
                    satisfaction=need.satisfaction,
                    opportunity=opportunity,
                    classification=classification,
                )
            )
        scored.sort(key=lambda s: (-s.opportunity, -s.importance, s.statement))
        underserved = [s for s in scored if s.classification in ("fortement sous-servi", "sous-servi")]
        out = NeedsOutput(needs=scored, underserved=len(underserved))
        if not scored:
            out.summary = "Aucun besoin renseigné : ajoutez des enregistrements « need » (importance, satisfaction)."
            return out
        overserved = [s for s in scored if s.classification == "sur-servi"]
        out.summary = (
            f"{len(scored)} besoin(s) analysé(s) : {len(underserved)} sous-servi(s), {len(overserved)} sur-servi(s)."
        )
        out.metrics = {
            "needs.underserved": float(len(underserved)),
            "needs.max_opportunity": scored[0].opportunity,
        }
        for scored_need in underserved[:3]:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    severity="warning" if scored_need.classification == "fortement sous-servi" else "info",
                    title=f"Besoin {scored_need.classification} : {scored_need.statement}",
                    detail=f"Importance {scored_need.importance}/10, satisfaction {scored_need.satisfaction}/10, "
                    f"score d'opportunité {scored_need.opportunity}.",
                    metric="needs.opportunity",
                    value=scored_need.opportunity,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Concevoir une réponse au besoin « {scored_need.statement} »",
                    rationale=f"Écart importance − satisfaction de "
                    f"{scored_need.importance - scored_need.satisfaction:.1f} points "
                    f"(score {scored_need.opportunity}).",
                    impact=clamp(scored_need.opportunity / 2, 0, 10),
                    effort=5,
                    confidence=0.55,
                    urgency=clamp(scored_need.opportunity - 5, 0, 10),
                    risk=0.2,
                    tags=["besoin", "produit"],
                    evidence=[f"importance={scored_need.importance}", f"satisfaction={scored_need.satisfaction}"],
                    follow_up=FollowUp(skill="business-intelligence", action="detect_opportunities", payload={}),
                )
            )
        if overserved:
            out.insights.append(
                Insight(
                    kind="observation",
                    title="Besoins sur-servis (simplifier ou baisser les coûts)",
                    detail=", ".join(s.statement for s in overserved[:5]),
                )
            )
        return out

    @action("Détecter et chiffrer les opportunités commerciales (besoins × marchés × concurrence)", risk="write")
    def detect_opportunities(self, ctx: SkillContext, data: OpportunityScanInput) -> OpportunityScanOutput:
        needs = self._needs(ctx, data.needs)
        markets = self._markets(ctx, data.markets)
        competitors = self._competitors(ctx, data.competitors)
        scored = self.find_unmet_needs(ctx, NeedsInput(needs=needs)).needs if needs else []
        need_by_key = {n.key: n for n in needs}
        if competitors:
            intensity = self.analyze_competition(ctx, CompetitionInput(competitors=competitors)).intensity
        else:
            intensity = 0.5  # inconnue : hypothèse médiane, confiance réduite
        sizes = {s.id: s for s in self.size_market(ctx, SizeMarketInput(markets=markets)).markets} if markets else {}
        found: list[DetectedOpportunity] = []
        for need_score in scored:
            if need_score.classification not in ("fortement sous-servi", "sous-servi"):
                continue
            if need_score.opportunity < data.min_opportunity:
                continue
            need = need_by_key[need_score.id]
            segment = _norm(need.segment)
            matched = [m for m in markets if segment and segment in (_norm(m.id), _norm(m.name))]
            targets: list[Market | None] = list(matched or markets) or [None]
            gap_factor = clamp((need_score.opportunity - 10) / 10, 0.1, 1.0)
            for market in targets:
                confidence = 0.35 + (0.15 if matched else 0.0) + (0.15 if market else 0.0)
                confidence += 0.15 if competitors else 0.0
                confidence += 0.1 * (need.frequency or 0.0)
                confidence = round(min(confidence, 0.9), 3)
                if market is not None:
                    size = sizes[market.key]
                    potential = to_decimal(size.som * _dec(gap_factor) * _dec(1 - 0.5 * intensity))
                    reach = size.obtainable_customers
                    title = f"{need.statement} — {market.name}"
                    market_id: str | None = market.key
                else:
                    potential, reach, market_id = Decimal("0.00"), 0, None
                    title = need.statement
                opp_id = stable_id("opp", need.key, market_id or "-")
                existing = ctx.store.get("opportunity", opp_id) or {}
                found.append(
                    DetectedOpportunity(
                        id=opp_id,
                        title=title,
                        description=(
                            f"Besoin {need_score.classification} (score {need_score.opportunity}) "
                            + (f"sur le marché {market.name}" if market else "sans marché dimensionné")
                            + f" ; intensité concurrentielle {intensity:.2f}."
                        ),
                        need_id=need.key,
                        market_id=market_id,
                        potential_value=potential,
                        reach=reach,
                        confidence=confidence,
                        score=round(need_score.opportunity * confidence, 3),
                        status=existing.get("status", "new"),
                    )
                )
        found.sort(key=lambda o: (-o.potential_value, -o.score))
        persisted = 0
        if data.persist:
            for opp in found:
                need = need_by_key[opp.need_id]
                ctx.store.put(
                    "opportunity",
                    opp.id,
                    {
                        "id": opp.id,
                        "title": opp.title,
                        "description": opp.description,
                        "source_skill": self.name,
                        "potential_value": str(opp.potential_value),
                        "confidence": opp.confidence,
                        "score": opp.score,
                        "status": opp.status,
                        "tags": [t for t in ("besoin", need.segment) if t],
                        "need_id": opp.need_id,
                        "market_id": opp.market_id,
                        "reach": opp.reach,
                    },
                )
                persisted += 1
        out = OpportunityScanOutput(opportunities=found, persisted=persisted, competition_intensity=intensity)
        if not found:
            out.summary = (
                f"Aucune opportunité détectée : aucun besoin sous-servi au-dessus du seuil {data.min_opportunity}."
            )
            return out
        total = to_decimal(sum((o.potential_value for o in found), Decimal("0")))
        out.summary = (
            f"{len(found)} opportunité(s) détectée(s), potentiel cumulé {total}/an ; {persisted} enregistrée(s)."
        )
        out.metrics = {"opportunities.count": float(len(found)), "opportunities.potential": float(total)}
        best_value = max((o.potential_value for o in found), default=Decimal("0"))
        for opp in found[:3]:
            weight = float(opp.potential_value / best_value) if best_value > 0 else 0.3
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title=f"Opportunité : {opp.title}",
                    detail=f"Potentiel {opp.potential_value}/an, {opp.reach} clients atteignables, confiance "
                    f"{opp.confidence:.0%}.",
                    value=float(opp.potential_value),
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Instruire l'opportunité « {opp.title} »",
                    rationale=opp.description,
                    impact=clamp(4 + 6 * weight, 0, 10),
                    effort=3,
                    confidence=opp.confidence,
                    urgency=5,
                    risk=0.2,
                    estimated_value=opp.potential_value,
                    tags=["opportunité"],
                    evidence=[f"potentiel={opp.potential_value}", f"score={opp.score}"],
                    follow_up=FollowUp(skill="business-development", action="rank_opportunities", payload={}),
                )
            )
        return out

    @action("Estimer le potentiel économique d'une offre (revenu annuel, LTV, scénarios)")
    def estimate_potential(self, ctx: SkillContext, data: PotentialInput) -> PotentialOutput:
        price = data.price
        margin = _dec(data.gross_margin)
        churn = data.churn_monthly
        if data.billing == "monthly":
            retention_months = sum((1 - churn) ** m for m in range(12))
            per_customer = to_decimal(price * _dec(retention_months))
            if churn > 0:
                ltv = to_decimal(price * margin / _dec(churn))
                assumption = f"prix × marge / churn mensuel ({churn:.1%})"
            else:
                ltv = to_decimal(price * margin * 36)
                assumption = "churn nul déclaré : LTV plafonnée à 36 mois"
        elif data.billing == "annual":
            per_customer = to_decimal(price)
            annual_churn = 1 - (1 - churn) ** 12
            if annual_churn > 0:
                ltv = to_decimal(price * margin / _dec(annual_churn))
                assumption = f"prix annuel × marge / churn annualisé ({annual_churn:.1%})"
            else:
                ltv = to_decimal(price * margin * 3)
                assumption = "churn nul déclaré : LTV plafonnée à 3 ans"
        else:
            per_customer = to_decimal(price)
            ltv = to_decimal(price * margin)
            assumption = "achat unique : LTV = prix × marge"
        scenarios: list[Scenario] = []
        for label, factor in (("pessimiste", 0.5), ("central", 1.0), ("optimiste", 1.5)):
            rate = min(1.0, data.conversion_rate * factor)
            customers = data.target_customers * rate
            scenarios.append(
                Scenario(
                    name=label,
                    conversion_rate=round(rate, 4),
                    customers=round(customers, 2),
                    annual_revenue=to_decimal(per_customer * _dec(customers)),
                )
            )
        central = scenarios[1]
        out = PotentialOutput(
            name=data.name,
            customers=central.customers,
            annual_revenue_per_customer=per_customer,
            annual_revenue=central.annual_revenue,
            ltv=ltv,
            ltv_assumption=assumption,
            scenarios=scenarios,
            summary=(
                f"« {data.name} » : {central.customers:g} client(s) attendu(s), revenu annuel central "
                f"{central.annual_revenue} (fourchette {scenarios[0].annual_revenue} – "
                f"{scenarios[2].annual_revenue}), LTV {ltv}."
            ),
        )
        out.metrics = {"potential.annual_revenue": float(central.annual_revenue), "potential.ltv": float(ltv)}
        if data.billing == "monthly" and churn >= 0.08:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Attrition élevée",
                    detail=f"Churn mensuel {churn:.0%} : la moitié des clients part en "
                    f"{max(1, round(0.693 / churn))} mois environ.",
                    value=churn,
                )
            )
        return out


SKILL = BusinessIntelligenceSkill
