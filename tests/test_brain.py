from __future__ import annotations

from decimal import Decimal

import pytest

from sovereign.app import Sovereign
from sovereign.brain.council import Council, CouncilFacts
from sovereign.brain.hypotheses import explain
from sovereign.brain.prioritize import merge, prioritize, score
from sovereign.brain.simulation import simulate
from sovereign.skills.base import FollowUp, Recommendation


@pytest.fixture
def app(clock):
    application = Sovereign.ephemeral(clock=clock)
    yield application
    application.close()


def rec(**kwargs) -> Recommendation:
    base = {"title": "Test", "rationale": "r", "skill": "finance-budget"}
    base.update(kwargs)
    return Recommendation(**base).finalize("finance-budget")


# -- modèle du monde ------------------------------------------------------------------------------
def test_belief_confirmation_and_contradiction(app):
    belief = app.world.add(
        "Le CAC reste sous 80 €", metric="marketing.cac", comparator="lt", threshold=80, confidence=0.6
    )
    [c] = app.world.confront({"marketing.cac": 60})
    assert c.outcome == "confirmation" and c.posterior > 0.6
    app.kernel.clock.advance(days=2)
    [c2] = app.world.confront({"marketing.cac": 140})
    assert c2.outcome == "contradiction" and c2.posterior < c.posterior
    assert len(app.world.get(belief.id).evidence) == 2


def test_same_fact_is_counted_once(app):
    app.world.add("Marge > 30 %", metric="m", comparator="gt", threshold=0.3, confidence=0.5)
    first = app.world.confront({"m": 0.5})[0]
    again = app.world.confront({"m": 0.5})[0]
    assert again.prior == again.posterior == first.posterior


def test_confidence_bounded_and_validation(app):
    app.world.add("x > 0", metric="x", comparator="gt", threshold=0, confidence=0.9)
    for day in range(30):
        app.kernel.clock.advance(days=1)
        app.world.confront({"x": 100 + day})
    assert app.world.list()[0].confidence <= 0.98
    with pytest.raises(ValueError):
        app.world.add("incomplète", metric="x")


def test_inconsistent_and_stale_beliefs(app):
    app.world.add("Churn < 2 %", metric="churn", comparator="lt", threshold=0.02, confidence=0.8)
    app.world.add("Churn > 5 %", metric="churn", comparator="gt", threshold=0.05, confidence=0.7)
    assert len(app.world.inconsistencies()) == 1
    app.kernel.clock.advance(days=40)
    assert len(app.world.stale(30)) == 2


def test_learned_belief_is_stable(app):
    b1 = app.world.learn("strategy:seo", "SEO réussit", 0.7, 4)
    b2 = app.world.learn("strategy:seo", "SEO réussit", 0.8, 5)
    assert b1.id == b2.id and b2.confidence == pytest.approx(0.8)


# -- hypothèses, priorisation, simulation ---------------------------------------------------------------
def test_hypotheses_target_the_right_family(app):
    app.world.add("CAC < 80", metric="marketing.cac", comparator="lt", threshold=80, confidence=0.7)
    contradictions = app.world.confront({"marketing.cac": 200})
    hyps = explain(contradictions)
    assert hyps and hyps[0].direction == "au-dessus"
    assert any("canal" in h.statement for h in hyps)


def test_prioritization_rewards_roi_and_merges_duplicates():
    cheap = rec(
        title="A",
        impact=6,
        effort=2,
        confidence=0.7,
        estimated_value=Decimal("1000"),
        estimated_cost=Decimal("100"),
    )
    costly = rec(
        title="B",
        impact=6,
        effort=2,
        confidence=0.7,
        estimated_value=Decimal("1000"),
        estimated_cost=Decimal("900"),
    )
    assert score(cheap) > score(costly)
    dup = rec(title="A", impact=6, effort=2, confidence=0.5, evidence=["autre angle"])
    merged = merge([cheap, dup])
    assert len(merged) == 1 and merged[0].confidence > 0.7 and "autre angle" in merged[0].evidence
    assert prioritize([costly, cheap])[0].recommendation.title == "A"


