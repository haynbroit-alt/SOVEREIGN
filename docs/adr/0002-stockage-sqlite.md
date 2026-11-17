# ADR 0002 — Mémoire et données : SQLite (WAL) + FTS5

- **Statut** : accepté — 2026-11-17

## Contexte

Il faut conserver : encaissements et dépenses (exactitude absolue), décisions et leçons (recherche), croyances,
observations (séries), événements, tâches durables, validations, rapports, données métier hétérogènes.

## Décision

- **SQLite 3.4x+** en mode WAL, migrations versionnées (`core/db.py`).
- Tables dédiées pour les données critiques ; **magasin documentaire** `records(kind, id, data JSON)` validé par
  Pydantic pour les données métier évolutives (marchés, campagnes, projets…).
- **Invariants garantis par la base** (triggers) : registre des encaissements et journal d'audit append-only,
  montant approuvé d'une dépense immuable, dépenses non supprimables.
- Montants en **centimes entiers** ; jamais de flottant pour l'argent.
- **FTS5** (`unicode61 remove_diacritics 2`, BM25) pour la mémoire des décisions.

## Alternatives

| Option | Pour | Contre |
|---|---|---|
| PostgreSQL | Concurrence multi-nœuds, JSONB, pgvector | Un serveur à opérer, sauvegardes, coût |
| SQLite (retenu) | Zéro opération, fichier unique, transactionnel, FTS5 | Un seul nœud écrivain |
| Base vectorielle | Recherche sémantique | Utilité non démontrée à cette échelle (voir veille) |

## Conséquences

- Déploiement trivial (un fichier), y compris dans GitHub Actions (état persistant sur une branche dédiée).
- Les écritures concurrentes sont sérialisées (`BEGIN IMMEDIATE`) — testé avec 20 threads sur le budget.
- Migration vers PostgreSQL possible : l'accès passe par `Database`, `RecordStore` et des requêtes SQL standard ;
  à envisager au-delà d'un seul processus écrivain.
