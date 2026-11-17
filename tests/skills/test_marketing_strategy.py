from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from sovereign.skills.base import SkillError

SKILL = "marketing-strategy"

SEGMENTS = [
    {
        "id": "pme",
        "name": "PME services",
        "size": 1000,
        "pain": 8,
        "willingness_to_pay": "500",
        "reachability": 0.5,
        "competition": 0.2,
    },
    {
        "id": "tpe",
        "name": "TPE",
        "size": 3000,
        "pain": 4,
        "willingness_to_pay": "100",
        "reachability": 0.9,
        "competition": 0.8,
    },
]
CHANNELS = [
    {"name": "search", "cpc": "1.00", "cvr": 0.04, "average_order_value": "100"},
    {"name": "social", "cpc": "0.50", "cvr": 0.01, "average_order_value": "100"},
]
CAMPAIGNS = [
    {
        "id": "c1",
        "name": "Search marque",
        "channel": "search",
        "spend": "1000",
        "impressions": 100000,
        "clicks": 2000,
        "conversions": 100,
        "revenue": "4000",
    },
    {
        "id": "c2",
        "name": "Display large",
        "channel": "display",
        "spend": "1000",
        "impressions": 200000,
        "clicks": 1000,
        "conversions": 20,
        "revenue": "800",
    },
    {
        "id": "c3",
        "name": "Social A",
        "channel": "social",
        "spend": "500",
        "impressions": 50000,
        "clicks": 500,
        "conversions": 15,
        "revenue": "1000",
    },
    {
        "id": "c4",
        "name": "Social B",
        "channel": "social",
        "spend": "500",
        "impressions": 50000,
        "clicks": 500,
        "conversions": 40,
        "revenue": "2600",
        "variant_of": "c3",
    },
]


def test_segment_attractiveness_weights(kernel):
    out = kernel.run_skill(SKILL, "segment_audiences", {"segments": SEGMENTS})
    scores = {s.id: s.attractiveness for s in out.segments}
    assert scores["pme"] == pytest.approx(0.605)
    assert scores["tpe"] == pytest.approx(0.505)
    assert out.icp == "PME services"
    assert out.segments[0].potential_revenue == Decimal("250000.00")
    rec = out.recommendations[0]
    assert "PME services" in rec.title and rec.follow_up.action == "design_strategy"


def test_plan_campaign_allocation_follows_e_squared(funded):
    out = funded.run_skill(SKILL, "plan_campaign", {"objective": "sales", "budget": "500", "channels": CHANNELS})
    assert out.planned_budget == Decimal("500.00") and not out.capped
    shares = {a.channel: a.share for a in out.allocations}
    assert shares == {"search": 0.8, "social": 0.2}
    budgets = {a.channel: a.budget for a in out.allocations}
    assert budgets == {"search": Decimal("400.00"), "social": Decimal("100.00")}
    # conversions = cvr/cpc × √(b × b₀), b₀ = 250
    assert out.allocations[0].expected_conversions == pytest.approx(0.04 * (400 * 250) ** 0.5, abs=0.01)
    assert out.expected_roas == pytest.approx(3.162, abs=0.002)
    rec = out.recommendations[0]
    assert rec.external and rec.estimated_cost == Decimal("500.00")
    assert rec.follow_up.skill == "finance-budget" and rec.follow_up.payload == {"amount": "500.00"}


def test_plan_campaign_capped_by_available_budget(funded, kernel):
    capped = funded.run_skill(SKILL, "plan_campaign", {"objective": "leads", "budget": "5000", "channels": CHANNELS})
    assert capped.capped and capped.planned_budget == Decimal("1000.00")
    assert any("réduit" in i.title for i in capped.insights)


def test_plan_campaign_without_budget_proposes_nothing(kernel):
    out = kernel.run_skill(SKILL, "plan_campaign", {"objective": "leads", "budget": "300", "channels": CHANNELS})
    assert out.planned_budget == Decimal("0.00")
    assert out.recommendations == []
    assert out.insights[0].severity == "critical"


def test_plan_campaign_validation(funded):
    with pytest.raises(SkillError):
        funded.run_skill(
            SKILL,
            "plan_campaign",
            {"objective": "sales", "budget": "100", "channels": [{"name": "x", "cpc": "1", "cvr": 0.1}]},
        )
    with pytest.raises(ValidationError):
        funded.run_skill(SKILL, "plan_campaign", {"budget": "100", "channels": [{"name": "x", "cpc": "1", "cvr": 1.5}]})
    with pytest.raises(ValidationError):
        funded.run_skill(SKILL, "plan_campaign", {"budget": "100", "channels": []})


