from __future__ import annotations

import json
import math
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sovereign.kernel import Kernel
from sovereign.skills.base import SkillError

SKILL = "innovation-creativity"
DIMENSIONS = {"techno": ["IA", "IoT"], "marché": ["PME", "santé"], "modèle": ["abonnement", "usage"]}


def test_morphological_exploration_excludes_incompatible(kernel):
    out = kernel.run_skill(SKILL, "explore_concepts", {"dimensions": DIMENSIONS, "incompatible": [["IoT", "usage"]]})
    assert out.total_combinations == 8
    assert out.excluded_incompatible == 2
    assert len(out.concepts) == 6 and not out.sampled
    assert all(not ({"IoT", "usage"} <= set(c.components.values())) for c in out.concepts)
    # Sans offre existante ni besoin : nouveauté 1, couverture neutre 0,5 → score 0,8
    assert all(c.score == pytest.approx(0.8) for c in out.concepts)


def test_novelty_uses_jaccard_against_existing_offerings(kernel):
    kernel.store.put(
        "offering",
        "o1",
        {
            "id": "o1",
            "name": "Offre actuelle",
            "owner": "us",
            "segment": "PME",
            "technologies": ["IA"],
            "business_model": "abonnement",
        },
    )
    out = kernel.run_skill(SKILL, "explore_concepts", {"dimensions": DIMENSIONS, "top": 8})
    clone = next(c for c in out.concepts if c.label == "IA + PME + abonnement")
    assert clone.novelty == 0.0 and clone.closest_offering == "Offre actuelle"
    assert out.concepts[-1].label == "IA + PME + abonnement"  # le clone de l'existant est classé dernier


def test_need_coverage_and_sampling(kernel):
    kernel.store.put(
        "need",
        "n1",
        {"id": "n1", "statement": "automatiser la facturation", "importance": 8, "satisfaction": 3},
    )
    out = kernel.run_skill(
        SKILL,
        "explore_concepts",
        {"dimensions": {"fonction": ["facturation automatique", "agenda partagé"], "cible": ["artisans"]}},
    )
    best = out.concepts[0]
    assert best.components["fonction"] == "facturation automatique" and best.need_coverage == 1.0
    big = {d: [f"{d}{i}" for i in range(10)] for d in ("a", "b", "c")}
    sampled = kernel.run_skill(SKILL, "explore_concepts", {"dimensions": big, "max_combinations": 100, "top": 5})
    assert sampled.total_combinations == 1000 and sampled.sampled and sampled.evaluated == 100


def test_explore_without_dimensions_errors(kernel):
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "explore_concepts", {})
    kernel.store.put("innovation_space", "s1", {"id": "s1", "dimensions": DIMENSIONS})
    assert kernel.run_skill(SKILL, "explore_concepts", {}).total_combinations == 8


def test_scamper_covers_seven_operators(kernel):
    out = kernel.run_skill(
        SKILL,
        "scamper",
        {
            "product": "Logiciel de devis",
            "components": ["catalogue", "signature"],
            "audience": "artisans",
            "business_model": "abonnement",
        },
    )
    operators = {i.operator for i in out.ideas}
    assert operators == {
        "Substituer",
        "Combiner",
        "Adapter",
        "Modifier",
        "Proposer un autre usage",
        "Éliminer",
        "Inverser",
    }
    assert any("paiement à l'usage" in i.idea for i in out.ideas)
    assert out.source == "déterministe"
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "scamper", {"product": "  "})


def test_scamper_optional_llm_enrichment_is_budgeted(clock):
    payload = {"ideas": [{"operator": "Combiner", "target": "offre", "idea": "Devis + financement intégré"}]}
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=500, output_tokens=300),
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response)))
    settings = Kernel.ephemeral().settings.model_copy(
        update={"llm": Kernel.ephemeral().settings.llm.model_copy(update={"enabled": True})}
    )
    kernel = Kernel(settings, clock=clock, db_path=":memory:", llm_client_factory=lambda: client)
    kernel.finance.record_collection("1000", "2026-11-01")
    out = kernel.run_skill(SKILL, "scamper", {"product": "Logiciel de devis"})
    assert out.source == "déterministe + llm"
    assert out.ideas[-1].idea == "Devis + financement intégré"
    assert kernel.finance.expenses("spent")[0].category == "ia"  # l'appel IA est une dépense tracée
    kernel.close()


def test_white_spaces_distinguish_us_competitors_nobody(kernel):
    payload = {
        "segments": [
            {"id": "s1", "name": "PME", "size": 1000},
            {"id": "s2", "name": "Grands comptes", "size": 250},
        ],
        "needs": [
            {"id": "n1", "statement": "suivi trésorerie", "importance": 8},
            {"id": "n2", "statement": "conformité RGPD", "importance": 6, "segment": "s2"},
        ],
        "offerings": [
            {"id": "o1", "name": "Notre app", "owner": "us", "segment": "PME", "needs": ["n1"]},
            {
                "id": "o2",
                "name": "Concurrent",
                "owner": "competitor",
                "segment": "Grands comptes",
                "needs": ["suivi trésorerie"],
            },
        ],
    }
    out = kernel.run_skill(SKILL, "find_white_spaces", payload)
    assert (out.cells_total, out.covered_by_us, out.covered_by_competitors_only, out.uncovered) == (
        3,
        1,
        1,
        1,
    )
    first, second = out.white_spaces
    assert first.need == "conformité RGPD" and first.coverage == "personne" and first.weight == pytest.approx(0.15)
    assert second.coverage == "concurrents seulement" and second.weight == pytest.approx(0.12)
    assert out.metrics["innovation.coverage_ratio"] == pytest.approx(1 / 3, abs=1e-4)
    assert len(out.recommendations) == 2


