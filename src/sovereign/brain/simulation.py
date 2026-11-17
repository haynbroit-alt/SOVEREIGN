"""SIMULER : bac à sable Monte-Carlo avant toute exécution.

Pour une recommandation, on tire N scénarios :
- succès ~ Bernoulli(p), avec p = confiance × (1 − 0,5 × risque) ;
- valeur en cas de succès ~ triangulaire(v × (1 − u), v, v × (1 + u/2)), u = incertitude = 1,2 − confiance ;
- coût ~ triangulaire(0,9 c, c, 1,4 c) (les dépassements sont plus fréquents que les économies).

Résultat net = valeur obtenue − coût. Si aucun montant n'est estimé, la simulation se fait en « points
d'impact » (impact 0-10, coût ≈ 0,3 × effort) et le signale. Le tirage est reproductible (graine dérivée de
l'identifiant de la recommandation).

Le classement de risque décide de la suite de la boucle : RISQUE FAIBLE → exécution interne automatique,
RISQUE ÉLEVÉ → revue humaine.
"""

from __future__ import annotations

import hashlib
import random
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from sovereign.core.stats import percentile
from sovereign.skills.base import Recommendation


class SimulationResult(BaseModel):
    recommendation_id: str
    title: str
    unit: Literal["money", "impact_points"]
    runs: int
    p_success: float
    expected_value: float
    p_loss: float
    p5: float
    p50: float
    p95: float
    risk_class: Literal["low", "high"]
    reasons: list[str]


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def simulate(
    rec: Recommendation,
    *,
    available_budget: Decimal | None = None,
    runs: int = 2000,
    seed: int | None = None,
) -> SimulationResult:
    rng = random.Random(seed if seed is not None else _seed(rec.id or rec.title))
    p_success = max(0.0, min(1.0, rec.confidence * (1 - 0.5 * rec.risk)))
    uncertainty = max(0.05, 1.2 - rec.confidence)
    monetary = rec.estimated_value is not None or rec.estimated_cost is not None
    if monetary:
        value = float(rec.estimated_value or 0)
        cost = float(rec.estimated_cost or 0)
        unit: Literal["money", "impact_points"] = "money"
    else:
        value = rec.impact
        cost = 0.3 * rec.effort
        unit = "impact_points"
    outcomes: list[float] = []
    for _ in range(runs):
        gained = 0.0
        if rng.random() < p_success and value > 0:
            gained = rng.triangular(value * (1 - uncertainty), value * (1 + uncertainty / 2), value)
            gained = max(gained, 0.0)
        spent = rng.triangular(cost * 0.9, cost * 1.4, cost) if cost > 0 else 0.0
        outcomes.append(gained - spent)
    expected = sum(outcomes) / runs
    p_loss = sum(1 for o in outcomes if o < 0) / runs
    reasons: list[str] = []
    if rec.external:
        reasons.append("action externe (client, publication, achat)")
    if not rec.reversible:
        reasons.append("action irréversible")
    if rec.estimated_cost and rec.estimated_cost > 0:
        reasons.append("engage une dépense (paiement humain obligatoire)")
        if available_budget is not None and rec.estimated_cost > available_budget * Decimal("0.25"):
            reasons.append("coût > 25 % du budget disponible")
    if p_loss > 0.35:
        reasons.append(f"probabilité de perte {p_loss:.0%} > 35 %")
    if rec.confidence < 0.4:
        reasons.append(f"confiance faible ({rec.confidence:.0%})")
    if expected <= 0:
        reasons.append("espérance non positive")
    return SimulationResult(
        recommendation_id=rec.id, title=rec.title, unit=unit, runs=runs, p_success=round(p_success, 4),
        expected_value=round(expected, 2), p_loss=round(p_loss, 4), p5=round(percentile(outcomes, 5), 2),
        p50=round(percentile(outcomes, 50), 2), p95=round(percentile(outcomes, 95), 2),
        risk_class="high" if reasons else "low", reasons=reasons,
    )
