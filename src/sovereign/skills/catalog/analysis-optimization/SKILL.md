---
name: analysis-optimization
description: >-
  Analyse les performances des projets (atteinte des KPI, santé, tendances), identifie les points faibles,
  détecte les fonctionnalités inutilisées et leur coût, recommande des améliorations priorisées (ICE) et vérifie
  statistiquement l'effet réel d'une modification (test t de Welch). Audite aussi l'utilité réelle des Skills et
  commandes de SOVEREIGN. À utiliser pour évaluer un projet, optimiser un produit ou mesurer un changement.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.analysis_optimization"
  actions: "assess, analyze_projects, detect_unused_features, recommend_improvements, verify_change, audit_system"
  keywords: "analyse, analyser, performance, performances, projet, projets, kpi, indicateur, point faible, faiblesse, fonctionnalité inutile, fonctionnalités, optimisation, optimiser, amélioration, améliorer, vérifier, avant après, test a/b, audit, performance review, optimization, unused feature, improvement"
  events: "project.*, change.*, kpi.*, metric.*, cycle.weekly, cycle.monthly"
  data: "project, change"
  risk: "read_only"
---

# Analyse et Optimisation

## Description

Analyste de performance du directeur. Méthodes :

- **Atteinte d'un KPI** : `valeur / cible` si « plus haut = mieux », `cible / valeur` si « plus bas = mieux »,
  bornée à [0 ; 1,5]. **Santé d'un projet** : moyenne pondérée des atteintes, chaque KPI plafonné à 1 (une
  sur-performance ne masque pas un KPI en échec). Statuts : `on_track` ≥ 0,9, `at_risk` ≥ 0,7, `off_track` sinon.
- **Tendance** : régression linéaire sur `history` + valeur courante (≥ 3 points) ; pente relative à la moyenne,
  seuil ±2 % par période, interprétée selon le sens du KPI (`improving`, `stable`, `degrading`).
- **Fonctionnalités inutiles** : part d'utilisateurs = `users / active_users` ; une fonctionnalité est signalée si
  sa part est sous le seuil (5 % par défaut) **et** ses usages sont dans le quintile inférieur du projet, sauf si
  elle est sortie depuis moins de 30 jours. Courbe de Pareto : fonctionnalités portant 80 % des usages.
  Le coût de maintenance mensuel des fonctionnalités signalées est chiffré (économie annuelle potentielle).
- **Backlog ICE** : impact × confiance × facilité, à partir des KPI sous la cible, des tendances négatives et
  des fonctionnalités inutiles.
- **Vérification après modification** : test t de Welch (variances inégales) entre mesures avant et après, d de
  Cohen ; verdict `amélioré` / `dégradé` (p < 0,05, selon l'objectif) ou `non concluant` avec la taille
  d'échantillon nécessaire (règle de Lehr : n ≈ 16 σ² / δ² par groupe, puissance 80 %).
- **Auto-audit** : la même logique appliquée à SOVEREIGN : Skills jamais utilisées, fiabilité < 90 %,
  recommandations jamais adoptées, commandes inutilisées — pour ne garder que ce qui apporte de la valeur.

## Quand l'utiliser

- « Comment vont nos projets ? », « Quels sont nos points faibles ? » → `analyze_projects`
- « Quelles fonctionnalités ne servent à rien ? » → `detect_unused_features`
- « Que faut-il améliorer en priorité ? » → `recommend_improvements`
- « Notre modification a-t-elle vraiment marché ? » → `verify_change`
- « SOVEREIGN lui-même est-il utile ? » → `audit_system` (utilisé par `/improve` et `/audit`)
- Le directeur appelle `assess` aux cycles hebdomadaire et mensuel.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Projets + changements enregistrés + auto-audit du système | lecture |
| `analyze_projects` | Atteinte des KPI, santé, statut, tendances, points faibles | lecture |
| `detect_unused_features` | Fonctionnalités peu utilisées, coût évitable, Pareto | lecture |
| `recommend_improvements` | Backlog d'améliorations priorisé ICE | lecture |
| `verify_change` | Effet réel avant/après (Welch, Cohen, taille d'échantillon) | lecture |
| `audit_system` | Utilité réelle des Skills et commandes de SOVEREIGN | lecture |

## Entrées et sorties

Les entrées vides utilisent l'espace de travail (kinds `project` et `change`). Schémas JSON complets :
`sovereign skill show analysis-optimization`.

```bash
sovereign skill run analysis-optimization analyze_projects --input '{"projects": [{"id": "app", "name": "App", "kpis": [{"name": "activation", "value": 0.32, "target": 0.4, "history": [0.36, 0.34, 0.33]}]}]}'
sovereign skill run analysis-optimization verify_change --input '{"metric": "conversion", "before": [2.1, 2.3, 2.0, 2.2], "after": [2.6, 2.8, 2.7, 2.9], "goal": "increase"}'
sovereign skill run analysis-optimization audit_system --input '{}'
```

Sorties : `projects[].health/status/kpis/weak_points`, `unused[]` avec `user_share` et coût,
`backlog[]` (ICE), `verdict` + `p_value` + `cohens_d` + `sample_size_needed`, et pour l'auto-audit
`never_used_skills`, `unreliable_skills`, `unadopted_skills`, `never_used_commands`. Contrat commun : `summary`,
`insights`, `recommendations`, `metrics` (`projects.health_avg`, `projects.<id>.health`,
`features.wasted_monthly_cost`, `system.avg_reliability`…).

## Déclencheurs

- Mots-clés : analyse, performance, projet, KPI, point faible, fonctionnalité inutile, optimisation, audit…
- Événements : `project.*`, `change.*`, `kpi.*`, `metric.*`, `cycle.weekly`, `cycle.monthly`.

## Sécurité

- Lecture seule : la Skill n'écrit rien et ne modifie aucun produit.
- Retirer une fonctionnalité affecte des utilisateurs : ces recommandations sont marquées `external` et passent
  donc par la revue humaine du directeur.
- L'auto-audit ne propose aucun retrait tant que l'historique est insuffisant (< 20 exécutions par défaut) :
  pas de conclusion hâtive sur un système neuf.
- Les verdicts statistiques indiquent toujours la p-valeur ; « non concluant » n'est jamais présenté comme
  un succès.

## Tests

`tests/skills/test_analysis_optimization.py` : atteinte et santé calculées à la main, KPI « plus bas = mieux »,
tendance dégradée, fonctionnalités inutiles (seuil, quintile, fonctionnalités récentes, Pareto, coût),
backlog ICE trié, vérification Welch (amélioré / dégradé / non concluant + taille d'échantillon), changement
enregistré avec objectif déduit du projet, auto-audit (historique insuffisant, fiabilité, adoption), espace vide.
