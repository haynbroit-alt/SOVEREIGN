from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from sovereign.skills.base import SkillError

SKILL = "analysis-optimization"


def _project(**overrides):
    base = {
        "id": "p1",
        "name": "Plateforme",
        "kpis": [
            {"name": "revenu", "value": 80, "target": 100, "direction": "higher", "weight": 2},
            {"name": "churn", "value": 5, "target": 4, "direction": "lower"},
            {"name": "nps", "value": 60, "target": 50},
        ],
    }
    base.update(overrides)
    return base


def _features_project():
    return {
        "id": "app",
        "name": "Appli",
        "active_users": 1000,
        "features": [
            {"name": "A", "users": 600, "uses": 5000},
            {"name": "B", "users": 300, "uses": 2000},
            {"name": "C", "users": 20, "uses": 40, "maintenance_cost_monthly": "200"},
            {"name": "D", "users": 10, "uses": 20, "released_on": "2026-11-07"},
            {"name": "E", "users": 400, "uses": 1500},
            {"name": "F", "users": 30, "uses": 100},
        ],
    }


def test_health_is_weighted_and_capped(kernel):
    out = kernel.run_skill(SKILL, "analyze_projects", {"projects": [_project()]})
    project = out.projects[0]
    by_name = {k.name: k for k in project.kpis}
    assert by_name["revenu"].attainment == pytest.approx(0.8)
    assert by_name["churn"].attainment == pytest.approx(0.8)  # 4 / 5 : plus bas = mieux
    assert by_name["nps"].attainment == pytest.approx(1.2)
    # (0,8×2 + 0,8×1 + min(1,2 ; 1)×1) / 4 = 0,85
    assert project.health == pytest.approx(0.85)
    assert project.status == "at_risk"
    assert len(project.weak_points) == 2
    assert out.metrics["projects.p1.health"] == pytest.approx(0.85)
    assert out.average_health == pytest.approx(0.85)


def test_off_track_project_gets_recommendation(kernel):
    project = _project(kpis=[{"name": "ventes", "value": 30, "target": 100}])
    out = kernel.run_skill(SKILL, "analyze_projects", {"projects": [project]})
    assert out.projects[0].status == "off_track"
    assert any(i.severity == "critical" for i in out.insights)
    rec = out.recommendations[0]
    assert "ventes" in rec.title and rec.urgency == 8 and rec.skill == SKILL


def test_lower_is_better_with_zero_value_and_degrading_trend(kernel):
    project = _project(
        kpis=[
            {"name": "incidents", "value": 0, "target": 2, "direction": "lower"},
            {"name": "conversion", "value": 3.5, "target": 3, "history": [5, 4.5, 4]},
        ]
    )
    out = kernel.run_skill(SKILL, "analyze_projects", {"projects": [project]})
    kpis = {k.name: k for k in out.projects[0].kpis}
    assert kpis["incidents"].attainment == 1.5
    assert kpis["conversion"].trend == "degrading"
    assert kpis["conversion"].trend_slope == pytest.approx(-0.5)
    assert any("tendance défavorable" in w for w in out.projects[0].weak_points)
    assert any(i.kind == "trend" for i in out.insights)


def test_unused_features_threshold_quintile_recent_and_pareto(kernel):
    out = kernel.run_skill(SKILL, "detect_unused_features", {"projects": [_features_project()]})
    assert [f.feature for f in out.unused] == ["C"]  # D est récente, F n'est pas dans le quintile inférieur
    rows = {f.feature: f for f in out.features}
    assert rows["D"].recent and not rows["D"].unused
    assert rows["C"].user_share == pytest.approx(0.02)
    assert {f.feature for f in out.features if f.in_pareto} == {"A", "B"}
    assert out.wasted_monthly_cost == Decimal("200.00")
    rec = out.recommendations[0]
    assert rec.estimated_value == Decimal("2400.00") and rec.external
    assert out.metrics["features.unused_count"] == 1.0


def test_improvement_backlog_sorted_by_ice(kernel):
    projects = [_project(), _features_project()]
    out = kernel.run_skill(SKILL, "recommend_improvements", {"projects": projects})
    ices = [item.ice for item in out.backlog]
    assert ices == sorted(ices, reverse=True)
    kinds = {item.kind for item in out.backlog}
    assert {"kpi", "fonctionnalité"} <= kinds
    assert out.recommendations and len(out.recommendations) <= 5


