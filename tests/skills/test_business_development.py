from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from sovereign.skills.base import SkillError

SKILL = "business-development"

SAAS = {
    "id": "saas",
    "name": "SaaS",
    "fixed_costs_monthly": "2000",
    "cac": "300",
    "revenue_streams": [
        {
            "name": "Pro",
            "type": "subscription",
            "price": "50",
            "volume_per_month": 100,
            "gross_margin": 0.8,
            "churn_monthly": 0.05,
        }
    ],
}
AGENCY = {
    "id": "agence",
    "name": "Agence",
    "fixed_costs_monthly": "4000",
    "cac": "500",
    "revenue_streams": [
        {"name": "Projet", "type": "one_off", "price": "3000", "volume_per_month": 2, "gross_margin": 0.3}
    ],
}
GOOD = {
    "id": "good",
    "name": "Rentable",
    "fixed_costs_monthly": "5000",
    "cac": "400",
    "revenue_streams": [
        {
            "name": "Abo",
            "type": "subscription",
            "price": "100",
            "volume_per_month": 200,
            "gross_margin": 0.8,
            "churn_monthly": 0.02,
        }
    ],
}
BAD = {
    "id": "bad",
    "name": "Perdant",
    "fixed_costs_monthly": "1000",
    "cac": "200",
    "revenue_streams": [
        {
            "name": "Mini",
            "type": "subscription",
            "price": "20",
            "volume_per_month": 10,
            "gross_margin": 0.5,
            "churn_monthly": 0.2,
        }
    ],
}


def test_business_model_unit_economics(kernel):
    out = kernel.run_skill(SKILL, "evaluate_business_model", {"models": [SAAS, AGENCY, GOOD, BAD]})
    by_id = {m.id: m for m in out.models}
    saas = by_id["saas"]
    assert saas.monthly_revenue == Decimal("5000.00") and saas.monthly_contribution == Decimal("4000.00")
    assert saas.operating_result_monthly == Decimal("2000.00")
    assert saas.ltv == Decimal("800.00") and saas.ltv_cac == pytest.approx(2.667, abs=1e-3)
    assert saas.payback_months == 7.5 and saas.verdict == "fragile"
    agency = by_id["agence"]
    assert agency.ltv == Decimal("900.00") and agency.break_even_volume == pytest.approx(4.4)
    assert agency.operating_result_monthly == Decimal("-2200.00") and agency.verdict == "fragile"
    assert by_id["good"].verdict == "viable" and by_id["good"].ltv_cac == 10.0
    assert by_id["bad"].verdict == "non viable"
    titles = [r.title for r in out.recommendations]
    assert any("SaaS" in t and "266.67" in t for t in titles)
    assert any("point mort" in t and "+2.4" in t for t in titles)
    assert out.metrics["bizdev.saas.ltv_cac"] == pytest.approx(2.667, abs=1e-3)
    assert out.metrics["bizdev.mrr"] == pytest.approx(5000 + 6000 + 20000 + 200)


def test_business_model_from_workspace_and_validation(kernel):
    kernel.store.put("business_model", "saas", SAAS)
    assert kernel.run_skill(SKILL, "evaluate_business_model", {}).models[0].name == "SaaS"
    with pytest.raises(ValidationError):
        kernel.run_skill(SKILL, "evaluate_business_model", {"models": [{**SAAS, "revenue_streams": []}]})
    with pytest.raises(ValidationError):
        bad_margin = {**SAAS, "revenue_streams": [{**SAAS["revenue_streams"][0], "gross_margin": 1.2}]}
        kernel.run_skill(SKILL, "evaluate_business_model", {"models": [bad_margin]})


def test_compare_revenue_streams(kernel):
    model = {"name": "Mixte", "revenue_streams": [SAAS["revenue_streams"][0], AGENCY["revenue_streams"][0]]}
    out = kernel.run_skill(SKILL, "compare_revenue_streams", {"models": [model]})
    scores = {s.name: s.score for s in out.streams}
    assert scores["Pro"] == pytest.approx(0.96)
    assert scores["Projet"] == pytest.approx(0.405)
    assert out.streams[0].rank == 1 and out.streams[0].name == "Pro"
    assert any("récurrent" in r.title for r in out.recommendations)
    assert not any("Dépendance" in i.title for i in out.insights)  # 69 % < 70 %


