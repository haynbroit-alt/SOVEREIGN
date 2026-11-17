"""Planificateur : décide quels cycles sont DUS, sans appel IA.

``tick()`` est idempotent et peu coûteux (quelques requêtes SQL) : on peut l'appeler toutes les minutes (serveur),
toutes les heures (cron) ou une fois par jour (GitHub Actions). Un cycle est dû si :
- quotidien : il n'a pas réussi aujourd'hui et l'heure configurée est passée ;
- hebdomadaire : il n'a pas réussi cette semaine ISO et le jour configuré est atteint ;
- mensuel : il n'a pas réussi ce mois-ci et le jour configuré est atteint.
Un cycle manqué (machine éteinte) est rattrapé au tick suivant de la même période.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from sovereign.core.clock import period_key

if TYPE_CHECKING:
    from sovereign.app import Sovereign


class Scheduler:
    def __init__(self, app: Sovereign) -> None:
        self.app = app

    def due(self) -> list[str]:
        settings = self.app.kernel.settings.automation
        now = self.app.kernel.clock.now()
        due: list[str] = []
        checks = {
            "daily": now.hour >= settings.daily_hour,
            "weekly": now.weekday() >= settings.weekly_weekday,
            "monthly": now.day >= settings.monthly_day,
        }
        for cycle, time_ok in checks.items():
            if not time_ok:
                continue
            row = self.app.kernel.db.one(
                "SELECT status FROM cycle_runs WHERE cycle = ? AND period = ?",
                (cycle, period_key(cycle, now)),
            )
            if row is None or row["status"] == "failed":
                due.append(cycle)
        return due

    def tick(self) -> list[dict[str, Any]]:
        """Enfile les cycles dus (tâches idempotentes par période)."""

        now = self.app.kernel.clock.now()
        enqueued = []
        for cycle in self.due():
            task = self.app.queue.enqueue(
                "cycle.run",
                {"cycle": cycle},
                idempotency_key=f"cycle:{cycle}:{period_key(cycle, now)}",
                priority={"monthly": 3, "weekly": 2, "daily": 1}[cycle],
            )
            enqueued.append({"cycle": cycle, "task_id": task.id, "status": task.status})
        return enqueued

    def run_forever(self, stop: threading.Event) -> None:
        interval = self.app.kernel.settings.automation.scheduler_tick_seconds
        while not stop.is_set():
            self.tick()
            stop.wait(interval)
