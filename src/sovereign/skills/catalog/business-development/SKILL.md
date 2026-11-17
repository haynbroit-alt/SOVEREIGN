---
name: business-development
description: >-
  Développe l'activité : imagine de nouvelles activités à partir des actifs existants (matrice d'Ansoff), évalue
  les modèles économiques (MRR, LTV, CAC, délai de récupération, point mort), compare les sources de revenus,
  classe les opportunités (RICE pondéré par la cohérence stratégique), prépare des plans de lancement par
  phases avec jalons go/no-go et budget borné par l'enveloppe autorisée, et arbitre un portefeuille de projets
  (développer, valider le marché, maintenir, fusionner, geler, archiver). À utiliser pour toute question de modèle
  économique, de nouvelle activité, de revenus, de lancement ou de choix entre projets.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.business_development"
  actions: "assess, evaluate_business_model, compare_revenue_streams, rank_opportunities, generate_activities, launch_plan, portfolio_review"
  keywords: "modèle économique, business model, nouvelle activité, nouvelles activités, développement commercial, business development, lancement, lancer, plan de lancement, go-to-market, sources de revenus, source de revenus, revenus récurrents, rentabilité unitaire, unit economics, LTV, CAC, point mort, diversification, Ansoff, RICE, priorisation des opportunités, launch, portefeuille, portfolio, projets à développer, quels projets, geler, archiver, fusionner, dépôts, écosystème, focus"
  events: "opportunity.*, business_model.*, ecosystem.*, cycle.monthly"
  data: "business_model, opportunity, asset, market, segment, need, project"
  risk: "write"
---

# Business Development

## Description

Le développeur d'activité du directeur. Méthodes explicites et chiffrées :

- **Modèle économique** (par flux de revenus) : revenu mensuel `prix × volume`, contribution
  `revenu × marge brute` ; LTV d'un abonnement `prix × marge / churn mensuel` (36 mois si churn inconnu), d'un
  achat unique `prix × marge`, d'un flux d'usage ou de commission `prix × marge × 12`. Au niveau du modèle :
  LTV pondérée par le revenu, `LTV/CAC`, délai de récupération `CAC / contribution mensuelle unitaire` (flux
  récurrents), résultat mensuel `contribution − coûts fixes`, point mort `coûts fixes / contribution par unité`.
  Verdict : **viable** (LTV/CAC ≥ 3, récupération ≤ 12 mois, résultat ≥ 0), **non viable** (LTV/CAC < 1, ou
  perte avec point mort > 3 × le volume actuel), sinon **fragile**.
- **Sources de revenus** : score `0,5 × contribution normalisée + 0,3 × prévisibilité + 0,2 × marge brute`, avec
  une prévisibilité de 1,0 (abonnement), 0,7 (usage), 0,6 (commission), 0,4 (ponctuel). Alerte de dépendance si
  une source porte ≥ 70 % de la contribution.
- **Opportunités** : `RICE = portée × impact × confiance / effort`. L'impact suit l'échelle RICE (3 ; 2 ; 1 ;
  0,5 ; 0,25) selon le potentiel relatif ; la portée vaut 100 si elle n'est pas renseignée (signalé). Score final
  `RICE × (1 + 0,5 × cohérence)`, la cohérence mesurant le recouvrement entre l'opportunité et nos actifs
  (kind `asset`). Les opportunités `rejected` sont exclues.
- **Nouvelles activités** : matrice d'Ansoff à partir des actifs : pénétration (produit actuel × marché actuel,
  risque 1), développement produit (compétence/technologie × marché actuel, 2), développement marché (produit ×
  nouveau marché, 3), diversification (4). Les nouveaux marchés viennent des segments des besoins non couverts.
- **Plan de lancement** : phases validation (10 % du budget), MVP (40 %), lancement (30 %), croissance (20 %),
  calendrier en semaines, objectifs et critères go/no-go chiffrés. **Les phases sont financées dans l'ordre par
  le disponible réel (enveloppe de 10 %)** ; le reste est explicitement « à financer par les encaissements
  futurs ».
