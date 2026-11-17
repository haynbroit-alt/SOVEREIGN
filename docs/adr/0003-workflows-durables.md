# ADR 0003 — Workflows durables et files de tâches : Temporal étudié, file SQLite retenue

- **Statut** : accepté — 2026-11-17

## Besoin réel

- Cycles quotidien / hebdomadaire / mensuel composés de quelques étapes (analyses de Skills en millisecondes,
  rapport, notification), **idempotents par période** et **reprenables** après un arrêt.
- Tâches déclenchées par des événements (règles), avec **reprises** et **anti-doublons**.
- Volume : quelques dizaines à milliers de tâches par jour, un seul processus.

## Options étudiées (versions du 2026-11-17)

| Option | Infrastructure | Forces | Limites pour SOVEREIGN |
|---|---|---|---|
| **Temporal** (`temporalio` 1.36.1) | Cluster Temporal (ou Temporal Cloud, payant) | Référence des workflows durables, historique d'événements, signaux, workflows de plusieurs mois | Un service à opérer en permanence (ou un abonnement) pour des workflows de quelques secondes : complexité et coût non justifiés |
| **DBOS Transact** (`dbos` 3.6.0) | Bibliothèque ; PostgreSQL (SQLite supporté en Python, vérifié dans le code : `_sys_db_sqlite.py`) | Pas de serveur, décorateurs `@workflow`/`@step`, files, cron intégrés | Tire SQLAlchemy, psycopg, croniter… ; runtime global ; apport marginal vs le besoin |
| **Restate**, **Hatchet**, **Inngest** | Un serveur / service | Modernes, bonnes ergonomies | Encore un service à opérer |
| **Celery / RQ** | Broker Redis/RabbitMQ | Mûrs | Files, pas de workflows durables ; un broker à opérer |
| **File SQLite maison** (retenu) | Aucune (même base) | ~200 lignes testées : baux, reprises exponentielles, clés d'idempotence, points de reprise par étape, lettres mortes | À remplacer si plusieurs nœuds ou workflows de longue durée avec attentes humaines distribuées |

## Décision

File durable SQLite (`automation/queue.py`) + `CycleRunner` dont chaque étape passe par `queue.step()` (journal
des résultats : une reprise rejoue le journal au lieu de ré-exécuter). Interface volontairement proche de
DBOS/Temporal (tâche = workflow, `step` = activité) pour faciliter une migration.

## Critères de migration (mesurables)

- Plus d'un processus écrivain, ou plus de ~50 tâches/seconde → **DBOS sur PostgreSQL** (même modèle mental).
- Workflows multi-services de plusieurs jours avec signaux externes → **Temporal**.

## Preuves

`tests/test_automation.py` : idempotence, priorité, reprise après expiration de bail (« crash »), backoff puis
lettre morte, points de reprise, cycles idempotents par période, planificateur rattrapant les cycles manqués.
