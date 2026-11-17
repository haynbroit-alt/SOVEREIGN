"""Bus d'événements persistant.

Chaque événement est d'abord écrit en base (traçabilité, rejouabilité, dédoublonnage), puis distribué aux
abonnés. Les abonnés (règles d'automatisation) transforment les événements pertinents en tâches durables —
jamais en appels IA systématiques.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sovereign.core.clock import Clock, iso, parse_dt
from sovereign.core.db import Database, dumps, loads
from sovereign.core.ids import new_id

log = logging.getLogger("sovereign.events")


class Event(BaseModel):
    id: str
    type: str
    source: str = "internal"
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    dedup_key: str | None = None


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self._subscribers: list[tuple[str, Handler]] = []
        self._dispatch_depth = 0

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subscribers.append((pattern, handler))

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "internal",
        dedup_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> Event | None:
        """Persiste puis distribue. Retourne ``None`` si l'événement est un doublon (dedup_key déjà vu)."""

        event = Event(
            id=new_id("evt"),
            type=event_type,
            source=source,
            payload=payload or {},
            occurred_at=occurred_at or self.clock.now(),
            dedup_key=dedup_key,
        )
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO events(id, type, source, payload, occurred_at, dedup_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event.id, event.type, event.source, dumps(event.payload), iso(event.occurred_at), dedup_key),
        )
        if cursor.rowcount == 0:
            log.info("événement dupliqué ignoré : %s (%s)", event_type, dedup_key)
            return None
        self._dispatch(event)
        return event

    def _dispatch(self, event: Event) -> None:
        if self._dispatch_depth > 8:  # garde-fou contre les cascades d'événements infinies
            log.error("cascade d'événements interrompue sur %s", event.type)
            return
        self._dispatch_depth += 1
        try:
            for pattern, handler in list(self._subscribers):
                if fnmatch.fnmatchcase(event.type, pattern):
                    try:
                        handler(event)
                    except Exception:  # un abonné défaillant ne doit pas bloquer les autres
                        log.exception("abonné en échec pour %s", event.type)
            self.db.execute("UPDATE events SET processed_at = ? WHERE id = ?", (iso(self.clock.now()), event.id))
        finally:
            self._dispatch_depth -= 1

    def recent(self, pattern: str = "*", since: datetime | None = None, limit: int = 200) -> list[Event]:
        params: list[Any] = []
        sql = "SELECT * FROM events"
        if since is not None:
            sql += " WHERE occurred_at >= ?"
            params.append(iso(since))
        sql += " ORDER BY occurred_at DESC, rowid DESC LIMIT ?"
        params.append(limit * 5 if pattern != "*" else limit)
        events = [
            Event(
                id=r["id"],
                type=r["type"],
                source=r["source"],
                payload=loads(r["payload"]),
                occurred_at=parse_dt(r["occurred_at"]),
                dedup_key=r["dedup_key"],
            )
            for r in self.db.query(sql, params)
        ]
        return [e for e in events if fnmatch.fnmatchcase(e.type, pattern)][:limit]
