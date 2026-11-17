---
name: business-development
description: >-
  Développe l'activité : imagine de nouvelles activités à partir des actifs existants (matrice d'Ansoff), évalue
  les modèles économiques (MRR, LTV, CAC, délai de récupération, point mort), compare les sources de revenus,
  classe les opportunités (RICE pondéré par la cohérence stratégique) et prépare des plans de lancement par
  phases avec jalons go/no-go et budget borné par l'enveloppe autorisée. À utiliser pour toute question de modèle
  économique, de nouvelle activité, de revenus ou de lancement.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.business_development"
  actions: "assess, evaluate_business_model, compare_revenue_streams, rank_opportunities, generate_activities, launch_plan"
  keywords: "modèle économique, business model, nouvelle activité, nouvelles activités, développement commercial, business development, lancement, lancer, plan de lancement, go-to-market, sources de revenus, source de revenus, revenus récurrents, rentabilité unitaire, unit economics, LTV, CAC, point mort, diversification, Ansoff, RICE, priorisation des opportunités, launch"
  events: "opportunity.*, business_model.*, cycle.monthly"
  data: "business_model, opportunity, asset, market, segment, need"
  risk: "read_only"
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

## Quand l'utiliser

- « Notre modèle est-il rentable ? » → `evaluate_business_model`
- « Quelles sources de revenus développer ? » → `compare_revenue_streams`
- « Quelle opportunité poursuivre en premier ? » → `rank_opportunities`
- « Quelles nouvelles activités lancer ? » → `generate_activities`
- « Prépare le lancement de… » → `launch_plan`
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

## Entrées et sorties

Listes vides ⇒ espace de travail (`business_model`, `opportunity`, `asset`, `market`, `segment`, `need`).
Schémas JSON : `sovereign skill show business-development`.

```bash
sovereign skill run business-development evaluate_business_model --input '{"models": [{"name": "SaaS", "revenue_streams": [{"name": "Pro", "type": "subscription", "price": "50", "volume_per_month": 100, "gross_margin": 0.8, "churn_monthly": 0.05}], "fixed_costs_monthly": "2000", "cac": "300"}]}'
sovereign skill run business-development rank_opportunities
sovereign skill run business-development launch_plan --input '{"name": "Offre Pro", "potential_annual_revenue": "60000", "target_customers": 120}'
```

Sorties principales : `models[].{ltv, ltv_cac, payback_months, break_even_volume, verdict, weaknesses}`,
`streams[].score`, `ranking[].{rice, strategic_fit, score}`, `activities[].{quadrant, risk_level}`,
`phases[].{budget, funded, funding_status, go_no_go}` et `funding_gap`. Métriques : `bizdev.mrr`,
`bizdev.<modèle>.ltv_cac`, `bizdev.<modèle>.payback_months`, `bizdev.top_opportunity_score`,
`bizdev.launch_funding_gap`.

## Déclencheurs

- Mots-clés : modèle économique, nouvelle activité, lancement, sources de revenus, LTV, CAC, point mort…
- Événements : `opportunity.*`, `business_model.*`, `cycle.monthly`.
- Composition : `business-intelligence.detect_opportunities` → `rank_opportunities` → `launch_plan` →
  `finance-budget.check_spend` (suivis proposés par les recommandations).

## Sécurité

- Lecture seule : aucune écriture, aucune dépense, aucun contact externe. Le démarrage d'une phase est une
  recommandation `external` soumise à la validation du directeur.
- Aucun plan n'est financé au-delà du disponible réel : sans encaissement ni budget explicite, `launch_plan`
  refuse (`SkillError`) plutôt que d'inventer un budget.
- Entrées validées : prix > 0, marges et churn dans [0, 1], au moins un flux de revenus par modèle.

## Tests

`tests/skills/test_business_development.py` vérifie sur des valeurs calculées à la main : LTV, LTV/CAC,
récupération, point mort et les trois verdicts ; scores des flux de revenus ; RICE, impact, portée estimée,
cohérence avec les actifs et exclusion des opportunités rejetées ; quadrants d'Ansoff ; financement séquentiel
des phases et écart de financement ; espace vide ; validation des entrées.
