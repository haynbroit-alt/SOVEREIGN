"""Scénario complet sur l'espace de démonstration (entreprise fictive) : vérifie que chaque capacité produit
des résultats concrets et cohérents, pas seulement qu'elle « s'exécute »."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sovereign.app import Sovereign
from sovereign.core.clock import FixedClock
from sovereign.workspace import DEMO_PATH, export_workspace, import_workspace, load_file


@pytest.fixture(scope="module")
def demo():
    clock = FixedClock(datetime(2026, 11, 17, 9, 0, tzinfo=UTC))
    app = Sovereign.ephemeral(clock=clock)
    report = import_workspace(app, load_file(DEMO_PATH), actor="demo")
    yield app, report, clock
    app.close()


def test_import_goes_through_the_budget_gate(demo):
    app, report, _ = demo
    assert report["collections"] == 7 and report["refunds"] == 1
    assert report["expenses"].get("spent") == 3
    assert report["expenses"].get("review") == 1  # le conseil SEO à 2 000 € attend un humain
    status = app.kernel.finance.status()
    assert status.collected_total == Decimal("44110.00")
    assert status.envelope == Decimal("4411.00")


def test_director_uses_every_relevant_signal(demo):
    app, _, _ = demo
    report = app.director.run("Que dois-je décider cette semaine ?", deep=True)
    contradicted = {c.metric for c in report.confrontations if c.outcome == "contradiction"}
    assert "finance.collections_trend" in contradicted
    titles = " ".join(i.title for i in report.insights)
    assert "facturation électronique" in titles  # risque réglementaire critique détecté par la veille
    assert report.priorities and report.decisions
    assert any(d.route == "human_review" for d in report.decisions)
    assert report.learned  # les résultats de décisions passées sont appris


def test_each_skill_delivers_concrete_results(demo):
    app, _, _ = demo
    k = app.kernel
    competition = k.run_skill("business-intelligence", "analyze_competition", {})
    assert competition.metrics["competition.hhi"] == pytest.approx(1016, abs=1)
    needs = k.run_skill("business-intelligence", "find_unmet_needs", {})
    assert needs.metrics["needs.underserved"] >= 2
    campaigns = k.run_skill("marketing-strategy", "measure_campaigns", {})
    assert campaigns.metrics["marketing.cac"] == pytest.approx(3150 / 64, rel=1e-3)
    model = k.run_skill("business-development", "evaluate_business_model", {})
    assert model.summary
    conflicts = k.run_skill("intelligent-management", "detect_conflicts", {})
    kinds = {c.kind for c in conflicts.conflicts}
    assert len(kinds) >= 2  # ressource partagée + recommandations contradictoires (Facebook Ads)
    risks = k.run_skill("watch-anticipation", "assess_risks", {})
    assert risks.metrics["risk.critical_count"] >= 1
    comparison = k.run_skill("negotiation-strategy", "assess", {})
    assert comparison.summary
    unused = k.run_skill("analysis-optimization", "detect_unused_features", {})
    names = {f.feature for f in unused.unused}
    assert {"export comptable", "messagerie interne"} <= names
    assert "bilan carbone chantier" not in names  # sortie il y a moins de 30 jours
    verified = k.run_skill("analysis-optimization", "verify_change", {"change_id": "ch-onboarding"})
    assert verified.verdict == "amélioré" and verified.p_value < 0.05
    feasibility = k.run_skill("innovation-creativity", "evaluate_feasibility", {})
    verdicts = {r.title: r.verdict for r in feasibility.evaluations}
    assert verdicts["Place de marché de fournitures"] == "no-go"
    memory = k.run_skill("memory-learning", "detect_repeated_errors", {})
    assert any("ads-social" in p.pattern or "facebook" in p.pattern for p in memory.patterns)


def test_cycles_and_commands_on_demo(demo):
    app, _, _ = demo
    for cycle in ("daily", "weekly", "monthly"):
        assert app.cycles.run(cycle)["status"] == "succeeded"
    app.worker.run_once()
    for command in ("/strategy", "/opportunities", "/projects", "/roadmap", "/improve", "/audit", "/skills"):
        result = app.commands.run(command)
        assert result.ok and result.sections, command
    opportunities = app.kernel.store.list("opportunity")
    assert opportunities and all(Decimal(str(o["potential_value"])) >= 0 for o in opportunities)
    utility = app.kernel.skill_utility()
    assert sum(1 for u in utility.values() if u["runs"] > 0) == 10  # les dix Skills ont servi


def test_export_round_trip(demo):
    app, _, _ = demo
    exported = export_workspace(app)
    fresh = Sovereign.ephemeral()
    report = import_workspace(fresh, exported)
    assert fresh.kernel.finance.collected_total() == app.kernel.finance.collected_total()
    assert report["records"]["market"] == 2
    fresh.close()


def test_council_confronts_stored_comparison_and_negotiation(demo):
    app, _, _ = demo
    comparison = app.commands.run("/council cmp-hebergeur")
    assert comparison.data["negotiation"]["winner"]
    negotiation = app.commands.run("/council neg-negoce")
    assert negotiation.data["negotiation"]["zopa_exists"] is True
    business = app.commands.run("/business")
    assert "negotiation-strategy.assess" in business.data["outputs"]
