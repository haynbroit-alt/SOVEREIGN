---
name: innovation-creativity
description: >-
  Explore de nouveaux concepts par analyse morphologique, imagine des produits originaux avec SCAMPER, combine
  des technologies existantes jamais associées, identifie les possibilités inexploitées (espaces blancs
  segment × besoin) et évalue la faisabilité des idées (technique, économique, légal, opérationnel, calendrier).
  À utiliser pour innover, trouver de nouvelles idées de produits ou trier des idées avant d'investir.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.innovation_creativity"
  actions: "assess, explore_concepts, scamper, find_white_spaces, combine_technologies, evaluate_feasibility"
  keywords: "innovation, innover, créativité, créatif, idée, idées, nouveau produit, concept, concepts, scamper, espace blanc, inexploité, combiner, technologie, technologies, faisabilité, faisable, prototype, brainstorming, new product, idea, feasibility, white space"
  events: "idea.*, innovation.*, opportunity.*, cycle.monthly"
  data: "innovation_space, offering, idea, need, segment, asset, agent"
  risk: "write"
---

# Innovation et Créativité

## Description

Le laboratoire d'idées du directeur, entièrement déterministe et reproductible (le LLM n'est qu'un
enrichissement optionnel de SCAMPER). Méthodes :

- **Analyse morphologique** (`explore_concepts`) : produit cartésien des options de chaque dimension
  (technologie, marché, modèle, canal…). Au-delà du plafond (500 par défaut), un échantillonnage régulier
  déterministe couvre tout l'espace. Les paires incompatibles sont exclues. **Nouveauté** = 1 − similarité de
  Jaccard maximale avec les offres existantes ; **couverture** = part des besoins importants (importance ≥ 7)
  dont les termes apparaissent dans le concept. Score = 0,6 × nouveauté + 0,4 × couverture.
- **SCAMPER** (`scamper`) : sept opérateurs (Substituer, Combiner, Adapter, Modifier, Proposer un autre usage,
  Éliminer, Inverser) appliqués aux attributs du produit (composants, public, canal, modèle économique).
- **Espaces blancs** (`find_white_spaces`) : matrice segment × besoin ; une cellule couverte par une de nos
  offres n'est pas un espace blanc ; sinon elle est « personne » (poids × 1) ou « concurrents seulement »
  (poids × 0,6). Poids = importance/10 × taille relative du segment.
- **Combinaison de technologies** (`combine_technologies`) : paires (ou trios) de technologies disponibles
  jamais réunies dans une offre ; nouveauté = 1 − recouvrement/2 ; rattachement au besoin le plus proche.
- **Faisabilité TELOS** (`evaluate_feasibility`) : technique = 1 − complexité/10 ; économique = moyenne
  géométrique de l'accessibilité budgétaire (coût vs **budget disponible réel** de la Skill Finance), des
  preuves de marché et, si fourni, du retour potentiel (valeur/coût, 3× ⇒ 1) ; légal = 1 − risque
  réglementaire ; opérationnel = part des compétences requises présentes chez les agents enregistrés ;
  calendrier = 1 si ≤ 6 mois, sinon 6/mois. Score global = moyenne géométrique (une dimension nulle ne peut
  pas être compensée). Verdict : **go** ≥ 0,7, **explorer** ≥ 0,45, sinon **no-go**, avec facteurs bloquants.

## Quand l'utiliser

- « Quels nouveaux produits pourrions-nous créer ? » → `explore_concepts`, `scamper`
- « Où personne ne répond aux besoins ? » → `find_white_spaces`
- « Que peut-on faire avec nos technologies ? » → `combine_technologies`
- « Cette idée est-elle réaliste ? » → `evaluate_feasibility`
- `/opportunities` et le cycle mensuel appellent `assess`.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Panorama : espaces blancs, concepts, combinaisons, faisabilité des idées stockées | lecture |
| `explore_concepts` | Analyse morphologique, nouveauté × besoins | lecture |
| `scamper` | Variantes originales d'un produit (7 opérateurs) | lecture |
| `find_white_spaces` | Cellules segment × besoin inexploitées | lecture |
| `combine_technologies` | Combinaisons de technologies inédites | lecture |
| `evaluate_feasibility` | Score TELOS, verdict, facteurs bloquants (`persist` écrit dans l'idée) | écriture |

## Entrées et sorties

Entrées vides ⇒ espace de travail (kinds `innovation_space`, `offering`, `idea`, `need`, `segment`, `asset`,
`agent`). Schémas complets : `sovereign skill show innovation-creativity`.

```bash
sovereign skill run innovation-creativity explore_concepts --input '{"dimensions": {"techno": ["IA", "IoT"], "marché": ["PME", "santé"], "modèle": ["abonnement", "usage"]}, "incompatible": [["IoT", "usage"]]}'
sovereign skill run innovation-creativity scamper --input '{"product": "Logiciel de devis", "components": ["catalogue", "signature"], "audience": "artisans", "business_model": "abonnement"}'
sovereign skill run innovation-creativity evaluate_feasibility --input '{"ideas": [{"id": "i1", "title": "Assistant IA", "technical_complexity": 4, "required_skills": ["python"], "estimated_cost": "300", "time_to_market_months": 3, "regulatory_risk": 0.1, "market_evidence": 0.8}]}'
```

Sorties : `concepts[]` (nouveauté, couverture, score, offre la plus proche), `ideas[]` SCAMPER,
`white_spaces[]` (couverture, poids), `combinations[]`, `evaluations[]` (5 dimensions, verdict, compétences
manquantes, facteurs bloquants). Métriques : `innovation.white_spaces`, `innovation.coverage_ratio`,
`innovation.best_concept_score`, `innovation.ideas_go`, `innovation.feasibility_avg`.

## Déclencheurs

- Mots-clés : innovation, idée, concept, nouveau produit, SCAMPER, espace blanc, technologie, faisabilité…
- Événements : `idea.*`, `innovation.*`, `opportunity.*`, `cycle.monthly`.

## Sécurité

- Aucune action externe : la Skill propose, le directeur et le garde-fou financier décident.
- La faisabilité économique lit le budget disponible RÉEL (10 % des encaissements) : une idée plus chère que
  l'enveloppe est signalée comme bloquée, jamais financée implicitement.
- L'appel LLM de `scamper` est optionnel, soumis au budget, et n'altère jamais les idées déterministes.
- Le seul effet d'écriture (`persist`) se limite aux enregistrements `idea` de l'espace de travail.

## Tests

`tests/skills/test_innovation_creativity.py` : combinatoire et exclusions, échantillonnage au-delà du plafond,
nouveauté de Jaccard, SCAMPER (7 opérateurs, inversion du modèle), espaces blancs (nous / concurrents /
personne, besoins rattachés à un segment), combinaisons déjà existantes exclues, faisabilité TELOS calculée à
la main (budget réel, compétences manquantes, verdicts), persistance, espace vide.
