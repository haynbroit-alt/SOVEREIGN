---
name: business-intelligence
description: >-
  Étudie les marchés (TAM, SAM, SOM et projection de croissance), évalue la concurrence (indice HHI,
  positionnement prix/qualité, fonctionnalités rares ou manquantes), identifie les besoins non satisfaits
  (score d'opportunité d'Ulwick), détecte et chiffre les opportunités commerciales et estime le potentiel
  économique d'une offre. À utiliser pour toute analyse de marché, de concurrence ou d'opportunité.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.business_intelligence"
  actions: "assess, size_market, analyze_competition, find_unmet_needs, detect_opportunities, estimate_potential"
  keywords: "marché, marchés, taille de marché, part de marché, opportunité, opportunités, concurrence, concurrent, concurrents, besoin, besoins, besoins non satisfaits, potentiel, potentiel économique, analyse commerciale, TAM, SOM, market sizing, competitor, competition, competitive landscape, opportunity, unmet needs"
  events: "market.*, competitor.*, need.*, signal.competitor, cycle.monthly"
  data: "market, competitor, need, opportunity"
  risk: "write"
---

# Business Intelligence

## Description

Le service d'études du directeur. Il transforme des données de marché structurées en chiffres décisionnels,
sans hypothèse cachée :

- **Dimensionnement** : `TAM = clients totaux × valeur annuelle par client` ; `SAM = TAM × part adressable` ;
  `SOM = SAM × part atteignable`. Projection du SOM sur l'horizon : `SOM × (1 + g)^t`.
- **Concurrence** : indice de Herfindahl-Hirschman `HHI = Σ (part × 100)²` avec les seuils du DOJ américain
  (< 1 500 peu concentré, 1 500–2 500 modérément concentré, > 2 500 très concentré) ; part non couverte
  `1 − Σ parts` ; positionnement par rapport aux médianes de prix et de qualité (premium, rapport qualité/prix,
  low-cost, surévalué) ; fonctionnalités **rares** (≤ 1/3 des concurrents, leviers de différenciation) et
  **standard** (≥ 2/3, attendues par le marché — signalées si absentes de notre offre) ; intensité
  concurrentielle `0,5 × (1 − HHI/10 000) + 0,3 × min(1, n/10) + 0,2 × (1 − part non couverte)`.
- **Besoins non satisfaits** : score d'opportunité d'Ulwick `importance + max(importance − satisfaction, 0)` ;
  ≥ 12 « fortement sous-servi », ≥ 10 (avec importance > satisfaction) « sous-servi », satisfaction >
  importance « sur-servi ».
- **Opportunités** : chaque besoin sous-servi est croisé avec les marchés (de son segment s'il est renseigné) :
  `potentiel = SOM × facteur d'écart × (1 − 0,5 × intensité)` avec `facteur d'écart = (score − 10)/10` borné à
  [0,1 ; 1]. La confiance dépend de la qualité des données disponibles (marché apparié, concurrence connue,
  fréquence du besoin). Les opportunités sont enregistrées (kind `opportunity`, identifiant stable : un
  nouveau passage ne réinitialise pas un statut déjà décidé).
- **Potentiel d'une offre** : revenu annuel (rétention mensuelle `Σ (1 − churn)^m` sur 12 mois), LTV
  (`prix × marge / churn`), scénarios pessimiste / central / optimiste (conversion × 0,5 / × 1 / × 1,5).

## Quand l'utiliser

- « Quelle est la taille de notre marché ? » → `size_market`
- « Qui sont nos concurrents, où est l'espace ? » → `analyze_competition`
- « Quels besoins clients sont mal servis ? » → `find_unmet_needs`
- « Quelles opportunités commerciales poursuivre ? » → `detect_opportunities`, puis
  `business-development.rank_opportunities`
- « Combien peut rapporter cette offre ? » → `estimate_potential`
- Le directeur l'appelle via `assess` pour `/business`, `/opportunities` et le cycle mensuel.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Synthèse marché + concurrence + besoins de l'espace de travail | lecture |
| `size_market` | TAM, SAM, SOM, clients atteignables, projection | lecture |
| `analyze_competition` | HHI, concentration, quadrants prix/qualité, fonctionnalités rares/standard/manquantes | lecture |
| `find_unmet_needs` | Scores d'Ulwick et classement des besoins | lecture |
| `detect_opportunities` | Opportunités chiffrées, enregistrées dans l'espace de travail | écriture |
| `estimate_potential` | Revenu annuel, LTV et scénarios d'une offre | lecture |

## Entrées et sorties

Toute liste vide en entrée est remplacée par les enregistrements de l'espace de travail (`market`,
`competitor`, `need`). Schémas JSON complets : `sovereign skill show business-intelligence`.

```bash
sovereign skill run business-intelligence size_market --input '{"markets": [{"id": "pme-fr", "name": "PME France", "total_customers": 40000, "serviceable_ratio": 0.25, "obtainable_share": 0.05, "annual_value_per_customer": "1200", "growth_rate": 0.12}]}'
sovereign skill run business-intelligence analyze_competition --input '{"our_features": ["facturation", "api"]}'
sovereign skill run business-intelligence detect_opportunities --input '{"min_opportunity": 11}'
sovereign skill run business-intelligence estimate_potential --input '{"name": "Offre Pro", "price": "49", "target_customers": 2000, "conversion_rate": 0.03, "churn_monthly": 0.04, "gross_margin": 0.8}'
```

Sorties : `size_market.markets[].{tam, sam, som, obtainable_customers, som_projection}`,
`analyze_competition.{hhi, concentration, intensity, positions, rare_features, table_stakes, missing_features}`,
`find_unmet_needs.needs[].{opportunity, classification}`, `detect_opportunities.opportunities[]`,
`estimate_potential.{annual_revenue, ltv, scenarios}` — plus `summary`, `insights`, `recommendations`,
`metrics` (`market.tam`, `market.som`, `competition.hhi`, `needs.underserved`, `opportunities.potential`…).

## Déclencheurs

- Mots-clés : marché, part de marché, concurrence, opportunité, besoins non satisfaits, potentiel, TAM…
- Événements : `market.*`, `competitor.*`, `need.*`, `signal.competitor` (veille), `cycle.monthly`.
- Composition : les recommandations d'opportunité proposent un suivi `business-development.rank_opportunities`.

## Sécurité

- Lecture seule, sauf `detect_opportunities` qui écrit uniquement des enregistrements `opportunity` dans
  l'espace de travail (aucune action externe, aucune dépense).
- Les données incohérentes sont refusées (parts de marché > 100 %, ratios hors [0, 1], notes hors [1, 10]).
- Les chiffres reposent exclusivement sur les données fournies : la confiance affichée baisse quand des données
  manquent (marché non apparié, concurrence inconnue).

## Tests

`tests/skills/test_business_intelligence.py` vérifie sur des valeurs calculées à la main : TAM/SAM/SOM et
projection, HHI et seuils de concentration, quadrants et fonctionnalités, scores d'Ulwick, persistance et
stabilité des opportunités, potentiel et LTV, repli sur l'espace de travail, espace vide, validation des entrées.