def test_measure_campaigns_verdicts_and_ab_test(kernel):
    for camp in CAMPAIGNS:
        kernel.store.put("campaign", camp["id"], camp)
    out = kernel.run_skill(SKILL, "measure_campaigns", {})
    verdicts = {c.id: c.verdict for c in out.campaigns}
    assert verdicts == {"c1": "scale", "c2": "pause", "c3": "keep", "c4": "scale"}
    c1 = next(c for c in out.campaigns if c.id == "c1")
    assert (c1.ctr, c1.cvr, c1.cpa, c1.roas, c1.roi) == (0.02, 0.05, Decimal("10.00"), 4.0, 3.0)
    assert out.total_spend == Decimal("3000.00") and out.overall_roas == pytest.approx(2.8)
    assert out.overall_cac == Decimal("17.14")
    test = out.ab_tests[0]
    assert test.control == "Social A" and test.significant and test.winner == "Social B"
    assert test.relative_lift == pytest.approx(40 / 15 - 1, abs=1e-3)
    titles = [r.title for r in out.recommendations]
    assert any("pause" in t and "Display large" in t for t in titles)
    assert any("Généraliser" in t for t in titles)
    assert out.metrics["marketing.cac"] == pytest.approx(17.14)


def test_ab_test_not_significant(kernel):
    control = {**CAMPAIGNS[2], "clicks": 1000, "conversions": 30}
    variant = {**CAMPAIGNS[3], "clicks": 1000, "conversions": 32}
    out = kernel.run_skill(SKILL, "measure_campaigns", {"campaigns": [control, variant]})
    assert out.ab_tests[0].significant is False and out.ab_tests[0].winner is None


def test_trends_classification(kernel):
    series = [
        {
            "id": "ia",
            "topic": "IA générative",
            "points": [{"period": f"m{i}", "value": v} for i, v in enumerate([10, 12, 14, 16, 18, 20])],
        },
        {
            "id": "fax",
            "topic": "Fax",
            "points": [{"period": f"m{i}", "value": v} for i, v in enumerate([20, 18, 16, 14])],
        },
        {
            "id": "crm",
            "topic": "CRM",
            "points": [{"period": f"m{i}", "value": v} for i, v in enumerate([10, 10.1, 9.9, 10])],
        },
        {
            "id": "new",
            "topic": "Nouveau",
            "points": [{"period": "m0", "value": 1}, {"period": "m1", "value": 2}],
        },
    ]
    out = kernel.run_skill(SKILL, "analyze_trends", {"series": series})
    classes = {t.id: t.classification for t in out.trends}
    assert classes == {"ia": "rising", "fax": "declining", "crm": "stable", "new": "insufficient_data"}
    ia = next(t for t in out.trends if t.id == "ia")
    assert ia.slope == pytest.approx(2) and ia.relative_slope == pytest.approx(2 / 15, abs=1e-6)
    assert ia.growth == pytest.approx(1.0) and ia.acceleration == pytest.approx(0)
    assert out.metrics == {"marketing.trends_rising": 1.0, "marketing.trends_declining": 1.0}
    assert any("IA générative" in r.title for r in out.recommendations)


def test_design_strategy_hypothesis_then_observed(kernel):
    for seg in SEGMENTS:
        kernel.store.put("segment", seg["id"], seg)
    kernel.store.put(
        "need",
        "n1",
        {
            "id": "n1",
            "statement": "Relancer les impayés sans y passer ses soirées",
            "segment": "PME services",
            "importance": 9,
            "satisfaction": 3,
        },
    )
    first = kernel.run_skill(SKILL, "design_strategy", {"offer_name": "Nova"})
    assert first.priority_segment == "PME services"
    assert "Relancer les impayés" in first.positioning and "Nova" in first.positioning
    assert all(c.basis == "hypothèse" for c in first.channel_mix)
    assert [p.window for p in first.plan] == ["J+30", "J+60", "J+90"]
    for camp in CAMPAIGNS[:3]:
        kernel.store.put("campaign", camp["id"], camp)
    second = kernel.run_skill(SKILL, "design_strategy", {})
    assert second.channel_mix[0].channel == "search" and second.channel_mix[0].basis == "observé"
    assert sum(c.share for c in second.channel_mix) == pytest.approx(1, abs=1e-3)


def test_design_strategy_requires_segments(kernel):
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "design_strategy", {})


def test_assess_empty_then_full(kernel):
    empty = kernel.run_skill(SKILL, "assess")
    assert "Aucune donnée marketing" in empty.summary
    for seg in SEGMENTS:
        kernel.store.put("segment", seg["id"], seg)
    for camp in CAMPAIGNS:
        kernel.store.put("campaign", camp["id"], camp)
    full = kernel.run_skill(SKILL, "assess")
    assert (full.segments, full.campaigns, full.trend_series) == (2, 4, 0)
    assert "marketing.roas" in full.metrics and "marketing.icp_attractiveness" in full.metrics
    assert full.recommendations and all(r.skill == SKILL and r.id for r in full.recommendations)
