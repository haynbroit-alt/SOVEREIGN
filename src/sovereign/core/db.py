"""Base SQLite : mémoire persistante, registre comptable append-only, file de tâches durable.

Choix (voir docs/adr/0002-stockage-sqlite.md) : SQLite 3.4x+ en mode WAL couvre largement la charge d'un
directeur IA mono-organisation, sans serveur à opérer, avec FTS5 pour la recherche plein texte.
Les invariants critiques (registre comptable non modifiable, journal d'audit) sont garantis par
des triggers SQL, pas seulement par le code applicatif.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"non sérialisable : {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(value, default=_default, ensure_ascii=False, sort_keys=True)


def loads(value: str | bytes | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


MIGRATIONS: list[str] = [
    # 1 — socle
    """
    CREATE TABLE records (
        kind TEXT NOT NULL,
        id TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (kind, id)
    );

    -- Registre des encaissements RÉELS. Append-only : aucune modification, aucune suppression.
    CREATE TABLE ledger (
        id TEXT PRIMARY KEY,
        entry_type TEXT NOT NULL CHECK (entry_type IN ('collection', 'refund')),
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        currency TEXT NOT NULL,
        occurred_on TEXT NOT NULL,
        counterparty TEXT,
        description TEXT,
        reference TEXT UNIQUE,
        invoice_id TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL
    );
    CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
    CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

    CREATE TABLE expenses (
        id TEXT PRIMARY KEY,
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        actual_cents INTEGER CHECK (actual_cents IS NULL OR actual_cents >= 0),
        currency TEXT NOT NULL,
        category TEXT NOT NULL,
        vendor TEXT,
        description TEXT,
        recurring TEXT NOT NULL DEFAULT 'none' CHECK (recurring IN ('none', 'monthly', 'yearly')),
        status TEXT NOT NULL CHECK (status IN ('approved', 'rejected', 'review', 'spent', 'cancelled')),
        decision_reason TEXT,
        requested_by TEXT,
        reference TEXT UNIQUE,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        spent_at TEXT
    );
    CREATE TRIGGER expenses_no_delete BEFORE DELETE ON expenses
        BEGIN SELECT RAISE(ABORT, 'expenses cannot be deleted'); END;
    CREATE TRIGGER expenses_amount_frozen BEFORE UPDATE OF amount_cents ON expenses
        BEGIN SELECT RAISE(ABORT, 'approved amount is immutable'); END;

    CREATE TABLE audit_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id TEXT,
        details TEXT
    );
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

    CREATE TABLE decisions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        context TEXT NOT NULL DEFAULT '',
        decision TEXT NOT NULL,
        rationale TEXT NOT NULL DEFAULT '',
        options TEXT NOT NULL DEFAULT '[]',
        category TEXT NOT NULL DEFAULT 'general',
        tags TEXT NOT NULL DEFAULT '',
        skills TEXT NOT NULL DEFAULT '[]',
        recommendation_ids TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0.5,
        expected TEXT,
        review_at TEXT,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'succeeded', 'partial', 'failed', 'abandoned')),
        outcome TEXT,
        score REAL,
        lesson TEXT,
        source TEXT NOT NULL DEFAULT 'human',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE decisions_fts USING fts5(
        title, context, decision, rationale, tags, lesson,
        content='decisions', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER decisions_ai AFTER INSERT ON decisions BEGIN
        INSERT INTO decisions_fts(rowid, title, context, decision, rationale, tags, lesson)
        VALUES (new.rowid, new.title, new.context, new.decision, new.rationale, new.tags, coalesce(new.lesson, ''));
    END;
    CREATE TRIGGER decisions_ad AFTER DELETE ON decisions BEGIN
        INSERT INTO decisions_fts(decisions_fts, rowid, title, context, decision, rationale, tags, lesson)
        VALUES ('delete', old.rowid, old.title, old.context, old.decision, old.rationale, old.tags,
                coalesce(old.lesson, ''));
    END;
    CREATE TRIGGER decisions_au AFTER UPDATE ON decisions BEGIN
        INSERT INTO decisions_fts(decisions_fts, rowid, title, context, decision, rationale, tags, lesson)
        VALUES ('delete', old.rowid, old.title, old.context, old.decision, old.rationale, old.tags,
                coalesce(old.lesson, ''));
        INSERT INTO decisions_fts(rowid, title, context, decision, rationale, tags, lesson)
        VALUES (new.rowid, new.title, new.context, new.decision, new.rationale, new.tags, coalesce(new.lesson, ''));
    END;

    CREATE TABLE beliefs (
        id TEXT PRIMARY KEY,
        statement TEXT NOT NULL,
        topic TEXT NOT NULL DEFAULT 'general',
        metric TEXT,
        comparator TEXT CHECK (comparator IS NULL OR comparator IN ('lt', 'le', 'gt', 'ge', 'between')),
        threshold REAL,
        threshold_high REAL,
        confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        evidence TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT 'human',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_tested_at TEXT
    );

    CREATE TABLE observations (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        metric TEXT NOT NULL,
        value REAL NOT NULL,
        at TEXT NOT NULL,
        source TEXT NOT NULL,
        data TEXT
    );
    CREATE INDEX observations_metric_at ON observations(metric, at);

    CREATE TABLE events (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        source TEXT NOT NULL,
        payload TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        dedup_key TEXT UNIQUE,
        processed_at TEXT
    );
    CREATE INDEX events_type ON events(type, occurred_at);

    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead')),
        priority INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        run_after TEXT NOT NULL,
        lease_until TEXT,
        idempotency_key TEXT UNIQUE,
        result TEXT,
        error TEXT,
        parent_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX tasks_ready ON tasks(status, run_after, priority);

    CREATE TABLE task_steps (
        task_id TEXT NOT NULL,
        step TEXT NOT NULL,
        output TEXT,
        completed_at TEXT NOT NULL,
        PRIMARY KEY (task_id, step)
    );

    CREATE TABLE skill_runs (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        skill TEXT NOT NULL,
        action TEXT NOT NULL,
        mission_id TEXT,
        ok INTEGER NOT NULL,
        duration_ms REAL NOT NULL,
        n_insights INTEGER NOT NULL DEFAULT 0,
        n_recommendations INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        at TEXT NOT NULL
    );
    CREATE INDEX skill_runs_skill ON skill_runs(skill, at);

    CREATE TABLE recommendation_log (
        id TEXT NOT NULL,
        skill TEXT NOT NULL,
        title TEXT NOT NULL,
        mission_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('proposed', 'adopted', 'rejected', 'executed')),
        at TEXT NOT NULL,
        PRIMARY KEY (id, status)
    );

    CREATE TABLE approvals (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL,
        risk TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
        mission_id TEXT,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT,
        reason TEXT
    );

    CREATE TABLE api_keys (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        prefix TEXT NOT NULL,
        hash TEXT NOT NULL UNIQUE,
        scopes TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at TEXT
    );

    CREATE TABLE cycle_runs (
        cycle TEXT NOT NULL,
        period TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
        started_at TEXT NOT NULL,
        finished_at TEXT,
        report_id TEXT,
        task_id TEXT,
        PRIMARY KEY (cycle, period)
    );

    CREATE TABLE reports (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        content_md TEXT NOT NULL,
        data TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE command_runs (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        command TEXT NOT NULL,
        ok INTEGER NOT NULL,
        duration_ms REAL NOT NULL,
        source TEXT NOT NULL,
        at TEXT NOT NULL
    );
    """,
]


class Database:
    """Connexion SQLite partagée, protégée par un verrou (API, worker et planificateur dans un même process)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # -- migrations ------------------------------------------------------------------
    def migrate(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            row = self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            current = int(row[0])
            for version, script in enumerate(MIGRATIONS, start=1):
                if version <= current:
                    continue
                try:
                    self._conn.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + script
                        + f"\nINSERT INTO schema_migrations(version, applied_at) VALUES ({version}, datetime('now'));"
                        + "\nCOMMIT;"
                    )
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise
                current = version
            return current

    # -- accès -------------------------------------------------------------------------
    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Transaction (réentrante). ``BEGIN IMMEDIATE`` sérialise les écritures concurrentes."""

        with self._lock:
            if self._depth == 0:
                self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        row = self.one(sql, params)
        return None if row is None else row[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
