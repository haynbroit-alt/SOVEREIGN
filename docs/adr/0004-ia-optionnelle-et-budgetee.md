# ADR 0004 — Raisonnement LLM optionnel, budgété, jamais décisionnaire

- **Statut** : accepté — 2026-11-17

## Décision

- Le cœur de SOVEREIGN est **déterministe** : chaque analyse repose sur des méthodes explicites et testées
  (TAM/SAM/SOM, HHI, score d'opportunité d'Ulwick, RICE, ICE, TOPSIS, ZOPA, test de Welch, test z de deux
  proportions, CUSUM, a posteriori bêta, Monte-Carlo…). Il fonctionne sans aucune IA externe.
- Claude (SDK officiel `anthropic`, modèle par défaut `claude-opus-5-5`, effort `medium`) est une **option**
  (`SOVEREIGN_LLM_ENABLED=1`) utilisée pour : enrichir les arguments du conseil, SCAMPER, débats de négociation.
- **Chaque appel est une dépense** soumise à la règle des 10 % : coût maximal estimé autorisé AVANT l'appel,
  coût réel (tokens) déclaré APRÈS ; plafond par appel (`max_cost_per_call`, 0,50 par défaut).
- Sorties structurées (JSON Schema strict) validées par Pydantic ; refus (`stop_reason == "refusal"`), erreur
  réseau ou sortie invalide ⇒ repli déterministe et annulation de la réservation budgétaire.
- Les arguments générés par l'IA sont tracés « [IA] » avec un poids faible : **le verdict, les vetos et les
  scores restent calculés de façon déterministe et auditable**.
- Les cycles automatiques n'appellent jamais l'IA, sauf `llm.allow_in_cycles = true`.

## Conséquences

- Coût nul par défaut ; reproductibilité des décisions ; tests sans réseau.
- `tests/test_llm_gateway.py` couvre : désactivation par défaut, appel budgété et coût réel ≤ autorisé, refus du
  budget, plafond par appel, refus du modèle, erreur réseau, sortie invalide.
