"""Worker : exécute les tâches durables de la file.

Types de tâches :
- ``skill.run``        action d'une Skill (suivi d'une recommandation, réaction à un événement)
- ``cycle.run``        cycle quotidien / hebdomadaire / mensuel (workflow durable à étapes)
- ``director.mission`` boucle cognitive complète sur une mission
- ``notify``           notification sortante
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sovereign.automation.queue import Task
from sovereign.core.telemetry import span

if TYPE_CHECKING:
    from sovereign.app import Sovereign

log = logging.getLogger("sovereign.worker")
Handler = Callable[[Task], Any]


class Worker:
    def __init__(self, app: Sovereign) -> None:
        self.app = app
        self.handlers: dict[str, Handler] = {
            "skill.run": self._skill_run,
            "cycle.run": self._cycle_run,
            "director.mission": self._director_mission,
            "notify": self._notify,
        }

    def _skill_run(self, task: Task) -> Any:
        payload = task.payload
        out = self.app.kernel.run_skill(payload["skill"], payload["action"], payload.get("input") or {},
                                        mission_id=payload.get("mission_id"), caller="worker")
        return {"summary": out.summary, "insights": len(out.insights), "recommendations": len(out.recommendations)}

    def _cycle_run(self, task: Task) -> Any:
        return self.app.cycles.run(task.payload["cycle"], task_id=task.id, force=task.payload.get("force", False))

    def _director_mission(self, task: Task) -> Any:
        payload = task.payload
        report = self.app.director.run(payload.get("mission", "Analyse de situation"),
                                       events=payload.get("events"), mode=payload.get("mode", "event"),
                                       deep=payload.get("deep", False))
        return {"summary": report.summary, "report_id": report.report_id}

    def _notify(self, task: Task) -> Any:
        payload = task.payload
        return {"sent": self.app.notifier.send(payload["title"], payload.get("text", ""), payload.get("data"),
                                               payload.get("level", "info"))}

    def run_once(self, max_tasks: int = 100) -> int:
        """Traite les tâches prêtes (pour cron / GitHub Actions). Retourne le nombre de tâches traitées."""

        processed = 0
        while processed < max_tasks:
            task = self.app.queue.claim()
            if task is None:
                break
            processed += 1
            handler = self.handlers.get(task.kind)
            if handler is None:
                self.app.queue.fail(task.id, f"type de tâche inconnu : {task.kind}")
                continue
            with span(f"task.{task.kind}", task_id=task.id, attempt=task.attempts):
                try:
                    result = handler(task)
                except Exception as exc:
                    log.exception("tâche %s en échec", task.id)
                    failed = self.app.queue.fail(task.id, f"{type(exc).__name__}: {exc}")
                    if failed.status == "dead":
                        self.app.kernel.events.publish("task.dead", {"task_id": task.id, "kind": task.kind,
                                                                     "error": failed.error}, source="worker")
                    continue
            self.app.queue.complete(task.id, result)
        return processed

    def run_forever(self, stop: threading.Event, poll_seconds: float | None = None) -> None:
        interval = poll_seconds or self.app.kernel.settings.automation.worker_poll_seconds
        while not stop.is_set():
            if self.run_once() == 0:
                stop.wait(interval)
