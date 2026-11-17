from __future__ import annotations

import math
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sovereign.skills.base import SkillError

SKILL = "negotiation-strategy"

CRM = {
    "title": "CRM",
    "criteria": [{"name": "prix", "weight": 2, "direction": "cost"}, {"name": "qualite", "weight": 3}],
    "proposals": [
        {"name": "A", "values": {"prix": 100, "qualite": 7}},
        {"name": "B", "values": {"prix": 80, "qualite": 6}},
    ],
}


def test_weighted_sum_and_topsis_match_hand_computation(kernel):
    out = kernel.run_skill(SKILL, "compare_proposals", CRM)
    scores = {r.name: r for r in out.ranking}
    assert scores["A"].weighted_score == pytest.approx(0.6)
    assert scores["B"].weighted_score == pytest.approx(0.4)
    # TOPSIS calculé à la main
    w_p, w_q = 0.4, 0.6
    n_p, n_q = math.sqrt(100**2 + 80**2), math.sqrt(7**2 + 6**2)
    a = (w_p * 100 / n_p, w_q * 7 / n_q)
    b = (w_p * 80 / n_p, w_q * 6 / n_q)
    best, worst = (b[0], a[1]), (a[0], b[1])
    d_plus, d_minus = math.dist(a, best), math.dist(a, worst)
    assert scores["A"].topsis == pytest.approx(d_minus / (d_plus + d_minus), abs=1e-4)
    assert out.winner == "A" and out.methods_agree and out.stability == 1.0
    assert out.recommendations[0].title.startswith("Retenir « A »")


def test_sensitivity_detects_fragile_ranking(kernel):
    out = kernel.run_skill(
        SKILL,
        "compare_proposals",
        {
            "criteria": [
                {"name": "prix", "weight": 1, "direction": "cost"},
                {"name": "qualite", "weight": 1},
            ],
            "proposals": [
                {"name": "Premium", "values": {"prix": 100, "qualite": 10}},
                {"name": "Eco", "values": {"prix": 50, "qualite": 5}},
            ],
        },
    )
    assert out.stability < 1 and out.sensitive_criteria
    assert any(i.title == "Classement sensible aux poids" for i in out.insights)


def test_compare_validation_and_store_fallback(kernel):
    bad = dict(
        CRM,
        proposals=[{"name": "A", "values": {"prix": 1}}, {"name": "B", "values": {"prix": 2, "qualite": 1}}],
    )
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "compare_proposals", bad)
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "compare_proposals", {})
    kernel.store.put("comparison", "cmp1", {"id": "cmp1", **CRM})
    assert kernel.run_skill(SKILL, "compare_proposals", {}).winner == "A"
    with pytest.raises(ValidationError):
        kernel.run_skill(SKILL, "compare_proposals", {"criteria": [{"name": "x", "weight": 0}]})


def test_buyer_negotiation_plan(kernel):
    out = kernel.run_skill(
        SKILL,
        "prepare_negotiation",
        {
            "title": "Achat CRM",
            "we_are": "buyer",
            "our_batna": 1200,
            "our_reservation": 1000,
            "our_target": 800,
            "their_estimated_reservation": 850,
            "issues": [
                {"name": "durée d'engagement", "our_cost": 2, "their_value": 8},
                {"name": "support 24/7", "our_cost": 8, "their_value": 3},
            ],
        },
    )
    assert out.zopa_exists and (out.zopa_low, out.zopa_high) == (Decimal("850.00"), Decimal("1000.00"))
    assert out.anchor == Decimal("750.00")
    assert [s.position for s in out.concession_plan] == [
        Decimal("875.00"),
        Decimal("937.50"),
        Decimal("975.00"),
        Decimal("1000.00"),
    ]
    assert [s.concession for s in out.concession_plan][:2] == [Decimal("125.00"), Decimal("62.50")]
    stances = {t.issue: t.stance for t in out.trades}
    assert stances == {"durée d'engagement": "concéder", "support 24/7": "protéger"}
    assert "1000" in out.walk_away and out.metrics["negotiation.zopa_width"] == 150.0


def test_seller_without_zopa_and_warnings(kernel):
    out = kernel.run_skill(
        SKILL,
        "prepare_negotiation",
        {
            "we_are": "seller",
            "our_batna": 650,
            "our_reservation": 600,
            "our_target": 900,
            "their_estimated_reservation": 550,
        },
    )
    assert out.zopa_exists is False
    assert out.warnings and "BATNA" in out.warnings[0]
    assert any("BATNA" in r.title for r in out.recommendations)
    assert out.anchor >= Decimal("900")
    with pytest.raises(SkillError):
        kernel.run_skill(
            SKILL,
            "prepare_negotiation",
            {"we_are": "buyer", "our_batna": 1, "our_reservation": 10, "our_target": 20},
        )


def test_negotiation_loaded_from_store(kernel):
    kernel.store.put(
        "negotiation",
        "n1",
        {
            "id": "n1",
            "title": "Fournisseur X",
            "counterpart": "X",
            "we_are": "buyer",
            "our_batna": "5000",
            "our_reservation": "4500",
            "our_target": "4000",
        },
    )
    out = kernel.run_skill(SKILL, "prepare_negotiation", {"negotiation_id": "n1"})
    assert out.title == "Fournisseur X" and out.zopa_exists is None
    assert out.concession_plan[-1].position == Decimal("4500.00")


