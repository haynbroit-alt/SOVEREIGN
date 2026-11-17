"""Modèle du monde : les croyances du directeur, testables, révisées par les faits.

Une croyance peut être purement qualitative (« Le marché PME est notre cœur de cible ») ou testable
(métrique + comparateur + seuil : « Le CAC reste < 80 € »). Chaque observation de la métrique la CONFIRME ou la
CONTREDIT ; la confiance est révisée par mise à jour bayésienne en log-cote :

    logit(confiance') = logit(confiance) ± ln(force)

La force (rapport de vraisemblance, 2 par défaut) est modulée par l'écart relatif au seuil : un fait très éloigné
du seuil pèse plus qu'un fait limite. La confiance reste bornée à [0,02 ; 0,98] : aucune croyance n'est
définitivement acquise ni définitivement rejetée.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from sovereign.core.clock import Clock, iso, parse_dt
from sovereign.core.db import Database, dumps, loads
from sovereign.core.ids import new_id
from sovereign.core.stats import clamp, logit, sigmoid

Comparator = Literal["lt", "le", "gt", "ge", "between"]
_SYMBOLS = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥"}


class Belief(BaseModel):
    id: str
    statement: str
    topic: str = "general"
    metric: str | None = None
    comparator: Comparator | None = None
    threshold: float | None = None
    threshold_high: float | None = None
    confidence: float = Field(0.6, ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "human"
    created_at: datetime
    updated_at: datetime
    last_tested_at: datetime | None = None

    @property
    def testable(self) -> bool:
        return self.metric is not None and self.comparator is not None and self.threshold is not None

    def expectation(self) -> str:
        if not self.testable:
            return "non testable"
        if self.comparator == "between":
            return f"{self.metric} ∈ [{self.threshold} ; {self.threshold_high}]"
        return f"{self.metric} {_SYMBOLS[self.comparator or 'lt']} {self.threshold}"

    def holds(self, value: float) -> bool:
        t = self.threshold if self.threshold is not None else 0.0
        match self.comparator:
            case "lt":
                return value < t
            case "le":
                return value <= t
            case "gt":
                return value > t
            case "ge":
                return value >= t
            case "between":
                high = self.threshold_high if self.threshold_high is not None else math.inf
                return t <= value <= high
        return True


class Confrontation(BaseModel):
    belief_id: str
    statement: str
    metric: str
    observed: float
    expected: str
    outcome: Literal["confirmation", "contradiction"]
    prior: float
    posterior: float

    @property
    def shift(self) -> float:
        return self.posterior - self.prior


class WorldModel:
    MIN, MAX = 0.02, 0.98

    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def add(
        self,
        statement: str,
        *,
        topic: str = "general",
        metric: str | None = None,
        comparator: Comparator | None = None,
        threshold: float | None = None,
        threshold_high: float | None = None,
        confidence: float = 0.6,
        source: str = "human",
        belief_id: str | None = None,
    ) -> Belief:
        if (metric is None) != (comparator is None) or (comparator is not None and threshold is None):
            raise ValueError("une croyance testable exige metric, comparator et threshold")
        if comparator == "between" and threshold_high is None:
            raise ValueError("'between' exige threshold_high")
        now = self.clock.now()
        belief = Belief(
            id=belief_id or new_id("bel"), statement=statement, topic=topic, metric=metric, comparator=comparator,
            threshold=threshold, threshold_high=threshold_high, confidence=clamp(confidence, self.MIN, self.MAX),
            source=source, created_at=now, updated_at=now,
        )
        self.db.execute(
            """INSERT INTO beliefs(id, statement, topic, metric, comparator, threshold, threshold_high, confidence,
                                   evidence, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET statement = excluded.statement, confidence = excluded.confidence,
                    updated_at = excluded.updated_at""",
            (belief.id, statement, topic, metric, comparator, threshold, threshold_high, belief.confidence, "[]",
             source, iso(now), iso(now)),
        )
        return self.get(belief.id)

    def _row(self, row: Any) -> Belief:
        return Belief(
            id=row["id"], statement=row["statement"], topic=row["topic"], metric=row["metric"],
            comparator=row["comparator"], threshold=row["threshold"], threshold_high=row["threshold_high"],
            confidence=row["confidence"], evidence=loads(row["evidence"]), source=row["source"],
            created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
            last_tested_at=parse_dt(row["last_tested_at"]) if row["last_tested_at"] else None,
        )

    def get(self, belief_id: str) -> Belief:
        row = self.db.one("SELECT * FROM beliefs WHERE id = ?", (belief_id,))
        if row is None:
            raise KeyError(belief_id)
        return self._row(row)

    def list(self, topic: str | None = None) -> list[Belief]:
        if topic:
            rows = self.db.query("SELECT * FROM beliefs WHERE topic = ? ORDER BY confidence DESC", (topic,))
        else:
            rows = self.db.query("SELECT * FROM beliefs ORDER BY topic, confidence DESC")
        return [self._row(r) for r in rows]

    def _save(self, belief: Belief, tested: bool) -> None:
        self.db.execute(
            "UPDATE beliefs SET confidence = ?, evidence = ?, updated_at = ?, last_tested_at = ? WHERE id = ?",
            (belief.confidence, dumps(belief.evidence[-25:]), iso(self.clock.now()),
             iso(self.clock.now()) if tested else (iso(belief.last_tested_at) if belief.last_tested_at else None),
             belief.id),
        )

    def confront(self, observations: dict[str, float], strength: float = 2.0) -> list[Confrontation]:
        """CONFRONTER : faits observés vs croyances testables."""

        results: list[Confrontation] = []
        for belief in self.list():
            if not belief.testable or belief.metric not in observations:
                continue
            value = float(observations[belief.metric])
            confirmed = belief.holds(value)
            if self._already_counted(belief, value):
                # Un même fait ne compte qu'une fois : relancer une analyse ne doit pas « gonfler » la confiance.
                results.append(Confrontation(
                    belief_id=belief.id, statement=belief.statement, metric=belief.metric or "", observed=value,
                    expected=belief.expectation(), outcome="confirmation" if confirmed else "contradiction",
                    prior=belief.confidence, posterior=belief.confidence,
                ))
                continue
            reference = abs(belief.threshold or 0.0) or 1.0
            distance = min(abs(value - (belief.threshold or 0.0)) / reference, 1.0)
            ratio = 1.0 + (strength - 1.0) * (0.5 + distance)  # faits limites ≈ moins probants
            prior = belief.confidence
            delta = math.log(ratio) * (1 if confirmed else -1)
            posterior = clamp(sigmoid(logit(prior) + delta), self.MIN, self.MAX)
            belief.confidence = round(posterior, 4)
            belief.evidence.append({
                "at": iso(self.clock.now()), "metric": belief.metric, "observed": value,
                "outcome": "confirmation" if confirmed else "contradiction",
            })
            self._save(belief, tested=True)
            results.append(Confrontation(
                belief_id=belief.id, statement=belief.statement, metric=belief.metric or "", observed=value,
                expected=belief.expectation(), outcome="confirmation" if confirmed else "contradiction",
                prior=round(prior, 4), posterior=belief.confidence,
            ))
        return results

    def _already_counted(self, belief: Belief, value: float) -> bool:
        if not belief.evidence:
            return False
        last = belief.evidence[-1]
        if last.get("observed") != value or "at" not in last:
            return False
        return self.clock.now() - parse_dt(last["at"]) < timedelta(hours=24)

    def learn(self, topic: str, statement: str, probability: float, samples: float) -> Belief:
        """RÉVISER à partir de résultats mesurés (croyance apprise, identifiant stable par sujet)."""

        belief_id = f"bel_learned_{topic.replace(' ', '_').replace(':', '_')}"[:64]
        try:
            belief = self.get(belief_id)
        except KeyError:
            return self.add(statement, topic=topic, confidence=probability, source="learning", belief_id=belief_id)
        belief.confidence = clamp(probability, self.MIN, self.MAX)
        belief.evidence.append({"at": iso(self.clock.now()), "learned_probability": probability, "samples": samples})
        self.db.execute("UPDATE beliefs SET statement = ? WHERE id = ?", (statement, belief_id))
        self._save(belief, tested=True)
        return self.get(belief_id)

    def stale(self, days: int = 30) -> list[Belief]:
        limit = self.clock.now() - timedelta(days=days)
        return [
            b for b in self.list()
            if b.testable and (b.last_tested_at or b.created_at) < limit
        ]

    def inconsistencies(self) -> list[tuple[Belief, Belief]]:
        """Paires de croyances fortes sur la même métrique qui ne peuvent pas être vraies ensemble."""

        strong = [b for b in self.list() if b.testable and b.confidence >= 0.6]
        pairs: list[tuple[Belief, Belief]] = []
        for i, a in enumerate(strong):
            for b in strong[i + 1:]:
                if a.metric != b.metric:
                    continue
                if not _compatible(a, b):
                    pairs.append((a, b))
        return pairs


def _interval(b: Belief) -> tuple[float, float]:
    t = b.threshold if b.threshold is not None else 0.0
    match b.comparator:
        case "lt" | "le":
            return (-math.inf, t)
        case "gt" | "ge":
            return (t, math.inf)
        case "between":
            return (t, b.threshold_high if b.threshold_high is not None else math.inf)
    return (-math.inf, math.inf)


def _compatible(a: Belief, b: Belief) -> bool:
    low_a, high_a = _interval(a)
    low_b, high_b = _interval(b)
    return max(low_a, low_b) < min(high_a, high_b) or (
        max(low_a, low_b) == min(high_a, high_b) and a.comparator in ("le", "ge", "between")
        and b.comparator in ("le", "ge", "between")
    )
