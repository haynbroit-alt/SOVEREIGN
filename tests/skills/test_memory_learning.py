from __future__ import annotations

import pytest

from sovereign.core.memory import MemoryError_


def _decide(kernel, title, category="pricing", tags=("pro",), expected=None, review=None):
    return kernel.run_skill(
        "memory-learning",
        "record_decision",
        {
            "title": title,
            "decision": f"Faire {title}",
            "category": category,
            "tags": list(tags),
            "expected": expected,
            "review_in_days": review,
        },
    ).decision


def test_record_and_search_accent_insensitive(kernel):
    _decide(kernel, "Hausse des prix de l'offre Équipe")
    hits = kernel.run_skill("memory-learning", "search", {"query": "equipe prix"}).hits
    assert hits and "Équipe" in hits[0].title


def test_outcome_scoring(kernel):
    decision = _decide(kernel, "Offre Pro", expected={"metric": "mrr", "baseline": 1000, "target": 2000})
    done = kernel.run_skill("memory-learning", "record_outcome", {"decision_id": decision.id, "actual": 1950})
    assert done.decision.status == "succeeded" and done.decision.score == pytest.approx(0.95)
    other = _decide(kernel, "Offre Lite", expected={"metric": "mrr", "baseline": 1000, "target": 2000})
    failed = kernel.run_skill("memory-learning", "record_outcome", {"decision_id": other.id, "actual": 1100})
    assert failed.decision.status == "failed" and "Échec" in failed.decision.lesson


def test_outcome_requires_status_without_numbers(kernel):
    decision = _decide(kernel, "Partenariat")
    with pytest.raises(MemoryError_):
        kernel.memory.record_outcome(decision.id)
    closed = kernel.memory.record_outcome(decision.id, status="partial")
    assert closed.score == 0.5


def test_repeated_errors_and_adaptation(kernel):
    for i in range(3):
        d = _decide(kernel, f"Campagne display {i}", category="ads-display", tags=("display",))
        kernel.memory.record_outcome(d.id, status="failed")
    for i in range(4):
        d = _decide(kernel, f"SEO article {i}", category="seo", tags=("contenu",))
        kernel.memory.record_outcome(d.id, status="succeeded")
    errors = kernel.run_skill("memory-learning", "detect_repeated_errors", {})
    assert errors.patterns[0].occurrences == 3
    assert any(i.severity == "critical" for i in errors.insights)
    adapt = kernel.run_skill("memory-learning", "adapt_strategies", {"min_samples": 3})
    advice = {s.group: s.advice for s in adapt.strategies}
    assert advice["ads-display"] == "éviter"
    assert advice["seo"] == "favoriser"


def test_similar_failure_warning(kernel):
    d = _decide(kernel, "Campagne radio locale", category="radio")
    kernel.memory.record_outcome(d.id, status="failed")
    again = kernel.run_skill(
        "memory-learning",
        "record_decision",
        {"title": "Campagne radio nationale", "decision": "Radio", "category": "radio"},
    )
    assert any(i.kind == "risk" for i in again.insights)


def test_due_reviews_and_assess(kernel, clock):
    _decide(kernel, "Embauche commercial", review=7)
    assert kernel.run_skill("memory-learning", "assess").due_reviews == []
    clock.advance(days=8)
    out = kernel.run_skill("memory-learning", "assess")
    assert len(out.due_reviews) == 1
    assert any("Mesurer" in r.title for r in out.recommendations)


def test_adopted_recommendations_are_tracked(kernel):
    kernel.run_skill(
        "memory-learning",
        "record_decision",
        {
            "title": "Suivre la reco",
            "decision": "ok",
            "skills": ["finance-budget"],
            "recommendation_ids": ["rec_abc"],
        },
    )
    assert kernel.skill_utility()["finance-budget"]["adopted"] == 1
