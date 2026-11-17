"""SKILL 9 — INNOVATION ET CRÉATIVITÉ.

Explore de nouveaux concepts (analyse morphologique), imagine des produits originaux (SCAMPER), combine des
technologies existantes, identifie les possibilités inexploitées (espaces blancs segment × besoin) et évalue
la
faisabilité des idées (TELOS : technique, économique, légal, opérationnel, calendrier).
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sovereign.core.ids import stable_id
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

STOPWORDS = {
    "avec",
    "pour",
    "dans",
    "plus",
    "moins",
    "sans",
    "leur",
    "leurs",
    "nous",
    "vous",
    "elle",
    "elles",
    "sont",
    "être",
    "avoir",
    "faire",
    "cette",
    "ces",
    "des",
    "les",
    "une",
    "aux",
    "par",
    "sur",
    "que",
    "qui",
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "their",
    "your",
    "more",
    "less",
}


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).lower().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _tokens(text: str) -> set[str]:
    """Racines simples (≥ 4 lettres, tronquées à 6) pour rapprocher besoins, options et technologies."""

    words = re.findall(r"[a-z0-9]+", _norm(text))
    return {w[:6] for w in words if len(w) >= 4 and w not in STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


# -- modèles de l'espace de travail ---------------------------------------------------------------------
class Offering(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    owner: Literal["us", "competitor"] = "competitor"
    segment: str
    needs: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    business_model: str | None = None

    def attribute_set(self) -> set[str]:
        values = {self.segment, *self.technologies, *self.needs}
        if self.business_model:
            values.add(self.business_model)
        return {_norm(v) for v in values if v}


class NeedRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    statement: str
    segment: str | None = None
    importance: float = Field(5.0, ge=1, le=10)
    satisfaction: float = Field(5.0, ge=1, le=10)


class SegmentRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    size: int = Field(1, ge=0)


class Idea(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str | None = None
    components: dict[str, str] = Field(default_factory=dict)
    technical_complexity: float = Field(ge=1, le=10)
    required_skills: list[str] = Field(default_factory=list)
    estimated_cost: Decimal = Field(ge=0)
    time_to_market_months: float = Field(ge=0)
    regulatory_risk: float = Field(ge=0, le=1)
    market_evidence: float = Field(ge=0, le=1)
    status: str | None = None


# -- entrées / sorties -------------------------------------------------------------------------------------
class ExploreInput(BaseModel):
    dimensions: dict[str, list[str]] = Field(
        default_factory=dict, description="Vide ⇒ innovation_space du store"
    )
    incompatible: list[list[str]] = Field(default_factory=list, description="Paires d'options incompatibles")
    space_id: str | None = None
    max_combinations: int = Field(500, ge=1, le=20000)
    top: int = Field(10, ge=1, le=100)


class Concept(BaseModel):
    id: str
    label: str
    components: dict[str, str]
    novelty: float
    need_coverage: float
    score: float
    closest_offering: str | None


class ExploreOutput(SkillOutput):
    total_combinations: int
    evaluated: int
    excluded_incompatible: int
    sampled: bool
    concepts: list[Concept]


class ScamperInput(BaseModel):
    product: str
    components: list[str] = Field(default_factory=list)
    audience: str | None = None
    channel: str | None = None
    business_model: str | None = None
    use_llm: bool = True


class ScamperIdea(BaseModel):
    operator: Literal[
        "Substituer", "Combiner", "Adapter", "Modifier", "Proposer un autre usage", "Éliminer", "Inverser"
    ]
    target: str
    idea: str


class _LLMScamper(BaseModel):
    ideas: list[ScamperIdea]


class ScamperOutput(SkillOutput):
    ideas: list[ScamperIdea]
    source: Literal["déterministe", "déterministe + llm"]


class WhiteSpacesInput(BaseModel):
    segments: list[SegmentRef] = Field(default_factory=list, description="Vide ⇒ segments du store")
    needs: list[NeedRef] = Field(default_factory=list, description="Vide ⇒ besoins du store")
    offerings: list[Offering] = Field(default_factory=list, description="Vide ⇒ offres du store")
    top: int = Field(10, ge=1, le=200)


class WhiteSpace(BaseModel):
    segment: str
    need: str
    importance: float
    segment_size: int
    coverage: Literal["personne", "concurrents seulement"]
    weight: float


class WhiteSpacesOutput(SkillOutput):
    cells_total: int
    covered_by_us: int
    covered_by_competitors_only: int
    uncovered: int
    white_spaces: list[WhiteSpace]


class CombineInput(BaseModel):
    technologies: list[str] = Field(default_factory=list, description="Vide ⇒ assets « technology » du store")
    needs: list[str] = Field(default_factory=list, description="Vide ⇒ besoins du store")
    max_size: Literal[2, 3] = 2
    top: int = Field(10, ge=1, le=100)


class TechCombination(BaseModel):
    technologies: list[str]
    novelty: float
    need: str | None
    use_case: str
    score: float


class CombineOutput(SkillOutput):
    candidates: int
    already_combined: int
    combinations: list[TechCombination]


class FeasibilityInput(BaseModel):
    ideas: list[Idea] = Field(default_factory=list, description="Vide ⇒ idées du store")
    potential_values: dict[str, Decimal] = Field(
        default_factory=dict, description="Valeur annuelle attendue par id d'idée (optionnel)"
    )
    persist: bool = Field(False, description="Écrire le résultat dans l'idée (kind 'idea')")


class FeasibilityResult(BaseModel):
    idea_id: str
    title: str
    technical: float
    economic: float
    legal: float
    operational: float
    schedule: float
    overall: float
    verdict: Literal["go", "explorer", "no-go"]
    missing_skills: list[str]
    blocking_factors: list[str]


class FeasibilityOutput(SkillOutput):
    available_budget: Decimal
    capabilities_known: bool
    evaluations: list[FeasibilityResult]


class AssessOutput(SkillOutput):
    spaces: int
    offerings: int
    ideas: int
    white_spaces: int
    concepts: int


# -- chargements -----------------------------------------------------------------------------------------
def _load(ctx: SkillContext, kind: str, model: type[BaseModel]) -> list:
    items = []
    for raw in ctx.store.list(kind):
        try:
            items.append(model.model_validate(raw))
        except ValidationError as exc:
            raise SkillError(f"enregistrement « {kind} » invalide ({raw.get('id')}) : {exc}") from exc
    return items


def _need_matches(offering: Offering, need: NeedRef) -> bool:
    keys = {_norm(n) for n in offering.needs}
    return _norm(need.id) in keys or _norm(need.statement) in keys


def _segment_matches(value: str | None, segment: SegmentRef) -> bool:
    if value is None:
        return True
    return _norm(value) in {_norm(segment.id), _norm(segment.name)}


REVERSALS = {
    "abonnement": "paiement à l'usage",
    "subscription": "paiement à l'usage",
    "usage": "forfait illimité",
    "ponctuel": "abonnement",
    "one_off": "abonnement",
    "vente": "location",
    "commission": "abonnement fixe",
    "freemium": "essai payant premium",
    "b2c": "b2b",
    "b2b": "b2c",
}


class InnovationCreativitySkill(Skill):
    name = "innovation-creativity"
    version = "1.0.0"

    # -- diagnostic ------------------------------------------------------------------------------------
    @action("Panorama innovation : espaces blancs, concepts, combinaisons, faisabilité des idées (directeur)")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        spaces = ctx.store.list("innovation_space")
        offerings = _load(ctx, "offering", Offering)
        ideas = _load(ctx, "idea", Idea)
        out = AssessOutput(
            spaces=len(spaces), offerings=len(offerings), ideas=len(ideas), white_spaces=0, concepts=0
        )
        parts: list[str] = []
        if ctx.store.count("segment") and ctx.store.count("need"):
            spaces_out = self.find_white_spaces(ctx, WhiteSpacesInput(top=5))
            out.white_spaces = len(spaces_out.white_spaces)
            out.insights.extend(spaces_out.insights)
            out.recommendations.extend(spaces_out.recommendations)
            out.metrics.update(spaces_out.metrics)
            parts.append(spaces_out.summary)
        if spaces:
            try:
                explored = self.explore_concepts(ctx, ExploreInput(top=5))
            except SkillError as exc:
                out.insights.append(
                    Insight(kind="observation", title="Espace d'innovation inexploitable", detail=str(exc))
                )
            else:
                out.concepts = len(explored.concepts)
                out.insights.extend(explored.insights)
                out.recommendations.extend(explored.recommendations)
                out.metrics.update(explored.metrics)
                parts.append(explored.summary)
        technologies = [a["name"] for a in ctx.store.list("asset") if a.get("kind") == "technology"]
        if len(technologies) >= 2:
            combined = self.combine_technologies(ctx, CombineInput(top=5))
            out.insights.extend(combined.insights)
            out.metrics.update(combined.metrics)
            parts.append(combined.summary)
        if ideas:
            feasibility = self.evaluate_feasibility(ctx, FeasibilityInput())
            out.insights.extend(feasibility.insights)
            out.recommendations.extend(feasibility.recommendations)
            out.metrics.update(feasibility.metrics)
            parts.append(feasibility.summary)
        if not parts:
            out.summary = (
                "Aucune donnée d'innovation : importez des « segment » et « need » (espaces blancs), un "
                "« innovation_space » (dimensions à combiner), des « offering » (offres existantes), "
                "des « asset » "
                "de type technology et des « idea » (faisabilité)."
            )
        else:
            out.summary = " ".join(parts)
        return out

    # -- analyse morphologique -------------------------------------------------------------------------
    @action("Explorer de nouveaux concepts par analyse morphologique (nouveauté × besoins couverts)")
    def explore_concepts(self, ctx: SkillContext, data: ExploreInput) -> ExploreOutput:
        dimensions, incompatible = data.dimensions, data.incompatible
        if not dimensions:
            spaces = ctx.store.list("innovation_space")
            space = next((s for s in spaces if data.space_id in (None, s.get("id"))), None)
            if space is None:
                raise SkillError(
                    "aucune dimension fournie et aucun « innovation_space » dans l'espace de travail"
                )
            dimensions = space.get("dimensions", {})
            incompatible = incompatible or space.get("incompatible", [])
        names = [d for d, options in dimensions.items() if options]
        if not names:
            raise SkillError("toutes les dimensions sont vides")
        options = [list(dict.fromkeys(dimensions[d])) for d in names]
        total = math.prod(len(o) for o in options)
        forbidden = [{_norm(a) for a in pair} for pair in incompatible if len(pair) >= 2]
        offerings = _load(ctx, "offering", Offering)
        important_needs = [n for n in _load(ctx, "need", NeedRef) if n.importance >= 7]
        sampled = total > data.max_combinations
        indices = (
            [i * total // data.max_combinations for i in range(data.max_combinations)]
            if sampled
            else range(total)
        )
        concepts: list[Concept] = []
        excluded = 0
        for index in indices:
            combo: list[str] = []
            rest = index
            for opts in reversed(options):
                combo.append(opts[rest % len(opts)])
                rest //= len(opts)
            combo.reverse()
            values = {_norm(v) for v in combo}
            if any(pair <= values for pair in forbidden):
                excluded += 1
                continue
            best, closest = 0.0, None
            for offering in offerings:
                similarity = _jaccard(values, offering.attribute_set())
                if similarity > best:
                    best, closest = similarity, offering.name
            novelty = round(1.0 - best, 4)
            concept_tokens = set().union(*(_tokens(v) for v in combo))
            if important_needs:
                covered = [n for n in important_needs if _tokens(n.statement) & concept_tokens]
                coverage = round(len(covered) / len(important_needs), 4)
            else:
                coverage = 0.5
            score = round(0.6 * novelty + 0.4 * coverage, 4)
            components = dict(zip(names, combo, strict=True))
            concepts.append(
                Concept(
                    id=stable_id("cpt", *combo),
                    label=" + ".join(combo),
                    components=components,
                    novelty=novelty,
                    need_coverage=coverage,
                    score=score,
                    closest_offering=closest,
                )
            )
        concepts.sort(key=lambda c: (-c.score, c.label))
        top = concepts[: data.top]
        out = ExploreOutput(
            total_combinations=total,
            evaluated=len(concepts) + excluded,
            excluded_incompatible=excluded,
            sampled=sampled,
            concepts=top,
        )
        out.metrics["innovation.concepts_evaluated"] = float(len(concepts))
        if top:
            out.metrics["innovation.best_concept_score"] = top[0].score
        for concept in top[:3]:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title=f"Concept prometteur : {concept.label}",
                    detail=(
                        f"Nouveauté {concept.novelty:.0%} "
                        f"(plus proche : {concept.closest_offering or 'aucune'}), "
                        f"besoins importants couverts {concept.need_coverage:.0%}."
                    ),
                    value=concept.score,
                )
            )
        if top and top[0].score >= 0.6:
            best = top[0]
            out.recommendations.append(
                Recommendation(
                    title=f"Formaliser et tester le concept « {best.label} »",
                    rationale=(
                        "Meilleur compromis nouveauté / besoins importants de l'exploration morphologique."
                    ),
                    impact=round(min(10.0, 3 + 7 * best.score), 2),
                    effort=4,
                    confidence=0.4 + 0.3 * best.need_coverage,
                    urgency=4,
                    risk=0.25,
                    tags=["innovation", "concept"],
                    evidence=[f"score={best.score}", f"nouveauté={best.novelty}"],
                )
            )
        mode = f"échantillon de {data.max_combinations}" if sampled else "exploration complète"
        out.summary = (
            f"{total} combinaisons possibles ({mode}), {excluded} incompatibles exclues, "
            f"{len(top)} concept(s) retenu(s)"
            + ("." if important_needs else " (aucun besoin important connu : couverture neutre).")
        )
        return out

    # -- SCAMPER ---------------------------------------------------------------------------------------
    @action("Imaginer des variantes originales d'un produit avec SCAMPER (enrichissement LLM optionnel)")
    def scamper(self, ctx: SkillContext, data: ScamperInput) -> ScamperOutput:
        product = data.product.strip()
        if not product:
            raise SkillError("nom de produit requis")
        comps = [c for c in data.components if c.strip()]
        audience = data.audience or "les clients actuels"
        ideas: list[ScamperIdea] = []

        def add(operator: str, target: str, idea: str) -> None:
            ideas.append(ScamperIdea(operator=operator, target=target, idea=idea))  # type: ignore[arg-type]

        for comp in comps[:2]:
            add(
                "Substituer",
                comp,
                f"Remplacer « {comp} » de {product} par une alternative moins coûteuse, "
                "plus rapide ou plus durable.",
            )
        add("Substituer", "public", f"Proposer {product} à un autre public que {audience}.")
        add(
            "Combiner",
            "offre",
            f"Associer {product} à un service complémentaire pour {audience} (offre groupée).",
        )
        if len(comps) >= 2:
            add(
                "Combiner",
                f"{comps[0]} + {comps[1]}",
                f"Fusionner « {comps[0]} » et « {comps[1]} » en une seule étape pour réduire la friction.",
            )
        other_channel = (
            "la vente directe" if data.channel and "ligne" in _norm(data.channel) else "un canal en ligne"
        )
        add(
            "Adapter",
            "canal",
            f"Adapter {product} à {other_channel}"
            + (f" (aujourd'hui : {data.channel})." if data.channel else "."),
        )
        add(
            "Adapter",
            "contexte",
            f"S'inspirer d'un secteur voisin ayant résolu le même problème que {product}.",
        )
        if comps:
            add(
                "Modifier", comps[0], f"Amplifier « {comps[0]} » pour créer une version premium de {product}."
            )
        add("Modifier", "format", f"Réduire {product} à l'essentiel : version « lite » à prix d'entrée.")
        add(
            "Proposer un autre usage", "marché", f"Utiliser {product} dans un contexte B2B ou institutionnel."
        )
        if comps:
            add(
                "Éliminer",
                comps[-1],
                f"Version de {product} sans « {comps[-1]} » : moins chère, plus simple.",
            )
        else:
            add("Éliminer", "étapes", f"Supprimer l'étape la moins valorisée du parcours de {product}.")
        if data.business_model:
            reversed_model = REVERSALS.get(_norm(data.business_model), "un modèle inverse (usage ↔ forfait)")
            add(
                "Inverser",
                "modèle économique",
                f"Passer de « {data.business_model} » à « {reversed_model} ».",
            )
        add(
            "Inverser",
            "rôle du client",
            f"Faire co-créer {product} par {audience} (communauté, contributions).",
        )
        source: Literal["déterministe", "déterministe + llm"] = "déterministe"
        if data.use_llm and ctx.llm:
            prompt = (
                f"Produit : {product}\nComposants : {', '.join(comps) or 'non précisés'}\n"
                f"Public : {audience}\n"
                f"Canal : {data.channel or 'non précisé'}\nModèle : {data.business_model or 'non précisé'}\n"
                "Propose une idée concrète et originale par opérateur SCAMPER."
            )
            extra = ctx.llm.structured(
                "Tu es un expert en innovation produit. Réponds en français, idées concrètes et réalisables.",
                prompt,
                _LLMScamper,
                purpose="SCAMPER",
                max_tokens=2000,
            )
            if extra is not None:
                ideas.extend(extra.ideas)
                source = "déterministe + llm"
        out = ScamperOutput(
            ideas=ideas, source=source, summary=f"{len(ideas)} idée(s) SCAMPER pour {product} ({source})."
        )
        out.metrics["innovation.scamper_ideas"] = float(len(ideas))
        return out

    # -- espaces blancs --------------------------------------------------------------------------------
    @action("Identifier les possibilités inexploitées : cellules segment × besoin non couvertes")
    def find_white_spaces(self, ctx: SkillContext, data: WhiteSpacesInput) -> WhiteSpacesOutput:
        segments = data.segments or _load(ctx, "segment", SegmentRef)
        needs = data.needs or _load(ctx, "need", NeedRef)
        offerings = data.offerings or _load(ctx, "offering", Offering)
        if not segments or not needs:
            out = WhiteSpacesOutput(
                cells_total=0, covered_by_us=0, covered_by_competitors_only=0, uncovered=0, white_spaces=[]
            )
            out.summary = "Segments et besoins requis pour cartographier les espaces blancs."
            return out
        max_size = max((s.size for s in segments), default=1) or 1
        cells = 0
        ours = competitors_only = nobody = 0
        spaces: list[WhiteSpace] = []
        for segment in segments:
            for need in needs:
                if not _segment_matches(need.segment, segment):
                    continue
                cells += 1
                covering = [
                    o for o in offerings if _segment_matches(o.segment, segment) and _need_matches(o, need)
                ]
                if any(o.owner == "us" for o in covering):
                    ours += 1
                    continue
                coverage: Literal["personne", "concurrents seulement"]
                if covering:
                    competitors_only += 1
                    coverage, factor = "concurrents seulement", 0.6
                else:
                    nobody += 1
                    coverage, factor = "personne", 1.0
                weight = round(need.importance / 10 * (segment.size / max_size) * factor, 4)
                spaces.append(
                    WhiteSpace(
                        segment=segment.name,
                        need=need.statement,
                        importance=need.importance,
                        segment_size=segment.size,
                        coverage=coverage,
                        weight=weight,
                    )
                )
        spaces.sort(key=lambda w: (-w.weight, w.segment, w.need))
        top = spaces[: data.top]
        out = WhiteSpacesOutput(
            cells_total=cells,
            covered_by_us=ours,
            covered_by_competitors_only=competitors_only,
            uncovered=nobody,
            white_spaces=top,
        )
        out.metrics["innovation.white_spaces"] = float(len(spaces))
        if cells:
            out.metrics["innovation.coverage_ratio"] = round(ours / cells, 4)
        for space in top[:3]:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title=f"Espace blanc : « {space.need} » pour {space.segment}",
                    detail=(
                        f"Couvert par : {space.coverage} ; importance {space.importance}/10, "
                        f"taille {space.segment_size}."
                    ),
                    value=space.weight,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Explorer une offre « {space.need} » pour le segment {space.segment}",
                    rationale=(
                        "Aucun acteur ne couvre ce besoin sur ce segment."
                        if space.coverage == "personne"
                        else "Seuls des concurrents couvrent ce besoin : nous en sommes absents."
                    ),
                    impact=round(min(10.0, 2 + 8 * space.weight), 2),
                    effort=6,
                    confidence=0.45 if space.coverage == "personne" else 0.55,
                    urgency=4,
                    risk=0.3 if space.coverage == "personne" else 0.25,
                    tags=["innovation", "espace-blanc"],
                    evidence=[f"poids={space.weight}", f"importance={space.importance}"],
                )
            )
        out.summary = (
            f"{cells} cellule(s) segment × besoin : {ours} couverte(s) par nous, {competitors_only} "
            f"uniquement par des concurrents, {nobody} par personne."
        )
        return out

    # -- combinaison de technologies -------------------------------------------------------------------
    @action("Combiner des technologies existantes non encore associées pour répondre à un besoin")
    def combine_technologies(self, ctx: SkillContext, data: CombineInput) -> CombineOutput:
        technologies = data.technologies or [
            a["name"] for a in ctx.store.list("asset") if a.get("kind") == "technology"
        ]
        technologies = list(dict.fromkeys(t for t in technologies if t.strip()))
        if len(technologies) < 2:
            out = CombineOutput(candidates=0, already_combined=0, combinations=[])
            out.summary = "Au moins deux technologies sont nécessaires (entrée ou assets « technology »)."
            return out
        if data.needs:
            needs = [(n, 5.0) for n in data.needs]
        else:
            stored = sorted(_load(ctx, "need", NeedRef), key=lambda n: -n.importance)
            needs = [(n.statement, n.importance) for n in stored]
        offerings = _load(ctx, "offering", Offering)
        existing = [{_norm(t) for t in o.technologies} for o in offerings]
        combos: list[TechCombination] = []
        candidates = already = 0
        for size in range(2, data.max_size + 1):
            for group in itertools.combinations(technologies, size):
                candidates += 1
                normalized = {_norm(t) for t in group}
                if any(normalized <= techs for techs in existing):
                    already += 1
                    continue
                overlap = max((len(normalized & techs) / len(normalized) for techs in existing), default=0.0)
                novelty = round(1.0 - overlap / 2, 4)
                tokens = set().union(*(_tokens(t) for t in group))
                need, importance = None, 0.0
                if needs:
                    need, importance = max(needs, key=lambda n: (len(_tokens(n[0]) & tokens), n[1]))
                label = " + ".join(group)
                use_case = (
                    f"Combiner {label} pour répondre à « {need} »."
                    if need
                    else f"Combiner {label} dans une offre inédite."
                )
                score = round(0.7 * novelty + 0.3 * importance / 10, 4)
                combos.append(
                    TechCombination(
                        technologies=list(group), novelty=novelty, need=need, use_case=use_case, score=score
                    )
                )
        combos.sort(key=lambda c: (-c.score, c.technologies))
        top = combos[: data.top]
        out = CombineOutput(candidates=candidates, already_combined=already, combinations=top)
        out.metrics["innovation.tech_combinations"] = float(len(combos))
        for combo in top[:3]:
            out.insights.append(
                Insight(
                    kind="opportunity",
                    title=f"Combinaison inexploitée : {' + '.join(combo.technologies)}",
                    detail=combo.use_case,
                    value=combo.score,
                )
            )
        out.summary = (
            f"{candidates} combinaison(s) examinée(s), {already} déjà présente(s) dans les offres, "
            f"{len(top)} proposée(s)."
        )
        return out

    # -- faisabilité -----------------------------------------------------------------------------------
    @action(
        "Évaluer la faisabilité des idées (technique, économique, légal, opérationnel, calendrier)",
        risk="write",
    )
    def evaluate_feasibility(self, ctx: SkillContext, data: FeasibilityInput) -> FeasibilityOutput:
        ideas = data.ideas or _load(ctx, "idea", Idea)
        available = ctx.finance.available()
        capabilities = {_norm(c) for agent in ctx.store.list("agent") for c in agent.get("capabilities", [])}
        known = bool(capabilities)
        results: list[FeasibilityResult] = []
        for idea in ideas:
            required = [s for s in idea.required_skills if s.strip()]
            missing = [s for s in required if _norm(s) not in capabilities] if known else []
            technical = max(0.05, 1 - idea.technical_complexity / 10)
            if known:
                operational = max(0.05, 1 - len(missing) / len(required)) if required else 1.0
            else:
                operational = 0.6 if required else 1.0
            cost = to_decimal(idea.estimated_cost)
            if cost <= 0 or cost <= available:
                affordability = 1.0
            else:
                affordability = max(0.05, float(max(available, Decimal("0")) / cost))
            factors = [affordability, max(0.05, idea.market_evidence)]
            potential = data.potential_values.get(idea.id)
            if potential is not None:
                ratio = float(to_decimal(potential) / cost) if cost > 0 else 3.0
                factors.append(min(1.0, max(0.05, ratio / 3)))
            economic = math.prod(factors) ** (1 / len(factors))
            legal = max(0.05, 1 - idea.regulatory_risk)
            schedule = 1.0 if idea.time_to_market_months <= 6 else max(0.05, 6 / idea.time_to_market_months)
            dims = {
                "technique": technical,
                "économique": economic,
                "légal": legal,
                "opérationnel": operational,
                "calendrier": schedule,
            }
            overall = math.prod(dims.values()) ** (1 / len(dims))
            verdict: Literal["go", "explorer", "no-go"] = (
                "go" if overall >= 0.7 else "explorer" if overall >= 0.45 else "no-go"
            )
            blocking: list[str] = []
            if technical < 0.4:
                blocking.append(f"complexité technique élevée ({idea.technical_complexity}/10)")
            if affordability < 1:
                blocking.append(f"coût {cost} > budget disponible {available}")
            if idea.market_evidence < 0.4:
                blocking.append(f"preuves de marché faibles ({idea.market_evidence:.0%})")
            if legal < 0.4:
                blocking.append(f"risque réglementaire élevé ({idea.regulatory_risk:.0%})")
            if missing and operational < 0.4:
                blocking.append("compétences manquantes : " + ", ".join(missing))
            if schedule < 0.4:
                blocking.append(f"délai de mise sur le marché long ({idea.time_to_market_months} mois)")
            result = FeasibilityResult(
                idea_id=idea.id,
                title=idea.title,
                technical=round(technical, 4),
                economic=round(economic, 4),
                legal=round(legal, 4),
                operational=round(operational, 4),
                schedule=round(schedule, 4),
                overall=round(overall, 4),
                verdict=verdict,
                missing_skills=missing,
                blocking_factors=blocking,
            )
            results.append(result)
            if data.persist:
                raw = ctx.store.get("idea", idea.id) or idea.model_dump(mode="json")
                raw["feasibility"] = result.model_dump(mode="json")
                raw["status"] = "evaluated"
                ctx.store.put("idea", idea.id, raw)
        results.sort(key=lambda r: -r.overall)
        out = FeasibilityOutput(available_budget=available, capabilities_known=known, evaluations=results)
        if results:
            out.metrics["innovation.ideas_go"] = float(sum(1 for r in results if r.verdict == "go"))
            out.metrics["innovation.feasibility_avg"] = round(
                sum(r.overall for r in results) / len(results), 4
            )
        for result in results:
            idea = next(i for i in ideas if i.id == result.idea_id)
            if result.verdict == "go":
                out.recommendations.append(
                    Recommendation(
                        title=f"Lancer un prototype de « {result.title} »",
                        rationale=(
                            f"Faisabilité globale {result.overall:.0%} "
                            "(moyenne géométrique des 5 dimensions TELOS)."
                        ),
                        impact=round(min(10.0, 3 + 6 * idea.market_evidence), 2),
                        effort=round(min(10.0, max(0.5, idea.technical_complexity)), 2),
                        confidence=round(min(0.9, result.overall), 3),
                        urgency=5,
                        risk=round(1 - result.overall, 3),
                        estimated_cost=to_decimal(idea.estimated_cost),
                        estimated_value=to_decimal(data.potential_values[idea.id])
                        if idea.id in data.potential_values
                        else None,
                        horizon_days=int(idea.time_to_market_months * 30),
                        tags=["innovation", "prototype"],
                        evidence=[
                            f"{k}={getattr(result, k)}"
                            for k in ("technical", "economic", "legal", "operational", "schedule")
                        ],
                    )
                )
            elif result.verdict == "no-go":
                out.insights.append(
                    Insight(
                        kind="risk",
                        severity="warning",
                        title=f"Idée non faisable : {result.title}",
                        detail="; ".join(result.blocking_factors) or "score global trop faible",
                        value=result.overall,
                    )
                )
            else:
                out.insights.append(
                    Insight(
                        kind="observation",
                        title=f"Idée à explorer : {result.title}",
                        detail="; ".join(result.blocking_factors) or "lever les incertitudes",
                        value=result.overall,
                    )
                )
        counts = {v: sum(1 for r in results if r.verdict == v) for v in ("go", "explorer", "no-go")}
        out.summary = (
            f"{len(results)} idée(s) évaluée(s) : {counts['go']} go, {counts['explorer']} à explorer, "
            f"{counts['no-go']} no-go (budget disponible {available})."
            + ("" if known else " Capacités des agents inconnues : faisabilité opérationnelle estimée.")
        )
        return out


SKILL = InnovationCreativitySkill
