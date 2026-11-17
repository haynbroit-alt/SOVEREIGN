---
name: negotiation-strategy
description: >-
  Compare des propositions concurrentes (somme pondérée, TOPSIS, sensibilité des poids), prépare les négociations
  commerciales (BATNA, ZOPA, ancrage, plan de concessions, règle de rupture, échanges), évalue avantages et
  inconvénients, fait confronter les arguments des agents IA du conseil (CEO, Risque, CFO, CMO, COO) et recommande
  la meilleure décision. À utiliser pour choisir entre options, préparer un accord ou trancher un désaccord.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.negotiation_strategy"
  actions: "assess, compare_proposals, prepare_negotiation, pros_cons, debate, recommend"
  keywords: "négociation, négocier, proposition, propositions, offre, offres, comparer, comparaison, choix, choisir, arbitrage, avantages, inconvénients, débat, débattre, conseil, accord, contrat, fournisseur, devis, recommandation, negotiation, compare, decision, trade-off"
  events: "proposal.*, negotiation.*, council.*"
  data: "comparison, negotiation"
  risk: "read_only"
---

# Négociation et Stratégie

## Description

Outil d'arbitrage du directeur, entièrement déterministe et explicable.

- **Comparaison multicritère** : normalisation min-max par critère (sens « bénéfice » ou « coût »), somme
  pondérée (poids renormalisés), puis **TOPSIS** (normalisation vectorielle, distances euclidiennes aux
  solutions idéale et anti-idéale, proximité relative `D− / (D+ + D−)`). Score combiné = moyenne des deux.
  **Sensibilité** : chaque poids est perturbé de ±20 % ; la stabilité est la part des perturbations où le
  gagnant reste premier ; les critères qui renversent le classement sont signalés.
- **Préparation de négociation** : ZOPA (acheteur : notre réservation ≥ réservation adverse estimée ; vendeur :
  l'inverse), ancrage au-delà de la cible (25 % de l'écart cible–réservation, borné à ±15 % de la réservation
  adverse), concessions décroissantes 50 % / 25 % / 15 % / 10 % de la distance ancrage → réservation, règle de
  rupture adossée à la BATNA, échanges (enjeux peu coûteux pour nous et très valorisés par l'autre partie :
  à concéder ; l'inverse : à protéger). Incohérences BATNA / réservation signalées.
- **Avantages / inconvénients** : scores −5 à +5 pondérés par dimension, score net, verdict favorable / mitigé
  / défavorable.
- **Débat des agents IA** : chaque agent argumente depuis sa perspective et à partir de faits chiffrés
  (valeur attendue, coût, probabilité de succès, réversibilité, cohérence stratégique, preuves de marché,
  budget disponible, délai, capacité). Une donnée absente donne un argument faible et neutre : le débat
  n'invente rien. Tour d'ouverture, puis réfutation (chaque agent répond à l'argument opposé le plus fort et
  concède partiellement s'il est dominé), vote pondéré par la pertinence et la force des arguments. **Veto
  budgétaire** : un coût supérieur au disponible (règle des 10 %) entraîne le rejet. Un LLM optionnel peut
  reformuler les arguments ; il ne décide jamais du verdict.

## Quand l'utiliser

- « Quel fournisseur / quelle offre choisir ? » → `compare_proposals`, puis `recommend`.
- « Comment aborder la négociation avec X ? » → `prepare_negotiation`.
- « Pour ou contre ? » → `pros_cons` (dimensions qualitatives) ou `debate` (faits chiffrés).
- `/council` utilise `debate` pour organiser la réunion des IA.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Analyse les comparaisons et négociations de l'espace de travail | lecture |
| `compare_proposals` | Somme pondérée + TOPSIS + sensibilité des poids | lecture |
| `prepare_negotiation` | ZOPA, ancrage, concessions, rupture, échanges | lecture |
| `pros_cons` | Avantages / inconvénients pondérés et verdict | lecture |
| `debate` | Débat contradictoire CEO / Risque / CFO / CMO / COO | lecture |
| `recommend` | Comparaison puis débat sur le gagnant → recommandation finale | lecture |

## Entrées et sorties

```bash
sovereign skill run negotiation-strategy compare_proposals --input '{"title": "CRM", "criteria": [{"name": "prix", "weight": 2, "direction": "cost"}, {"name": "qualite", "weight": 3}], "proposals": [{"name": "A", "values": {"prix": 100, "qualite": 7}}, {"name": "B", "values": {"prix": 80, "qualite": 6}}]}'
sovereign skill run negotiation-strategy prepare_negotiation --input '{"we_are": "buyer", "our_batna": 1200, "our_reservation": 1000, "our_target": 800, "their_estimated_reservation": 850}'
sovereign skill run negotiation-strategy debate --input '{"proposal": "Lancer l offre Pro", "facts": {"expected_value": 20000, "cost": 500, "probability_of_success": 0.6, "reversibility": 0.8}}'
```

Sorties : `ranking`, `winner`, `stability`, `sensitive_criteria` ; `zopa_*`, `anchor`, `concession_plan`,
`walk_away`, `trades` ; `transcript` (tours, positions, arguments), `final_positions`, `verdict`,
`confidence`, `conditions`, `vetoes`, `data_completeness`, `llm_used` ; plus le contrat commun.

## Déclencheurs

- Mots-clés : négociation, proposition, offre, comparer, choix, arbitrage, débat, conseil, contrat, devis…
- Événements : `proposal.*`, `negotiation.*`, `council.*`.

## Sécurité

- Aucune action externe : la Skill prépare et recommande, l'humain négocie et signe.
- Le verdict du débat est calculé, jamais délégué à un LLM ; le LLM, s'il est activé, est soumis au budget.
- Le veto budgétaire s'appuie sur le disponible réel du garde-fou financier.

## Tests

`tests/skills/test_negotiation_strategy.py` : calculs de somme pondérée et TOPSIS vérifiés à la main,
sensibilité, critères manquants, ZOPA acheteur/vendeur, ancrage et plan de concessions, échanges, avantages /
inconvénients, débat (adoption, rejet par veto budgétaire, données manquantes), recommandation combinée,
reformulation LLM simulée, espace vide.
