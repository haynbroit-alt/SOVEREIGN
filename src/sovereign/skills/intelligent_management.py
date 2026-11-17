"""SKILL 4 — MANAGEMENT INTELLIGENT.

Organise les équipes d'agents (IA, humains, services), distribue les missions selon les capacités et la
charge, mesure les performances (a posteriori bêta), détecte les conflits (ressources, recommandations
contradictoires,
surcharge, cycles de dépendances, dépendances échouées) et propose une meilleure coordination.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sovereign.core.money import to_decimal
from sovereign.core.stats import BetaPosterior, median
from sovereign.skills.base import (
    EmptyInput,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillOutput,
    TargetMetric,
    action,
)

ACTIVE = ("assigned", "in_progress")
CLOSED = ("done", "failed")


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    kind: Literal["ai", "human", "service"] = "ai"
    capabilities: list[str] = Field(default_factory=list)
    capacity: int = Field(1, ge=1)
    cost_per_mission: Decimal | None = Field(None, ge=0)
    reliability: float | None = Field(None, ge=0, le=1)


class MissionRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target: str
    direction: Literal["increase", "decrease"]


class MissionModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    required_capabilities: list[str] = Field(default_factory=list)
    priority: int = Field(5, ge=1, le=10)
    effort: float | None = Field(None, ge=0)
    status: Literal["todo", "assigned", "in_progress", "done", "failed"] = "todo"
    assigned_to: str | None = None
    resources: list[str] = Field(default_factory=list)
    start: datetime | None = None
    due: datetime | None = None
    depends_on: list[str] = Field(default_factory=list)
    quality: float | None = Field(None, ge=0, le=1)
    duration_hours: float | None = Field(None, ge=0)
    cost: Decimal | None = Field(None, ge=0)
    recommendation: MissionRecommendation | None = None


class TeamInput(BaseModel):
    """Agents et missions explicites ; listes vides ⇒ données de l'espace de travail."""

    agents: list[AgentModel] = Field(default_factory=list)
    missions: list[MissionModel] = Field(default_factory=list)


class AssignInput(TeamInput):
    apply: bool = Field(False, description="Vrai : enregistre les affectations ; faux : simulation")


class RegisterAgentOutput(SkillOutput):
    agent: AgentModel
    created: bool


class Assignment(BaseModel):
    mission_id: str
    mission_title: str
    agent_id: str
    agent_name: str
    priority: int
    reasons: list[str]


class Unassigned(BaseModel):
    mission_id: str
    mission_title: str
    reason: str


class AssignOutput(SkillOutput):
    assignments: list[Assignment]
    unassigned: list[Unassigned]
    applied: bool


class AgentPerformance(BaseModel):
    agent_id: str
    name: str
    done: int
    failed: int
    success_rate: float | None
    success_probability: float
    average_quality: float | None
    average_duration_hours: float | None
    average_cost: Decimal | None
    load: int
    capacity: int
    utilization: float
    underperforming: bool
    reasons: list[str]


class PerformanceOutput(SkillOutput):
    agents: list[AgentPerformance]


class Conflict(BaseModel):
    kind: Literal["resource", "contradiction", "overload", "dependency_cycle", "failed_dependency"]
    severity: Literal["info", "warning", "critical"]
    missions: list[str]
    agents: list[str]
    detail: str


class ConflictsOutput(SkillOutput):
    conflicts: list[Conflict]


class Move(BaseModel):
    mission_id: str
    from_agent: str
    to_agent: str
    reason: str


class CoordinationOutput(SkillOutput):
    conflicts: list[Conflict]
    moves: list[Move]
    sequence: list[str]
    unsequenced: list[str]


class AssessOutput(SkillOutput):
    agents: int
    missions_by_status: dict[str, int]
    utilization: float | None
    conflicts: int


def _caps(values: list[str]) -> set[str]:
    return {v.strip().lower() for v in values if v.strip()}


def _load_team(ctx: SkillContext, data: TeamInput) -> tuple[list[AgentModel], list[MissionModel]]:
    agents = data.agents or [AgentModel.model_validate(a) for a in ctx.store.list("agent")]
    missions = data.missions or [MissionModel.model_validate(m) for m in ctx.store.list("mission")]
    return agents, missions


def _current_load(missions: list[MissionModel]) -> dict[str, int]:
    load: dict[str, int] = defaultdict(int)
    for mission in missions:
        if mission.status in ACTIVE and mission.assigned_to:
            load[mission.assigned_to] += 1
    return load


