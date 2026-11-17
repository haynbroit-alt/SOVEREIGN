# Les 15 commandes

Utilisables partout de la même façon : CLI (`sovereign budget`), shell interactif (`sovereign shell`, puis
`/budget`), question libre (`sovereign ask "Que faire ?"` → `/director`), API (`POST /v1/commands/budget`,
`POST /v1/ask`), MCP (outil `budget`). Options : `--clé=valeur` ou `--drapeau`. Sortie JSON : `--json`.

| Commande | Rôle | Modules réellement appelés | Exemple |
|---|---|---|---|
| `/director [question]` | Consulter le directeur : boucle cognitive complète | Sélection de Skills → `assess` → modèle du monde → hypothèses → prévisions → priorisation → conseil → Monte-Carlo → exécution / validation humaine → mesure → apprentissage → (dreaming avec `--deep`) | `sovereign director "Faut-il recruter ?" --deep` · `--dry-run` (rien n'est exécuté) · `--llm` (arguments enrichis par Claude, budgétés) · `--top=5` |
| `/strategy` | Stratégie actuelle | Posture (redressement, consolidation, croissance, amorçage…) dérivée des finances ; objectifs ; stratégies apprises (`memory-learning.adapt_strategies`) ; convictions fortes du modèle du monde ; axes (BI, bizdev, marketing) | `sovereign strategy` |
| `/business [focus]` | Analyse commerciale | `business-intelligence`, `business-development`, `finance-budget`, `negotiation-strategy` (si comparaisons/négociations) | `sovereign business` |
| `/marketing` | Analyse marketing | `marketing-strategy.assess` + `design_strategy` | `sovereign marketing` |
| `/council <…>` | Réunion entre les IA | Proposition libre, `rec_…` ou `apr_…` → conseil CEO / Risque / CFO / CMO / COO / Mémoire + simulation ; id de **comparaison** → `negotiation-strategy.recommend` (TOPSIS, sensibilité, débat) ; id de **négociation** → `prepare_negotiation` (ZOPA, ancrage, concessions) | `sovereign council "Ouvrir un bureau à Lyon" --cost=8000 --value=30000 --confidence=0.5 --irreversible` · `sovereign council cmp-hebergeur` |
| `/opportunities` | Examiner les opportunités | `business-intelligence.detect_opportunities` (enregistre) → `business-development.rank_opportunities` (RICE) → `innovation-creativity.find_white_spaces` / `evaluate_feasibility` | `sovereign opportunities` |
| `/projects` | Analyser les projets | `analysis-optimization.analyze_projects` + `detect_unused_features`, `intelligent-management.assess` | `sovereign projects` |
| `/audit` | Audit général | Dreaming (croyances obsolètes/incompatibles, tâches mortes, validations en souffrance, Skills inutilisées, données manquantes, échéances), finances, `audit_system`, intégrité SQLite, journal d'audit | `sovereign audit` |
| `/budget` | Budget disponible | `finance-budget` : encaissé, enveloppe 10 %, engagé, dépensé, disponible, alertes, dépenses, prévision | `sovereign budget --months=12` |
| `/skills [nom]` | Les dix compétences | Registre + utilité mesurée ; détail : actions, schémas JSON, SKILL.md | `sovereign skills finance-budget` |
| `/memory [recherche]` | Décisions mémorisées | `memory-learning` (FTS5/BM25), revues dues, leçons | `sovereign memory "facebook"` |
| `/roadmap` | Objectifs à venir | Objectifs, jalons, revues de décisions, missions, validations en attente, délais | `sovereign roadmap --days=30` |
| `/status` | État réel du système | Base (taille, schéma, intégrité), données, compteurs, Skills chargées, file, cycles dus, LLM, budget, sécurité, OTel | `sovereign status` |
| `/improve` | Améliorations nécessaires | `analysis-optimization.recommend_improvements` + `audit_system` + dreaming | `sovereign improve` |
| `/report [type]` | Rapport de direction | Boucle du directeur (consultative) + finances + validations + leçons → Markdown en base et dans `~/.sovereign/reports/` | `sovereign report weekly` |

## Commandes opérationnelles (hors des 15)

```bash
sovereign init --demo                          # espace + démonstration
sovereign data import mes-donnees.yaml         # vos données (voir sovereign/workspace.py pour le format)
sovereign data import-csv collections releve.csv               # export bancaire / paiements, tel quel
sovereign data inbox                                           # importe et range tout ce qui est dans ~/.sovereign/inbox
sovereign data import-csv campaigns annonces.csv --channel meta_ads --map spend="Montant dépensé (EUR)"
sovereign finance revenue 1200 --from "Client A" --reference pay_42   # encaissement RÉEL
sovereign finance expense 80 outils --vendor "SaaS X"                 # approuvée / refusée / en revue
sovereign finance review exp_… --approve       # décision humaine
sovereign approvals list | approve apr_… | reject apr_… --reason "…"
sovereign outcome dec_… --actual 2950          # mesurer un résultat (apprentissage)
sovereign belief add "Le CAC reste < 80 €" --metric marketing.cac --comparator lt --threshold 80
sovereign event metric.anomaly --payload '{"z": 5, "metric": "essais"}'
sovereign cycle daily | weekly | monthly       # cycle immédiat (idempotent par période)
sovereign tick                                 # planificateur + worker (cron / GitHub Actions)
sovereign ecosystem scan ~/depots --include ~/autre-depot      # observer des dépôts git (lecture seule)
sovereign ecosystem assess evaluations.json --analyst "audit"  # joindre des évaluations d'analystes IA/humains
sovereign serve                                # API + MCP HTTP + planificateur + worker
sovereign mcp                                  # MCP stdio
sovereign apikey create admin --scope admin
sovereign skill run marketing-strategy plan_campaign --input '{"objective": "leads", "budget": "500", "channels": […]}'
```