def test_rank_opportunities_rice_and_fit(kernel):
    kernel.store.put("asset", "a1", {"id": "a1", "name": "Plateforme de facturation", "kind": "product"})
    for opp in [
        {
            "id": "o1",
            "title": "Relances de facturation automatisées",
            "potential_value": "100000",
            "confidence": 0.8,
            "reach": 500,
            "effort": 4,
        },
        {"id": "o2", "title": "Conseil RH", "potential_value": "40000", "confidence": 0.5},
        {
            "id": "o3",
            "title": "Idée écartée",
            "potential_value": "900000",
            "confidence": 0.9,
            "status": "rejected",
        },
    ]:
        kernel.store.put("opportunity", opp["id"], opp)
    out = kernel.run_skill(SKILL, "rank_opportunities", {})
    assert [r.id for r in out.ranking] == ["o1", "o2"]
    first, second = out.ranking
    assert first.impact == 3 and first.rice == 300 and first.strategic_fit == 1.0 and first.score == 450
    assert second.reach == 100 and second.reach_estimated and second.impact == 1 and second.rice == 10
    rec = out.recommendations[0]
    assert rec.follow_up.action == "launch_plan" and rec.follow_up.payload["name"] == first.title
    with_rejected = kernel.run_skill(SKILL, "rank_opportunities", {"include_rejected": True})
    assert {r.id for r in with_rejected.ranking} == {"o1", "o2", "o3"}
    # o3 : plus gros potentiel (impact 3) mais portée estimée → RICE 100 × 3 × 0,9 / 5 = 54 < 75 (o1)
    assert next(r for r in with_rejected.ranking if r.id == "o3").rice == 54


def test_generate_activities_ansoff(kernel):
    kernel.store.put("market", "m1", {"id": "m1", "name": "PME France"})
    kernel.store.put(
        "need", "n1", {"id": "n1", "statement": "x", "segment": "ETI", "importance": 8, "satisfaction": 2}
    )
    out = kernel.run_skill(
        SKILL,
        "generate_activities",
        {
            "assets": [
                {"name": "Plateforme Nova", "kind": "product"},
                {"name": "Moteur IA", "kind": "technology"},
            ]
        },
    )
    quadrants = {a.quadrant: a for a in out.activities}
    assert set(quadrants) == {
        "pénétration",
        "développement produit",
        "développement marché",
        "diversification",
    }
    assert "PME France" in quadrants["pénétration"].title
    assert "ETI" in quadrants["développement marché"].title
    assert quadrants["diversification"].risk_level == 4
    assert out.recommendations[0].risk == pytest.approx(0.1)


def test_launch_plan_sequential_funding(funded):
    out = funded.run_skill(
        SKILL, "launch_plan", {"name": "Offre Pro", "budget": "2000", "target_customers": 120}
    )
    budgets = [(p.name, p.budget, p.funded, p.funding_status) for p in out.phases]
    assert budgets == [
        ("validation", Decimal("200.00"), Decimal("200.00"), "financée"),
        ("MVP", Decimal("800.00"), Decimal("800.00"), "financée"),
        ("lancement", Decimal("600.00"), Decimal("0.00"), "à financer par les encaissements futurs"),
        ("croissance", Decimal("400.00"), Decimal("0.00"), "à financer par les encaissements futurs"),
    ]
    assert out.funding_gap == Decimal("1000.00") and out.total_weeks == 28
    assert (out.phases[1].start_week, out.phases[1].end_week) == (5, 12)
    assert "≥ 12 pré-inscriptions" in " ".join(out.phases[0].go_no_go)
    rec = out.recommendations[0]
    assert rec.external and rec.estimated_cost == Decimal("200.00")
    assert rec.follow_up.payload == {"amount": "200.00"}
    assert any(i.kind == "risk" for i in out.insights)


def test_launch_plan_budget_from_potential(funded):
    out = funded.run_skill(SKILL, "launch_plan", {"name": "X", "potential_annual_revenue": "5000"})
    assert out.requested_budget == Decimal("500.00") and out.funding_gap == Decimal("0.00")
    assert any("2500.00" in c for c in out.phases[-1].go_no_go)


def test_launch_plan_refuses_without_budget(kernel):
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "launch_plan", {"name": "Sans budget"})
    with pytest.raises(ValidationError):
        kernel.run_skill(SKILL, "launch_plan", {"name": ""})
    unfunded = kernel.run_skill(SKILL, "launch_plan", {"name": "Y", "budget": "800"})
    assert unfunded.funded_budget == Decimal("0.00") and unfunded.recommendations == []
    assert all(p.funding_status == "à financer par les encaissements futurs" for p in unfunded.phases)


def test_assess_empty_then_full(kernel):
    empty = kernel.run_skill(SKILL, "assess")
    assert "Aucune donnée de développement" in empty.summary
    kernel.store.put("business_model", "saas", SAAS)
    kernel.store.put("opportunity", "o1", {"id": "o1", "title": "Relances", "potential_value": "1000"})
    kernel.store.put("asset", "a1", {"id": "a1", "name": "Plateforme", "kind": "product"})
    full = kernel.run_skill(SKILL, "assess")
    assert (full.business_models, full.opportunities, full.assets) == (1, 1, 1)
    assert "bizdev.mrr" in full.metrics and "bizdev.top_opportunity_score" in full.metrics
    assert full.recommendations and all(r.skill == SKILL for r in full.recommendations)
