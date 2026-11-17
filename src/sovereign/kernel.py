"""Le noyau — « cerveau central » technique de SOVEREIGN.

Assemble configuration, horloge, base, magasin, bus d'événements, finances, mémoire, registre de Skills,
passerelle LLM et télémétrie. Toutes les interfaces (CLI, API REST, serveur MCP, automatisations) passent par
le même noyau : une seule source de vérité, une seule politique de sécurité.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sovereign.config import Settings, load_settings
from sovereign.core.clock import Clock, SystemClock, iso
from sovereign.core.db import Database
from sovereign.core.events import EventBus
from sovereign.core.finance import FinanceLedger
from sovereign.core.llm import LLMGateway
from sovereign.core.memory import DecisionMemory
from sovereign.core.security import ApiKeyStore
from sovereign.core.stats import BetaPosterior
from sovereign.core.store import RecordStore
from sovereign.core.telemetry import setup_telemetry, span
from sovereign.skills.base import SkillContext, SkillOutput
from sovereign.skills.registry import SkillRegistry


class Kernel:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        db_path: str | Path | None = None,
        llm_client_factory: Any = None,
    ) -> None:
        self.settings = settings
        self.clock = clock or SystemClock()
        self.db = Database(db_path if db_path is not None else settings.db_path)
        self.store = RecordStore(self.db, self.clock)
        self.events = EventBus(self.db, self.clock)
        self.finance = FinanceLedger(self.db, self.clock, settings.finance, self.events)
        self.memory = DecisionMemory(self.db, self.clock)
        self.api_keys = ApiKeyStore(self.db, self.clock)
        self.registry = SkillRegistry()
        self.llm = LLMGateway(settings.llm, self.finance, client_factory=llm_client_factory)
        setup_telemetry(settings.otel_enabled)

    @classmethod
    def open(cls, home: str | Path | None = None, **kwargs: Any) -> Kernel:
        return cls(load_settings(home), **kwargs)

    @classmethod
    def ephemeral(cls, clock: Clock | None = None, **settings_overrides: Any) -> Kernel:
        """Noyau en mémoire (tests, simulations « bac à sable »)."""

        settings = Settings(**settings_overrides)
        return cls(settings, clock=clock, db_path=":memory:")

    def context(self, mission_id: str | None = None, caller: str = "system") -> SkillContext:
        return SkillContext(kernel=self, mission_id=mission_id, caller=caller)

    def run_skill(
        self,
        skill: str,
        action: str,
        payload: dict[str, Any] | BaseModel | None = None,
        *,
        mission_id: str | None = None,
        caller: str = "system",
    ) -> SkillOutput:
        instance = self.registry.get(skill)
        started = time.perf_counter()
        error: str | None = None
        result: SkillOutput | None = None
        with span(f"skill.{skill}.{action}", skill=skill, action=action, mission=mission_id or ""):
            try:
                result = instance.execute(action, self.context(mission_id, caller), payload)
                return result
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                duration = (time.perf_counter() - started) * 1000
                now = iso(self.clock.now())
                self.db.execute(
                    """INSERT INTO skill_runs(skill, action, mission_id, ok, duration_ms, n_insights,
                                              n_recommendations, error, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (skill, action, mission_id, int(error is None), round(duration, 3),
                     len(result.insights) if result else 0, len(result.recommendations) if result else 0,
                     error, now),
                )
                if result is not None:
                    for rec in result.recommendations:
                        self.store.put("recommendation", rec.id, rec)
                        self.db.execute(
                            """INSERT OR IGNORE INTO recommendation_log(id, skill, title, mission_id, status, at)
                               VALUES (?, ?, ?, ?, 'proposed', ?)""",
                            (rec.id, rec.skill, rec.title, mission_id, now),
                        )
                    for metric, value in result.metrics.items():
                        self.db.execute(
                            "INSERT INTO observations(metric, value, at, source) VALUES (?, ?, ?, ?)",
                            (metric, float(value), now, f"skill:{skill}.{action}"),
                        )

    # -- mesure de l'utilité réelle -----------------------------------------------------------
    def skill_utility(self) -> dict[str, dict[str, Any]]:
        """Utilité mesurée par Skill : usage, fiabilité, adoption des recommandations, résultats obtenus."""

        stats: dict[str, dict[str, Any]] = {
            name: {"runs": 0, "failures": 0, "avg_ms": 0.0, "proposed": 0, "adopted": 0, "succeeded": 0,
                   "failed": 0, "last_run": None}
            for name in self.registry.names
        }
        for row in self.db.query(
            """SELECT skill, COUNT(*) AS runs, SUM(1 - ok) AS failures, AVG(duration_ms) AS avg_ms,
                      MAX(at) AS last_run FROM skill_runs GROUP BY skill"""
        ):
            if row["skill"] in stats:
                stats[row["skill"]].update(runs=row["runs"], failures=row["failures"] or 0,
                                           avg_ms=round(row["avg_ms"] or 0.0, 2), last_run=row["last_run"])
        for row in self.db.query(
            "SELECT skill, status, COUNT(DISTINCT id) AS n FROM recommendation_log GROUP BY skill, status"
        ):
            if row["skill"] in stats and row["status"] in ("proposed", "adopted"):
                stats[row["skill"]][row["status"]] = row["n"]
        for row in self.db.query(
            "SELECT skills, status FROM decisions WHERE status IN ('succeeded', 'partial', 'failed')"
        ):
            import json

            for name in json.loads(row["skills"]):
                if name in stats:
                    key = "failed" if row["status"] == "failed" else "succeeded"
                    stats[name][key] += 1 if row["status"] != "partial" else 0.5
        for values in stats.values():
            posterior = BetaPosterior(values["succeeded"], values["failed"])
            values["success_posterior"] = round(posterior.mean, 3)
            values["adoption_rate"] = round(values["adopted"] / values["proposed"], 3) if values["proposed"] else None
            reliability = 1 - (values["failures"] / values["runs"]) if values["runs"] else None
            values["reliability"] = round(reliability, 3) if reliability is not None else None
        return stats

    def skill_priors(self) -> dict[str, float]:
        """A priori d'utilité pour la sélection : n'existe que pour les Skills ayant un historique de résultats."""

        return {
            name: values["success_posterior"]
            for name, values in self.skill_utility().items()
            if values["succeeded"] + values["failed"] > 0
        }

    def available_data(self) -> set[str]:
        kinds = set(self.store.kinds())
        if self.db.scalar("SELECT COUNT(*) FROM ledger"):
            kinds.add("ledger")
        if self.db.scalar("SELECT COUNT(*) FROM decisions"):
            kinds.add("decision")
        return kinds

    def close(self) -> None:
        self.db.close()
