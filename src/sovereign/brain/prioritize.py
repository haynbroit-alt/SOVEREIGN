"""PRIORISER : impact, ROI, urgence — avec la confiance et l'effort comme modérateurs.

    base     = impact × confiance × (0,6 + 0,04 × urgence) / √effort
    risque   = 1 − 0,5 × risque
    ROI      = (valeur − coût) / coût, borné à [−0,9 ; 3]  →  bonus = 1 + ROI / 3   (si chiffrable)
    priorité = base × risque × bonus

Les recommandations identiques (même identifiant stable) venant de plusieurs Skills sont fusionnées :
la convergence de plusieurs angles d'analyse augmente la confiance (règle « noisy-OR »).
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from sovereign.skills.base import Recommendation


class Priority(BaseModel):
    rank: int
    score: float
    roi: float | None
    recommendation: Recommendation


def roi(rec: Recommendation) -> float | None:
    if rec.estimated_cost is None or rec.estimated_cost <= 0 or rec.estimated_value is None:
        return None
    return float((rec.estimated_value - rec.estimated_cost) / rec.estimated_cost)


def score(rec: Recommendation) -> float:
    base = rec.impact * rec.confidence * (0.6 + 0.04 * rec.urgency) / math.sqrt(rec.effort)
    penalty = 1 - 0.5 * rec.risk
    value = roi(rec)
    bonus = 1 + max(-0.9, min(3.0, value)) / 3 if value is not None else 1.0
    return round(base * penalty * bonus, 4)


def merge(recommendations: list[Recommendation]) -> list[Recommendation]:
    merged: dict[str, Recommendation] = {}
    for rec in recommendations:
        key = rec.id or rec.title
        if key not in merged:
            merged[key] = rec.model_copy(deep=True)
            continue
        current = merged[key]
        current.confidence = round(1 - (1 - current.confidence) * (1 - rec.confidence * 0.5), 4)
        current.evidence = list(dict.fromkeys(current.evidence + rec.evidence))
        current.tags = list(dict.fromkeys(current.tags + rec.tags))
        current.urgency = max(current.urgency, rec.urgency)
    return list(merged.values())


def prioritize(recommendations: list[Recommendation]) -> list[Priority]:
    ranked = sorted(merge(recommendations), key=lambda r: (-score(r), r.title))
    return [Priority(rank=i + 1, score=score(r), roi=roi(r), recommendation=r) for i, r in enumerate(ranked)]