def _overlap(a: MissionModel, b: MissionModel) -> bool:
    low = datetime.min.replace(tzinfo=None)
    a_start = a.start.replace(tzinfo=None) if a.start else low
    b_start = b.start.replace(tzinfo=None) if b.start else low
    a_end = a.due.replace(tzinfo=None) if a.due else datetime.max
    b_end = b.due.replace(tzinfo=None) if b.due else datetime.max
    return a_start < b_end and b_start < a_end


def _cycles(missions: list[MissionModel]) -> list[list[str]]:
    graph = {m.id: [d for d in m.depends_on] for m in missions}
    state: dict[str, int] = {}
    stack: list[str] = []
    found: list[list[str]] = []
    seen_cycles: set[frozenset[str]] = set()

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                cycle = stack[stack.index(nxt) :]
                key = frozenset(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    found.append([*cycle, nxt])
            elif state.get(nxt) is None:
                visit(nxt)
        stack.pop()
        state[node] = 2

    for node in graph:
        if state.get(node) is None:
            visit(node)
    return found


class IntelligentManagementSkill(Skill):
    name = "intelligent-management"
    version = "1.0.0"

    @action("Diagnostic des équipes d'agents : charge, performances, conflits")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        agents, missions = _load_team(ctx, TeamInput())
        by_status: dict[str, int] = defaultdict(int)
        for mission in missions:
            by_status[mission.status] += 1
        if not agents and not missions:
            return AssessOutput(
                agents=0,
                missions_by_status={},
                utilization=None,
                conflicts=0,
                summary="Aucun agent ni mission : importez des enregistrements « agent » et « mission » "
                "(ou utilisez register_agent) pour organiser les équipes.",
            )
        load = _current_load(missions)
        capacity = sum(a.capacity for a in agents)
        utilization = (sum(load.get(a.id, 0) for a in agents) / capacity) if capacity else None
        report = self.coordination_report(ctx, TeamInput(agents=agents, missions=missions))
        perf = self.measure_performance(ctx, TeamInput(agents=agents, missions=missions))
        todo = by_status.get("todo", 0)
        out = AssessOutput(
            agents=len(agents),
            missions_by_status=dict(by_status),
            utilization=round(utilization, 3) if utilization is not None else None,
            conflicts=len(report.conflicts),
            summary=(
                f"{len(agents)} agent(s), {len(missions)} mission(s) dont {todo} à affecter ; "
                f"{len(report.conflicts)} conflit(s) détecté(s)"
                + (f" ; charge {utilization:.0%}." if utilization is not None else ".")
            ),
        )
        out.metrics = {
            "management.agents": float(len(agents)),
            "management.missions_open": float(todo + sum(by_status.get(s, 0) for s in ACTIVE)),
            "management.conflicts": float(len(report.conflicts)),
        }
        if utilization is not None:
            out.metrics["management.utilization"] = round(utilization, 4)
        closed = by_status.get("done", 0) + by_status.get("failed", 0)
        if closed:
            out.metrics["management.success_rate"] = round(by_status.get("done", 0) / closed, 4)
        out.insights.extend(report.insights + perf.insights)
        out.recommendations.extend(report.recommendations + perf.recommendations)
        if todo and agents:
            preview = self.assign_missions(ctx, AssignInput(agents=agents, missions=missions, apply=False))
            if preview.assignments:
                out.recommendations.append(
                    Recommendation(
                        title=f"Affecter {len(preview.assignments)} mission(s) en attente",
                        rationale="Des agents compatibles disposent de capacité libre.",
                        impact=min(10, 3 + sum(a.priority for a in preview.assignments) / 5),
                        effort=1,
                        confidence=0.8,
                        urgency=6,
                        risk=0.05,
                        tags=["management", "affectation"],
                        evidence=[f"{a.mission_title} → {a.agent_name}" for a in preview.assignments[:5]],
                    )
                )
            if preview.unassigned:
                out.insights.append(
                    Insight(
                        kind="weakness",
                        severity="warning",
                        title=f"{len(preview.unassigned)} mission(s) sans agent compatible",
                        detail="; ".join(f"{u.mission_title} : {u.reason}" for u in preview.unassigned[:5]),
                    )
                )
        return out

    @action("Enregistrer ou actualiser un agent (capacités, capacité, coût, fiabilité)", risk="write")
    def register_agent(self, ctx: SkillContext, data: AgentModel) -> RegisterAgentOutput:
        created = ctx.store.get("agent", data.id) is None
        ctx.store.put("agent", data.id, data)
        verb = "enregistré" if created else "actualisé"
        return RegisterAgentOutput(agent=data, created=created, summary=f"Agent {data.name} {verb}.")

    @action("Distribuer les missions « todo » aux agents compatibles (simulation ou application)", risk="write")
    def assign_missions(self, ctx: SkillContext, data: AssignInput) -> AssignOutput:
        agents, missions = _load_team(ctx, data)
        load = _current_load(missions)
        status_by_id = {m.id: m.status for m in missions}
        todo = sorted((m for m in missions if m.status == "todo"), key=lambda m: (-m.priority, m.id))
        assignments: list[Assignment] = []
        unassigned: list[Unassigned] = []
        for mission in todo:
            failed_deps = [d for d in mission.depends_on if status_by_id.get(d) == "failed"]
            if failed_deps:
                unassigned.append(
                    Unassigned(
                        mission_id=mission.id,
                        mission_title=mission.title,
                        reason=f"dépend d'une mission échouée ({', '.join(failed_deps)})",
                    )
                )
                continue
            required = _caps(mission.required_capabilities)
            compatible = [a for a in agents if required <= _caps(a.capabilities)]
            if not compatible:
                unassigned.append(
                    Unassigned(
                        mission_id=mission.id,
                        mission_title=mission.title,
                        reason=f"aucun agent ne couvre {sorted(required)}",
                    )
                )
                continue
            free = [a for a in compatible if load.get(a.id, 0) < a.capacity]
            if not free:
                unassigned.append(
                    Unassigned(
                        mission_id=mission.id,
                        mission_title=mission.title,
                        reason="agents compatibles à pleine capacité",
                    )
                )
                continue
            best = min(
                free,
                key=lambda a: (
                    load.get(a.id, 0) / a.capacity,
                    -(a.reliability if a.reliability is not None else 0.8),
                    a.cost_per_mission if a.cost_per_mission is not None else Decimal("0"),
                    a.id,
                ),
            )
            reasons = [
                f"couvre {sorted(required) or ['aucune exigence']}",
                f"charge {load.get(best.id, 0)}/{best.capacity}",
            ]
            if best.reliability is not None:
                reasons.append(f"fiabilité {best.reliability:.0%}")
            if best.cost_per_mission is not None:
                reasons.append(f"coût {best.cost_per_mission}")
            load[best.id] = load.get(best.id, 0) + 1
            assignments.append(
                Assignment(
                    mission_id=mission.id,
                    mission_title=mission.title,
                    agent_id=best.id,
                    agent_name=best.name,
                    priority=mission.priority,
                    reasons=reasons,
                )
            )
            if data.apply:
                record = mission.model_copy(update={"status": "assigned", "assigned_to": best.id})
                ctx.store.put("mission", record.id, record.model_dump(mode="json"))
        mode = "appliquées" if data.apply else "proposées (simulation)"
        out = AssignOutput(
            assignments=assignments,
            unassigned=unassigned,
            applied=data.apply,
            summary=(f"{len(assignments)} affectation(s) {mode}, {len(unassigned)} mission(s) non affectée(s)."),
        )
        for item in unassigned:
            if "aucun agent" in item.reason:
                out.recommendations.append(
                    Recommendation(
                        title=f"Recruter ou équiper un agent pour « {item.mission_title} »",
                        rationale=item.reason.capitalize() + ".",
                        impact=5,
                        effort=4,
                        confidence=0.7,
                        urgency=5,
                        risk=0.1,
                        tags=["management", "capacités"],
                    )
                )
        return out

    @action("Mesurer les performances des agents (succès, qualité, durée, coût, charge)")
    def measure_performance(self, ctx: SkillContext, data: TeamInput) -> PerformanceOutput:
        agents, missions = _load_team(ctx, data)
        load = _current_load(missions)
        rows: list[AgentPerformance] = []
        for agent in agents:
            own = [m for m in missions if m.assigned_to == agent.id]
            done = [m for m in own if m.status == "done"]
            failed = [m for m in own if m.status == "failed"]
            closed = len(done) + len(failed)
            qualities = [m.quality for m in done if m.quality is not None]
            durations = [m.duration_hours for m in own if m.status in CLOSED and m.duration_hours is not None]
            costs = [m.cost for m in own if m.cost is not None]
            rows.append(
                AgentPerformance(
                    agent_id=agent.id,
                    name=agent.name,
                    done=len(done),
                    failed=len(failed),
                    success_rate=round(len(done) / closed, 3) if closed else None,
                    success_probability=round(BetaPosterior(len(done), len(failed)).mean, 3),
                    average_quality=round(sum(qualities) / len(qualities), 3) if qualities else None,
                    average_duration_hours=round(sum(durations) / len(durations), 2) if durations else None,
                    average_cost=to_decimal(sum(costs, Decimal("0")) / len(costs)) if costs else None,
                    load=load.get(agent.id, 0),
                    capacity=agent.capacity,
                    utilization=round(load.get(agent.id, 0) / agent.capacity, 3),
                    underperforming=False,
                    reasons=[],
                )
            )
        rates = [r.success_rate for r in rows if r.success_rate is not None]
        reference = median(rates) if rates else None
        for row in rows:
            if row.success_rate is not None and reference is not None and row.success_rate < reference - 0.2:
                row.reasons.append(f"taux de succès {row.success_rate:.0%} < médiane {reference:.0%} − 20 pts")
            if row.average_quality is not None and row.average_quality < 0.6:
                row.reasons.append(f"qualité moyenne {row.average_quality:.0%} < 60 %")
            row.underperforming = bool(row.reasons)
        out = PerformanceOutput(agents=rows, summary=f"Performances de {len(rows)} agent(s) mesurées.")
        for row in rows:
            if row.underperforming:
                out.insights.append(
                    Insight(
                        kind="weakness",
                        severity="warning",
                        title=f"Sous-performance : {row.name}",
                        detail="; ".join(row.reasons),
                        value=row.success_rate,
                    )
                )
                out.recommendations.append(
                    Recommendation(
                        title=f"Réduire la charge confiée à {row.name} et analyser ses échecs",
                        rationale="; ".join(row.reasons),
                        impact=5,
                        effort=2,
                        confidence=min(0.9, 0.4 + 0.1 * (row.done + row.failed)),
                        urgency=5,
                        risk=0.1,
                        tags=["management", "performance"],
                        evidence=[f"{row.done} réussies, {row.failed} échouées"],
                    )
                )
        return out

    @action("Détecter les conflits : ressources, contradictions, surcharge, cycles, dépendances échouées")
    def detect_conflicts(self, ctx: SkillContext, data: TeamInput) -> ConflictsOutput:
        agents, missions = _load_team(ctx, data)
        conflicts: list[Conflict] = []
        live = [m for m in missions if m.status not in CLOSED]
        for i, first in enumerate(live):
            for second in live[i + 1 :]:
                shared = set(first.resources) & set(second.resources)
                if shared and _overlap(first, second):
                    conflicts.append(
                        Conflict(
                            kind="resource",
                            severity="warning",
                            missions=[first.id, second.id],
                            agents=[a for a in (first.assigned_to, second.assigned_to) if a],
                            detail=f"Ressource(s) {sorted(shared)} sollicitée(s) simultanément par "
                            f"« {first.title} » et « {second.title} ».",
                        )
                    )
                if (
                    first.recommendation
                    and second.recommendation
                    and first.recommendation.target.lower() == second.recommendation.target.lower()
                    and first.recommendation.direction != second.recommendation.direction
                ):
                    conflicts.append(
                        Conflict(
                            kind="contradiction",
                            severity="critical",
                            missions=[first.id, second.id],
                            agents=[a for a in (first.assigned_to, second.assigned_to) if a],
                            detail=f"« {first.title} » veut {first.recommendation.direction} "
                            f"et « {second.title} » "
                            f"veut {second.recommendation.direction} la même cible « "
                            f"{first.recommendation.target} ».",
                        )
                    )
        load = _current_load(missions)
        for agent in agents:
            if load.get(agent.id, 0) > agent.capacity:
                conflicts.append(
                    Conflict(
                        kind="overload",
                        severity="warning",
                        missions=[m.id for m in missions if m.assigned_to == agent.id and m.status in ACTIVE],
                        agents=[agent.id],
                        detail=(
                            f"{agent.name} porte {load[agent.id]} mission(s) pour une capacité de {agent.capacity}."
                        ),
                    )
                )
        for cycle in _cycles(missions):
            conflicts.append(
                Conflict(
                    kind="dependency_cycle",
                    severity="critical",
                    missions=cycle[:-1],
                    agents=[],
                    detail="Cycle de dépendances : " + " → ".join(cycle),
                )
            )
        failed = {m.id for m in missions if m.status == "failed"}
        for mission in live:
            broken = [d for d in mission.depends_on if d in failed]
            if broken:
                conflicts.append(
                    Conflict(
                        kind="failed_dependency",
                        severity="warning",
                        missions=[mission.id, *broken],
                        agents=[mission.assigned_to] if mission.assigned_to else [],
                        detail=f"« {mission.title} » dépend de mission(s) échouée(s) : {', '.join(broken)}.",
                    )
                )
        out = ConflictsOutput(conflicts=conflicts, summary=f"{len(conflicts)} conflit(s) détecté(s).")
        for conflict in conflicts:
            out.insights.append(
                Insight(
                    kind="risk",
                    severity=conflict.severity,
                    title=f"Conflit ({conflict.kind})",
                    detail=conflict.detail,
                    data={"missions": conflict.missions},
                )
            )
            if conflict.kind in ("contradiction", "dependency_cycle"):
                out.recommendations.append(
                    Recommendation(
                        title=(
                            "Arbitrer les recommandations contradictoires"
                            if conflict.kind == "contradiction"
                            else "Casser le cycle de dépendances"
                        ),
                        expected_metric=TargetMetric(metric="management.conflicts", direction="decrease"),
                        rationale=conflict.detail,
                        impact=6,
                        effort=2,
                        confidence=0.9,
                        urgency=8,
                        risk=0.05,
                        tags=["management", "coordination"],
                        evidence=conflict.missions,
                    )
                )
        return out

    @action("Rapport de coordination : rééquilibrage de charge et séquencement des missions")
    def coordination_report(self, ctx: SkillContext, data: TeamInput) -> CoordinationOutput:
        agents, missions = _load_team(ctx, data)
        conflicts = self.detect_conflicts(ctx, TeamInput(agents=agents, missions=missions))
        load = _current_load(missions)
        by_id = {a.id: a for a in agents}
        moves: list[Move] = []
        for agent in agents:
            excess = load.get(agent.id, 0) - agent.capacity
            if excess <= 0:
                continue
            movable = sorted(
                (m for m in missions if m.assigned_to == agent.id and m.status == "assigned"),
                key=lambda m: (m.priority, m.id),
            )
            for mission in movable:
                if excess <= 0:
                    break
                required = _caps(mission.required_capabilities)
                targets = [
                    a
                    for a in agents
                    if a.id != agent.id and required <= _caps(a.capabilities) and load.get(a.id, 0) < a.capacity
                ]
                if not targets:
                    continue
                target = min(targets, key=lambda a: (load.get(a.id, 0) / a.capacity, a.id))
                moves.append(
                    Move(
                        mission_id=mission.id,
                        from_agent=agent.id,
                        to_agent=target.id,
                        reason=f"{agent.name} surchargé ; {target.name} compatible et disponible",
                    )
                )
                load[agent.id] -= 1
                load[target.id] = load.get(target.id, 0) + 1
                excess -= 1
        # Séquencement topologique (Kahn), priorité décroissante en cas d'égalité.
        pending = {m.id: m for m in missions if m.status not in CLOSED}
        indegree = {mid: sum(1 for d in m.depends_on if d in pending) for mid, m in pending.items()}
        sequence: list[str] = []
        ready = sorted((mid for mid, deg in indegree.items() if deg == 0), key=lambda i: (-pending[i].priority, i))
        while ready:
            current = ready.pop(0)
            sequence.append(current)
            for mid, mission in pending.items():
                if current in mission.depends_on and mid not in sequence:
                    indegree[mid] -= 1
                    if indegree[mid] == 0:
                        ready.append(mid)
            ready.sort(key=lambda i: (-pending[i].priority, i))
        unsequenced = [mid for mid in pending if mid not in sequence]
        out = CoordinationOutput(
            conflicts=conflicts.conflicts,
            moves=moves,
            sequence=sequence,
            unsequenced=unsequenced,
            summary=(
                f"{len(conflicts.conflicts)} conflit(s), {len(moves)} rééquilibrage(s) proposé(s), "
                f"{len(sequence)} mission(s) séquencée(s)"
                + (f", {len(unsequenced)} bloquée(s) par un cycle." if unsequenced else ".")
            ),
        )
        out.insights.extend(conflicts.insights)
        out.recommendations.extend(conflicts.recommendations)
        if moves:
            out.recommendations.append(
                Recommendation(
                    title=f"Rééquilibrer {len(moves)} mission(s) entre agents",
                    rationale=("Des agents dépassent leur capacité alors que des agents compatibles sont disponibles."),
                    impact=6,
                    effort=1.5,
                    confidence=0.8,
                    urgency=7,
                    risk=0.05,
                    tags=["management", "charge"],
                    evidence=[f"{m.mission_id}: {by_id[m.from_agent].name} → {by_id[m.to_agent].name}" for m in moves],
                )
            )
        return out


SKILL = IntelligentManagementSkill