def test_simulation_is_reproducible_and_classifies_risk():
    safe = rec(title="Interne", impact=7, effort=2, confidence=0.8, risk=0.1)
    r1, r2 = simulate(safe), simulate(safe)
    assert r1 == r2 and r1.risk_class == "low" and r1.unit == "impact_points"
    spend = rec(title="Achat", estimated_cost=Decimal("400"), estimated_value=Decimal("300"), confidence=0.4)
    result = simulate(spend, available_budget=Decimal("1000"))
    assert result.risk_class == "high" and result.p_loss > 0.5 and result.unit == "money"
    assert any("dépense" in reason for reason in result.reasons)


# -- conseil --------------------------------------------------------------------------------------------
def test_council_budget_veto():
    proposal = rec(
        title="Grosse campagne",
        impact=8,
        estimated_cost=Decimal("5000"),
        estimated_value=Decimal("20000"),
        confidence=0.7,
        tags=["marketing"],
    )
    verdict = Council().deliberate(
        proposal, CouncilFacts(budget_available=Decimal("1000"), p_loss=0.2, expected_value=3000)
    )
    assert verdict.vetoes and verdict.verdict in ("reporter", "rejeter")
    assert any(p.member == "CFO" and p.veto for p in verdict.positions)


def test_council_adopts_strong_safe_proposal():
    proposal = rec(
        title="Optimiser le tunnel",
        impact=8,
        effort=2,
        confidence=0.8,
        risk=0.1,
        urgency=8,
        evidence=["taux de conversion 1,2 %"],
        tags=["clients"],
    )
    verdict = Council().deliberate(
        proposal,
        CouncilFacts(
            budget_available=Decimal("1000"),
            p_loss=0.05,
            expected_value=5,
            strategic_fit=0.8,
            past_success_rate=0.8,
            past_samples=5,
        ),
    )
    assert verdict.verdict == "adopter" and not verdict.vetoes
    assert len(verdict.positions) == 6 and verdict.minutes[0].startswith("Ouverture")


def test_council_risk_veto_on_irreversible_uncertain():
    proposal = rec(title="Pivot total", impact=9, confidence=0.3, reversible=False)
    verdict = Council().deliberate(proposal, CouncilFacts(p_loss=0.3, expected_value=1))
    assert any(v.startswith("RISQUE") for v in verdict.vetoes) and verdict.verdict == "rejeter"


def test_council_llm_enrichment_is_optional_and_non_binding():
    class FakeLLM:
        def structured(self, system, prompt, model, **kwargs):
            return model.model_validate({"members": [{"member": "CEO", "additional_arguments": ["Argument IA"]}]})

    proposal = rec(title="X", impact=7, confidence=0.7)
    plain = Council().deliberate(proposal, CouncilFacts(p_loss=0.1, expected_value=2))
    enriched = Council(llm=FakeLLM()).deliberate(proposal, CouncilFacts(p_loss=0.1, expected_value=2), use_llm=True)
    assert enriched.llm_enriched and enriched.verdict == plain.verdict
    assert any(a.text.startswith("[IA]") for p in enriched.positions for a in p.arguments)


# -- directeur ------------------------------------------------------------------------------------------
def _declining(app):
    for month, amount in [
        ("2026-07", 4000),
        ("2026-08", 3500),
        ("2026-09", 3000),
        ("2026-10", 2500),
        ("2026-11", 2000),
    ]:
        app.kernel.finance.record_collection(amount, f"{month}-05")
    app.kernel.finance.issue_invoice("Retard SA", "3000", "2026-09-01", "2026-10-01")


