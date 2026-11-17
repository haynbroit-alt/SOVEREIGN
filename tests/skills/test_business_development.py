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
    kernel.store.put("need", "n1", {"id": "n1", "statement": "x", "segment": "ETI", "importance": 8, "satisfaction": 2})
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
    out = funded.run_skill(SKILL, "launch_plan", {"name": "Offre Pro", "budget": "2000", "target_customers": 120})
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


# -- Revue de portefeuille ---------------------------------------------------------------------------------
def _project(
    pid,
    *,
    potential=None,
    effort=None,
    confidence=0.6,
    maturity="prototype",
    loc=1000,
    verdict=None,
    overlaps_with=(),
    complementary="",
    secrets=(),
    tests_failed=0,
    kpis=None,
):
    record = {
        "id": f"repo-{pid}",
        "name": pid.title(),
        "kpis": kpis if kpis is not None else [{"name": "tests", "value": 1, "target": 1}],
        "repo": {
            "name": pid,
            "loc": loc,
            "secret_findings": list(secrets),
            "tests_failed": tests_failed,
            "days_since_last_commit": 3,
        },
        "overlaps": [],
    }
    if potential is not None:
        record["assessment"] = {
            "commercial_potential": potential,
            "effort_to_mvp_weeks": effort,
            "confidence": confidence,
            "maturity": maturity,
            "verdict": verdict,
            "verdict_reason": "raison IA",
            "overlaps_with": list(overlaps_with),
            "complementary_to_sovereign": complementary,
        }
    return record


def _seed(kernel, *records):
    for record in records:
        kernel.store.put("project", record["id"], record)


def test_portfolio_review_rules(kernel):
    _seed(
        kernel,
        _project("paie", potential=5, effort=8, confidence=0.55, maturity="mvp", verdict="develop"),
        _project("ref", potential=5, effort=6, confidence=0.55, verdict="develop"),
        _project("bonus", potential=4, effort=10, confidence=0.5, verdict="develop"),
        _project("tr", potential=3, effort=6, confidence=0.45, verdict="develop"),
        _project("cortex", potential=1, effort=20, overlaps_with=["tr"], verdict="freeze"),
        _project(
            "hive", potential=2, effort=10, overlaps_with=["ref"], complementary="canal Telegram", verdict="merge"
        ),
        _project("velyx", potential=3, effort=2, overlaps_with=["ref"], complementary="connecteurs", verdict="freeze"),
        _project("mondo", potential=3, effort=4, loc=0, maturity="idea", verdict="freeze"),
        _project("artiste", potential=1, effort=0, loc=0, maturity="idea", verdict="archive"),
        _project("inconnu"),
    )
    out = kernel.run_skill(SKILL, "portfolio_review", {"focus_limit": 2})
    verdicts = {d.project_id.removeprefix("repo-"): d for d in out.decisions}
    assert {k: v.verdict for k, v in verdicts.items()} == {
        "ref": "développer",  # rendement 5×0,775/1,75 = 2,214
        "paie": "développer",  # 5×0,775/2 = 1,938
        "bonus": "maintenir",  # potentiel ≥ 4 mais hors limite d'encours
        "velyx": "valider le marché",  # test de 2 semaines avant toute fusion
        "inconnu": "évaluer",
        "hive": "fusionner",  # doublon de ref avec un actif propre
        "cortex": "geler",  # doublon de tr, sans actif propre
        "tr": "geler",  # potentiel 3 pour 6 semaines
        "mondo": "geler",  # idée sans code : gelée, l'archivage est réservé aux potentiels ≤ 1,5
        "artiste": "archiver",
    }
    assert out.focus == ["Ref", "Paie"]
    assert verdicts["ref"].return_on_effort == pytest.approx(2.214, abs=1e-3)
    assert verdicts["hive"].merge_into == "Ref" and verdicts["velyx"].merge_into == "Ref"
    # 6 + 20 + 4 + 0 semaines évitées (tr, cortex, mondo, artiste)
    assert out.freed_weeks == pytest.approx(30.0)
    # Désaccords avec les analystes signalés, jamais tranchés en faveur de l'IA
    assert verdicts["tr"].agrees_with_ai is False and verdicts["bonus"].agrees_with_ai is True
    assert verdicts["velyx"].agrees_with_ai is True  # « freeze » couvre la validation sans développement
    assert any(d.startswith("Tr :") for d in out.divergences)
    assert verdicts["inconnu"].ai_verdict is None and verdicts["inconnu"].agrees_with_ai is None
    assert out.metrics["portfolio.focus"] == 2
    assert verdicts["mondo"].agrees_with_ai is True
    titles = [r.title for r in out.recommendations]
    assert any(t.startswith("Concentrer l'effort sur « Ref » et « Paie »") for t in titles)
    assert any("Fusionner « Hive » dans « Ref »" in t for t in titles)
    assert any("Tester le marché de « Velyx »" in t for t in titles)


