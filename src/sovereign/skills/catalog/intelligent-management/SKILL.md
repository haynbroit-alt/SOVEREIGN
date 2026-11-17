---
name: intelligent-management
description: >-
  Organise les équipes d'agents (IA, humains, services) du directeur : enregistre leurs capacités, distribue les
  missions selon les compétences requises, la charge et la fiabilité, mesure les performances, détecte les
  conflits (ressources partagées, recommandations contradictoires, surcharge, cycles de dépendances) et propose
  rééquilibrage et séquencement. À utiliser pour toute question d'équipe, d'affectation, de charge ou de
  coordination.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.intelligent_management"
  actions: "assess, register_agent, assign_missions, measure_performance, detect_conflicts, coordination_report"
  keywords: "équipe, équipes, agent, agents, mission, missions, affectation, affecter, distribuer, charge, capacité, performance, conflit, conflits, coordination, organisation, management, manager, team, workload, assignment"
  events: "mission.*, agent.*, task.failed, cycle.daily, cycle.weekly"
  data: "agent, mission"
  risk: "write"
---

# Management Intelligent

## Description

Chef d'orchestre des équipes d'agents. Un **agent** déclare ses capacités, sa capacité (missions simultanées),
son coût par mission et sa fiabilité ; une **mission** déclare les capacités requises, sa priorité, ses
ressources, ses dates, ses dépendances et, une fois close, sa qualité, sa durée et son coût.

Méthodes :

- **Affectation** gloutonne par priorité décroissante : un agent n'est candidat que s'il couvre 100 % des
  capacités requises (comparaison insensible à la casse) et dispose d'une capacité libre ; départage par charge
  relative la plus faible, puis fiabilité, puis coût. Les missions dépendant d'une mission échouée ne sont pas
  affectées. Par défaut c'est une **simulation** (`apply=false`).
- **Performance** : taux de succès observé, probabilité de succès a posteriori Bêta(1+succès, 1+échecs), qualité,
  durée et coût moyens, charge/capacité. Sous-performance si taux < médiane − 20 points ou qualité < 60 %.
- **Conflits** : (1) ressource partagée par deux missions actives dont les intervalles [début, échéance] se
  chevauchent ; (2) recommandations contradictoires (même cible, directions opposées) ; (3) agent au-delà de sa
  capacité ; (4) cycle de dépendances (parcours en profondeur) ; (5) mission dépendant d'une mission échouée.
- **Coordination** : déplacement des missions « assigned » les moins prioritaires des agents surchargés vers des
  agents compatibles disponibles, et séquencement topologique (algorithme de Kahn, priorité en cas d'égalité).

## Quand l'utiliser

- « Qui doit faire quoi ? » → `assign_missions` (simulation), puis `apply=true` pour enregistrer.
- « Qui performe ? » → `measure_performance`.
- « Y a-t-il des blocages entre missions ? » → `detect_conflicts`, `coordination_report`.
- Nouvel agent IA ou humain disponible → `register_agent`.
- Le directeur l'appelle via `assess` (cycle quotidien) pour proposer les affectations et signaler les conflits.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Charge, missions par statut, conflits, affectations possibles | lecture |
| `register_agent` | Enregistre ou actualise un agent | écriture |
| `assign_missions` | Distribue les missions « todo » (simulation ou application) | écriture |
| `measure_performance` | Succès, qualité, durée, coût, utilisation par agent | lecture |
| `detect_conflicts` | Ressources, contradictions, surcharge, cycles, dépendances échouées | lecture |
| `coordination_report` | Rééquilibrage de charge et séquencement des missions | lecture |

## Entrées et sorties

Les listes `agents` / `missions` vides signifient « utiliser l'espace de travail ».

```bash
sovereign skill run intelligent-management register_agent --input '{"id": "a-redac", "name": "Rédacteur IA", "kind": "ai", "capabilities": ["rédaction", "seo"], "capacity": 3}'
sovereign skill run intelligent-management assign_missions --input '{"apply": false}'
sovereign skill run intelligent-management detect_conflicts --input '{}'
```

Sorties : `assignments` (mission → agent + raisons), `unassigned` (raison), `agents` (performances),
`conflicts` (type, gravité, missions, agents, détail), `moves` et `sequence` (coordination), plus le contrat
commun `summary`, `insights`, `recommendations`, `metrics` (`management.utilization`,
`management.conflicts`, `management.success_rate`…).

## Déclencheurs

- Mots-clés : équipe, agent, mission, affectation, charge, conflit, coordination, performance…
- Événements : `mission.*`, `agent.*`, `task.failed`, `cycle.daily`, `cycle.weekly`.

## Sécurité

- Aucune action externe : l'affectation ne fait qu'enregistrer `assigned_to` dans l'espace de travail, et
  seulement si `apply=true`.
- Aucune mission n'est confiée à un agent qui ne couvre pas toutes les capacités requises.
- Les capacités déclarées sont respectées : jamais d'affectation au-delà de `capacity`.

## Tests

`tests/skills/test_intelligent_management.py` : affectation par priorité et capacité, simulation vs
application, capacités manquantes, dépendance échouée, performances bayésiennes et sous-performance, les cinq
types de conflits, rééquilibrage, séquencement topologique, espace vide.
