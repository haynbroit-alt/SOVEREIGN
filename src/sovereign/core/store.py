"""Magasin de documents typés (kind, id) au-dessus de SQLite/JSON.

Les données métier (marchés, campagnes, projets, signaux, agents…) ont des formes variées et évolutives :
un magasin documentaire validé par Pydantic évite une migration SQL par champ, tout en restant
interrogeable (JSON1) et transactionnel. Les données critiques (argent, décisions) ont leurs tables dédiées.
"""

from __future__ import annotations

import builtins
from typing import Any, TypeVar, overload

from pydantic import BaseModel

from sovereign.core.clock import Clock, iso
from sovereign.core.db import Database, dumps, loads

M = TypeVar("M", bound=BaseModel)


class RecordStore:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def put(self, kind: str, record_id: str, data: BaseModel | dict[str, Any]) -> None:
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        now = iso(self.clock.now())
        self.db.execute(
            """
            INSERT INTO records(kind, id, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (kind, record_id, dumps(payload), now, now),
        )

    @overload
    def get(self, kind: str, record_id: str) -> dict[str, Any] | None: ...
    @overload
    def get(self, kind: str, record_id: str, model: type[M]) -> M | None: ...
    def get(self, kind: str, record_id: str, model: type[M] | None = None) -> Any:
        row = self.db.one("SELECT data FROM records WHERE kind = ? AND id = ?", (kind, record_id))
        if row is None:
            return None
        data = loads(row["data"])
        return model.model_validate(data) if model else data

    @overload
    def list(self, kind: str) -> list[dict[str, Any]]: ...
    @overload
    def list(self, kind: str, model: type[M]) -> builtins.list[M]: ...
    def list(self, kind: str, model: type[M] | None = None) -> builtins.list[Any]:
        rows = self.db.query("SELECT data FROM records WHERE kind = ? ORDER BY created_at, id", (kind,))
        items = [loads(r["data"]) for r in rows]
        return [model.model_validate(i) for i in items] if model else items

    def delete(self, kind: str, record_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM records WHERE kind = ? AND id = ?", (kind, record_id))
        return cursor.rowcount > 0

    def count(self, kind: str | None = None) -> int:
        if kind is None:
            return int(self.db.scalar("SELECT COUNT(*) FROM records") or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM records WHERE kind = ?", (kind,)) or 0)

    def kinds(self) -> dict[str, int]:
        rows = self.db.query("SELECT kind, COUNT(*) AS n FROM records GROUP BY kind ORDER BY kind")
        return {r["kind"]: int(r["n"]) for r in rows}
