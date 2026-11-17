# Veille technologique — choix vérifiés (section 15)

Relevé effectué le **2026-11-17** directement auprès des registres officiels (PyPI JSON API, tags Git des
actions GitHub) et du code source des paquets installés. Méthode pour chaque choix : documentation / code source
officiel, dernière version stable, date de publication, problèmes connus, alternatives, compatibilité, coût.

| Brique | Choix | Version | Publiée le | Licence | Pourquoi | Alternatives écartées |
|---|---|---|---|---|---|---|
| Langage | Python | 3.12 – 3.13 | — | PSF | Écosystème IA/données, typage moderne | — |
| Gestion de projet | uv + hatchling | uv (CI), hatchling 1.33.0 | 2026-10-28 | MIT | Résolution verrouillée (`uv.lock`), rapide | pip-tools, Poetry (plus lents, verrou moins strict) |
| Modèles / validation | Pydantic | 2.14.3 | 2026-11-05 | MIT | Schémas JSON → MCP et OpenAPI gratuits | dataclasses (pas de schéma), attrs |
| Protocole IA | **MCP** (SDK officiel `mcp`) | 2.3.0 | 2026-10-02 | MIT | Standard ouvert, spec **2026-07-28** (sans état) | API propriétaires par fournisseur |
| API HTTP | FastAPI (Starlette 1.10) | 0.163.1 | 2026-11-15 | MIT | OpenAPI 3.1 automatique, même Starlette que MCP | Litestar 2.25 (excellent, mais doublon de pile avec MCP) |
| Client HTTP | **httpx2** | 2.16.2 | 2026-11-16 | BSD-3 | Successeur adopté par `mcp`, `anthropic` et Starlette | httpx 0.28.1 (**aucune version depuis 2024-12-06**, déprécié par Starlette) |
| CLI | Typer + Rich | 0.29.1 / 15.0.0 | 2026-11-11 / 2026-04-12 | MIT | Annotations de type, rendu Markdown | Click brut (plus verbeux) |
| Serveur ASGI | Uvicorn | 0.54.0 | 2026-09-25 | BSD-3 | Standard, utilisé par le SDK MCP | Hypercorn |
| Observabilité | **OpenTelemetry** API (+ SDK/OTLP en option) | 1.45.2 | 2026-10-29 | Apache-2.0 | Standard ouvert, neutre ; no-op sans SDK | Instrumentation propriétaire |
| LLM (optionnel) | SDK `anthropic` | 2.9.0 | 2026-11-16 | Apache-2.0 | SDK officiel ; sorties structurées, repli serveur | — |
| Mémoire | **SQLite** (stdlib) + FTS5 | 3.53.1 | — | domaine public | Zéro serveur, WAL, triggers d'intégrité, BM25 | PostgreSQL (voir ADR 0002) |
| Workflows durables | File durable SQLite maison | — | — | MIT | Voir ADR 0003 | Temporal 1.36.1, DBOS 3.6.0, Restate, Celery |
| Veille RSS (optionnel) | feedparser | 6.0.14 | 2026-07-30 | BSD-2 | Robuste aux flux mal formés ; repli stdlib | — |
| Qualité | Ruff, mypy, pytest | 0.17.5 / 2.4.1 / 9.2.0 | 2026-11 / 2026-10 / 2026-10 | MIT | Standards actuels | flake8 + black + isort (remplacés par Ruff) |
| CI | actions/checkout, astral-sh/setup-uv, actions/upload-artifact | v7.0.1 / v10.3.3 / v7.0.2 | — | MIT | Épinglées par **SHA de commit** (chaîne d'approvisionnement) | Tags flottants (`@v7`) |

## Constats qui ont changé le code

- **MCP 2.x** : `FastMCP` a été renommé `MCPServer` (`mcp.server.mcpserver`) ; l'ancien import lève une erreur
  explicite. Un code écrit « de mémoire » aurait cassé. La spécification 2026-07-28 supprime la session
  protocolaire : le serveur est monté en mode `stateless_http`.
- **httpx → httpx2** : `httpx` n'a plus de version depuis décembre 2024 ; Starlette avertit que son usage dans
  `TestClient` est déprécié. Tout le code HTTP de SOVEREIGN utilise `httpx2`.
- **`ToolAnnotations`** (paquet `mcp_types`) expose des champs `snake_case` (alias camelCase acceptés).
- **SDK `anthropic` 2.9** : `client.beta.messages.create` accepte `betas`, `fallbacks` et `output_config`
  (vérifié par introspection de la signature installée) ; le mode de pensée de Claude Opus 5.5 ne se désactive
  pas, l'effort est réglé explicitement (`medium`).
- **Actions GitHub** : versions majeures actuelles `checkout@v7`, `setup-uv@v10`, `upload-artifact@v7`.

## Non retenus délibérément

- **Recherche sémantique vectorielle** : à l'échelle d'une mémoire de décisions (centaines à milliers
  d'entrées), FTS5/BM25 insensible aux accents suffit et ne coûte rien. Critère de réévaluation : > 10 000
  décisions ou besoin de rapprochement sémantique mesuré comme insuffisant (requêtes sans résultat pertinent).
- **Temporal** : excellent pour des workflows distribués de longue durée, mais impose un cluster (ou Temporal
  Cloud payant) injustifié ici (ADR 0003).
- **Client MCP sortant** (SOVEREIGN pilotant d'autres serveurs MCP) : sans serveur cible configuré, aucune valeur
  démontrable aujourd'hui ; l'exécution passe par la file durable et les équipes d'agents. Critère d'ajout : un
  premier outil externe réel à piloter.
