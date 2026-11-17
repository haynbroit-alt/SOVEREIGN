from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from sovereign.skills.base import SkillError

SKILL = "business-intelligence"

MARKET = {
    "id": "pme-fr",
    "name": "PME France",
    "total_customers": 40000,
    "serviceable_ratio": 0.25,
    "obtainable_share": 0.05,
    "annual_value_per_customer": "1200",
    "growth_rate": 0.10,
    "horizon_years": 2,
}
COMPETITORS = [
    {
        "id": "a",
        "name": "Alpha",
        "market_share": 0.5,
        "price": "100",
        "quality": 8,
        "features": ["API", "export", "mobile"],
    },
    {
        "id": "b",
        "name": "Beta",
        "market_share": 0.3,
        "price": "60",
        "quality": 4,
        "features": ["API", "export"],
    },
    {
        "id": "c",
        "name": "Gamma",
        "market_share": 0.1,
        "price": "120",
        "quality": 5,
        "features": ["API", "ia"],
    },
]
NEEDS = [
    {
        "id": "n1",
        "statement": "Relances automatiques",
        "segment": "PME France",
        "importance": 9,
        "satisfaction": 3,
        "frequency": 0.8,
    },
    {"id": "n2", "statement": "Export comptable", "importance": 7, "satisfaction": 6},
    {"id": "n3", "statement": "Thèmes graphiques", "importance": 3, "satisfaction": 7},
]


def test_size_market_math(kernel):
    out = kernel.run_skill(SKILL, "size_market", {"markets": [MARKET]})
    size = out.markets[0]
    assert size.tam == Decimal("48000000.00")
    assert size.sam == Decimal("12000000.00")
    assert size.som == Decimal("600000.00")
    assert size.obtainable_customers == 500
    assert [p.som for p in size.som_projection] == [Decimal("660000.00"), Decimal("726000.00")]
    assert out.metrics["market.som"] == 600000.0


def test_size_market_uses_workspace_and_focus_recommendation(kernel):
    kernel.store.put("market", "pme-fr", MARKET)
    kernel.store.put(
        "market", "eti", {**MARKET, "id": "eti", "name": "ETI", "total_customers": 5000, "growth_rate": 0.2}
    )
    out = kernel.run_skill(SKILL, "size_market", {})
    assert len(out.markets) == 2
    assert out.recommendations[0].title.endswith("« PME France »")
    assert any(i.kind == "opportunity" and "ETI" in i.title for i in out.insights)


def test_competition_hhi_quadrants_and_features(kernel):
    out = kernel.run_skill(
        SKILL, "analyze_competition", {"competitors": COMPETITORS, "our_features": ["export", "ia"]}
    )
    # 50² + 30² + 10² = 3500 → très concentré
    assert out.hhi == 3500.0
    assert out.concentration == "très concentré"
    assert out.uncovered_share == pytest.approx(0.1)
    quadrants = {p.name: p.quadrant for p in out.positions}
    assert quadrants == {"Alpha": "premium", "Beta": "low-cost", "Gamma": "premium"}
    assert out.value_gap is True
    assert out.table_stakes == ["API", "export"]
    assert out.missing_features == ["API"]
    assert set(out.rare_features) == {"ia", "mobile"}
    assert 0 < out.intensity < 1
    titles = [r.title for r in out.recommendations]
    assert any("niche" in t for t in titles)
    assert any("rapport qualité/prix" in t for t in titles)
    assert any("API" in t for t in titles)


def test_competition_rejects_shares_above_100(kernel):
    bad = [{**COMPETITORS[0], "market_share": 0.7}, {**COMPETITORS[1], "market_share": 0.5}]
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "analyze_competition", {"competitors": bad})


def test_unmet_needs_ulwick(kernel):
    out = kernel.run_skill(SKILL, "find_unmet_needs", {"needs": NEEDS})
    scores = {n.id: (n.opportunity, n.classification) for n in out.needs}
    assert scores["n1"] == (15.0, "fortement sous-servi")
    assert scores["n2"] == (8.0, "servi")
    assert scores["n3"] == (3.0, "sur-servi")
    assert out.underserved == 1
    assert out.needs[0].id == "n1"
    assert out.recommendations[0].follow_up.action == "detect_opportunities"


