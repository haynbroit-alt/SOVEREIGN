# SOVEREIGN

**Autonomous Business Intelligence Director** — un directeur général artificiel spécialisé en business,
marketing, stratégie, management et coordination d'intelligences artificielles.

> *Anticiper. Décider. Diriger. Faire grandir.*

SOVEREIGN observe votre activité, confronte les faits à ses croyances, formule des hypothèses, anticipe,
priorise, fait délibérer un conseil d'IA (CEO, Risque, CFO, CMO, COO, Mémoire), simule chaque décision avant de
l'exécuter, confie les actions sûres à des équipes d'agents, soumet les actions risquées à votre validation,
mesure les résultats et apprend — en boucle.

**Règles non négociables** : aucun accès bancaire ; dépenses limitées à **10 % des revenus réellement encaissés** ;
validation humaine pour tout risque élevé ; l'IA générative est optionnelle, budgétée et jamais décisionnaire.

## Démarrage en 1 minute

```bash
uv sync                                   # Python 3.12+ (uv : https://docs.astral.sh/uv/)
uv run sovereign init --demo              # crée ~/.sovereign et charge une entreprise FICTIVE de démonstration
uv run sovereign director "Que dois-je décider cette semaine ?" --deep
uv run sovereign shell                    # puis tapez /budget, /strategy, /council …, ou une question
```

## Les 10 compétences (Skills)

