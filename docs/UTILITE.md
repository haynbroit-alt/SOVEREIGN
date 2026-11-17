# Utilité réelle — mesure et honnêteté (section 18)

> « Si une fonctionnalité n'apporte aucune valeur, ne l'ajoute pas artificiellement. »

## Comment SOVEREIGN mesure son utilité (en continu, pas une fois)

| Signal | Où il est mesuré | Ce qu'il prouve |
|---|---|---|
| Exécutions, durée, échecs par Skill | table `skill_runs` (chaque appel) | Usage réel et fiabilité |
| Recommandations proposées → adoptées → exécutées | table `recommendation_log` | Les analyses débouchent-elles sur des décisions ? |
| Résultat des décisions (score attendu vs réel) | table `decisions` (+ mesure automatique à l'échéance) | Les décisions issues d'une Skill réussissent-elles ? |
| Usage des commandes | table `command_runs` | Quelles commandes servent vraiment |
| Synthèse | `/skills` (utilité par Skill), `/audit` et `/improve` (Skills jamais utilisées, peu fiables, jamais adoptées → **candidates au retrait**) | Décision d'élaguer fondée sur des données |

La Skill `analysis-optimization` s'applique **à SOVEREIGN lui-même** (`audit_system`) : la même méthode qui
repère une fonctionnalité inutile dans vos produits repère une Skill ou une commande inutile dans le directeur.
Les Skills dont les décisions réussissent gagnent un bonus de sélection (a posteriori bêta) ; celles qui
échouent en perdent : l'utilité mesurée oriente directement le comportement.

## Boucle d'apprentissage fermée

Les recommandations portent, quand c'est possible, une **métrique cible** (`TargetMetric`) : risques critiques,
conflits d'équipes, santé des projets, coût des fonctionnalités inutiles, ROAS et CAC, dépendances obsolètes,
tendance des encaissements, créances, dépenses en revue, revues de décisions dues. À l'adoption (par le directeur
ou par un humain), l'attendu chiffré est enregistré avec sa base observée et une date de revue ; à l'échéance,
la phase MESURER compare la dernière observation à la cible, clôture la décision (succès / partiel / échec),
et l'utilité de la Skill émettrice est mise à jour — **sans saisie humaine**. Démontré par
`tests/test_brain.py::test_learning_loop_closes_automatically`.

## Mesure sur l'espace de démonstration (entreprise fictive, 2026-11-17)

Scénario : import de la démo, une boucle du directeur en mode approfondi, les trois cycles, puis les 15
commandes (dont `/council` sur une comparaison et sur une négociation). Aucun appel IA.

| Skill | Exécutions | Constats | Recommandations (distinctes) | Adoptées/exécutées | Latence moy. | Fiabilité |
|---|---|---|---|---|---|---|
| `analysis-optimization` | 8 | 31 | 15 | 0 | 0.6 ms | 100% |
| `business-development` | 6 | 10 | 1 | 0 | 0.4 ms | 100% |
| `business-intelligence` | 5 | 35 | 8 | 0 | 0.4 ms | 100% |
| `finance-budget` | 8 | 21 | 3 | 2 | 0.3 ms | 100% |
| `innovation-creativity` | 5 | 34 | 6 | 0 | 4.4 ms | 100% |
| `intelligent-management` | 4 | 9 | 6 | 2 | 0.4 ms | 100% |
| `marketing-strategy` | 8 | 24 | 5 | 1 | 0.5 ms | 100% |
| `memory-learning` | 4 | 4 | 6 | 1 | 0.4 ms | 100% |
| `negotiation-strategy` | 3 | 4 | 3 | 0 | 0.3 ms | 100% |
| `watch-anticipation` | 4 | 32 | 6 | 1 | 1.1 ms | 100% |

Performances mesurées : boucle complète du directeur (13 phases, 5 Skills) ≈ 100 ms ; cycle quotidien ≈ 35 ms ;
commandes 0–20 ms. Coût : 0 € (aucun appel IA par défaut).

### Ce que la démonstration prouve

Sur des données fictives mais cohérentes, chaque Skill produit des résultats **vérifiables par calcul**
(couverts par `tests/test_e2e_demo.py`) :
- HHI = 1 016 (marché peu concentré) ; besoins sous-servis identifiés (score d'Ulwick) ;
- CAC = 3 150 € / 64 conversions = 49,2 € ; variante B d'une campagne significativement meilleure (test z) ;
- conflits d'équipes détectés (ressource partagée, recommandations contradictoires sur Facebook Ads) ;
- risque réglementaire critique (facturation électronique) et rupture CUSUM sur les essais hebdomadaires ;
- fonctionnalités inutiles (« export comptable », « messagerie interne ») sans pénaliser une nouveauté de 7 jours ;
- amélioration de l'onboarding prouvée par test de Welch (p < 0,05) ;
- « place de marché » jugée infaisable (coût > budget, complexité, preuves faibles) ;
- erreur répétée détectée (deux échecs Facebook Ads) et prise en compte par le membre MÉMOIRE du conseil ;
- croyances contredites par les faits (encaissements en baisse, ROAS < 4) → confiance révisée, hypothèses.

### Ce que la démonstration ne prouve PAS (et comment le prouver)

- **La valeur business réelle** : elle ne se mesure que sur vos données et dans la durée. Le taux d'adoption
  des recommandations et le score des décisions mesurées (tables ci-dessus) en sont les indicateurs ; ils
  deviennent significatifs après quelques semaines de cycles et une dizaine de décisions mesurées.
- **Les seuils et pondérations** (scores de priorité, attractivité des segments, vetos du conseil) sont des
  choix explicites, documentés et testés, pas des vérités : ils doivent être recalibrés avec vos résultats. La
  boucle APPRENDRE/RÉVISER le fait partiellement (croyances apprises, a priori de sélection).
- **L'adoption à 0** pour plusieurs Skills dans la démo signifie seulement qu'aucun humain n'a encore validé
  leurs propositions (5 validations sont en attente) — pas qu'elles sont inutiles.

## Corrections apportées grâce à la mesure

| Constat mesuré | Correction |
|---|---|
| `negotiation-strategy` jamais appelée en fonctionnement normal | Reliée à `/council <comparaison|négociation>` et à `/business` |
| Un coût connu sans valeur chiffrée simulé comme « 100 % de pertes » | La simulation raisonne alors en points d'impact (test de non-régression) |
| Impact 10/10 pour +20 % de budget sur une petite campagne | Impact pondéré par le poids de la campagne ; ROAS marginal à 70 % du moyen |
| Règle « fonctionnalité inutile » trop stricte avec peu de fonctionnalités | Quintile calculé hors nouveautés, ou < 1 % des usages |
| 5 encaissements → 5 diagnostics identiques | Coalescence des règles : une analyse par heure |
| Relancer `/director` gonflait la confiance des croyances | Un même fait n'est compté qu'une fois par 24 h |

## Fonctionnalités volontairement non ajoutées

Recherche vectorielle, client MCP sortant, Temporal : utilité non démontrable aujourd'hui ; critères de
réévaluation dans `docs/VEILLE_TECHNOLOGIQUE.md`.
