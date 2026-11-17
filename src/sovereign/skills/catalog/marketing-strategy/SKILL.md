---
name: marketing-strategy
description: >-
  Construit la stratégie marketing : identifie et classe les publics cibles (ICP), conçoit des campagnes dont le
  budget est borné par l'enveloppe autorisée, mesure leur efficacité (CTR, CVR, CPA, ROAS, ROI, tests A/B
  significatifs), analyse les tendances et propose positionnement, mix de canaux, messages et plan 30/60/90 jours.
  À utiliser pour toute question de marketing, de cible, de campagne, de canal ou de tendance.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.marketing_strategy"
  actions: "assess, segment_audiences, plan_campaign, measure_campaigns, analyze_trends, design_strategy"
  keywords: "marketing, stratégie marketing, campagne, campagnes, publicité, public cible, cible, cibles, audience, segment, segments, tendance, tendances, positionnement, acquisition, conversion, canal, canaux, ROAS, CTR, campaign, advertising, ads, trend, targeting, brand"
  events: "campaign.*, marketing.*, signal.market, cycle.weekly, cycle.monthly"
  data: "segment, campaign, trend_series, need"
  risk: "read_only"
---

# Marketing Stratégique

## Description

Le directeur marketing du système. Toutes les recommandations sont dérivées de chiffres explicites :

- **Publics cibles** : attractivité d'un segment =
  `0,25 × taille normalisée + 0,25 × douleur/10 + 0,25 × disposition à payer normalisée + 0,15 × accessibilité
  + 0,10 × (1 − concurrence)` (normalisation min-max sur l'ensemble des segments). Potentiel annuel =
  `taille × disposition à payer × accessibilité`. Le premier segment est l'**ICP** (profil client idéal).
- **Plan de campagne** : efficacité d'un canal = conversions par euro (`cvr / cpc`), valeur par euro
  (`× panier moyen`) pour l'objectif ventes, impressions par euro pour la notoriété. Modèle de réponse concave
  `r(b) = e × √(b × b₀)` (rendements décroissants) ; l'allocation optimale est `b_i ∝ e_i²`.
  **Le budget planifié est borné par le disponible de l'enveloppe de 10 %** (`finance-budget`) : la réduction
  est signalée, et un plan sans budget ne propose aucun lancement.
- **Mesure** : CTR, CVR, CPA, ROAS, ROI par campagne ; verdict `pause` si ROAS < 0,5 ou (ROAS < 1 et < 70 % de
  la médiane), `scale` si ROAS > 1 et ≥ 130 % de la médiane, sinon `keep`. Tests A/B (`variant_of`) par test z
  de deux proportions sur les taux de conversion des clics (significatif si p < 0,05).
- **Tendances** : régression linéaire sur chaque série ; pente relative à la moyenne (seuil ± 5 % par période),
  croissance totale, accélération (pente de la seconde moitié − pente de la première).
- **Stratégie** : ICP, positionnement construit à partir du besoin le plus mal servi du segment (kind `need`),
  mix de canaux pondéré par le ROAS **observé** (sinon hypothèse de départ explicitement signalée), messages,
  plan 30/60/90 jours et KPI.

## Quand l'utiliser

- « Qui devons-nous cibler ? » → `segment_audiences`
- « Prépare une campagne de 2 000 € » → `plan_campaign` (puis `finance-budget.request_expense`)
- « Nos campagnes sont-elles rentables ? » → `measure_campaigns`
- « Quelles tendances monter en priorité ? » → `analyze_trends`
- « Quelle stratégie marketing ? » → `design_strategy`
- Le directeur l'appelle via `assess` pour `/marketing` et les cycles hebdomadaire et mensuel.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Synthèse cibles + campagnes + tendances de l'espace de travail | lecture |
| `segment_audiences` | Classement multicritère des segments, ICP, potentiel | lecture |
| `plan_campaign` | Allocation par canal, prévisions, KPI — bornées par le budget autorisé | lecture |
| `measure_campaigns` | Indicateurs, verdicts pause/scale, tests A/B | lecture |
| `analyze_trends` | Pente, croissance, accélération, classement des tendances | lecture |
| `design_strategy` | Positionnement, mix de canaux, messages, plan 30/60/90, KPI | lecture |

## Entrées et sorties

Les listes vides sont remplacées par l'espace de travail (`segment`, `campaign`, `trend_series`, `need`).
Schémas JSON : `sovereign skill show marketing-strategy`.

```bash
sovereign skill run marketing-strategy segment_audiences
sovereign skill run marketing-strategy plan_campaign --input '{"objective": "sales", "budget": "1500", "channels": [{"name": "search", "cpc": "1.20", "cvr": 0.04, "average_order_value": "90"}, {"name": "social", "cpc": "0.60", "cvr": 0.015, "average_order_value": "90"}]}'
sovereign skill run marketing-strategy measure_campaigns
sovereign skill run marketing-strategy design_strategy --input '{"offer_name": "Offre Pro"}'
```

Sorties principales : `segments[].attractiveness`, `icp` ; `allocations[]`, `planned_budget`, `capped`,
`expected_roas`, `kpi_targets` ; `campaigns[].{ctr, cvr, cpa, roas, roi, verdict}`, `ab_tests[]` ;
`trends[].classification` ; `positioning`, `channel_mix`, `messages`, `plan`. Métriques publiées :
`marketing.roas`, `marketing.cac`, `marketing.ctr`, `marketing.icp_attractiveness`, `marketing.trends_rising`.

## Déclencheurs

- Mots-clés : marketing, campagne, publicité, cible, segment, tendance, positionnement, canal, ROAS…
- Événements : `campaign.*`, `marketing.*`, `signal.market`, `cycle.weekly`, `cycle.monthly`.
- Composition : un plan de campagne propose un suivi `finance-budget.check_spend` ; le classement des segments
  propose `design_strategy`.

## Sécurité

- Lecture seule : la Skill n'achète aucun espace publicitaire et n'engage aucune dépense. Les recommandations
  touchant l'extérieur (lancer, mettre en pause, augmenter un budget) sont marquées `external` et passent par la
  validation du directeur (revue humaine si risque élevé).
- Tout budget proposé est plafonné par le disponible réel ; sans encaissement, aucune campagne n'est proposée.
- Les données incohérentes sont refusées (taux hors [0, 1], coûts négatifs ou nuls, objectif ventes sans panier).

## Tests

`tests/skills/test_marketing_strategy.py` vérifie : pondérations de l'attractivité, allocation `∝ e²` et
plafonnement par le budget disponible, verdicts pause/scale, test A/B significatif ou non, classement des
tendances et accélération, stratégie fondée sur le ROAS observé ou sur des hypothèses signalées, espace vide,
validation des entrées.
