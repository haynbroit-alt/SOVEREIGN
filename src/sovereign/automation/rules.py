"""Mode CONTINU : règles événement → action.

Les règles sont déterministes et peu coûteuses : elles ne déclenchent une analyse (et a fortiori un appel IA)
que lorsqu'un événement est réellement significatif. Chaque déclenchement crée une tâche durable idempotente
(clé dérivée de l'événement) : un même événement rejoué ne déclenche pas deux fois la même action.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sovereign.core.events import Event

if TYPE_CHECKING:
    from sovereign.app import Sovereign

log = logging.getLogger("sovereign.rules")


@dataclass
class Rule:
    name: str
    pattern: str
    description: str
    condition: Callable[[Event], bool]
    action: Callable[[Sovereign, Event], dict[str, Any] | None]


def _enqueue_skill(
    skill: str, action: str, priority: int = 5, coalesce: bool = False
) -> Callable[[Sovereign, Event], dict[str, Any]]:
    """``coalesce`` : une rafale d'événements identiques ne déclenche qu'UNE analyse par heure."""

    def run(app: Sovereign, event: Event) -> dict[str, Any]:
        bucket = app.kernel.clock.now().strftime("%Y-%m-%dT%H") if coalesce else event.id
        task = app.queue.enqueue(
            "skill.run",
            {"skill": skill, "action": action, "input": {}},
            idempotency_key=f"rule:{skill}.{action}:{bucket}",
            priority=priority,
        )
        return {"task_id": task.id}

    return run


def _notify(level: str) -> Callable[[Sovereign, Event], dict[str, Any]]:
    def run(app: Sovereign, event: Event) -> dict[str, Any]:
        task = app.queue.enqueue(
            "notify",
            {"title": event.type, "text": _describe(event), "data": event.payload, "level": level},
            idempotency_key=f"rule:notify:{event.id}",
            priority=8,
        )
        return {"task_id": task.id}

    return run


def _describe(event: Event) -> str:
    p = event.payload
    match event.type:
        case "budget.threshold_crossed":
            return (
                f"Budget consommé à {float(p.get('utilization', 0)):.0%} (seuil {float(p.get('threshold', 0)):.0%})."
                f" Disponible : {p.get('available')}."
            )
        case "expense.review":
            return f"Dépense de {p.get('amount')} ({p.get('category')}) en attente de validation humaine."
        case "approval.requested":
            return f"Validation humaine demandée : {p.get('title')}."
        case "task.dead":
            return f"Tâche {p.get('kind')} en échec définitif : {p.get('error')}."
        case _:
            return f"Événement {event.type}."


def _director_on_contradiction(app: Sovereign, event: Event) -> dict[str, Any]:
    task = app.queue.enqueue(
        "director.mission",
        {
            "mission": f"Comprendre l'événement {event.type}",
            "events": [event.type],
            "mode": "event",
            "deep": True,
        },
        idempotency_key=f"rule:director:{event.id}",
        priority=6,
    )
    return {"task_id": task.id}


def _significant_payment(event: Event) -> bool:
    try:
        return float(event.payload.get("amount", 0)) > 0
    except (TypeError, ValueError):
        return False


DEFAULT_RULES: list[Rule] = [
    Rule(
        "budget-alert",
        "budget.threshold_crossed",
        "Seuil budgétaire franchi → alerte + diagnostic financier",
        lambda e: True,
        lambda app, e: {
            **_notify("warning")(app, e),
            **{"diag": _enqueue_skill("finance-budget", "assess", 9)(app, e)},
        },
    ),
    Rule(
        "expense-review",
        "expense.review",
        "Dépense importante → notification de validation humaine",
        lambda e: True,
        _notify("warning"),
    ),
    Rule(
        "approval-requested",
        "approval.requested",
        "Validation demandée → notification",
        lambda e: True,
        _notify("info"),
    ),
    Rule(
        "revenue-recorded",
        "finance.collection_recorded",
        "Encaissement → recalcul du diagnostic financier",
        _significant_payment,
        _enqueue_skill("finance-budget", "assess", 4, coalesce=True),
    ),
    Rule(
        "outcome-learning",
        "decision.outcome_recorded",
        "Résultat mesuré → adaptation des stratégies",
        lambda e: True,
        _enqueue_skill("memory-learning", "adapt_strategies", 3, coalesce=True),
    ),
    Rule("task-dead", "task.dead", "Tâche en échec définitif → alerte", lambda e: True, _notify("critical")),
    Rule(
        "signals-ingested",
        "watch.signals_ingested",
        "Nouveaux signaux de veille → détection de tendances",
        lambda e: int(e.payload.get("count", 0)) >= 5,
        _enqueue_skill("watch-anticipation", "detect_trends", 3),
    ),
    Rule(
        "kpi-anomaly",
        "metric.anomaly",
        "Anomalie de KPI → boucle cognitive approfondie",
        lambda e: abs(float(e.payload.get("z", 0))) >= 3.5,
        _director_on_contradiction,
    ),
    Rule(
        "external-github-release",
        "github.release",
        "Nouvelle release suivie → veille technologique",
        lambda e: True,
        _enqueue_skill("watch-anticipation", "check_technologies", 2),
    ),
]


class RuleEngine:
    def __init__(self, app: Sovereign) -> None:
        self.app = app
        self.rules: list[Rule] = []
        self.fired: list[tuple[str, str]] = []

    def install(self, rule: Rule) -> None:
        self.rules.append(rule)

        def handler(event: Event) -> None:
            self._fire(rule, event)

        self.app.kernel.events.subscribe(rule.pattern, handler)

    def install_defaults(self) -> None:
        for rule in DEFAULT_RULES:
            self.install(rule)

    def _fire(self, rule: Rule, event: Event) -> None:
        if not rule.condition(event):
            return
        result = rule.action(self.app, event)
        self.fired.append((rule.name, event.id))
        log.info("règle %s déclenchée par %s → %s", rule.name, event.type, result)

    def describe(self) -> list[dict[str, str]]:
        return [{"name": r.name, "event": r.pattern, "description": r.description} for r in self.rules]
