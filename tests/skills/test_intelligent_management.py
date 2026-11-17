from __future__ import annotations

import pytest
from pydantic import ValidationError

SKILL = "intelligent-management"

AGENTS = [
    {
        "id": "a1",
        "name": "Rédacteur",
        "kind": "ai",
        "capabilities": ["SEO", "rédaction"],
        "capacity": 2,
        "reliability": 0.9,
        "cost_per_mission": "5",
    },
    {
        "id": "a2",
        "name": "Analyste SEO",
        "kind": "ai",
        "capabilities": ["seo"],
        "capacity": 1,
        "reliability": 0.95,
    },
    {"id": "a3", "name": "Media buyer", "kind": "human", "capabilities": ["ads"], "capacity": 1},
]
MISSIONS = [
    {"id": "m1", "title": "Article SEO", "required_capabilities": ["seo", "rédaction"], "priority": 8},
    {"id": "m2", "title": "Audit SEO", "required_capabilities": ["seo"], "priority": 9},
    {"id": "m3", "title": "Campagne ads", "required_capabilities": ["ads"], "priority": 5},
    {"id": "m4", "title": "Logo", "required_capabilities": ["design"], "priority": 7},
    {"id": "m5", "title": "Mots-clés", "required_capabilities": ["seo"], "priority": 3},
]


def _seed(kernel, agents=AGENTS, missions=MISSIONS):
    for agent in agents:
        kernel.store.put("agent", agent["id"], agent)
    for mission in missions:
        kernel.store.put("mission", mission["id"], mission)


def test_assignment_respects_priority_capabilities_and_capacity(kernel):
    _seed(kernel)
    out = kernel.run_skill(SKILL, "assign_missions", {})
    mapping = {a.mission_id: a.agent_id for a in out.assignments}
    assert mapping == {"m2": "a2", "m1": "a1", "m3": "a3", "m5": "a1"}
    assert [u.mission_id for u in out.unassigned] == ["m4"]
    assert not out.applied
    assert "assigned_to" not in kernel.store.get("mission", "m1")  # simulation : rien n'est écrit
    assert any("Recruter" in r.title for r in out.recommendations)


def test_assignment_applied_is_persisted(kernel):
    _seed(kernel)
    out = kernel.run_skill(SKILL, "assign_missions", {"apply": True})
    assert out.applied
    stored = kernel.store.get("mission", "m2")
    assert stored["status"] == "assigned" and stored["assigned_to"] == "a2"
    again = kernel.run_skill(SKILL, "assign_missions", {})
    assert not again.assignments  # plus de missions « todo » affectables


def test_failed_dependency_blocks_assignment(kernel):
    _seed(
        kernel,
        missions=[
            {
                "id": "x",
                "title": "Préparer",
                "required_capabilities": ["seo"],
                "status": "failed",
                "assigned_to": "a2",
            },
            {"id": "y", "title": "Publier", "required_capabilities": ["seo"], "depends_on": ["x"]},
        ],
    )
    out = kernel.run_skill(SKILL, "assign_missions", {})
    assert out.unassigned[0].mission_id == "y" and "échouée" in out.unassigned[0].reason


def test_register_agent(kernel):
    out = kernel.run_skill(
        SKILL, "register_agent", {"id": "z", "name": "Nouveau", "capabilities": ["vente"], "capacity": 2}
    )
    assert out.created and kernel.store.get("agent", "z")["capacity"] == 2
    again = kernel.run_skill(SKILL, "register_agent", {"id": "z", "name": "Nouveau", "capacity": 3})
    assert not again.created
    with pytest.raises(ValidationError):
        kernel.run_skill(SKILL, "register_agent", {"id": "w", "name": "X", "capacity": 0})


def test_performance_and_underperformance(kernel):
    missions = [
        *[
            {
                "id": f"d{i}",
                "title": "ok",
                "status": "done",
                "assigned_to": "a1",
                "quality": 0.9,
                "duration_hours": 2,
                "cost": "10",
            }
            for i in range(3)
        ],
        {"id": "p1", "title": "ok", "status": "done", "assigned_to": "a2", "quality": 0.5},
        {"id": "p2", "title": "ko", "status": "failed", "assigned_to": "a2"},
        {"id": "p3", "title": "ko", "status": "failed", "assigned_to": "a2"},
    ]
    _seed(kernel, missions=missions)
    out = kernel.run_skill(SKILL, "measure_performance", {})
    perf = {a.agent_id: a for a in out.agents}
    assert perf["a1"].success_rate == 1.0 and perf["a1"].success_probability == pytest.approx(0.8)
    assert perf["a1"].average_duration_hours == 2 and str(perf["a1"].average_cost) == "10.00"
    assert perf["a2"].success_rate == pytest.approx(0.333, abs=1e-3)
    assert perf["a2"].underperforming and not perf["a1"].underperforming
    assert any("a2" not in r.title and "Analyste" in r.title for r in out.recommendations)


