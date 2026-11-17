"""DÉCIDER : le conseil des IA (CEO Agent + Risk Agent + direction élargie).

Chaque membre évalue une proposition depuis son angle, à partir de FAITS chiffrés (simulation, budget,
historique des résultats, capacité d'exécution) — pas d'opinions gratuites :

    CEO     croissance, impact, valeur, alignement stratégique
    RISQUE  probabilité de perte, réversibilité, actions externes, confiance — DROIT DE VETO
    CFO     ROI, budget disponible (règle des 10 %) — DROIT DE VETO budgétaire
    CMO     effet client / marché, preuves de marché
    COO     effort, capacité d'exécution des équipes d'agents
    MÉMOIRE taux de succès passé des décisions similaires

Déroulé : déclarations d'ouverture → réfutations (chaque membre répond à l'argument opposé le plus fort ;
s'il ne peut le contrer, il nuance sa position) → vote pondéré par la pertinence du sujet pour chaque membre →
verdict. Un veto (budget dépassé ; perte probable > 50 % sauf pari réversible à espérance positive ; irréversible et
incertain) bloque l'adoption.

Si la passerelle LLM est disponible et autorisée, Claude peut reformuler et compléter les arguments ; le score,
les vetos et le verdict restent calculés de façon déterministe et auditable.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from sovereign.skills.base import Recommendation

Stance = Literal["soutien", "conditionnel", "opposition"]
Verdict = Literal["adopter", "adopter_sous_conditions", "reporter", "rejeter"]


class Argument(BaseModel):
    text: str
    weight: float = Field(ge=0, le=1)
    polarity: Literal["pour", "contre"]


class Position(BaseModel):
    member: str
    role: str
    score: float = Field(ge=-1, le=1)
    stance: Stance
    relevance: float
    arguments: list[Argument]
    veto: str | None = None
    rebuttal: str | None = None


class CouncilFacts(BaseModel):
    """Faits mis à disposition du conseil pour UNE proposition."""

    expected_value: float | None = None
    p_success: float | None = None
    p_loss: float | None = None
    unit: str = "impact_points"
    budget_available: Decimal = Decimal("0")
    budget_utilization: float = 0.0
    past_success_rate: float | None = None
    past_samples: int = 0
    team_load: float | None = Field(None, description="Charge moyenne des agents (0-1+)")
    strategic_fit: float = Field(0.5, ge=0, le=1)


class CouncilVerdict(BaseModel):
    recommendation_id: str
    title: str
    verdict: Verdict
    score: float
    confidence: float
    vetoes: list[str]
    positions: list[Position]
    conditions: list[str]
    minutes: list[str]
    llm_enriched: bool = False


def _stance(score: float) -> Stance:
    if score >= 0.2:
        return "soutien"
    if score <= -0.2:
        return "opposition"
    return "conditionnel"


def _score(arguments: list[Argument]) -> float:
    pro = sum(a.weight for a in arguments if a.polarity == "pour")
    con = sum(a.weight for a in arguments if a.polarity == "contre")
    total = pro + con
    return 0.0 if total == 0 else round((pro - con) / total, 4)


def _has_tag(rec: Recommendation, *words: str) -> bool:
    text = " ".join([*rec.tags, rec.title, rec.skill]).lower()
    return any(w in text for w in words)


class CouncilMember:
    name = ""
    role = ""

    def relevance(self, rec: Recommendation) -> float:
        return 1.0

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        raise NotImplementedError


class CEO(CouncilMember):
    name, role = "CEO", "Directeur général — croissance et valeur"

    def relevance(self, rec: Recommendation) -> float:
        return 1.2

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        args: list[Argument] = []
        if rec.impact >= 7:
            args.append(Argument(text=f"Impact business élevé ({rec.impact:.0f}/10).", weight=0.8, polarity="pour"))
        elif rec.impact <= 3:
            args.append(
                Argument(
                    text=f"Impact limité ({rec.impact:.0f}/10) : faible levier.",
                    weight=0.5,
                    polarity="contre",
                )
            )
        if facts.expected_value is not None:
            if facts.expected_value > 0:
                args.append(
                    Argument(
                        text=f"Espérance de gain positive ({facts.expected_value:.2f} {facts.unit}).",
                        weight=0.7,
                        polarity="pour",
                    )
                )
            else:
                args.append(Argument(text="Espérance de gain nulle ou négative.", weight=0.8, polarity="contre"))
        if facts.strategic_fit >= 0.6:
            args.append(Argument(text="Aligné avec les objectifs stratégiques.", weight=0.6, polarity="pour"))
        elif facts.strategic_fit <= 0.3:
            args.append(Argument(text="Peu aligné avec les objectifs déclarés.", weight=0.4, polarity="contre"))
        if rec.urgency >= 8:
            args.append(Argument(text="Fenêtre d'action courte : attendre coûte.", weight=0.5, polarity="pour"))
        return args, None


class RiskOfficer(CouncilMember):
    name, role = "RISQUE", "Directeur des risques — protection du capital"

    def relevance(self, rec: Recommendation) -> float:
        return 1.2

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        args: list[Argument] = []
        veto: str | None = None
        if facts.p_loss is not None:
            positive_bet = facts.expected_value is not None and facts.expected_value > 0 and rec.reversible
            if facts.p_loss > 0.5 and not positive_bet:
                veto = f"Perte probable ({facts.p_loss:.0%} des scénarios simulés)."
            elif facts.p_loss > 0.5:
                # Un pari réversible à espérance positive se borne par un critère d'arrêt ; on ne le bloque pas.
                args.append(
                    Argument(
                        text="Pari réversible à espérance positive : acceptable avec un critère d'arrêt chiffré.",
                        weight=0.3,
                        polarity="pour",
                    )
                )
            if facts.p_loss > 0.25:
                args.append(
                    Argument(
                        text=f"{facts.p_loss:.0%} des scénarios simulés perdent de la valeur.",
                        weight=min(1.0, facts.p_loss + 0.3),
                        polarity="contre",
                    )
                )
            else:
                args.append(
                    Argument(text=f"Risque de perte contenu ({facts.p_loss:.0%}).", weight=0.5, polarity="pour")
                )
        if not rec.reversible:
            args.append(Argument(text="Décision irréversible.", weight=0.7, polarity="contre"))
            if rec.confidence < 0.5:
                veto = veto or "Irréversible avec une confiance < 50 %."
        else:
            args.append(Argument(text="Réversible : l'erreur est rattrapable.", weight=0.4, polarity="pour"))
        if rec.external:
            args.append(
                Argument(
                    text="Touche l'extérieur : validation humaine indispensable.",
                    weight=0.4,
                    polarity="contre",
                )
            )
        if rec.confidence < 0.4:
            args.append(
                Argument(
                    text=f"Confiance faible ({rec.confidence:.0%}) : données insuffisantes.",
                    weight=0.6,
                    polarity="contre",
                )
            )
        return args, veto


class CFO(CouncilMember):
    name, role = "CFO", "Directeur financier — ROI et règle des 10 %"

    def relevance(self, rec: Recommendation) -> float:
        return 1.3 if rec.estimated_cost or _has_tag(rec, "finance", "budget", "coût", "prix") else 0.8

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        args: list[Argument] = []
        veto: str | None = None
        cost = rec.estimated_cost or Decimal("0")
        if cost > 0:
            if cost > facts.budget_available:
                veto = f"Coût {cost} > budget disponible {facts.budget_available} (10 % des encaissements) : interdit."
                args.append(Argument(text="Dépasse l'enveloppe autorisée.", weight=1.0, polarity="contre"))
            elif facts.budget_available > 0 and cost > facts.budget_available * Decimal("0.5"):
                args.append(
                    Argument(text="Consomme plus de la moitié du budget disponible.", weight=0.6, polarity="contre")
                )
            else:
                args.append(Argument(text="Finançable dans l'enveloppe.", weight=0.4, polarity="pour"))
        else:
            args.append(Argument(text="Aucune dépense requise.", weight=0.5, polarity="pour"))
        if rec.estimated_value is not None and cost > 0:
            ratio = float((rec.estimated_value - cost) / cost)
            args.append(
                Argument(
                    text=f"ROI estimé {ratio:.0%}.",
                    weight=min(1.0, abs(ratio) / 2 + 0.2),
                    polarity="pour" if ratio > 0 else "contre",
                )
            )
        if facts.budget_utilization >= 0.8:
            args.append(
                Argument(
                    text=f"Budget déjà utilisé à {facts.budget_utilization:.0%}.",
                    weight=0.5,
                    polarity="contre",
                )
            )
        return args, veto


class CMO(CouncilMember):
    name, role = "CMO", "Directeur marketing — clients et marché"

    def relevance(self, rec: Recommendation) -> float:
        return 1.3 if _has_tag(rec, "marketing", "client", "campagne", "segment", "marché", "ventes") else 0.6

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        args: list[Argument] = []
        if _has_tag(rec, "client", "marché", "marketing", "ventes", "revenus", "opportunité"):
            args.append(
                Argument(text="Effet direct sur les clients ou le chiffre d'affaires.", weight=0.6, polarity="pour")
            )
        if rec.evidence:
            args.append(
                Argument(
                    text=f"Appuyé par {len(rec.evidence)} élément(s) de preuve.",
                    weight=min(0.8, 0.2 + 0.1 * len(rec.evidence)),
                    polarity="pour",
                )
            )
        else:
            args.append(Argument(text="Aucune preuve de marché fournie.", weight=0.4, polarity="contre"))
        return args, None


class COO(CouncilMember):
    name, role = "COO", "Directeur des opérations — capacité d'exécution"

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        args: list[Argument] = []
        if rec.effort >= 7:
            args.append(Argument(text=f"Effort élevé ({rec.effort:.0f}/10).", weight=0.6, polarity="contre"))
        elif rec.effort <= 3:
            args.append(Argument(text=f"Exécution légère ({rec.effort:.0f}/10).", weight=0.5, polarity="pour"))
        if facts.team_load is not None:
            if facts.team_load > 0.9:
                args.append(Argument(text=f"Équipes saturées ({facts.team_load:.0%}).", weight=0.6, polarity="contre"))
            elif facts.team_load < 0.6:
                args.append(Argument(text="Capacité disponible dans les équipes.", weight=0.4, polarity="pour"))
        return args, None


class MemoryMember(CouncilMember):
    name, role = "MÉMOIRE", "Gardien de l'expérience — résultats passés"

    def relevance(self, rec: Recommendation) -> float:
        return 1.0

    def evaluate(self, rec: Recommendation, facts: CouncilFacts) -> tuple[list[Argument], str | None]:
        if facts.past_success_rate is None or facts.past_samples == 0:
            return [
                Argument(text="Aucun précédent mesuré : décision exploratoire.", weight=0.2, polarity="contre")
            ], None
        polarity: Literal["pour", "contre"] = "pour" if facts.past_success_rate >= 0.5 else "contre"
        weight = min(1.0, abs(facts.past_success_rate - 0.5) * 2 * min(1.0, facts.past_samples / 5) + 0.2)
        return [
            Argument(
                text=f"Décisions similaires : {facts.past_success_rate:.0%} de succès sur {facts.past_samples}.",
                weight=round(weight, 3),
                polarity=polarity,
            )
        ], None


DEFAULT_MEMBERS: list[CouncilMember] = [CEO(), RiskOfficer(), CFO(), CMO(), COO(), MemoryMember()]


class LLMArguments(BaseModel):
    """Format de l'enrichissement optionnel par LLM."""

    class MemberArguments(BaseModel):
        member: str
        additional_arguments: list[str]

    members: list[MemberArguments]


