---
name: memory-learning
description: >-
  Mémoire durable du directeur : conserve les décisions importantes avec leur contexte et leur résultat attendu,
  compare les résultats passés, identifie les erreurs répétitives, capitalise les leçons et adapte les stratégies
  selon les résultats mesurés. À utiliser pour se souvenir, retrouver une décision, tirer des leçons ou décider
  quelle approche favoriser ou éviter.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.memory_learning"
  actions: "assess, record_decision, record_outcome, search, compare_results, detect_repeated_errors, lessons, adapt_strategies"
  keywords: "mémoire, décision, décisions, souvenir, historique, leçon, leçons, erreur, erreurs, apprentissage, apprendre, passé, résultat, résultats, memory, lesson, learning, history"
  events: "decision.*, cycle.weekly"
  data: "decision"
  risk: "write"
---

# Mémoire et Apprentissage

## Description

Chaque décision importante est mémorisée avec : contexte, options étudiées, choix, justification, Skills et
recommandations à l'origine du choix, niveau de confiance, **attendu chiffré** (métrique, base, cible, sens) et
date de revue. À l'échéance, le résultat réel est mesuré : le score `(réel − base) / (cible − base)` détermine le
statut (`succeeded` ≥ 0,9 ; `partial` ≥ 0,4 ; sinon `failed`) et une leçon est formulée.

L'apprentissage est bayésien : pour chaque catégorie (ou tag), un a posteriori Bêta-Bernoulli donne la
probabilité de succès et son incertitude. Les stratégies sont alors à **favoriser**, **éviter**, **maintenir**
ou **explorer** (incertitude trop forte). Ces mêmes résultats alimentent l'utilité mesurée de chaque Skill
(`/skills`) et la révision du modèle du monde.

Recherche : SQLite FTS5 (BM25, insensible aux accents) sur titres, contextes, décisions, justifications, tags et
leçons.

## Quand l'utiliser

- « Qu'avions-nous décidé sur… ? » → `search`
- Après une décision du conseil ou humaine → `record_decision`
- À l'échéance de revue → `record_outcome`
- « Quelles approches marchent ? » → `compare_results`, `adapt_strategies`
- « Répétons-nous les mêmes erreurs ? » → `detect_repeated_errors`
- Revue hebdomadaire : `assess` liste les revues dues et les échecs récurrents.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Décisions ouvertes, revues dues, taux de succès, erreurs répétées | lecture |
| `record_decision` | Mémorise une décision (alerte si proche d'un échec passé) | écriture |
| `record_outcome` | Mesure le résultat, calcule score et leçon, publie `decision.outcome_recorded` | écriture |
| `search` | Recherche plein texte classée BM25 | lecture |
| `compare_results` | Statistiques par catégorie / tag (a posteriori bêta) | lecture |
| `detect_repeated_errors` | Échecs récurrents partageant une catégorie ou un tag | lecture |
| `lessons` | Leçons capitalisées | lecture |
| `adapt_strategies` | Favoriser / éviter / maintenir / explorer | lecture |

## Entrées et sorties

```bash
sovereign skill run memory-learning record_decision --input '{"title": "Lancer l offre Pro", "decision": "Lancer à 49 €/mois", "category": "pricing", "tags": ["pro"], "expected": {"metric": "mrr", "baseline": 2000, "target": 3000}, "review_in_days": 30}'
sovereign skill run memory-learning record_outcome --input '{"decision_id": "dec_…", "actual": 2950}'
sovereign memory "pricing"
```

## Déclencheurs

- Mots-clés : mémoire, décision, leçon, erreur, historique, apprentissage…
- Événements : `decision.*`, `cycle.weekly` (revue des résultats).

## Sécurité

- Les décisions ne sont jamais supprimées ; un résultat ne peut pas « rouvrir » une décision.
- Les requêtes de recherche sont assainies (termes cités) : aucune injection de syntaxe FTS.
- Une décision ne déclenche aucune action par elle-même : l'exécution passe par le directeur et ses garde-fous.

## Tests

`tests/skills/test_memory_learning.py` : mémorisation, recherche accentuée, score et statut automatiques,
erreurs répétées, adaptation bayésienne, revues dues, alerte de similarité avec un échec.