| # | Skill | Méthodes | Actions |
|---|---|---|---|
| 1 | `business-intelligence` | TAM/SAM/SOM, HHI (seuils DOJ), score d'opportunité d'Ulwick, potentiel par scénarios | `size_market`, `analyze_competition`, `find_unmet_needs`, `detect_opportunities`, `estimate_potential` |
| 2 | `marketing-strategy` | Attractivité des segments, allocation à rendements décroissants (bornée au budget), CTR/CVR/CPA/ROAS, A/B (test z), tendances | `segment_audiences`, `plan_campaign`, `measure_campaigns`, `analyze_trends`, `design_strategy` |
| 3 | `business-development` | Économie unitaire (LTV, LTV/CAC, payback, point mort), RICE, matrice d'Ansoff, plans de lancement à jalons | `evaluate_business_model`, `compare_revenue_streams`, `rank_opportunities`, `generate_activities`, `launch_plan` |
| 4 | `intelligent-management` | Affectation sous contrainte de capacité, performance (a posteriori bêta), 5 types de conflits, tri topologique | `register_agent`, `assign_missions`, `measure_performance`, `detect_conflicts`, `coordination_report` |
| 5 | `watch-anticipation` | Termes émergents (z de Poisson), CUSUM, z robuste, carte des risques, versions PyPI/npm, RSS/Atom | `ingest_signals`, `detect_trends`, `detect_market_changes`, `assess_risks`, `check_technologies`, `fetch_feed`, `prepare_decisions` |
| 6 | `negotiation-strategy` | Somme pondérée + TOPSIS + sensibilité, ZOPA/BATNA, concessions, débat entre IA | `compare_proposals`, `prepare_negotiation`, `pros_cons`, `debate`, `recommend` |
| 7 | `finance-budget` | Registre append-only, enveloppe 10 %, revue humaine, prévision linéaire + **scénarios Monte-Carlo** (bootstrap des croissances réelles : probabilité d'épuisement, P10/P50/P90), alertes de seuil | `status`, `record_revenue`, `issue_invoice`, `collect_invoice`, `request_expense`, `confirm_expense`, `check_spend`, `forecast` |
| 8 | `analysis-optimization` | Santé des KPI, fonctionnalités inutiles (Pareto), ICE, test de Welch + d de Cohen, audit de SOVEREIGN lui-même | `analyze_projects`, `detect_unused_features`, `recommend_improvements`, `verify_change`, `audit_system` |
| 9 | `innovation-creativity` | Analyse morphologique, SCAMPER, espaces blancs, combinaisons de technologies, faisabilité (moyenne géométrique) | `explore_concepts`, `scamper`, `find_white_spaces`, `combine_technologies`, `evaluate_feasibility` |
| 10 | `memory-learning` | FTS5/BM25, score attendu vs réel, erreurs répétées, adaptation bayésienne | `record_decision`, `record_outcome`, `search`, `compare_results`, `detect_repeated_errors`, `lessons`, `adapt_strategies` |

Chaque Skill a aussi une action `assess` (diagnostic sur vos données), un `SKILL.md` au format ouvert
**Agent Skills** (description, usage, actions, entrées/sorties, déclencheurs, sécurité, tests), des schémas
JSON publiés, et ses tests. Le directeur **ne charge que les Skills pertinentes** pour chaque mission.

## Les 15 commandes

`/director` `/strategy` `/business` `/marketing` `/council` `/opportunities` `/projects` `/audit` `/budget`
`/skills` `/memory` `/roadmap` `/status` `/improve` `/report` — détail et exemples : [docs/COMMANDES.md](docs/COMMANDES.md).

## Interfaces

| Interface | Usage |
|---|---|
| CLI | `sovereign <commande>`, `sovereign shell`, `sovereign ask "…"`, `--json` |
| API REST | `sovereign serve` → `http://127.0.0.1:8765/docs` (OpenAPI), clé : `sovereign apikey create admin --scope admin` |
| MCP | `sovereign mcp` (stdio) — déjà déclaré dans `.mcp.json` pour Claude Code ; HTTP Streamable sur `/mcp` avec `serve` |
| Webhooks | Stripe (encaissements réels), GitHub, générique signé HMAC |
| GitHub Actions | `.github/workflows/sovereign-cycles.yml` : cycles quotidiens, commandes manuelles, `repository_dispatch` |

## Vos données

**Exports CSV, tels quels** (relevé bancaire, export de paiements, rapport de campagnes publicitaires…) :

```bash
sovereign data import-csv collections releve.csv          # seul l'argent RÉELLEMENT reçu est importé
sovereign data import-csv campaigns rapport-annonces.csv --channel google_ads
sovereign data import-csv segment segments.csv            # n'importe quel type de données (market, need…)
```

Séparateur, encodage, nombres (« 1 234,56 € », « 1,234.56 ») et dates (« 05/11/2026 ») sont détectés ;
les en-têtes FR/EN courants sont reconnus (« Montant », « Crédit », « Statut », « Coût », « Impr. »,
« Conv. value »…) et `--map champ=colonne` couvre le reste. Statuts non payés, devises étrangères et dates
futures sont refusés ; réimporter un fichier ne double rien. Aussi disponible en API
(`POST /v1/workspace/import-csv/{kind}`) et en MCP (outil `import_csv`).

**Sans ordinateur (GitHub)** : déposez ces fichiers dans `inbox/` sur la branche `sovereign-state` ; le cycle
suivant les importe (`sovereign data inbox` en local). Détails : [docs/AUTOMATISATION.md](docs/AUTOMATISATION.md).

**Fichier complet** : `sovereign data import mon-entreprise.yaml` — même format que la démo
(`src/sovereign/data/demo_workspace.yaml`) : marchés, concurrents, besoins, segments, campagnes, modèles
économiques, agents et missions, signaux, risques, dépendances techniques, projets et KPI, idées, objectifs,
encaissements, factures, dépenses (soumises au garde-fou), croyances, décisions passées.

## Configuration

Variables `SOVEREIGN_*` ou `~/.sovereign/sovereign.toml` : `SOVEREIGN_HOME`, `SOVEREIGN_CURRENCY`,
`SOVEREIGN_SPEND_RATIO` (≤ 0,10, refusé au-delà), `SOVEREIGN_LLM_ENABLED=1` (+ identifiants Anthropic standard),
`SOVEREIGN_LLM_MODEL`, `SOVEREIGN_NOTIFY_WEBHOOK_URL`, `SOVEREIGN_WEBHOOK_SECRET_<SOURCE>`, `SOVEREIGN_OTEL`.

## Documentation

- [Architecture et boucle cognitive](docs/ARCHITECTURE.md)
- [Commandes](docs/COMMANDES.md) · [Automatisations](docs/AUTOMATISATION.md)
- [Veille technologique et versions vérifiées](docs/VEILLE_TECHNOLOGIQUE.md) · [Décisions d'architecture](docs/adr/)
- [Utilité réelle mesurée](docs/UTILITE.md)

## Qualité

```bash
uv run pytest -o addopts="" -q --cov=sovereign    # 268 tests (Python 3.12 à 3.14), couverture ≈ 92 %
uv run ruff check src tests && uv run mypy
```

## Limites connues

- La démonstration prouve l'exactitude des calculs, pas la valeur business : celle-ci se mesure sur vos données
  (taux d'adoption des recommandations, score des décisions mesurées — voir [UTILITE.md](docs/UTILITE.md)).
- Pondérations et seuils sont explicites et documentés ; ils sont à recalibrer avec vos résultats.
- Un seul processus écrivain (SQLite) ; au-delà, migration PostgreSQL / DBOS documentée (ADR 0002, 0003).
- L'analyse de texte (veille, correspondances) repose sur une racinisation simple FR/EN.
