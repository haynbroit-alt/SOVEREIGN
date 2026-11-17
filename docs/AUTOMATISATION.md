# Automatisations

Principe : **les automatisations sont déclenchées par des événements pertinents ou des échéances, pas par des
appels systématiques aux IA.** Toutes les analyses automatiques sont déterministes (millisecondes, coût nul).

## Cycles proactifs

| Cycle | Dû quand | Ce qu'il fait | Skills (uniquement si des données existent) |
|---|---|---|---|
| QUOTIDIEN | chaque jour à partir de 7 h (UTC par défaut) | analyse de situation, priorités du jour | finance, analyse/optimisation, management, veille |
| HEBDOMADAIRE | chaque semaine ISO (lundi) | assignation des missions aux agents, évaluation des résultats, révision des stratégies, **dreaming** | mémoire, analyse, marketing, veille |
| MENSUEL | chaque mois (le 1er) | renouvellement des dépenses récurrentes **via le garde-fou des 10 %**, performance commerciale, opportunités | finance, BI, marketing, bizdev, innovation |
| CONTINU | à chaque événement | règles ci-dessous | selon la règle |

- **Idempotents par période** (`cycle_runs`), **durables** (étapes journalisées : une reprise ne ré-exécute pas une
  étape terminée), **rattrapés** si la machine était éteinte.
- Chaque cycle produit un rapport (base + fichier Markdown) et une notification.

## Règles événementielles (mode continu)

| Règle | Événement | Condition | Action |
|---|---|---|---|
| budget-alert | `budget.threshold_crossed` | seuil 80 % ou 100 % franchi | notification + diagnostic financier |
| expense-review | `expense.review` | dépense > 50 % du disponible | notification de validation humaine |
| approval-requested | `approval.requested` | — | notification |
| revenue-recorded | `finance.collection_recorded` | montant > 0 | diagnostic financier (**1 par heure max**) |
| outcome-learning | `decision.outcome_recorded` | — | adaptation des stratégies (1 par heure max) |
| task-dead | `task.dead` | tâche en échec définitif | alerte critique |
| signals-ingested | `watch.signals_ingested` | ≥ 5 signaux | détection de tendances |
| kpi-anomaly | `metric.anomaly` | \|z\| ≥ 3,5 | boucle cognitive approfondie |
| external-github-release | `github.release` | — | veille technologique |

Chaque déclenchement crée une **tâche durable idempotente** (un même événement rejoué ne déclenche rien de plus).

## Webhooks entrants (`POST /v1/webhooks/{source}`)

| Source | Authentification | Effet |
|---|---|---|
| `stripe` | `Stripe-Signature` (HMAC-SHA256, horodatage ±300 s) | `payment_intent.succeeded` / `invoice.paid` → encaissement RÉEL (idempotent sur l'id Stripe) ; `charge.refunded` → remboursement |
| `github` | `X-Hub-Signature-256` | release publiée → veille ; workflow échoué → `github.ci_failed` |
| autre (`generic`…) | `X-Sovereign-Timestamp` + `X-Sovereign-Signature: sha256=HMAC(secret, "ts.corps")` | `{"type", "payload", "id"}` → événement (dédoublonné sur `id`) |

Secrets : `SOVEREIGN_WEBHOOK_SECRET_<SOURCE>` (ex. `SOVEREIGN_WEBHOOK_SECRET_STRIPE=whsec_…`). Sans secret, la
source est refusée (404) ; signature invalide → 401, jamais transmise au bus.

## Notifications sortantes

`SOVEREIGN_NOTIFY_WEBHOOK_URL` (+ `SOVEREIGN_NOTIFY_WEBHOOK_SECRET` pour signer) : JSON compatible Slack (`text`)
avec données structurées ; utilisable avec n8n, Zapier, Make, un relais Slack/Teams ou un serveur maison.

## Déploiements possibles

1. **Serveur** : `sovereign serve` (API + MCP + planificateur toutes les 60 s + worker).
2. **cron** : `sovereign tick` (enfile les cycles dus et traite la file) toutes les heures.
3. **GitHub Actions** (`.github/workflows/sovereign-cycles.yml`) : exécution quotidienne, déclenchement manuel
   d'une commande, événements externes via `repository_dispatch` :
   ```bash
   curl -X POST -H "Authorization: Bearer <jeton GitHub>" \
     https://api.github.com/repos/<propriétaire>/<dépôt>/dispatches \
     -d '{"event_type": "sovereign-event", "client_payload": {"type": "metric.anomaly", "payload": {"z": 5}}}'
   ```
   L'état est conservé sur la branche `sovereign-state` ; les rapports sont publiés en artefacts. Les workflows
   planifiés ne s'exécutent que depuis la branche par défaut.

   **Vérifié en conditions réelles** (2026-11-17, exécution n°1 sur GitHub) : cycles quotidien, hebdomadaire et
   mensuel exécutés, 3 rapports publiés en artefact, état poussé sur `sovereign-state`, durée totale 14 s.

## Observabilité

OpenTelemetry : spans `director.run`, `skill.<nom>.<action>`, `command.<nom>`, `task.<type>`. Export OTLP/HTTP
avec l'extra `otel` et `SOVEREIGN_OTEL=1` (variables standard `OTEL_EXPORTER_OTLP_ENDPOINT`…), ou console avec
`SOVEREIGN_OTEL=console`. Sans export, l'API OpenTelemetry est un no-op.