def test_combine_technologies_skips_existing_pairs(kernel):
    kernel.store.put("offering", "o1", {"id": "o1", "name": "OCR+IA", "segment": "PME", "technologies": ["IA", "OCR"]})
    out = kernel.run_skill(
        SKILL,
        "combine_technologies",
        {"technologies": ["IA", "OCR", "Blockchain"], "needs": ["traçabilité des factures"]},
    )
    assert (out.candidates, out.already_combined, len(out.combinations)) == (3, 1, 2)
    assert all(c.novelty == pytest.approx(0.75) for c in out.combinations)
    assert out.combinations[0].score == pytest.approx(0.675)
    trio = kernel.run_skill(SKILL, "combine_technologies", {"technologies": ["IA", "OCR", "Blockchain"], "max_size": 3})
    assert trio.candidates == 4
    assert any(c.novelty == pytest.approx(0.6667, abs=1e-4) for c in trio.combinations)
    lonely = kernel.run_skill(SKILL, "combine_technologies", {"technologies": ["IA"]})
    assert lonely.combinations == [] and "deux technologies" in lonely.summary


IDEAS = [
    {
        "id": "i1",
        "title": "Assistant IA",
        "technical_complexity": 3,
        "required_skills": ["python"],
        "estimated_cost": "300",
        "time_to_market_months": 3,
        "regulatory_risk": 0.1,
        "market_evidence": 0.8,
    },
    {
        "id": "i2",
        "title": "Plateforme ML",
        "technical_complexity": 8,
        "required_skills": ["rust", "ml"],
        "estimated_cost": "5000",
        "time_to_market_months": 18,
        "regulatory_risk": 0.6,
        "market_evidence": 0.2,
    },
]


def test_feasibility_telos_scores(funded):
    funded.store.put(
        "agent",
        "a1",
        {"id": "a1", "name": "Dev", "kind": "ai", "capabilities": ["Python", "design"], "capacity": 2},
    )
    out = funded.run_skill(SKILL, "evaluate_feasibility", {"ideas": IDEAS})
    go, nogo = out.evaluations
    assert go.idea_id == "i1" and go.verdict == "go"
    assert go.economic == pytest.approx(math.sqrt(0.8), abs=1e-4)
    assert go.overall == pytest.approx((0.7 * math.sqrt(0.8) * 0.9) ** 0.2, abs=1e-4)
    assert nogo.verdict == "no-go" and nogo.missing_skills == ["rust", "ml"]
    assert nogo.overall == pytest.approx((0.2 * 0.2 * 0.4 * 0.05 * (6 / 18)) ** 0.2, abs=1e-4)
    assert any("budget disponible" in b for b in nogo.blocking_factors)
    assert out.available_budget == Decimal("1000.00") and out.capabilities_known
    rec = out.recommendations[0]
    assert "Assistant IA" in rec.title and rec.estimated_cost == Decimal("300.00")


def test_feasibility_potential_unknown_capabilities_and_persist(funded):
    funded.store.put("idea", "i1", IDEAS[0])
    out = funded.run_skill(SKILL, "evaluate_feasibility", {"potential_values": {"i1": "1200"}, "persist": True})
    result = out.evaluations[0]
    assert result.economic == pytest.approx(0.8 ** (1 / 3), abs=1e-4)  # retour 4× ⇒ facteur plafonné à 1
    assert result.operational == 0.6 and not out.capabilities_known
    stored = funded.store.get("idea", "i1")
    assert stored["status"] == "evaluated" and stored["feasibility"]["verdict"] == result.verdict
    with pytest.raises(ValidationError):
        funded.run_skill(SKILL, "evaluate_feasibility", {"ideas": [{**IDEAS[0], "regulatory_risk": 2}]})


def test_assess_empty_then_full(funded):
    empty = funded.run_skill(SKILL, "assess")
    assert "Aucune donnée d'innovation" in empty.summary
    funded.store.put("segment", "s1", {"id": "s1", "name": "PME", "size": 500})
    funded.store.put("need", "n1", {"id": "n1", "statement": "suivi trésorerie", "importance": 9})
    funded.store.put("innovation_space", "sp", {"id": "sp", "dimensions": DIMENSIONS})
    funded.store.put("asset", "t1", {"id": "t1", "name": "IA", "kind": "technology"})
    funded.store.put("asset", "t2", {"id": "t2", "name": "OCR", "kind": "technology"})
    funded.store.put("idea", "i1", IDEAS[0])
    out = funded.run_skill(SKILL, "assess")
    assert (out.spaces, out.ideas, out.white_spaces) == (1, 1, 1)
    assert out.concepts == 5
    assert {"innovation.white_spaces", "innovation.ideas_go", "innovation.tech_combinations"} <= set(out.metrics)
    assert out.recommendations and all(r.skill == SKILL for r in out.recommendations)