- **Revue de portefeuille** (`portfolio_review`, projets du kind `project`, par ex. issus de
  `sovereign ecosystem scan` + `assess`). Règles explicites, dans l'ordre :
  1. **Test bon marché d'abord** : potentiel ≥ 3, test ≤ 3 semaines et du code existant → **valider le marché**
     (vendre l'existant, aucun développement), même pour un doublon : la fusion se décide après le test.
  2. **Doublons** : dans chaque recouvrement fort (paquet partagé ou similarité ≥ 0,5 mesurés, ou recouvrement
     constaté par un analyste), seul le projet le mieux classé (potentiel, maturité, santé, taille) reste
     candidat ; les autres sont **à fusionner** s'ils portent un actif propre (≥ 200 lignes), sinon **gelés**.
  3. Potentiel inconnu → **évaluer** ; idée sans code et potentiel ≤ 3 → **archiver**. **Focus** :
     `rendement de l'effort = potentiel × (0,5 + confiance/2) / (1 + effort/8)` ; parmi les projets de
     potentiel ≥ 4, les `focus_limit` meilleurs (2 par défaut) sont **à développer**, les suivants **maintenus** ;
     potentiel ≤ 1,5 → **archiver** ; sinon **geler**.
  4. Secrets exposés ou tests en échec → **bloquants** signalés quel que soit le verdict.
  5. Le verdict des analystes (IA ou humains, champ `assessment.verdict`) est confronté au sien : chaque
     **désaccord** est listé pour arbitrage humain. Il ne remplace jamais le calcul.

## Quand l'utiliser

- « Notre modèle est-il rentable ? » → `evaluate_business_model`
- « Quelles sources de revenus développer ? » → `compare_revenue_streams`
- « Quelle opportunité poursuivre en premier ? » → `rank_opportunities`
- « Quelles nouvelles activités lancer ? » → `generate_activities`
- « Prépare le lancement de… » → `launch_plan`
- « Sur quels projets me concentrer ? », « Que geler ? » → `portfolio_review` (`apply: true` enregistre le statut
  dans chaque projet ; `analysis-optimization` cesse alors de proposer des améliorations pour les projets gelés,
  archivés ou à fusionner)
- Le directeur l'appelle via `assess` pour `/business`, `/opportunities` et le cycle mensuel.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Synthèse modèles économiques + opportunités + actifs | lecture |
| `evaluate_business_model` | Économie unitaire, point mort, verdict | lecture |
| `compare_revenue_streams` | Classement des flux de revenus, dépendance | lecture |
| `rank_opportunities` | Classement RICE × cohérence stratégique | lecture |
| `generate_activities` | Pistes d'activités par quadrant d'Ansoff | lecture |
| `launch_plan` | Phases, calendrier, go/no-go, financement borné par le disponible | lecture |
| `portfolio_review` | Verdict par projet, focus, effort évité, désaccords avec les analystes | écriture si `apply` |

## Entrées et sorties

Listes vides ⇒ espace de travail (`business_model`, `opportunity`, `asset`, `market`, `segment`, `need`,
`project`).
Schémas JSON : `sovereign skill show business-development`.

```bash
sovereign skill run business-development evaluate_business_model --input '{"models": [{"name": "SaaS", "revenue_streams": [{"name": "Pro", "type": "subscription", "price": "50", "volume_per_month": 100, "gross_margin": 0.8, "churn_monthly": 0.05}], "fixed_costs_monthly": "2000", "cac": "300"}]}'
sovereign skill run business-development rank_opportunities
sovereign skill run business-development launch_plan --input '{"name": "Offre Pro", "potential_annual_revenue": "60000", "target_customers": 120}'
sovereign ecosystem scan ~/depots && sovereign ecosystem assess evaluations.json
sovereign skill run business-development portfolio_review --input '{"focus_limit": 2, "apply": true}'
```

Sorties principales : `models[].{ltv, ltv_cac, payback_months, break_even_volume, verdict, weaknesses}`,
`streams[].score`, `ranking[].{rice, strategic_fit, score}`, `activities[].{quadrant, risk_level}`,
`phases[].{budget, funded, funding_status, go_no_go}` et `funding_gap`,
`decisions[].{verdict, reasons, next_action, return_on_effort, blocking, merge_into, ai_verdict, agrees_with_ai}`,
`focus`, `freed_weeks`, `divergences`. Métriques : `bizdev.mrr`,
`bizdev.<modèle>.ltv_cac`, `bizdev.<modèle>.payback_months`, `bizdev.top_opportunity_score`,
`bizdev.launch_funding_gap`, `portfolio.active_projects`, `portfolio.focus`, `portfolio.freed_weeks`,
`portfolio.divergences`.

## Déclencheurs

- Mots-clés : modèle économique, nouvelle activité, lancement, sources de revenus, LTV, CAC, point mort…
- Événements : `opportunity.*`, `business_model.*`, `ecosystem.*` (scan ou évaluation de dépôts), `cycle.monthly`.
- Composition : `business-intelligence.detect_opportunities` → `rank_opportunities` → `launch_plan` →
  `finance-budget.check_spend` (suivis proposés par les recommandations).

## Sécurité

- Aucune dépense, aucun contact externe. Seule écriture : `portfolio_review` avec `apply: true` enregistre le
  statut décidé dans les projets de l'espace de travail (jamais dans les dépôts). Le démarrage d'une phase ou un
  test de marché est une recommandation `external` soumise à la validation du directeur.
- Le jugement des analystes IA est une donnée d'entrée parmi d'autres : un désaccord est signalé, jamais tranché
  en faveur de l'IA.
- Aucun plan n'est financé au-delà du disponible réel : sans encaissement ni budget explicite, `launch_plan`
  refuse (`SkillError`) plutôt que d'inventer un budget.
- Entrées validées : prix > 0, marges et churn dans [0, 1], au moins un flux de revenus par modèle.

## Tests

`tests/skills/test_business_development.py` vérifie sur des valeurs calculées à la main : LTV, LTV/CAC,
récupération, point mort et les trois verdicts ; scores des flux de revenus ; RICE, impact, portée estimée,
cohérence avec les actifs et exclusion des opportunités rejetées ; quadrants d'Ansoff ; financement séquentiel
des phases et écart de financement ; espace vide ; validation des entrées ; revue de portefeuille (doublons
gelés ou fusionnés, limite de focus, validation de marché, archivage, blocage par secrets, désaccords avec les
analystes, `apply` et effet sur `analysis-optimization`).
