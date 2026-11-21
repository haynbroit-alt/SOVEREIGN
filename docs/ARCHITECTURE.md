# Architecture

```
                 CLI · shell · API REST · MCP (stdio / HTTP) · webhooks · GitHub Actions
                                          │
                                 ┌────────▼────────┐
                                 │ Routeur de      │  15 commandes, mesurées (command_runs)
                                 │ commandes       │
                                 └────────┬────────┘
            ┌─────────────────────────────┼───────────────────────────────┐
   ┌────────▼────────┐          ┌─────────▼─────────┐           ┌─────────▼─────────┐
   │ CERVEAU CENTRAL │          │ AUTOMATISATIONS   │           │ GOUVERNANCE       │
   │ Directeur       │◄────────►│ cycles · règles   │──────────►│ validations       │
   │ Modèle du monde │          │ file durable      │           │ humaines          │
   │ Conseil des IA  │          │ planificateur     │           └───────────────────┘
   │ Simulation      │          └─────────┬─────────┘
   └────────┬────────┘                    │
            │ sélection par pertinence    │
   ┌────────▼─────────────────────────────▼──────────────────────────────────────┐
   │ 10 SKILLS (SKILL.md Agent Skills + code typé, chargés à la demande)          │
   └────────┬────────────────────────────────────────────────────────────────────┘
   ┌────────▼────────────────────────────────────────────────────────────────────┐
   │ NOYAU : SQLite WAL · registre append-only · mémoire FTS5 · bus d'événements │
   │ garde-fou 10 % · passerelle LLM budgétée · clés d'API · HMAC · OpenTelemetry │
   └─────────────────────────────────────────────────────────────────────────────┘
```

## La boucle cognitive (`brain/director.py`)

| Phase | Implémentation |
|---|---|
| OBSERVER | Sélection des Skills pertinentes (`SkillRegistry.select`) puis action `assess` de chacune ; métriques enregistrées en observations |
| COMPRENDRE | Croyances du modèle du monde liées aux métriques de la mission |
| CONFRONTER | Faits vs croyances testables → confirmation / contradiction, révision bayésienne en log-cote (un même fait compté une fois / 24 h) |
| HYPOTHÈSES | Sur contradiction : explications candidates par famille de métriques, chacune avec son test (action de Skill) ; tests exécutés en mode `--deep` |
| ANTICIPER | Régression sur l'historique des observations (prévision à 30 jours), opportunités, risques, anomalies |
| PRIORISER | impact × confiance × urgence / √effort × (1 − risque/2) × bonus ROI ; fusion des recommandations convergentes |
| DÉCIDER | Conseil CEO + RISQUE (veto) + CFO (veto budgétaire) + CMO + COO + MÉMOIRE ; réfutations ; vote pondéré |
| SIMULER | Monte-Carlo (2 000 scénarios, graine reproductible) : espérance, P(perte), P5/P50/P95 → classe de risque |
| EXÉCUTER | Risque faible : suivi automatique (tâche durable) ou mission confiée aux équipes d'agents |
| REVUE HUMAINE | Risque élevé (dépense, action externe, irréversible, perte probable) : validation humaine (jamais via MCP) |
| MESURER | Clôture automatique des décisions arrivées à échéance dont la métrique attendue est observée |
| APPRENDRE / RÉVISER | A posteriori bêta par catégorie → croyances apprises ; utilité des Skills → a priori de sélection |
| DREAMING | Auto-audit : croyances obsolètes ou incompatibles, tâches mortes, validations en souffrance, Skills inutilisées ou peu fiables, données manquantes, échéances à venir |

Une recommandation déjà adoptée ou en attente de validation n'est pas re-délibérée à chaque cycle.

## Modules

| Dossier | Contenu |
|---|---|
| `core/` | `db` (migrations, triggers), `store`, `events`, `finance` (règle des 10 %), `memory` (FTS5), `llm`, `security`, `stats`, `telemetry`, `clock`, `money`, `ids` |
| `skills/` | `base` (contrat), `registry` (découverte/sélection), 10 implémentations, `catalog/*/SKILL.md` |
| `brain/` | `director`, `world_model`, `council`, `simulation`, `prioritize`, `hypotheses`, `governance` |
| `automation/` | `queue` (durable), `worker`, `rules`, `cycles`, `scheduler`, `webhooks`, `notify` |
| `commands/` | `router`, `handlers` (15 commandes), `render` |
| `interfaces/` | `cli` (Typer), `api` (FastAPI), `mcp_server` (MCP 2.x) |
| `app.py`, `kernel.py`, `workspace.py` | Assemblage, noyau, import/export |

## Garanties de sécurité

1. **Aucun accès bancaire** : aucun code ne peut émettre un paiement.
2. **10 % des encaissements réels**, plafond refusé au démarrage s'il est dépassé ; transaction exclusive par demande.
3. **Append-only** (registre, audit) imposé par triggers SQL — `UPDATE`, `DELETE` et `INSERT OR REPLACE` refusés ;
   montants approuvés immuables. Limite : un accès direct au fichier de base permet de supprimer les triggers.
4. **Revue humaine** pour tout risque élevé ; aucune approbation possible par un agent via MCP.
5. **IA budgétée et non décisionnaire** (ADR 0004).
6. Clés d'API à portées (empreintes SHA-256), webhooks HMAC avec anti-rejeu, DNS-rebinding protégé pour MCP local.