def test_verify_change_improved_degraded_and_inconclusive(kernel):
    before = [10, 11, 9, 10, 10.5, 9.5]
    after = [12, 13, 12.5, 11.5, 12, 13]
    improved = kernel.run_skill(
        SKILL, "verify_change", {"metric": "ventes", "before": before, "after": after}
    )
    assert improved.verdict == "amélioré" and improved.p_value < 0.05
    assert improved.effect == pytest.approx(2.3333, abs=1e-3)
    assert improved.cohens_d is not None and improved.cohens_d > 1
    degraded = kernel.run_skill(
        SKILL, "verify_change", {"metric": "délai", "before": before, "after": after, "goal": "decrease"}
    )
    assert degraded.verdict == "dégradé" and degraded.recommendations[0].urgency == 8
    flat = kernel.run_skill(SKILL, "verify_change", {"before": [10, 12, 9, 11], "after": [10.5, 11, 12, 9.5]})
    assert flat.verdict == "non concluant"
    assert flat.sample_size_needed is not None and flat.sample_size_needed > 4
    assert "Prolonger" in flat.recommendations[0].title


def test_verify_recorded_change_infers_goal_from_project(kernel):
    kernel.store.put("project", "p1", _project())
    kernel.store.put(
        "change",
        "c1",
        {
            "id": "c1",
            "project_id": "p1",
            "description": "Onboarding guidé",
            "metric": "churn",
            "before": [5, 5.2, 4.9, 5.1],
            "after": [4, 4.1, 3.9, 4.2],
        },
    )
    out = kernel.run_skill(SKILL, "verify_change", {"change_id": "c1"})
    assert out.goal == "decrease" and out.verdict == "amélioré"


def test_verify_change_input_errors(kernel):
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "verify_change", {"before": [1], "after": [2, 3]})
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "verify_change", {"change_id": "inconnu"})
    with pytest.raises(ValidationError):
        kernel.run_skill(
            SKILL,
            "analyze_projects",
            {"projects": [_project(kpis=[{"name": "x", "value": 1, "target": 1, "weight": 0}])]},
        )


def test_audit_system_needs_history_then_flags(kernel):
    first = kernel.run_skill(SKILL, "audit_system", {})
    assert not first.sufficient_history and first.never_used_skills == []
    for _ in range(20):
        kernel.run_skill("finance-budget", "status")
    for i in range(10):
        kernel.db.execute(
            "INSERT INTO skill_runs(skill, action, ok, duration_ms, at) "
            "VALUES ('memory-learning', 'search', ?, 1, "
            "'2026-11-17T09:00:00+00:00')",
            (0 if i < 2 else 1,),
        )
    for i in range(5):
        kernel.db.execute(
            "INSERT INTO recommendation_log(id, skill, title, status, at) VALUES (?, 'finance-budget', 't', "
            "'proposed', '2026-11-17')",
            (f"rec_{i}",),
        )
    for _ in range(20):
        kernel.db.execute(
            "INSERT INTO command_runs(command, ok, duration_ms, source, at) VALUES ('/director', 1, 1,"
            " 'cli', '2026-11-17')"
        )
    out = kernel.run_skill(SKILL, "audit_system", {})
    assert out.sufficient_history
    used = {"finance-budget", "memory-learning", SKILL}  # SKILL : le premier audit a été compté
    assert out.never_used_skills == [n for n in kernel.registry.names if n not in used]
    assert out.unreliable_skills == ["memory-learning"]
    assert "finance-budget" in out.unadopted_skills
    assert "strategy" in out.never_used_commands and "director" not in out.never_used_commands
    assert any("Fiabiliser" in r.title for r in out.recommendations)
    assert out.metrics["system.skills_unreliable"] == 1.0


def test_assess_empty_and_with_workspace(kernel):
    empty = kernel.run_skill(SKILL, "assess")
    assert "Aucun projet" in empty.summary and empty.projects_analyzed == 0
    kernel.store.put("project", "p1", _project())
    kernel.store.put("project", "app", _features_project())
    kernel.store.put(
        "change", "c1", {"id": "c1", "metric": "nps", "before": [50, 52, 51], "after": [60, 61, 59]}
    )
    kernel.store.put("change", "bad", {"id": "bad", "metric": "x", "before": [1], "after": [2]})
    out = kernel.run_skill(SKILL, "assess")
    assert out.projects_analyzed == 2 and out.unused_features == 1 and out.changes_verified == 1
    assert "projects.health_avg" in out.metrics
    assert out.backlog and out.recommendations
    assert any("non vérifiable" in i.title for i in out.insights)


def test_invalid_workspace_project_raises(kernel):
    kernel.store.put("project", "broken", {"id": "broken"})
    with pytest.raises(SkillError):
        kernel.run_skill(SKILL, "analyze_projects", {})