def test_all_conflict_types(kernel):
    missions = [
        {
            "id": "r1",
            "title": "Tournage",
            "status": "in_progress",
            "assigned_to": "a1",
            "resources": ["studio"],
            "start": "2026-11-18T09:00:00Z",
            "due": "2026-11-18T12:00:00Z",
        },
        {
            "id": "r2",
            "title": "Photos",
            "status": "assigned",
            "assigned_to": "a2",
            "resources": ["studio"],
            "start": "2026-11-18T11:00:00Z",
            "due": "2026-11-18T15:00:00Z",
        },
        {
            "id": "r3",
            "title": "Hors créneau",
            "status": "assigned",
            "assigned_to": "a1",
            "resources": ["studio"],
            "start": "2026-11-19T09:00:00Z",
            "due": "2026-11-19T10:00:00Z",
        },
        {
            "id": "c1",
            "title": "Monter le prix",
            "recommendation": {"target": "prix Pro", "direction": "increase"},
        },
        {
            "id": "c2",
            "title": "Baisser le prix",
            "recommendation": {"target": "Prix Pro", "direction": "decrease"},
        },
        {"id": "o1", "title": "Ads 1", "status": "in_progress", "assigned_to": "a3"},
        {"id": "o2", "title": "Ads 2", "status": "assigned", "assigned_to": "a3"},
        {"id": "y1", "title": "A", "depends_on": ["y2"]},
        {"id": "y2", "title": "B", "depends_on": ["y1"]},
        {"id": "f1", "title": "Échouée", "status": "failed"},
        {"id": "f2", "title": "Suite", "depends_on": ["f1"]},
    ]
    _seed(kernel, missions=missions)
    out = kernel.run_skill(SKILL, "detect_conflicts", {})
    kinds = {c.kind for c in out.conflicts}
    assert kinds == {"resource", "contradiction", "overload", "dependency_cycle", "failed_dependency"}
    resource = [c for c in out.conflicts if c.kind == "resource"]
    assert len(resource) == 1 and set(resource[0].missions) == {"r1", "r2"}  # r3 ne chevauche pas
    assert any(r.title.startswith("Arbitrer") for r in out.recommendations)
    assert any(r.title.startswith("Casser") for r in out.recommendations)


def test_coordination_rebalances_and_sequences(kernel):
    agents = [
        {"id": "busy", "name": "Surchargé", "capabilities": ["seo"], "capacity": 1},
        {"id": "free", "name": "Disponible", "capabilities": ["seo", "ads"], "capacity": 2},
    ]
    missions = [
        {
            "id": "s1",
            "title": "Base",
            "status": "in_progress",
            "assigned_to": "busy",
            "priority": 9,
            "required_capabilities": ["seo"],
        },
        {
            "id": "s2",
            "title": "Suite",
            "status": "assigned",
            "assigned_to": "busy",
            "priority": 2,
            "required_capabilities": ["seo"],
            "depends_on": ["s1"],
        },
        {"id": "s3", "title": "Final", "status": "todo", "priority": 10, "depends_on": ["s2"]},
    ]
    _seed(kernel, agents=agents, missions=missions)
    out = kernel.run_skill(SKILL, "coordination_report", {})
    assert [(m.mission_id, m.to_agent) for m in out.moves] == [("s2", "free")]
    assert out.sequence == ["s1", "s2", "s3"]
    assert out.unsequenced == []
    assert any("Rééquilibrer" in r.title for r in out.recommendations)


def test_assess_empty_and_with_data(kernel):
    empty = kernel.run_skill(SKILL, "assess")
    assert empty.agents == 0 and "Aucun agent" in empty.summary
    _seed(kernel)
    out = kernel.run_skill(SKILL, "assess")
    assert out.agents == 3 and out.missions_by_status == {"todo": 5}
    assert out.metrics["management.utilization"] == 0.0
    assert any("Affecter 4 mission" in r.title for r in out.recommendations)
    assert any(i.title.startswith("1 mission(s) sans agent") for i in out.insights)


def test_explicit_lists_override_workspace(kernel):
    _seed(kernel)
    out = kernel.run_skill(
        SKILL,
        "assign_missions",
        {
            "agents": [{"id": "solo", "name": "Solo", "capabilities": ["design"], "capacity": 1}],
            "missions": [{"id": "d", "title": "Logo", "required_capabilities": ["design"]}],
        },
    )
    assert [(a.mission_id, a.agent_id) for a in out.assignments] == [("d", "solo")]
