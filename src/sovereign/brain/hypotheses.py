"""HYPOTHÈSES : quand un fait contredit une croyance, pourquoi ?

Base de connaissances d'explications plausibles par famille de métriques et sens de l'écart, chaque
hypothèse étant associée au test qui permet de la vérifier (une action de Skill). Les hypothèses sont classées par
plausibilité a priori × force de la contradiction. Ce sont des pistes d'investigation, pas des conclusions.
"""

from __future__ import annotations

import fnmatch
from typing import Literal

from pydantic import BaseModel

from sovereign.brain.world_model import Confrontation


class Hypothesis(BaseModel):
    statement: str
    plausibility: float
    test_skill: str
    test_action: str
    about: str
    direction: Literal["au-dessus", "en-dessous"]


# (motif de métrique, sens de l'écart observé) -> [(explication, plausibilité, skill, action)]
_KNOWLEDGE: list[tuple[str, str, list[tuple[str, float, str, str]]]] = [
    ("finance.collect*", "en-dessous", [
        ("Retards de paiement clients (créances échues)", 0.55, "finance-budget", "assess"),
        ("Baisse de la demande ou perte d'un client important", 0.5, "marketing-strategy", "assess"),
        ("Saisonnalité de l'activité", 0.3, "watch-anticipation", "detect_market_changes"),
    ]),
    ("finance.utilization", "au-dessus", [
        ("Dépenses récurrentes non réévaluées", 0.5, "finance-budget", "forecast"),
        ("Campagnes au ROAS insuffisant", 0.45, "marketing-strategy", "measure_campaigns"),
    ]),
    ("marketing.*cac*", "au-dessus", [
        ("Saturation du canal d'acquisition", 0.5, "marketing-strategy", "measure_campaigns"),
        ("Hausse du coût des enchères publicitaires", 0.4, "watch-anticipation", "detect_market_changes"),
        ("Baisse du taux de conversion du parcours", 0.45, "analysis-optimization", "analyze_projects"),
    ]),
    ("marketing.*roas*", "en-dessous", [
        ("Ciblage inadapté au segment prioritaire", 0.45, "marketing-strategy", "segment_audiences"),
        ("Message ou offre peu convaincants", 0.4, "marketing-strategy", "design_strategy"),
        ("Pression concurrentielle accrue", 0.35, "business-intelligence", "analyze_competition"),
    ]),
    ("projects.*", "en-dessous", [
        ("Objectifs irréalistes ou mal calibrés", 0.4, "analysis-optimization", "analyze_projects"),
        ("Manque de capacité dans les équipes d'agents", 0.45, "intelligent-management", "measure_performance"),
        ("Dépendances bloquantes entre missions", 0.35, "intelligent-management", "detect_conflicts"),
    ]),
    ("competition.*", "au-dessus", [
        ("Consolidation du marché ou entrant dominant", 0.5, "business-intelligence", "analyze_competition"),
        ("Signal concurrentiel manqué par la veille", 0.35, "watch-anticipation", "detect_trends"),
    ]),
    ("market.*", "en-dessous", [
        ("Hypothèses de taille de marché trop optimistes", 0.5, "business-intelligence", "size_market"),
        ("Segment cible mal choisi", 0.4, "marketing-strategy", "segment_audiences"),
    ]),
]
_DEFAULT = [
    ("Changement de contexte non modélisé", 0.35, "watch-anticipation", "assess"),
    ("Donnée incomplète ou erreur de mesure", 0.3, "analysis-optimization", "assess"),
    ("Croyance initiale mal calibrée", 0.3, "memory-learning", "compare_results"),
]


def _direction(c: Confrontation) -> Literal["au-dessus", "en-dessous"]:
    if c.expected.startswith(f"{c.metric} <") or c.expected.startswith(f"{c.metric} ≤"):
        return "au-dessus"
    if c.expected.startswith(f"{c.metric} >") or c.expected.startswith(f"{c.metric} ≥"):
        return "en-dessous"
    return "au-dessus"


def explain(contradictions: list[Confrontation], limit: int = 6) -> list[Hypothesis]:
    hypotheses: list[Hypothesis] = []
    for c in contradictions:
        if c.outcome != "contradiction":
            continue
        direction = _direction(c)
        strength = max(0.3, c.prior - c.posterior + 0.5)
        entries = next(
            (items for pattern, sense, items in _KNOWLEDGE
             if fnmatch.fnmatchcase(c.metric, pattern) and sense == direction),
            _DEFAULT,
        )
        for statement, prior, skill, action in entries:
            hypotheses.append(Hypothesis(
                statement=statement, plausibility=round(min(0.95, prior * strength), 3), test_skill=skill,
                test_action=action, about=c.statement, direction=direction,
            ))
    hypotheses.sort(key=lambda h: -h.plausibility)
    unique: dict[str, Hypothesis] = {}
    for h in hypotheses:
        unique.setdefault(h.statement, h)
    return list(unique.values())[:limit]
