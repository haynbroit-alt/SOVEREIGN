"""Contrat commun des Skills.

Une Skill est un module fonctionnel qui expose des ACTIONS typées :

    class MaSkill(Skill):
        name = "ma-skill"

        @action("Résumé de l'action", risk="read_only")
        def analyser(self, ctx: SkillContext, data: MonEntree) -> MaSortie: ...

- Entrées et sorties sont des modèles Pydantic (validés, sérialisables en JSON Schema → MCP / OpenAPI).
- Toute sortie hérite de ``SkillOutput`` : le cerveau central récolte uniformément constats (insights),
  recommandations et métriques, quelle que soit la Skill.
- Chaque Skill fournit une action ``assess`` qui travaille sur les données de l'espace de travail sans entrée :
  c'est elle que le directeur appelle quand il sélectionne la Skill pour une mission.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, Field

from sovereign.core.ids import stable_id

if TYPE_CHECKING:
    from sovereign.kernel import Kernel

Severity = Literal["info", "warning", "critical"]
InsightKind = Literal["observation", "opportunity", "risk", "anomaly", "trend", "contradiction", "strength", "weakness"]
ActionRisk = Literal["read_only", "write", "external"]


class SkillError(Exception):
    """Erreur métier explicite (entrée incohérente, donnée manquante…)."""


class Insight(BaseModel):
    kind: InsightKind = "observation"
    title: str
    detail: str = ""
    severity: Severity = "info"
    skill: str = ""
    metric: str | None = None
    value: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class FollowUp(BaseModel):
    """Action de suivi exécutable par le système (jamais un paiement ni un envoi externe non validé)."""

    skill: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Recommendation(BaseModel):
    id: str = ""
    title: str
    rationale: str
    skill: str = ""
    impact: float = Field(5.0, ge=0, le=10, description="Impact business attendu (0-10)")
    effort: float = Field(5.0, ge=0.5, le=10, description="Effort / complexité (0.5-10)")
    confidence: float = Field(0.5, ge=0, le=1)
    urgency: float = Field(5.0, ge=0, le=10)
    risk: float = Field(0.2, ge=0, le=1, description="Probabilité subjective d'effet négatif")
    estimated_value: Decimal | None = None
    estimated_cost: Decimal | None = None
    reversible: bool = True
    external: bool = Field(False, description="Agit hors du système (client, paiement, publication)")
    horizon_days: int | None = None
    tags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    follow_up: FollowUp | None = None

    def finalize(self, skill: str) -> Recommendation:
        self.skill = self.skill or skill
        if not self.id:
            self.id = stable_id("rec", self.skill, self.title)
        return self


class SkillOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    insights: list[Insight] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict, description="Métriques observées → modèle du monde")


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class SkillContext:
    """Accès contrôlé au cerveau central pendant l'exécution d'une action."""

    kernel: Kernel
    mission_id: str | None = None
    caller: str = "system"

    @property
    def store(self) -> Any:
        return self.kernel.store

    @property
    def db(self) -> Any:
        return self.kernel.db

    @property
    def clock(self) -> Any:
        return self.kernel.clock

    @property
    def events(self) -> Any:
        return self.kernel.events

    @property
    def finance(self) -> Any:
        return self.kernel.finance

    @property
    def memory(self) -> Any:
        return self.kernel.memory

    @property
    def settings(self) -> Any:
        return self.kernel.settings

    @property
    def llm(self) -> Any:
        """Passerelle LLM (ou ``None``). Toujours prévoir le chemin déterministe."""

        gateway = self.kernel.llm
        return gateway if gateway is not None and gateway.available else None

    def invoke(self, skill: str, action: str, payload: dict[str, Any] | BaseModel | None = None) -> Any:
        """Compose avec une autre Skill (tracé et mesuré comme un appel normal)."""

        return self.kernel.run_skill(skill, action, payload, mission_id=self.mission_id, caller=self.caller)


@dataclass
class ActionSpec:
    name: str
    summary: str
    risk: ActionRisk
    input_model: type[BaseModel]
    output_model: type[SkillOutput]
    fn: Callable[..., SkillOutput] = field(repr=False)

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "risk": self.risk,
            "input": self.input_model.model_json_schema(),
            "output": self.output_model.model_json_schema(),
        }


def action(summary: str, *, risk: ActionRisk = "read_only") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn.__sovereign_action__ = {"summary": summary, "risk": risk}  # type: ignore[attr-defined]
        return fn

    return decorator


class Skill:
    name: ClassVar[str] = ""
    version: ClassVar[str] = "1.0.0"

    def __init__(self) -> None:
        self._actions = self._collect_actions()

    @classmethod
    def _collect_actions(cls) -> dict[str, ActionSpec]:
        found: dict[str, ActionSpec] = {}
        for attr_name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            meta = getattr(member, "__sovereign_action__", None)
            if meta is None:
                continue
            hints = get_type_hints(member)
            params = [p for p in inspect.signature(member).parameters.values() if p.name not in ("self", "ctx")]
            if len(params) != 1:
                raise TypeError(f"{cls.__name__}.{attr_name} : une action prend exactement (self, ctx, data)")
            input_model = hints.get(params[0].name)
            output_model = hints.get("return")
            if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
                raise TypeError(f"{cls.__name__}.{attr_name} : l'entrée doit être un modèle Pydantic")
            if not (isinstance(output_model, type) and issubclass(output_model, SkillOutput)):
                raise TypeError(f"{cls.__name__}.{attr_name} : la sortie doit hériter de SkillOutput")
            found[attr_name] = ActionSpec(
                name=attr_name,
                summary=meta["summary"],
                risk=meta["risk"],
                input_model=input_model,
                output_model=output_model,
                fn=member,
            )
        return found

    @property
    def actions(self) -> dict[str, ActionSpec]:
        return self._actions

    def spec(self, action_name: str) -> ActionSpec:
        try:
            return self._actions[action_name]
        except KeyError as exc:
            raise SkillError(
                f"action inconnue « {action_name} » pour {self.name} (disponibles : {', '.join(sorted(self._actions))})"
            ) from exc

    def execute(self, action_name: str, ctx: SkillContext, payload: dict[str, Any] | BaseModel | None) -> SkillOutput:
        spec = self.spec(action_name)
        if isinstance(payload, spec.input_model):
            data = payload
        elif isinstance(payload, BaseModel):
            data = spec.input_model.model_validate(payload.model_dump())
        else:
            data = spec.input_model.model_validate(payload or {})
        result = spec.fn(self, ctx, data)
        if not isinstance(result, spec.output_model):
            raise TypeError(f"{self.name}.{action_name} a renvoyé {type(result).__name__}")
        for insight in result.insights:
            insight.skill = insight.skill or self.name
        for rec in result.recommendations:
            rec.finalize(self.name)
        return result
