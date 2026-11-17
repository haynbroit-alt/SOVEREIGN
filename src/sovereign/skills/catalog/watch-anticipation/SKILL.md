---
name: watch-anticipation
description: >-
  Veille et anticipation : surveille les évolutions technologiques (dernières versions PyPI / npm, retard,
  maintenance douteuse), détecte les tendances émergentes dans les signaux de veille et flux RSS/Atom, repère
  les changements de marché (ruptures CUSUM, anomalies robustes), évalue les risques et prépare l'agenda des
  prochaines décisions. À utiliser pour toute question de tendance, veille, risque, signal faible ou technologie.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.watch_anticipation"
  actions: "assess, ingest_signals, detect_trends, detect_market_changes, assess_risks, check_technologies, fetch_feed, prepare_decisions"
  keywords: "veille, tendance, tendances, signal, signaux, anticipation, anticiper, risque, risques, technologie, technologies, version, rupture, anomalie, changement de marché, flux rss, prochaines décisions, watch, trend, risk, technology, forecast"
  events: "signal.*, risk.*, metric.*, cycle.daily, cycle.weekly"
  data: "signal, risk, tech_dependency, metric_series"
  risk: "write"
---

# Veille et Anticipation

## Description

Radar du directeur. Il transforme des signaux épars en alertes chiffrées et en décisions à préparer.

Méthodes :

- **Tendances** : extraction de termes (mots ≥ 4 caractères sans mots vides FR/EN, insensibles aux accents, plus
  les tags en `#tag`), comptage par signal. Fenêtre récente (défaut 30 jours) contre fenêtre de référence (90
  jours précédents). Attendu `e = max(b × w/B, 0,5)`, score `z = (r − e)/√e` (approximation de Poisson), ratio
  lissé `(r+1)/(b × w/B + 1)`. Émergent si `z ≥ 2` et `r ≥ 3` ; en déclin si `z ≤ −2`.
- **Changements de marché** : séries de métriques (enregistrements `metric_series` et observations du modèle du
  monde) → rupture par CUSUM tabulaire bilatéral (k = 0,5, h = 4), anomalie du dernier point si |z robuste|
  (médiane/MAD) ≥ 3,5, variation moyenne du dernier quart contre le quart précédent.
- **Risques** : `score = probabilité × impact × (1 + (vitesse − 5)/10) × tendance` (hausse ×1,2, baisse ×0,8) ;
  critique ≥ 6, élevé ≥ 4, moyen ≥ 2 ; mitigation par défaut selon la catégorie si absente.
- **Technologies** : interrogation de `https://pypi.org/pypi/<nom>/json` (version + date de publication) ou
  `https://registry.npmjs.org/<nom>` (`dist-tags.latest` + `time`), comparaison de versions tolérante
  (majeur/mineur/correctif), « maintenance douteuse » si dernière publication > 540 jours. Toute erreur réseau
  donne le statut `unknown`, jamais une exception. Mode `offline` : utilise les versions déjà mémorisées.
- **Agenda** : risques critiques et élevés, tendances émergentes, dépendances critiques en retard, décisions dont
  la date de revue est atteinte (mémoire), avec priorité et échéance suggérée.

## Quand l'utiliser

- « Quelles tendances émergent ? » → `detect_trends` (après `ingest_signals` ou `fetch_feed`).
- « Nos dépendances sont-elles à jour / maintenues ? » → `check_technologies`.
- « Le marché a-t-il changé ? » → `detect_market_changes`.
- « Quels sont nos risques ? » → `assess_risks`.
- « Que devons-nous décider bientôt ? » → `prepare_decisions`.
- Le directeur l'appelle via `assess` (sans réseau) à chaque cycle.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Synthèse hors ligne : tendances, risques, dépendances, séries | lecture |
| `ingest_signals` | Enregistre des signaux, dédoublonnés par URL ou titre | écriture |
| `detect_trends` | Termes émergents et en déclin (z de Poisson) | lecture |
| `detect_market_changes` | Ruptures CUSUM, anomalies robustes, variations récentes | lecture |
| `assess_risks` | Score, carte des risques, mitigations | lecture |
| `check_technologies` | Dernières versions PyPI/npm, retard, maintenance (mémorise les versions) | écriture |
| `fetch_feed` | Lit un flux RSS/Atom et enregistre les entrées comme signaux | écriture |
| `prepare_decisions` | Agenda priorisé des décisions à préparer | lecture |

## Entrées et sorties

```bash
sovereign skill run watch-anticipation ingest_signals --input '{"signals": [{"title": "Agents IA autonomes en PME", "source": "blog", "published_at": "2026-11-10T08:00:00Z", "tags": ["ia"]}]}'
sovereign skill run watch-anticipation detect_trends --input '{"window_days": 30}'
sovereign skill run watch-anticipation check_technologies --input '{"dependencies": [{"name": "fastapi", "ecosystem": "pypi", "current_version": "0.110.0", "critical": true}]}'
sovereign skill run watch-anticipation fetch_feed --input '{"url": "https://example.org/feed.xml", "tags": ["marché"]}'
```

Sorties : `emerging` / `declining` (terme, occurrences récentes et de référence, ratio, z), `series` (rupture,
z robuste, anomalie, variation), `risks` + `heatmap`, `dependencies` (dernière version, retard, jours depuis la
publication, statut), `agenda`, plus `summary`, `insights`, `recommendations`, `metrics`
(`watch.emerging_terms`, `risk.top_score`, `tech.outdated`…).

## Déclencheurs

- Mots-clés : veille, tendance, signal, risque, technologie, version, rupture, anomalie…
- Événements : `signal.*`, `risk.*`, `metric.*`, `cycle.daily`, `cycle.weekly`.

## Sécurité

- Seules `check_technologies` (hors mode `offline`) et `fetch_feed` accèdent au réseau, en lecture seule, avec un
  délai de 10 s et un agent HTTP générique ; aucune donnée de l'utilisateur n'est envoyée.
- Les flux sont traités comme des données non fiables : taille plafonnée (5 Mo), balises HTML retirées, texte
  tronqué, analyse XML par expat (protégé contre l'expansion d'entités).
- `assess` ne fait jamais d'appel réseau : les cycles automatiques restent hors ligne et sans coût.

## Tests

`tests/skills/test_watch_anticipation.py` : dédoublonnage des signaux, tendances émergentes et en déclin,
rupture CUSUM et anomalie, score et carte des risques, comparaison de versions et maintenance douteuse avec
registres simulés (PyPI et npm), erreurs réseau, flux RSS et Atom (avec et sans feedparser), agenda des
décisions, espace vide. Aucun appel réseau réel.
