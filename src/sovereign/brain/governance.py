"""HUMAN REVIEW : file des validations humaines.

Tout ce qui est classé « risque élevé » (dépense, action externe, irréversible, perte probable) attend une
décision humaine. Une approbation :
- mémorise la décision (apprentissage) ;
- si la proposition comporte un coût, crée la DEMANDE de dépense auprès du garde-fou financier (qui re-vérifie
  la règle des 10 %) — le paiement reste manuel ;
- si elle comporte un suivi interne, l'enfile dans la file de tâches durable.
Un refus est aussi mémorisé : refuser est une décision.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from sovereign.core.clock import iso, parse_dt
from sovereign.core.db import dumps, loads
from sovereign.core.ids import new_id

ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class Approval(BaseModel):
    id: str
    kind: str
    title: str
    summary: str
    payload: dict[str, Any]
    risk: dict[str, Any]
    status: ApprovalStatus
    mission_id: str | None
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    reason: str | None = None


class ApprovalQueue:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.db = app.kernel.db
        self.clock = app.kernel.clock

    def create(self, kind: str, title: str, summary: str, payload: dict[str, Any], risk: dict[str, Any],
               mission_id: str | None = None) -> Approval:
        rec_id = payload.get("recommendation", {}).get("id")
        if rec_id:
            existing = self.db.one(
                "SELECT id FROM approvals WHERE status = 'pending' AND json_extract(payload, '$.recommendation.id') = ?",
                (rec_id,),
            )
            if existing is not None:  # pas de doublon de validation pour une même recommandation
                return self.get(existing["id"])
        approval_id = new_id("apr")
        self.db.execute(
            """INSERT INTO approvals(id, kind, title, summary, payload, risk, status, mission_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (approval_id, kind, title, summary, dumps(payload), dumps(risk), mission_id, iso(self.clock.now())),
        )
        self.app.kernel.events.publish("approval.requested", {"approval_id": approval_id, "title": title},
                                       source="governance")
        return self.get(approval_id)

    def _row(self, row: Any) -> Approval:
        return Approval(
            id=row["id"], kind=row["kind"], title=row["title"], summary=row["summary"],
            payload=loads(row["payload"]), risk=loads(row["risk"]), status=row["status"],
            mission_id=row["mission_id"], created_at=parse_dt(row["created_at"]),
            decided_at=parse_dt(row["decided_at"]) if row["decided_at"] else None,
            decided_by=row["decided_by"], reason=row["reason"],
        )

    def get(self, approval_id: str) -> Approval:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row is None:
            raise KeyError(f"validation inconnue : {approval_id}")
        return self._row(row)

    def list(self, status: str | None = "pending") -> list[Approval]:
        if status:
            rows = self.db.query("SELECT * FROM approvals WHERE status = ? ORDER BY created_at", (status,))
        else:
            rows = self.db.query("SELECT * FROM approvals ORDER BY created_at DESC")
        return [self._row(r) for r in rows]

    def decide(self, approval_id: str, approve: bool, actor: str, reason: str | None = None) -> dict[str, Any]:
        approval = self.get(approval_id)
        if approval.status != "pending":
            raise ValueError(f"validation déjà traitée ({approval.status})")
        status = "approved" if approve else "rejected"
        self.db.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, reason = ? WHERE id = ?",
            (status, iso(self.clock.now()), actor, reason, approval_id),
        )
        rec = approval.payload.get("recommendation", {})
        effects: dict[str, Any] = {"approval_id": approval_id, "status": status}
        kernel = self.app.kernel
        decision = kernel.memory.record(
            title=approval.title,
            decision=("Approuvé : " if approve else "Refusé : ") + rec.get("title", approval.title),
            context=approval.summary, rationale=reason or rec.get("rationale", ""),
            category=(rec.get("tags") or ["général"])[0], tags=rec.get("tags", []),
            skills=[rec["skill"]] if rec.get("skill") else [],
            recommendation_ids=[rec["id"]] if (rec.get("id") and approve) else [],
            confidence=float(rec.get("confidence", 0.5)), review_at=None, source=f"human:{actor}",
        )
        effects["decision_id"] = decision.id
        if approve:
            if rec.get("id"):
                kernel.db.execute(
                    """INSERT OR IGNORE INTO recommendation_log(id, skill, title, mission_id, status, at)
                       VALUES (?, ?, ?, ?, 'adopted', ?)""",
                    (rec["id"], rec.get("skill", ""), rec.get("title", ""), approval.mission_id,
                     iso(self.clock.now())),
                )
            cost = rec.get("estimated_cost")
            if cost is not None and Decimal(str(cost)) > 0:
                expense = kernel.finance.request_expense(
                    cost, (rec.get("tags") or ["divers"])[0], description=rec.get("title"),
                    requested_by=f"human:{actor}", reference=f"approval:{approval_id}",
                )
                effects["expense"] = {"id": expense.expense.id, "status": expense.expense.status,
                                      "message": expense.message}
            follow = rec.get("follow_up")
            if follow:
                task = self.app.queue.enqueue(
                    "skill.run", {"skill": follow["skill"], "action": follow["action"],
                                  "input": follow.get("payload", {}), "mission_id": approval.mission_id},
                    idempotency_key=f"approval:{approval_id}",
                )
                effects["task_id"] = task.id
        kernel.events.publish(f"approval.{status}", effects, source="governance")
        return effects