def test_portfolio_review_blocking_dispersion_and_apply(kernel):
    _seed(
        kernel,
        _project("fuite", potential=6, effort=4, secrets=["config.py:3 clé"]),
        _project("rouge", potential=5, effort=4, tests_failed=2),
        _project("b", potential=2, effort=8),
    )
    out = kernel.run_skill(SKILL, "portfolio_review", {"focus_limit": 1, "apply": True})
    fuite = next(d for d in out.decisions if d.name == "Fuite")
    assert fuite.verdict == "développer" and fuite.blocking == ["secrets exposés (1)"]
    security = [r for r in out.recommendations if r.title.startswith("Corriger « Fuite »")]
    assert security and security[0].urgency == 10
    assert any(i.metric == "portfolio.active_projects" for i in out.insights)  # 3 actifs pour 1 prioritaire
    assert kernel.store.get("project", "repo-b")["portfolio_status"] == "geler"
    # analysis-optimization ne propose plus d'amélioration pour un projet gelé
    kernel.store.put(
        "project",
        "repo-b",
        {**kernel.store.get("project", "repo-b"), "kpis": [{"name": "doc", "value": 1, "target": 10}]},
    )
    backlog = kernel.run_skill("analysis-optimization", "recommend_improvements", {})
    assert all(item.project != "B" for item in backlog.backlog)
    projects = kernel.run_skill("analysis-optimization", "analyze_projects", {})
    assert not any("« B »" in r.title for r in projects.recommendations)
    assert "gelé(s), archivé(s) ou à fusionner" in projects.summary


def test_portfolio_review_empty_and_assess(kernel):
    empty = kernel.run_skill(SKILL, "portfolio_review", {})
    assert empty.decisions == [] and "Aucun projet" in empty.summary
    _seed(kernel, _project("a", potential=5, effort=4), _project("b"))
    assessed = kernel.run_skill(SKILL, "assess", {})
    assert assessed.projects == 2 and "Focus : A." in assessed.summary
    assert kernel.store.get("project", "repo-a").get("portfolio_status") is None  # assess ne modifie rien


def test_portfolio_review_deadline_and_unassessed_peer(kernel, clock):
    noel = _project("maison", potential=5, effort=1.5, verdict="develop", maturity="mvp")
    noel["assessment"]["deadline"] = "2026-12-20"  # horloge de test : 17/11/2026
    _seed(
        kernel,
        noel,
        _project("velyx", potential=3, effort=6, overlaps_with=["sovereign"], verdict="freeze"),
        _project("sovereign"),  # non évalué : ni gelé par un pair, ni utilisé pour en geler un autre
    )
    out = kernel.run_skill(SKILL, "portfolio_review", {})
    maison = next(d for d in out.decisions if d.name == "Maison")
    assert maison.verdict == "valider le marché" and maison.agrees_with_ai is True  # « develop » = investir
    assert maison.days_left == 33 and any("échéance le 20/12/2026 (33 j)" in r for r in maison.reasons)
    test = next(r for r in out.recommendations if r.title.startswith("Tester le marché de « Maison »"))
    assert test.urgency == 10 and "(avant le 20/12)" in test.title
    verdicts = {d.name: d for d in out.decisions}
    assert verdicts["Sovereign"].verdict == "évaluer"
    assert verdicts["Velyx"].verdict == "geler" and verdicts["Velyx"].merge_into is None


def test_portfolio_review_internal_tool_absorbs_weak_duplicates(kernel):
    hub = _project("hub", potential=2, effort=14, verdict="develop")
    hub["assessment"]["role"] = "internal"
    _seed(
        kernel,
        hub,
        _project(
            "essaim", potential=2, effort=10, overlaps_with=["hub"], complementary="canal Telegram", verdict="merge"
        ),
        _project("prospect", potential=3, effort=2, overlaps_with=["hub"], verdict="freeze"),
        _project("produit", potential=6, effort=6, overlaps_with=["hub"], verdict="develop"),
    )
    out = kernel.run_skill(SKILL, "portfolio_review", {})
    verdicts = {d.name: d for d in out.decisions}
    assert verdicts["Hub"].verdict == "maintenir" and verdicts["Hub"].agrees_with_ai is True
    assert verdicts["Essaim"].verdict == "fusionner" and verdicts["Essaim"].merge_into == "Hub"
    assert verdicts["Prospect"].verdict == "valider le marché" and verdicts["Prospect"].merge_into == "Hub"
    assert verdicts["Produit"].verdict == "développer"  # un vrai produit n'est jamais absorbé par l'outil interne
    assert out.focus == ["Produit"]