def test_director_full_loop(app):
    _declining(app)
    app.world.add(
        "Les encaissements progressent",
        metric="finance.collections_trend",
        comparator="gt",
        threshold=0,
        confidence=0.7,
    )
    report = app.director.run("Comment vont nos finances et le budget ?", deep=True)
    names = [p.name for p in report.phases]
    for phase in (
        "OBSERVER",
        "COMPRENDRE",
        "CONFRONTER",
        "HYPOTHÈSES",
        "ANTICIPER",
        "PRIORISER",
        "DÉCIDER",
        "SIMULER",
        "EXÉCUTER / REVUE HUMAINE",
        "MESURER",
        "APPRENDRE / RÉVISER",
        "DREAMING",
    ):
        assert phase in names
    assert report.selected[0]["name"] == "finance-budget"
    assert any(c.outcome == "contradiction" for c in report.confrontations)
    assert report.hypotheses
    routes = {d.route for d in report.decisions}
    assert "human_review" in routes  # relancer un client = action externe
    assert app.approvals.list("pending")
    assert report.report_id and app.kernel.db.scalar("SELECT COUNT(*) FROM reports") == 1


def test_director_does_not_redecide_handled_recommendations(app):
    _declining(app)
    first = app.director.run("budget")
    second = app.director.run("budget")
    launched_first = {d.priority.recommendation.id for d in first.decisions if d.route != "none"}
    handled = {d.priority.recommendation.id for d in second.decisions if d.route == "already_handled"}
    assert launched_first and launched_first <= handled


def test_director_dry_run_executes_nothing(app):
    _declining(app)
    queued_before = app.queue.stats()["queued"]  # tâches créées par les règles (encaissements)
    assert queued_before == 1  # 5 encaissements coalescés en un seul diagnostic
    report = app.director.run("budget", execute=False)
    assert all(d.route in ("none",) for d in report.decisions)
    assert not app.approvals.list("pending") and app.queue.stats()["queued"] == queued_before


def test_low_risk_follow_up_is_enqueued(app):
    follow = rec(
        title="Analyser la marge",
        impact=7,
        effort=2,
        confidence=0.85,
        risk=0.05,
        urgency=7,
        follow_up=FollowUp(skill="finance-budget", action="forecast", payload={"months": 3}),
    )
    records = app.director._decide(prioritize([follow]), 3, "mis_test", True, False, app.kernel.clock.now())
    assert records[0].route == "execute" and records[0].task_id
    assert app.worker.run_once() == 1
    task = app.queue.get(records[0].task_id)
    assert task.status == "succeeded"


def test_auto_measurement_closes_decisions(app, clock):
    decision = app.kernel.memory.record(
        "Hausse de l'enveloppe",
        "Relancer",
        category="finance",
        expected={"metric": "finance.envelope", "baseline": 0, "target": 100},
        review_at=clock.now(),
    )
    clock.advance(days=1)
    app.kernel.finance.record_collection("1000", "2026-11-17")
    app.kernel.run_skill("finance-budget", "assess")
    measured = app.director._measure()
    assert measured and measured[0]["decision_id"] == decision.id and measured[0]["status"] == "succeeded"


def test_approval_flow_creates_expense_and_memory(app):
    app.kernel.finance.record_collection("10000", "2026-11-01")
    proposal = rec(
        title="Outil CRM",
        estimated_cost=Decimal("200"),
        estimated_value=Decimal("1500"),
        confidence=0.7,
        tags=["outils"],
    )
    approval = app.approvals.create(
        "recommendation",
        proposal.title,
        "test",
        {"recommendation": proposal.model_dump(mode="json")},
        {"class": "high"},
    )
    effects = app.approvals.decide(approval.id, approve=True, actor="fondateur")
    assert effects["expense"]["status"] == "approved"
    assert app.kernel.finance.available() == Decimal("800.00")
    assert app.kernel.memory.get(effects["decision_id"]).source == "human:fondateur"
    with pytest.raises(ValueError):
        app.approvals.decide(approval.id, approve=False, actor="x")
