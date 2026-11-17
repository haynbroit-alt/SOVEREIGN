"""Mémoire des décisions : ce qui a été décidé, pourquoi, ce qui était attendu, ce qui s'est réellement passé.

Recherche plein texte FTS5 (BM25, insensible aux accents). Les résultats mesurés alimentent l'apprentissage
(a posteriori bayésiens par catégorie / tag) et la révision du modèle du monde.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sovereign.core.clock import Clock, iso, parse_dt
from sovereign.core.db import Database, dumps, loads
from sovereign.core.ids import new_id
from sovereign.core.stats import clamp

DecisionStatus = Literal["open", "succeeded", "partial", "failed", "abandoned"]


class Expectation(BaseModel):
    metric: str
    baseline: float | None = None
    target: float
    direction: Literal["increase", "decrease"] = "increase"


class Decision(BaseModel):
    id: str
    title: str
    context: str = ""
    decision: str
    rationale: str = ""
    options: list[str] = Field(default_factory=list)
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    recommendation_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    expected: Expectation | None = None
    review_at: datetime | None = None
    status: DecisionStatus = "open"
    outcome: dict[str, Any] | None = None
    score: float | None = None
    lesson: str | None = None
    source: str = "human"
    created_at: datetime
    updated_at: datetime


class MemoryError_(Exception):
    pass


def fts_query(text: str) -> str:
    """Transforme un texte libre en requête FTS5 sûre (termes cités, OU logique, préfixes)."""

    tokens = [t for t in re.findall(r"\w+", text.lower()) if len(t) > 1]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"*' for t in tokens[:12])


class DecisionMemory:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def record(
        self,
        title: str,
        decision: str,
        *,
        context: str = "",
        rationale: str = "",
        options: list[str] | None = None,
        category: str = "general",
        tags: list[str] | None = None,
        skills: list[str] | None = None,
        recommendation_ids: list[str] | None = None,
        confidence: float = 0.5,
        expected: Expectation | dict[str, Any] | None = None,
        review_at: datetime | str | None = None,
        source: str = "human",
    ) -> Decision:
        now = self.clock.now()
        exp = Expectation.model_validate(expected) if isinstance(expected, dict) else expected
        record = Decision(
            id=new_id("dec"), title=title, context=context, decision=decision, rationale=rationale,
            options=options or [], category=category, tags=[t.lower() for t in (tags or [])],
            skills=skills or [], recommendation_ids=recommendation_ids or [], confidence=clamp(confidence),
            expected=exp, review_at=parse_dt(review_at) if review_at else None, source=source,
            created_at=now, updated_at=now,
        )
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO decisions(id, title, context, decision, rationale, options, category, tags, skills,
                       recommendation_ids, confidence, expected, review_at, status, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
                (record.id, title, context, decision, rationale, dumps(record.options), category,
                 " ".join(record.tags), dumps(record.skills), dumps(record.recommendation_ids), record.confidence,
                 record.expected.model_dump_json() if record.expected else None,
                 iso(record.review_at) if record.review_at else None, source, iso(now), iso(now)),
            )
            for rec_id in record.recommendation_ids:
                skill = record.skills[0] if record.skills else "unknown"
                self.db.execute(
                    """INSERT OR IGNORE INTO recommendation_log(id, skill, title, mission_id, status, at)
                       VALUES (?, ?, ?, NULL, 'adopted', ?)""",
                    (rec_id, skill, title, iso(now)),
                )
        return record

    def _row(self, row: Any) -> Decision:
        return Decision(
            id=row["id"], title=row["title"], context=row["context"], decision=row["decision"],
            rationale=row["rationale"], options=loads(row["options"]), category=row["category"],
            tags=[t for t in row["tags"].split() if t], skills=loads(row["skills"]),
            recommendation_ids=loads(row["recommendation_ids"]), confidence=row["confidence"],
            expected=Expectation.model_validate_json(row["expected"]) if row["expected"] else None,
            review_at=parse_dt(row["review_at"]) if row["review_at"] else None, status=row["status"],
            outcome=loads(row["outcome"]), score=row["score"], lesson=row["lesson"], source=row["source"],
            created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
        )

    def get(self, decision_id: str) -> Decision:
        row = self.db.one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
        if row is None:
            raise MemoryError_(f"décision inconnue : {decision_id}")
        return self._row(row)

    def list(self, status: str | None = None, category: str | None = None, limit: int = 200) -> list[Decision]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self.db.query(f"SELECT * FROM decisions{where} ORDER BY created_at DESC LIMIT ?", params)
        return [self._row(r) for r in rows]

    def search(self, text: str, limit: int = 10) -> list[tuple[Decision, float]]:
        query = fts_query(text)
        if not query:
            return []
        rows = self.db.query(
            """SELECT d.*, bm25(decisions_fts) AS rank FROM decisions_fts
               JOIN decisions d ON d.rowid = decisions_fts.rowid
               WHERE decisions_fts MATCH ? ORDER BY rank LIMIT ?""",
            (query, limit),
        )
        return [(self._row(r), -float(r["rank"])) for r in rows]

    def record_outcome(
        self,
        decision_id: str,
        *,
        actual: float | None = None,
        status: DecisionStatus | None = None,
        notes: str = "",
        lesson: str | None = None,
    ) -> Decision:
        """Mesure le résultat réel. Si l'attendu est chiffré, le score et le statut sont calculés."""

        decision = self.get(decision_id)
        score: float | None = None
        if decision.expected is not None and actual is not None:
            exp = decision.expected
            baseline = exp.baseline if exp.baseline is not None else 0.0
            span_ = exp.target - baseline
            if span_ == 0:
                score = 1.0 if actual == exp.target else 0.0
            else:
                score = (actual - baseline) / span_
            score = round(max(-1.0, min(1.5, score)), 4)
            if status is None:
                status = "succeeded" if score >= 0.9 else "partial" if score >= 0.4 else "failed"
        if status is None:
            raise MemoryError_("statut requis quand aucun résultat chiffré n'est comparable à l'attendu")
        if status == "open":
            raise MemoryError_("un résultat ne peut pas laisser la décision ouverte")
        if score is None:
            score = {"succeeded": 1.0, "partial": 0.5, "failed": 0.0, "abandoned": 0.0}[status]
        auto_lesson = lesson or self._lesson(decision, status, actual)
        outcome = {"actual": actual, "notes": notes, "measured_at": iso(self.clock.now())}
        self.db.execute(
            """UPDATE decisions SET status = ?, outcome = ?, score = ?, lesson = ?, updated_at = ? WHERE id = ?""",
            (status, dumps(outcome), score, auto_lesson, iso(self.clock.now()), decision_id),
        )
        return self.get(decision_id)

    @staticmethod
    def _lesson(decision: Decision, status: str, actual: float | None) -> str:
        exp = decision.expected
        measured = f" (mesuré : {actual} pour une cible {exp.target} sur {exp.metric})" if exp and actual is not None else ""
        if status == "succeeded":
            return f"Approche validée pour « {decision.category} » : {decision.decision}{measured}."
        if status == "partial":
            return f"Résultat partiel pour « {decision.category} » : ajuster l'exécution ou la cible{measured}."
        return (f"Échec pour « {decision.category} » : réexaminer les hypothèses avant de répéter "
                f"« {decision.decision} »{measured}.")

    def due_for_review(self, now: datetime | None = None) -> list[Decision]:
        moment = iso(now or self.clock.now())
        rows = self.db.query(
            "SELECT * FROM decisions WHERE status = 'open' AND review_at IS NOT NULL AND review_at <= ? "
            "ORDER BY review_at",
            (moment,),
        )
        return [self._row(r) for r in rows]
