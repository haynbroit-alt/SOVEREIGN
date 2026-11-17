"""File de tâches durable sur SQLite — exécution « durable » sans serveur à opérer.

Garanties :
- **Persistance** : une tâche enfilée survit à l'arrêt du processus.
- **Idempotence** : ``idempotency_key`` unique ⇒ enfiler deux fois la même chose ne crée qu'une tâche.
- **Bail (lease)** : une tâche prise par un worker qui plante redevient disponible à l'expiration du bail.
- **Reprises** : échec ⇒ nouvelle tentative avec attente exponentielle (5 s × 2^n, plafond 1 h), puis « dead ».
- **Points de reprise (checkpoints)** : ``step(task_id, nom, fn)`` mémorise le résultat de chaque étape ; une
  tâche relancée après un crash rejoue les étapes terminées depuis le journal au lieu de les ré-exécuter.

Voir docs/adr/0003-workflows-durables.md pour la comparaison avec Temporal, DBOS, Restate, Celery.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from sovereign.core.clock import Clock, iso, parse_dt
from sovereign.core.db import Database, dumps, loads
from sovereign.core.ids import new_id

TaskStatus = Literal["queued", "running", "succeeded", "failed", "dead"]
T = TypeVar("T")


class Task(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any]
    status: TaskStatus
    priority: int
    attempts: int
    max_attempts: int
    run_after: datetime
    lease_until: datetime | None
    idempotency_key: str | None
    result: Any = None
    error: str | None = None
    parent_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskQueue:
    def __init__(self, db: Database, clock: Clock, lease_seconds: int = 300) -> None:
        self.db = db
        self.clock = clock
        self.lease_seconds = lease_seconds

    def _row(self, row: Any) -> Task:
        return Task(
            id=row["id"],
            kind=row["kind"],
            payload=loads(row["payload"]),
            status=row["status"],
            priority=row["priority"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            run_after=parse_dt(row["run_after"]),
            lease_until=parse_dt(row["lease_until"]) if row["lease_until"] else None,
            idempotency_key=row["idempotency_key"],
            result=loads(row["result"]),
            error=row["error"],
            parent_id=row["parent_id"],
            created_at=parse_dt(row["created_at"]),
            updated_at=parse_dt(row["updated_at"]),
        )

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        priority: int = 0,
        run_after: datetime | None = None,
        max_attempts: int = 3,
        parent_id: str | None = None,
    ) -> Task:
        now = self.clock.now()
        with self.db.transaction():
            if idempotency_key:
                existing = self.db.one("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,))
                if existing is not None:
                    return self._row(existing)
            task_id = new_id("tsk")
            self.db.execute(
                """INSERT INTO tasks(id, kind, payload, status, priority, attempts, max_attempts, run_after,
                                     idempotency_key, parent_id, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', ?, 0, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    kind,
                    dumps(payload or {}),
                    priority,
                    max_attempts,
                    iso(run_after or now),
                    idempotency_key,
                    parent_id,
                    iso(now),
                    iso(now),
                ),
            )
        return self.get(task_id)

    def get(self, task_id: str) -> Task:
        row = self.db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            raise KeyError(task_id)
        return self._row(row)

    def claim(self) -> Task | None:
        """Prend la prochaine tâche prête (ou dont le bail a expiré : reprise après crash)."""

        now = iso(self.clock.now())
        with self.db.transaction():
            row = self.db.one(
                """SELECT * FROM tasks
                   WHERE (status = 'queued' AND run_after <= ?)
                      OR (status = 'running' AND lease_until IS NOT NULL AND lease_until < ?)
                   ORDER BY priority DESC, run_after, created_at LIMIT 1""",
                (now, now),
            )
            if row is None:
                return None
            lease = iso(self.clock.now() + timedelta(seconds=self.lease_seconds))
            self.db.execute(
                """UPDATE tasks SET status = 'running', attempts = attempts + 1, lease_until = ?, updated_at = ?
                   WHERE id = ?""",
                (lease, now, row["id"]),
            )
        return self.get(row["id"])

    def heartbeat(self, task_id: str) -> None:
        lease = iso(self.clock.now() + timedelta(seconds=self.lease_seconds))
        self.db.execute("UPDATE tasks SET lease_until = ? WHERE id = ? AND status = 'running'", (lease, task_id))

    def complete(self, task_id: str, result: Any = None) -> Task:
        self.db.execute(
            """UPDATE tasks SET status = 'succeeded', result = ?, error = NULL, lease_until = NULL, updated_at = ?
               WHERE id = ?""",
            (dumps(result), iso(self.clock.now()), task_id),
        )
        return self.get(task_id)

    def fail(self, task_id: str, error: str) -> Task:
        task = self.get(task_id)
        now = self.clock.now()
        if task.attempts >= task.max_attempts:
            self.db.execute(
                "UPDATE tasks SET status = 'dead', error = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                (error[:2000], iso(now), task_id),
            )
        else:
            delay = min(3600, 5 * 2**task.attempts)
            self.db.execute(
                """UPDATE tasks SET status = 'queued', error = ?, lease_until = NULL, run_after = ?, updated_at = ?
                   WHERE id = ?""",
                (error[:2000], iso(now + timedelta(seconds=delay)), iso(now), task_id),
            )
        return self.get(task_id)

    def step(self, task_id: str, name: str, fn: Callable[[], T]) -> T | Any:
        """Exécute une étape une seule fois : en cas de reprise, renvoie le résultat journalisé."""

        row = self.db.one("SELECT output FROM task_steps WHERE task_id = ? AND step = ?", (task_id, name))
        if row is not None:
            return loads(row["output"])
        output = fn()
        self.db.execute(
            "INSERT OR REPLACE INTO task_steps(task_id, step, output, completed_at) VALUES (?, ?, ?, ?)",
            (task_id, name, dumps(output), iso(self.clock.now())),
        )
        return loads(dumps(output))

    def steps(self, task_id: str) -> list[str]:
        return [
            r["step"]
            for r in self.db.query(
                "SELECT step FROM task_steps WHERE task_id = ? ORDER BY completed_at, rowid", (task_id,)
            )
        ]

    def list(self, status: str | None = None, limit: int = 100) -> list[Task]:
        if status:
            rows = self.db.query(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def stats(self) -> dict[str, int]:
        counts = {s: 0 for s in ("queued", "running", "succeeded", "failed", "dead")}
        for row in self.db.query("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
            counts[row["status"]] = row["n"]
        return counts

    def retry_dead(self, task_id: str) -> Task:
        self.db.execute(
            """UPDATE tasks SET status = 'queued', attempts = 0, run_after = ?, updated_at = ?
               WHERE id = ? AND status = 'dead'""",
            (iso(self.clock.now()), iso(self.clock.now()), task_id),
        )
        return self.get(task_id)