def test_detect_opportunities_persists_with_stable_ids(kernel):
    for item in [MARKET]:
        kernel.store.put("market", item["id"], item)
    for comp in COMPETITORS:
        kernel.store.put("competitor", comp["id"], comp)
    for need in NEEDS:
        kernel.store.put("need", need["id"], need)
    out = kernel.run_skill(SKILL, "detect_opportunities", {})
    assert out.persisted == 1
    opp = out.opportunities[0]
    # SOM 600 000 × écart (15−10)/10 = 0,5 × (1 − 0,5 × intensité)
    expected = Decimal("600000") * Decimal("0.5") * Decimal(str(1 - 0.5 * out.competition_intensity))
    assert opp.potential_value == expected.quantize(Decimal("0.01"))
    assert opp.reach == 500
    assert opp.confidence == pytest.approx(0.35 + 0.15 + 0.15 + 0.15 + 0.08)
    stored = kernel.store.get("opportunity", opp.id)
    assert stored["source_skill"] == SKILL and stored["status"] == "new"
    # Une décision humaine sur l'opportunité n'est pas écrasée par un nouveau passage
    kernel.store.put("opportunity", opp.id, {**stored, "status": "accepted"})
    again = kernel.run_skill(SKILL, "detect_opportunities", {})
    assert again.opportunities[0].id == opp.id
    assert kernel.store.get("opportunity", opp.id)["status"] == "accepted"
    assert again.recommendations[0].follow_up.skill == "business-development"


def test_detect_opportunities_without_market_and_no_persist(kernel):
    out = kernel.run_skill(SKILL, "detect_opportunities", {"needs": NEEDS, "persist": False})
    assert out.persisted == 0
    assert out.opportunities[0].potential_value == Decimal("0.00")
    assert kernel.store.count("opportunity") == 0
    none = kernel.run_skill(SKILL, "detect_opportunities", {"needs": NEEDS[1:]})
    assert none.opportunities == [] and "Aucune opportunité" in none.summary


def test_estimate_potential_subscription(kernel):
    out = kernel.run_skill(
        SKILL,
        "estimate_potential",
        {
            "name": "Offre Pro",
            "price": "50",
            "target_customers": 1000,
            "conversion_rate": 0.02,
            "churn_monthly": 0.05,
            "gross_margin": 0.8,
        },
    )
    assert out.customers == 20
    retention = sum(0.95**m for m in range(12))
    assert out.annual_revenue_per_customer == Decimal(str(50 * retention)).quantize(Decimal("0.01"))
    assert out.ltv == Decimal("800.00")
    assert [s.customers for s in out.scenarios] == [10, 20, 30]
    one_off = kernel.run_skill(
        SKILL,
        "estimate_potential",
        {
            "name": "Audit",
            "price": "2000",
            "billing": "one_off",
            "target_customers": 100,
            "conversion_rate": 0.8,
            "gross_margin": 0.5,
        },
    )
    assert one_off.ltv == Decimal("1000.00")
    assert one_off.scenarios[2].conversion_rate == 1.0  # plafonné à 100 %


def test_input_validation(kernel):
    with pytest.raises(ValidationError):
        kernel.run_skill(
            SKILL,
            "estimate_potential",
            {"name": "x", "price": "-1", "target_customers": 1, "conversion_rate": 0.1},
        )
    with pytest.raises(ValidationError):
        kernel.run_skill(SKILL, "size_market", {"markets": [{**MARKET, "serviceable_ratio": 1.5}]})
    with pytest.raises(ValidationError):
        kernel.run_skill(
            SKILL, "find_unmet_needs", {"needs": [{"statement": "x", "importance": 11, "satisfaction": 2}]}
        )


def test_assess_empty_then_full(kernel):
    empty = kernel.run_skill(SKILL, "assess")
    assert "Aucune donnée" in empty.summary and empty.markets == 0
    kernel.store.put("market", MARKET["id"], MARKET)
    for comp in COMPETITORS:
        kernel.store.put("competitor", comp["id"], comp)
    for need in NEEDS:
        kernel.store.put("need", need["id"], need)
    full = kernel.run_skill(SKILL, "assess")
    assert (full.markets, full.competitors, full.needs) == (1, 3, 3)
    for key in ("market.tam", "market.som", "competition.hhi", "needs.underserved"):
        assert key in full.metrics
    assert full.recommendations and all(r.skill == SKILL for r in full.recommendations)
    assert kernel.store.count("opportunity") == 0  # assess n'écrit rien