def test_pros_cons(kernel):
    out = kernel.run_skill(
        SKILL,
        "pros_cons",
        {
            "options": [
                {
                    "name": "Recruter",
                    "assessments": [
                        {"dimension": "capacité", "score": 4, "weight": 2},
                        {"dimension": "coût", "score": -2, "weight": 1},
                    ],
                },
                {"name": "Sous-traiter", "assessments": [{"dimension": "contrôle", "score": -3}]},
            ]
        },
    )
    assert out.best == "Recruter"
    first = out.options[0]
    assert first.net_score == pytest.approx(2.0) and first.verdict == "favorable"
    assert first.pros[0].startswith("capacité") and first.cons[0].startswith("coût")
    assert out.options[1].verdict == "défavorable"


def test_debate_adopts_strong_case(funded):
    out = funded.run_skill(
        SKILL,
        "debate",
        {
            "proposal": "Lancer l'offre Pro",
            "facts": {
                "expected_value": 20000,
                "cost": 500,
                "probability_of_success": 0.8,
                "reversibility": 0.8,
                "strategic_fit": 0.9,
                "market_evidence": 0.8,
                "time_to_value_months": 2,
                "team_capacity": 0.8,
            },
        },
    )
    assert out.verdict == "adopter" and not out.vetoes
    assert out.data_completeness == 1.0 and out.confidence > 0.6
    assert {t.agent for t in out.transcript if t.round == "ouverture"} == {
        "CEO",
        "Risque",
        "CFO",
        "CMO",
        "COO",
    }
    assert all(v > 0 for v in out.final_positions.values())
    assert not out.llm_used


def test_debate_budget_veto_uses_real_finance(funded):
    out = funded.run_skill(
        SKILL,
        "debate",
        {
            "proposal": "Salon international",
            "facts": {
                "expected_value": 50000,
                "cost": 5000,
                "probability_of_success": 0.9,
                "strategic_fit": 1.0,
            },
        },
    )
    assert out.verdict == "rejeter" and "1000.00" in out.vetoes[0]
    assert any(t.round == "réfutation" for t in out.transcript)


def test_debate_with_missing_data_is_cautious(kernel):
    out = kernel.run_skill(SKILL, "debate", {"proposal": "Idée floue", "agents": ["ceo", "cmo"]})
    assert out.data_completeness == 0.0
    assert out.verdict == "adopter sous conditions" and out.confidence <= 0.25
    assert any(i.title == "Débat sous-informé" for i in out.insights)
    with pytest.raises(ValidationError):
        kernel.run_skill(SKILL, "debate", {"proposal": "x", "agents": ["ceo", "astrologue"]})


def test_debate_optional_llm_only_rephrases(funded):
    class FakeLLM:
        available = True

        def structured(self, system, prompt, model, *, purpose, max_tokens):
            return model(statements=[{"agent": "CEO", "text": "Reformulé."}])

    funded.llm = FakeLLM()
    facts = {
        "expected_value": 20000,
        "cost": 500,
        "probability_of_success": 0.8,
        "reversibility": 0.8,
        "strategic_fit": 0.9,
        "market_evidence": 0.8,
        "time_to_value_months": 2,
        "team_capacity": 0.8,
    }
    out = funded.run_skill(SKILL, "debate", {"proposal": "Offre Pro", "facts": facts})
    assert out.llm_used and out.verdict == "adopter"
    ceo = next(t for t in out.transcript if t.agent == "CEO" and t.round == "ouverture")
    assert ceo.arguments[-1].text == "Reformulé." and ceo.arguments[-1].strength == 0.0


def test_recommend_combines_comparison_and_debate(funded):
    out = funded.run_skill(
        SKILL,
        "recommend",
        {
            **CRM,
            "facts": {
                "expected_value": 3000,
                "cost": 200,
                "probability_of_success": 0.7,
                "reversibility": 0.9,
            },
        },
    )
    assert out.winner == "A" and out.debate_verdict in ("adopter", "adopter sous conditions")
    assert out.comparison.winner == "A" and "A" in out.recommendation
    plain = funded.run_skill(SKILL, "recommend", CRM)
    assert plain.debate_verdict is None


def test_assess_empty_and_with_records(kernel):
    assert "Aucune comparaison" in kernel.run_skill(SKILL, "assess").summary
    kernel.store.put("comparison", "cmp1", {"id": "cmp1", **CRM})
    kernel.store.put(
        "negotiation",
        "n1",
        {
            "id": "n1",
            "title": "Achat",
            "we_are": "buyer",
            "our_batna": 1200,
            "our_reservation": 1000,
            "our_target": 800,
            "their_estimated_reservation": 850,
        },
    )
    out = kernel.run_skill(SKILL, "assess")
    assert out.comparisons == 1 and out.negotiations == 1
    assert "CRM → A" in out.summary and out.metrics["negotiation.n1.zopa_width"] == 150.0