class Council:
    def __init__(self, members: list[CouncilMember] | None = None, llm: Any = None) -> None:
        self.members = members or DEFAULT_MEMBERS
        self.llm = llm

    def deliberate(self, rec: Recommendation, facts: CouncilFacts, use_llm: bool = False) -> CouncilVerdict:
        minutes: list[str] = [f"Ouverture de séance : « {rec.title} »."]
        positions: list[Position] = []
        for member in self.members:
            arguments, veto = member.evaluate(rec, facts)
            score = _score(arguments)
            positions.append(
                Position(
                    member=member.name,
                    role=member.role,
                    score=score,
                    stance=_stance(score),
                    relevance=member.relevance(rec),
                    arguments=arguments,
                    veto=veto,
                )
            )
            aligned = [a for a in arguments if (a.polarity == "pour") == (score >= 0)] or arguments
            lead = max(aligned, key=lambda a: a.weight).text if aligned else "sans argument"
            minutes.append(f"{member.name} ({_stance(score)}) : {lead}")
        llm_enriched = False
        if use_llm and self.llm is not None:
            llm_enriched = self._enrich(rec, facts, positions)
        # Réfutations : chaque membre affronte l'argument opposé le plus fort des autres membres.
        for position in positions:
            own_polarity = "pour" if position.score >= 0 else "contre"
            opposing = [
                (other.member, arg)
                for other in positions
                if other is not position
                for arg in other.arguments
                if arg.polarity != own_polarity
            ]
            if not opposing:
                continue
            who, strongest = max(opposing, key=lambda x: x[1].weight)
            own_best = max((a.weight for a in position.arguments if a.polarity == own_polarity), default=0.0)
            if strongest.weight > own_best:
                position.score = round(position.score * 0.7, 4)
                position.stance = _stance(position.score)
                position.rebuttal = f"Nuance sa position face à {who} : « {strongest.text} »"
            else:
                position.rebuttal = f"Maintient sa position face à {who} : « {strongest.text} »"
            minutes.append(f"Réfutation — {position.member} : {position.rebuttal}")
        total_relevance = sum(p.relevance for p in positions)
        weighted = sum(p.score * p.relevance for p in positions) / total_relevance if total_relevance else 0.0
        spread = statistics.pstdev([p.score for p in positions]) if len(positions) > 1 else 0.0
        confidence = round(max(0.05, min(0.99, (abs(weighted) * 0.6 + (1 - spread) * 0.4) * rec.confidence + 0.2)), 3)
        vetoes = [f"{p.member} : {p.veto}" for p in positions if p.veto]
        conditions = [
            arg.text for p in positions if p.stance != "soutien" for arg in p.arguments if arg.polarity == "contre"
        ]
        if vetoes:
            budget_only = all(v.startswith("CFO") for v in vetoes)
            verdict: Verdict = "reporter" if budget_only and weighted > 0 else "rejeter"
        elif weighted >= 0.25:
            verdict = "adopter"
        elif weighted > 0:
            verdict = "adopter_sous_conditions"
        else:
            verdict = "rejeter"
        minutes.append(
            f"Vote pondéré : {weighted:+.2f} → {verdict.replace('_', ' ')}"
            + (f" (veto : {'; '.join(vetoes)})" if vetoes else "")
        )
        return CouncilVerdict(
            recommendation_id=rec.id,
            title=rec.title,
            verdict=verdict,
            score=round(weighted, 4),
            confidence=confidence,
            vetoes=vetoes,
            positions=positions,
            conditions=list(dict.fromkeys(conditions)),
            minutes=minutes,
            llm_enriched=llm_enriched,
        )

    def _enrich(self, rec: Recommendation, facts: CouncilFacts, positions: list[Position]) -> bool:
        system = (
            "Tu participes au conseil de direction d'une entreprise. Pour chaque membre listé, propose au plus deux "
            "arguments supplémentaires, factuels et spécifiques à la proposition, cohérents avec son rôle. "
            "N'invente aucun chiffre absent des faits fournis. Réponds en français."
        )
        prompt = (
            f"Proposition : {rec.title}\nJustification : {rec.rationale}\nFaits : {facts.model_dump_json()}\n"
            "Membres : " + "; ".join(f"{p.member} ({p.role}) — position {p.stance}" for p in positions)
        )
        result = self.llm.structured(
            system, prompt, LLMArguments, purpose=f"conseil : {rec.title[:60]}", max_tokens=1500
        )
        if result is None:
            return False
        by_member = {m.member.upper(): m.additional_arguments for m in result.members}
        for position in positions:
            for text in by_member.get(position.member.upper(), [])[:2]:
                polarity: Literal["pour", "contre"] = "pour" if position.score >= 0 else "contre"
                # Les arguments générés sont tracés mais à poids faible : ils éclairent, ils ne tranchent pas.
                position.arguments.append(Argument(text=f"[IA] {text}", weight=0.15, polarity=polarity))
        return True
