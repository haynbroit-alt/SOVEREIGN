"""SKILL 6 — NÉGOCIATION ET STRATÉGIE.

Compare des propositions (somme pondérée + TOPSIS + analyse de sensibilité), prépare les négociations
commerciales (BATNA, ZOPA, ancrage, concessions décroissantes, échanges), évalue avantages et inconvénients,
fait confronter les arguments des agents IA (débat contradictoire avec réfutations et vote pondéré) et
recommande
la meilleure décision. Le verdict est toujours déterministe ; un LLM peut seulement reformuler les arguments.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sovereign.core.money import to_decimal
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

Direction = Literal["benefit", "cost"]


# -- comparaison multicritère ------------------------------------------------------------------------
class Criterion(BaseModel):
    name: str
    weight: float = Field(gt=0)
    direction: Direction = "benefit"


class Proposal(BaseModel):
    name: str
    values: dict[str, float]


class CompareInput(BaseModel):
    title: str = "Comparaison"
    criteria: list[Criterion] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    comparison_id: str | None = Field(
        None, description="Charger une comparaison du store (sinon la plus récente)"
    )
    perturbation: float = Field(0.2, gt=0, lt=1)


class ProposalScore(BaseModel):
    name: str
    weighted_score: float
    topsis: float
    combined: float
    rank: int


class CompareOutput(SkillOutput):
    title: str
    ranking: list[ProposalScore]
    winner: str
    methods_agree: bool
    stability: float
    sensitive_criteria: list[str]


def _load_comparison(ctx: SkillContext, data: CompareInput) -> tuple[str, list[Criterion], list[Proposal]]:
    if data.criteria and data.proposals:
        return data.title, data.criteria, data.proposals
    records = ctx.store.list("comparison")
    if data.comparison_id:
        records = [r for r in records if r.get("id") == data.comparison_id]
        if not records:
            raise SkillError(f"comparaison inconnue : {data.comparison_id}")
    if not records:
        raise SkillError(
            "aucune proposition à comparer : fournissez criteria + proposals ou une « comparison »"
        )
    record = records[-1]
    return (
        record.get("title", data.title),
        [Criterion.model_validate(c) for c in record["criteria"]],
        [Proposal.model_validate(p) for p in record["proposals"]],
    )


def _score(
    criteria: list[Criterion], proposals: list[Proposal], weights: list[float]
) -> list[tuple[float, float]]:
    total = sum(weights)
    w = [x / total for x in weights]
    weighted = [0.0] * len(proposals)
    for j, criterion in enumerate(criteria):
        column = [p.values[criterion.name] for p in proposals]
        low, high = min(column), max(column)
        for i, value in enumerate(column):
            if high == low:
                norm = 1.0
            elif criterion.direction == "benefit":
                norm = (value - low) / (high - low)
            else:
                norm = (high - value) / (high - low)
            weighted[i] += w[j] * norm
    # TOPSIS : normalisation vectorielle, distances aux solutions idéale et anti-idéale.
    d_best = [0.0] * len(proposals)
    d_worst = [0.0] * len(proposals)
    for j, criterion in enumerate(criteria):
        column = [p.values[criterion.name] for p in proposals]
        norm = math.sqrt(sum(v * v for v in column)) or 1.0
        v = [w[j] * value / norm for value in column]
        best, worst = (max(v), min(v)) if criterion.direction == "benefit" else (min(v), max(v))
        for i, value in enumerate(v):
            d_best[i] += (value - best) ** 2
            d_worst[i] += (value - worst) ** 2
    closeness = []
    for i in range(len(proposals)):
        plus, minus = math.sqrt(d_best[i]), math.sqrt(d_worst[i])
        closeness.append(0.5 if plus + minus == 0 else minus / (plus + minus))
    return list(zip(weighted, closeness, strict=True))


def _winner(scores: list[tuple[float, float]], proposals: list[Proposal]) -> str:
    combined = [(a + b) / 2 for a, b in scores]
    best = max(range(len(proposals)), key=lambda i: (combined[i], -i))
    return proposals[best].name


# -- négociation --------------------------------------------------------------------------------------
class Issue(BaseModel):
    name: str
    our_cost: float = Field(ge=1, le=10)
    their_value: float = Field(ge=1, le=10)


class NegotiationInput(BaseModel):
    negotiation_id: str | None = None
    title: str | None = None
    counterpart: str | None = None
    we_are: Literal["buyer", "seller"] | None = None
    our_batna: Decimal | None = Field(None, ge=0)
    our_reservation: Decimal | None = Field(None, ge=0)
    our_target: Decimal | None = Field(None, ge=0)
    their_estimated_reservation: Decimal | None = Field(None, ge=0)
    issues: list[Issue] = Field(default_factory=list)


class ConcessionStep(BaseModel):
    step: int
    concession: Decimal
    position: Decimal


class Trade(BaseModel):
    issue: str
    stance: Literal["concéder", "protéger", "neutre"]
    reason: str


class NegotiationOutput(SkillOutput):
    title: str
    counterpart: str | None
    we_are: Literal["buyer", "seller"]
    zopa_exists: bool | None
    zopa_low: Decimal | None
    zopa_high: Decimal | None
    anchor: Decimal
    concession_plan: list[ConcessionStep]
    walk_away: str
    trades: list[Trade]
    warnings: list[str]


# -- avantages / inconvénients --------------------------------------------------------------------------
class Assessment(BaseModel):
    dimension: str
    score: float = Field(ge=-5, le=5)
    weight: float = Field(1.0, gt=0)
    note: str = ""


class OptionAssessment(BaseModel):
    name: str
    assessments: list[Assessment] = Field(min_length=1)


class ProsConsInput(BaseModel):
    options: list[OptionAssessment] = Field(min_length=1)


class OptionVerdict(BaseModel):
    name: str
    pros: list[str]
    cons: list[str]
    net_score: float
    verdict: Literal["favorable", "mitigé", "défavorable"]


class ProsConsOutput(SkillOutput):
    options: list[OptionVerdict]
    best: str


# -- débat entre agents -----------------------------------------------------------------------------------
class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_value: Decimal | None = None
    cost: Decimal | None = Field(None, ge=0)
    probability_of_success: float | None = Field(None, ge=0, le=1)
    reversibility: float | None = Field(None, ge=0, le=1)
    strategic_fit: float | None = Field(None, ge=0, le=1)
    market_evidence: float | None = Field(None, ge=0, le=1)
    budget_available: Decimal | None = None
    time_to_value_months: float | None = Field(None, ge=0)
    team_capacity: float | None = Field(None, ge=0, le=1)


AGENTS = ("ceo", "risk", "cfo", "cmo", "coo")
AGENT_LABELS = {"ceo": "CEO", "risk": "Risque", "cfo": "CFO", "cmo": "CMO", "coo": "COO"}
RELEVANCE = {"ceo": 1.2, "risk": 1.1, "cfo": 1.1, "cmo": 1.0, "coo": 1.0}


class DebateInput(BaseModel):
    proposal: str
    facts: Facts = Field(default_factory=Facts)
    agents: list[str] = Field(default_factory=lambda: list(AGENTS))
    use_llm: bool = True

    @model_validator(mode="after")
    def _known_agents(self) -> DebateInput:
        unknown = [a for a in self.agents if a not in AGENTS]
        if unknown or len(self.agents) < 2:
            raise ValueError(
                f"agents inconnus ou trop peu nombreux : {unknown or self.agents} (connus : {AGENTS})"
            )
        return self


class Argument(BaseModel):
    text: str
    strength: float = Field(ge=0, le=1)
    polarity: float = Field(ge=-1, le=1)
    evidence: str = ""


class Turn(BaseModel):
    round: Literal["ouverture", "réfutation"]
    agent: str
    stance: Literal["pour", "contre", "sous conditions"]
    score: float
    arguments: list[Argument]
    responds_to: str | None = None


class DebateOutput(SkillOutput):
    proposal: str
    transcript: list[Turn]
    final_positions: dict[str, float]
    verdict: Literal["adopter", "adopter sous conditions", "rejeter"]
    confidence: float
    conditions: list[str]
    vetoes: list[str]
    data_completeness: float
    llm_used: bool


class Reformulation(BaseModel):
    agent: str
    text: str


class Reformulations(BaseModel):
    statements: list[Reformulation]


class RecommendInput(CompareInput):
    facts: Facts | None = Field(None, description="Faits pour débattre de la proposition gagnante")


class RecommendOutput(SkillOutput):
    winner: str
    comparison: CompareOutput
    debate_verdict: str | None
    recommendation: str
    confidence: float


class AssessOutput(SkillOutput):
    comparisons: int
    negotiations: int


def _stance(score: float) -> Literal["pour", "contre", "sous conditions"]:
    return "pour" if score > 0.2 else "contre" if score < -0.2 else "sous conditions"


def _missing(name: str, label: str) -> Argument:
    return Argument(
        text=f"Donnée manquante : {label} — position prudente.", strength=0.2, polarity=0.0, evidence=name
    )


def _arguments(agent: str, facts: Facts) -> list[Argument]:
    args: list[Argument] = []
    ev, cost, budget = facts.expected_value, facts.cost, facts.budget_available
    roi = float((ev - cost) / cost) if ev is not None and cost not in (None, 0) else None
    if agent == "ceo":
        if roi is not None:
            args.append(
                Argument(
                    text=f"Retour attendu {roi:+.0%} : {'création' if roi > 0 else 'destruction'} de valeur.",
                    strength=min(1.0, 0.4 + abs(roi) / 2),
                    polarity=math.tanh(roi),
                    evidence=f"valeur {ev} / coût {cost}",
                )
            )
        else:
            args.append(_missing("expected_value", "valeur attendue ou coût"))
        if facts.strategic_fit is not None:
            args.append(
                Argument(
                    text=f"Cohérence stratégique {facts.strategic_fit:.0%}.",
                    strength=0.6,
                    polarity=2 * facts.strategic_fit - 1,
                    evidence=f"strategic_fit={facts.strategic_fit}",
                )
            )
        else:
            args.append(_missing("strategic_fit", "cohérence stratégique"))
    elif agent == "risk":
        if facts.probability_of_success is not None:
            p = facts.probability_of_success
            args.append(
                Argument(
                    text=f"Probabilité de succès estimée à {p:.0%}.",
                    strength=0.7,
                    polarity=max(-1.0, min(1.0, (p - 0.5) * 2.4)),
                    evidence=f"p={p}",
                )
            )
        else:
            args.append(_missing("probability_of_success", "probabilité de succès"))
        if facts.reversibility is not None:
            r = facts.reversibility
            args.append(
                Argument(
                    text=(
                        "Décision réversible : l'erreur coûterait peu."
                        if r >= 0.5
                        else "Décision difficilement réversible : prudence."
                    ),
                    strength=0.6,
                    polarity=max(-1.0, min(1.0, (r - 0.5) * 1.6)),
                    evidence=f"réversibilité={r}",
                )
            )
        else:
            args.append(_missing("reversibility", "réversibilité"))
        if cost is not None and budget is not None and cost > budget:
            args.append(
                Argument(
                    text=f"Le coût {cost} dépasse le budget disponible {budget}.",
                    strength=1.0,
                    polarity=-1.0,
                    evidence="règle des 10 %",
                )
            )
    elif agent == "cfo":
        if cost is not None and budget is not None:
            if cost > budget:
                args.append(
                    Argument(
                        text=f"Coût {cost} > disponible {budget} : non finançable (règle des 10 %).",
                        strength=1.0,
                        polarity=-1.0,
                        evidence=f"{cost} > {budget}",
                    )
                )
            else:
                share = float(cost / budget) if budget > 0 else 1.0
                args.append(
                    Argument(
                        text=f"Finançable : {share:.0%} du disponible.",
                        strength=0.6,
                        polarity=max(-0.6, 0.6 - share),
                        evidence=f"{cost} / {budget}",
                    )
                )
        else:
            args.append(_missing("cost", "coût ou budget disponible"))
        if roi is not None:
            args.append(
                Argument(
                    text=f"ROI {roi:+.0%}.",
                    strength=min(1.0, 0.3 + abs(roi) / 3),
                    polarity=math.tanh(roi),
                    evidence=f"roi={roi:.2f}",
                )
            )
        if facts.time_to_value_months is not None:
            t = facts.time_to_value_months
            args.append(
                Argument(
                    text=f"Retour attendu en {t:g} mois.",
                    strength=0.4,
                    polarity=max(-1.0, min(1.0, (6 - t) / 6)),
                    evidence=f"time_to_value={t}",
                )
            )
    elif agent == "cmo":
        if facts.market_evidence is not None:
            m = facts.market_evidence
            args.append(
                Argument(
                    text=f"Preuves de demande marché : {m:.0%}.",
                    strength=0.7,
                    polarity=2 * m - 1,
                    evidence=f"market_evidence={m}",
                )
            )
        else:
            args.append(_missing("market_evidence", "preuves de marché"))
        if facts.strategic_fit is not None:
            args.append(
                Argument(
                    text="Alignement avec le positionnement de marque.",
                    strength=0.4,
                    polarity=2 * facts.strategic_fit - 1,
                    evidence=f"fit={facts.strategic_fit}",
                )
            )
    elif agent == "coo":
        if facts.team_capacity is not None:
            c = facts.team_capacity
            args.append(
                Argument(
                    text=f"Capacité d'exécution disponible : {c:.0%}.",
                    strength=0.6,
                    polarity=max(-1.0, min(1.0, (c - 0.4) * 2)),
                    evidence=f"capacity={c}",
                )
            )
        else:
            args.append(_missing("team_capacity", "capacité de l'équipe"))
        if facts.time_to_value_months is not None:
            t = facts.time_to_value_months
            args.append(
                Argument(
                    text=f"Mise en œuvre en {t:g} mois.",
                    strength=0.4,
                    polarity=max(-1.0, min(1.0, (9 - t) / 9)),
                    evidence=f"time_to_value={t}",
                )
            )
    return args


def _agent_score(args: list[Argument]) -> float:
    total = sum(a.strength for a in args)
    return sum(a.strength * a.polarity for a in args) / total if total else 0.0


class NegotiationStrategySkill(Skill):
    name = "negotiation-strategy"
    version = "1.0.0"

    @action("Diagnostic : comparaisons et négociations en cours de l'espace de travail")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        comparisons = ctx.store.list("comparison")
        negotiations = ctx.store.list("negotiation")
        out = AssessOutput(comparisons=len(comparisons), negotiations=len(negotiations), summary="")
        if not comparisons and not negotiations:
            out.summary = (
                "Aucune comparaison ni négociation : importez des « comparison » (critères + propositions)"
                " ou des « negotiation » (BATNA, réservation, cible)."
            )
            return out
        parts = []
        for record in comparisons:
            result = self.compare_proposals(ctx, CompareInput(comparison_id=record["id"]))
            parts.append(f"{result.title} → {result.winner} (stabilité {result.stability:.0%})")
            out.insights.extend(result.insights)
            out.recommendations.extend(result.recommendations)
        for record in negotiations:
            prep = self.prepare_negotiation(ctx, NegotiationInput(negotiation_id=record["id"]))
            parts.append(
                f"{prep.title} : ZOPA "
                + ("oui" if prep.zopa_exists else "non" if prep.zopa_exists is False else "inconnue")
            )
            out.insights.extend(prep.insights)
            out.recommendations.extend(prep.recommendations)
            if prep.zopa_low is not None and prep.zopa_high is not None:
                out.metrics[f"negotiation.{record['id']}.zopa_width"] = float(prep.zopa_high - prep.zopa_low)
        out.summary = "; ".join(parts) + "."
        return out

    @action("Comparer des propositions : somme pondérée, TOPSIS et analyse de sensibilité des poids")
    def compare_proposals(self, ctx: SkillContext, data: CompareInput) -> CompareOutput:
        title, criteria, proposals = _load_comparison(ctx, data)
        if len(proposals) < 2:
            raise SkillError("au moins deux propositions sont nécessaires")
        names = [c.name for c in criteria]
        if len(set(names)) != len(names):
            raise SkillError("critères en double")
        for proposal in proposals:
            missing = [n for n in names if n not in proposal.values]
            if missing:
                raise SkillError(f"valeurs manquantes pour {proposal.name} : {missing}")
        weights = [c.weight for c in criteria]
        scores = _score(criteria, proposals, weights)
        combined = [(a + b) / 2 for a, b in scores]
        order = sorted(range(len(proposals)), key=lambda i: (-combined[i], i))
        ranking = [
            ProposalScore(
                name=proposals[i].name,
                weighted_score=round(scores[i][0], 4),
                topsis=round(scores[i][1], 4),
                combined=round(combined[i], 4),
                rank=r + 1,
            )
            for r, i in enumerate(order)
        ]
        winner = ranking[0].name
        ws_winner = proposals[max(range(len(proposals)), key=lambda i: (scores[i][0], -i))].name
        tp_winner = proposals[max(range(len(proposals)), key=lambda i: (scores[i][1], -i))].name
        trials = stable = 0
        sensitive: list[str] = []
        for j, criterion in enumerate(criteria):
            for factor in (1 - data.perturbation, 1 + data.perturbation):
                perturbed = list(weights)
                perturbed[j] *= factor
                trials += 1
                if _winner(_score(criteria, proposals, perturbed), proposals) == winner:
                    stable += 1
                elif criterion.name not in sensitive:
                    sensitive.append(criterion.name)
        stability = stable / trials if trials else 1.0
        margin = combined[order[0]] - combined[order[1]]
        out = CompareOutput(
            title=title,
            ranking=ranking,
            winner=winner,
            methods_agree=ws_winner == tp_winner,
            stability=round(stability, 3),
            sensitive_criteria=sensitive,
            summary=(
                f"« {winner} » arrive en tête ({ranking[0].combined:.2f} vs {ranking[1].combined:.2f}), "
                f"stabilité {stability:.0%} face à ±{data.perturbation:.0%} sur les poids."
            ),
        )
        out.metrics["negotiation.comparison_stability"] = round(stability, 4)
        if not out.methods_agree:
            out.insights.append(
                Insight(
                    kind="contradiction",
                    severity="warning",
                    title="Les méthodes divergent",
                    detail=f"Somme pondérée → {ws_winner}, TOPSIS → {tp_winner}.",
                )
            )
        if sensitive:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Classement sensible aux poids",
                    detail="Le gagnant change si l'on modifie : " + ", ".join(sensitive),
                )
            )
        out.recommendations.append(
            Recommendation(
                title=f"Retenir « {winner} » ({title})",
                rationale=(
                    f"Meilleur score combiné, écart {margin:.2f}, stabilité {stability:.0%}"
                    + ("" if out.methods_agree else ", mais méthodes divergentes")
                    + "."
                ),
                impact=6,
                effort=2,
                confidence=round(min(0.95, 0.35 + 0.5 * stability + min(0.1, margin)), 3),
                urgency=5,
                risk=round(0.4 - 0.3 * stability, 3),
                tags=["décision", "comparaison"],
                evidence=[f"{r.name}: {r.combined}" for r in ranking],
            )
        )
        return out

    @action("Préparer une négociation : ZOPA, ancrage, plan de concessions, règle de rupture, échanges")
    def prepare_negotiation(self, ctx: SkillContext, data: NegotiationInput) -> NegotiationOutput:
        merged = data.model_dump(exclude_none=True)
        if data.negotiation_id or data.our_reservation is None:
            records = ctx.store.list("negotiation")
            if data.negotiation_id:
                records = [r for r in records if r.get("id") == data.negotiation_id]
            if records:
                stored = dict(records[-1])
                stored.pop("id", None)
                merged = {**stored, **{k: v for k, v in merged.items() if v not in (None, [])}}
        try:
            params = NegotiationInput.model_validate(merged)
        except Exception as exc:
            raise SkillError(f"paramètres de négociation invalides : {exc}") from exc
        if (
            params.we_are is None
            or params.our_reservation is None
            or params.our_target is None
            or params.our_batna is None
        ):
            raise SkillError("we_are, our_batna, our_reservation et our_target sont requis")
        buyer = params.we_are == "buyer"
        reservation, target, batna = (
            to_decimal(params.our_reservation),
            to_decimal(params.our_target),
            to_decimal(params.our_batna),
        )
        theirs = (
            to_decimal(params.their_estimated_reservation)
            if params.their_estimated_reservation is not None
            else None
        )
        if buyer and target > reservation:
            raise SkillError("acheteur : la cible doit être ≤ au prix de réservation")
        if not buyer and target < reservation:
            raise SkillError("vendeur : la cible doit être ≥ au prix de réservation")
        warnings: list[str] = []
        if buyer and reservation > batna:
            warnings.append(
                f"Réservation {reservation} supérieure à la BATNA {batna} : mieux vaut l'alternative."
            )
        if not buyer and reservation < batna:
            warnings.append(
                f"Réservation {reservation} inférieure à la BATNA {batna} : mieux vaut l'alternative."
            )
        zopa_exists: bool | None = None
        low = high = None
        if theirs is not None:
            if buyer:
                zopa_exists = reservation >= theirs
                low, high = (theirs, reservation) if zopa_exists else (None, None)
            else:
                zopa_exists = theirs >= reservation
                low, high = (reservation, theirs) if zopa_exists else (None, None)
        gap = abs(reservation - target)
        if buyer:
            anchor = target - gap * Decimal("0.25")
            if theirs is not None:
                anchor = max(anchor, theirs * Decimal("0.85"))
            anchor = min(anchor, target)
        else:
            anchor = target + gap * Decimal("0.25")
            if theirs is not None:
                anchor = min(anchor, theirs * Decimal("1.15"))
            anchor = max(anchor, target)
        anchor = to_decimal(anchor)
        distance = abs(reservation - anchor)
        sign = Decimal(1) if buyer else Decimal(-1)
        position = anchor
        plan: list[ConcessionStep] = []
        for index, share in enumerate(
            (Decimal("0.50"), Decimal("0.25"), Decimal("0.15"), Decimal("0.10")), start=1
        ):
            concession = to_decimal(distance * share)
            position = to_decimal(position + sign * concession) if index < 4 else reservation
            plan.append(ConcessionStep(step=index, concession=concession, position=position))
        walk_away = (
            f"Rompre si le prix dépasse {reservation} : activer la BATNA ({batna})."
            if buyer
            else f"Rompre en dessous de {reservation} : activer la BATNA ({batna})."
        )
        trades: list[Trade] = []
        for issue in sorted(params.issues, key=lambda i: -(i.their_value - i.our_cost)):
            if issue.our_cost <= 4 and issue.their_value >= 6:
                trades.append(
                    Trade(
                        issue=issue.name,
                        stance="concéder",
                        reason="Peu coûteux pour nous, très valorisé par l'autre partie : monnaie d'échange.",
                    )
                )
            elif issue.our_cost >= 7 and issue.their_value <= 4:
                trades.append(
                    Trade(
                        issue=issue.name,
                        stance="protéger",
                        reason="Coûteux pour nous et peu valorisé par l'autre partie : ne pas céder.",
                    )
                )
            else:
                trades.append(
                    Trade(issue=issue.name, stance="neutre", reason="À échanger contre contrepartie.")
                )
        title = params.title or "Négociation"
        out = NegotiationOutput(
            title=title,
            counterpart=params.counterpart,
            we_are=params.we_are,
            zopa_exists=zopa_exists,
            zopa_low=low,
            zopa_high=high,
            anchor=anchor,
            concession_plan=plan,
            walk_away=walk_away,
            trades=trades,
            warnings=warnings,
            summary=(
                f"{title} : ancrage {anchor}, réservation {reservation}, "
                + (
                    "ZOPA " + (f"[{low} ; {high}]" if zopa_exists else "inexistante")
                    if zopa_exists is not None
                    else "ZOPA inconnue (réservation adverse non estimée)"
                )
                + "."
            ),
        )
        for warning in warnings:
            out.insights.append(
                Insight(
                    kind="contradiction", severity="warning", title="Paramètres incohérents", detail=warning
                )
            )
        if zopa_exists is False:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity="warning",
                    title="Aucune zone d'accord possible",
                    detail="Les réservations ne se recouvrent pas : préparer la BATNA.",
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Activer l'alternative (BATNA) pour « {title} »",
                    rationale=(
                        "Aucune ZOPA : négocier sur le prix seul est voué à l'échec ; "
                        "élargir les enjeux ou sortir."
                    ),
                    impact=6,
                    effort=3,
                    confidence=0.6,
                    urgency=6,
                    risk=0.2,
                    external=True,
                    tags=["négociation"],
                )
            )
        else:
            value = abs(reservation - anchor)
            out.recommendations.append(
                Recommendation(
                    title=f"Ouvrir « {title} » à {anchor} et suivre le plan de concessions",
                    rationale=(
                        "Ancrage au-delà de la cible, concessions décroissantes "
                        "signalant l'approche de la limite."
                    ),
                    impact=6,
                    effort=3,
                    confidence=0.65 if zopa_exists else 0.5,
                    urgency=5,
                    risk=0.15,
                    estimated_value=to_decimal(value),
                    external=True,
                    tags=["négociation"],
                    evidence=[f"pas {s.step}: {s.position}" for s in plan],
                )
            )
        if low is not None and high is not None:
            out.metrics["negotiation.zopa_width"] = float(high - low)
        return out

    @action("Évaluer avantages et inconvénients pondérés d'une ou plusieurs options")
    def pros_cons(self, ctx: SkillContext, data: ProsConsInput) -> ProsConsOutput:
        verdicts: list[OptionVerdict] = []
        for option in data.options:
            total_weight = sum(a.weight for a in option.assessments)
            net = sum(a.score * a.weight for a in option.assessments) / total_weight
            ordered = sorted(option.assessments, key=lambda a: -abs(a.score * a.weight))
            pros = [
                f"{a.dimension} (+{a.score:g}){' — ' + a.note if a.note else ''}"
                for a in ordered
                if a.score > 0
            ]
            cons = [
                f"{a.dimension} ({a.score:g}){' — ' + a.note if a.note else ''}"
                for a in ordered
                if a.score < 0
            ]
            verdict: Literal["favorable", "mitigé", "défavorable"] = (
                "favorable" if net >= 1.5 else "défavorable" if net <= -1.5 else "mitigé"
            )
            verdicts.append(
                OptionVerdict(
                    name=option.name, pros=pros, cons=cons, net_score=round(net, 3), verdict=verdict
                )
            )
        verdicts.sort(key=lambda v: -v.net_score)
        best = verdicts[0]
        out = ProsConsOutput(
            options=verdicts,
            best=best.name,
            summary=f"Meilleure option : {best.name} (score net {best.net_score:+.2f}, {best.verdict}).",
        )
        if best.verdict != "défavorable":
            out.recommendations.append(
                Recommendation(
                    title=f"Privilégier « {best.name} »",
                    rationale=f"Score net pondéré {best.net_score:+.2f}.",
                    impact=5,
                    effort=2,
                    confidence=min(0.9, 0.5 + abs(best.net_score) / 10),
                    urgency=4,
                    risk=0.15,
                    tags=["décision"],
                    evidence=best.pros[:3],
                )
            )
        return out

    @action("Faire débattre les agents IA (CEO, Risque, CFO, CMO, COO) : ouverture, réfutations, vote")
    def debate(self, ctx: SkillContext, data: DebateInput) -> DebateOutput:
        facts = data.facts
        if facts.budget_available is None:
            facts = facts.model_copy(update={"budget_available": ctx.finance.available()})
        provided = [v for k, v in facts.model_dump().items() if k != "budget_available"]
        completeness = sum(1 for v in provided if v is not None) / len(provided)
        transcript: list[Turn] = []
        arguments = {agent: _arguments(agent, facts) for agent in data.agents}
        scores = {agent: _agent_score(args) for agent, args in arguments.items()}
        for agent in data.agents:
            transcript.append(
                Turn(
                    round="ouverture",
                    agent=AGENT_LABELS[agent],
                    stance=_stance(scores[agent]),
                    score=round(scores[agent], 3),
                    arguments=arguments[agent],
                )
            )
        final = dict(scores)
        for agent in data.agents:
            own_sign = math.copysign(1, scores[agent]) if scores[agent] else 0
            opposing = [
                (other, arg)
                for other in data.agents
                if other != agent
                for arg in arguments[other]
                if arg.polarity * own_sign < 0 or (own_sign == 0 and arg.polarity < 0)
            ]
            if not opposing:
                continue
            other, strongest = max(opposing, key=lambda x: (x[1].strength * abs(x[1].polarity), x[0]))
            own_best = max((a.strength for a in arguments[agent] if a.polarity * own_sign > 0), default=0.0)
            if own_best >= strongest.strength:
                reply = Argument(
                    text=f"Maintient sa position face à {AGENT_LABELS[other]} : « {strongest.text} » "
                    "ne l'emporte pas sur ses propres éléments.",
                    strength=own_best,
                    polarity=own_sign,
                    evidence=strongest.evidence,
                )
            else:
                final[agent] = scores[agent] * 0.7
                reply = Argument(
                    text=f"Concède partiellement à {AGENT_LABELS[other]} : « {strongest.text} ».",
                    strength=strongest.strength,
                    polarity=strongest.polarity,
                    evidence=strongest.evidence,
                )
            transcript.append(
                Turn(
                    round="réfutation",
                    agent=AGENT_LABELS[agent],
                    stance=_stance(final[agent]),
                    score=round(final[agent], 3),
                    arguments=[reply],
                    responds_to=AGENT_LABELS[other],
                )
            )
        weights = {
            a: RELEVANCE[a] * max(0.2, sum(x.strength for x in arguments[a]) / max(1, len(arguments[a])))
            for a in data.agents
        }
        vote = sum(final[a] * weights[a] for a in data.agents) / sum(weights.values())
        vetoes: list[str] = []
        if (
            facts.cost is not None
            and facts.budget_available is not None
            and facts.cost > facts.budget_available
        ):
            vetoes.append(
                f"Veto budgétaire : coût {facts.cost} > disponible {facts.budget_available} (règle des 10 %)."
            )
        strong_opposition = [AGENT_LABELS[a] for a in data.agents if final[a] <= -0.6]
        conditions = [
            arg.text
            for a in data.agents
            if final[a] < 0.2
            for arg in arguments[a]
            if arg.polarity < 0 and arg.strength >= 0.4
        ]
        if vetoes:
            verdict: Literal["adopter", "adopter sous conditions", "rejeter"] = "rejeter"
        elif vote >= 0.25 and not strong_opposition:
            verdict = "adopter"
        elif vote > -0.1:
            verdict = "adopter sous conditions"
        else:
            verdict = "rejeter"
        confidence = round(max(0.1, min(0.95, (0.35 + abs(vote)) * (0.4 + 0.6 * completeness))), 3)
        llm_used = False
        if data.use_llm and ctx.llm is not None:
            prompt = "\n".join(
                f"{t.agent} ({t.round}) : " + " ".join(a.text for a in t.arguments) for t in transcript
            )
            rewritten = ctx.llm.structured(
                "Tu reformules en français, de façon concise et professionnelle, "
                "les arguments d'un conseil de "
                "direction. Ne change ni le sens, ni la position, ni les chiffres.",
                f"Proposition : {data.proposal}\n\n{prompt}",
                Reformulations,
                purpose="reformulation du débat",
                max_tokens=2000,
            )
            if rewritten is not None:
                llm_used = True
                by_agent = {s.agent: s.text for s in rewritten.statements}
                for turn in transcript:
                    if turn.round == "ouverture" and turn.agent in by_agent:
                        turn.arguments.append(
                            Argument(
                                text=by_agent[turn.agent],
                                strength=0.0,
                                polarity=0.0,
                                evidence="reformulation LLM",
                            )
                        )
        out = DebateOutput(
            proposal=data.proposal,
            transcript=transcript,
            final_positions={AGENT_LABELS[a]: round(final[a], 3) for a in data.agents},
            verdict=verdict,
            confidence=confidence,
            conditions=conditions[:5],
            vetoes=vetoes,
            data_completeness=round(completeness, 3),
            llm_used=llm_used,
            summary=(
                f"Verdict : {verdict} (vote {vote:+.2f}, confiance {confidence:.0%}, données "
                f"{completeness:.0%})" + (f" — {vetoes[0]}" if vetoes else "") + "."
            ),
        )
        out.metrics["debate.vote"] = round(vote, 4)
        if strong_opposition and verdict != "rejeter":
            out.insights.append(
                Insight(
                    kind="contradiction",
                    severity="warning",
                    title="Opposition forte au conseil",
                    detail="Opposition marquée de : " + ", ".join(strong_opposition),
                )
            )
        if completeness < 0.5:
            out.insights.append(
                Insight(
                    kind="weakness",
                    severity="warning",
                    title="Débat sous-informé",
                    detail=f"Seulement {completeness:.0%} des faits renseignés.",
                )
            )
        out.recommendations.append(
            Recommendation(
                title=f"{verdict.capitalize()} : {data.proposal}",
                rationale=out.summary
                + (
                    " Conditions : " + " | ".join(conditions[:3])
                    if conditions and verdict == "adopter sous conditions"
                    else ""
                ),
                impact=6,
                effort=2,
                confidence=confidence,
                urgency=5,
                risk=round(max(0.05, min(0.9, 0.5 - vote / 2)), 3),
                reversible=(facts.reversibility or 0.5) >= 0.5,
                estimated_value=facts.expected_value,
                estimated_cost=facts.cost,
                tags=["conseil", "débat"],
            )
        )
        return out

    @action("Recommander la meilleure décision : comparaison multicritère puis débat sur le gagnant")
    def recommend(self, ctx: SkillContext, data: RecommendInput) -> RecommendOutput:
        comparison = self.compare_proposals(
            ctx, CompareInput.model_validate(data.model_dump(exclude={"facts"}))
        )
        verdict: str | None = None
        confidence = comparison.recommendations[0].confidence
        text = f"Retenir « {comparison.winner} » (stabilité {comparison.stability:.0%})."
        out = RecommendOutput(
            winner=comparison.winner,
            comparison=comparison,
            debate_verdict=None,
            recommendation=text,
            confidence=confidence,
            summary=text,
        )
        if data.facts is not None:
            debate = self.debate(ctx, DebateInput(proposal=f"Retenir {comparison.winner}", facts=data.facts))
            verdict = debate.verdict
            confidence = round((confidence + debate.confidence) / 2, 3)
            if debate.verdict == "rejeter":
                text = (
                    f"« {comparison.winner} » est la meilleure proposition sur les critères, mais le conseil "
                    f"la rejette : {debate.summary}"
                )
            elif debate.verdict == "adopter sous conditions":
                text = f"Retenir « {comparison.winner} » sous conditions : " + "; ".join(
                    debate.conditions[:3]
                )
            else:
                text = f"Retenir « {comparison.winner} » : critères et conseil concordent."
            out.insights.extend(debate.insights)
        out.debate_verdict = verdict
        out.recommendation = text
        out.confidence = confidence
        out.summary = text
        out.insights.extend(comparison.insights)
        out.recommendations.append(
            Recommendation(
                title=f"Décision : {comparison.winner}"
                if verdict != "rejeter"
                else f"Ne pas engager « {comparison.winner} » en l'état",
                rationale=text,
                impact=6,
                effort=2,
                confidence=confidence,
                urgency=5,
                risk=0.15 if verdict != "rejeter" else 0.5,
                tags=["décision", "recommandation"],
            )
        )
        return out


SKILL = NegotiationStrategySkill
