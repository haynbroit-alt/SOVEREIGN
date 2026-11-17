# ADR 0001 — Pile technique : Python, Pydantic, FastAPI, MCP 2.x, httpx2

- **Statut** : accepté — 2026-11-17
- **Contexte** : SOVEREIGN doit être utilisable par un humain (CLI), par des services (API REST, webhooks) et par
  des IA (MCP), avec des entrées/sorties structurées et documentées.

## Décision

1. **Python 3.12+** et **Pydantic 2** : chaque action de Skill a un modèle d'entrée et de sortie ; ces modèles
   produisent gratuitement les schémas JSON publiés par MCP (outils) et par OpenAPI (API).
2. **Un seul routeur de commandes** partagé par la CLI, le shell interactif, l'API et MCP : une commande se
   comporte identiquement partout et son usage est mesuré au même endroit.
3. **FastAPI** pour l'API (OpenAPI 3.1 automatique) ; le serveur MCP HTTP Streamable est **monté dans la même
   application** (`/mcp`), derrière la même authentification par clé.
4. **MCP officiel 2.x** (`MCPServer`) : outils (15 commandes + Skills + écritures), ressources, prompts ;
   transports stdio et HTTP.
5. **httpx2** pour tout appel HTTP sortant.

## Conséquences

- Une seule pile ASGI (Starlette) pour l'API et MCP.
- Les validations humaines ne sont **jamais** exposées en MCP : un agent IA ne peut pas approuver ses propres
  propositions.
- Alternatives : Litestar (aussi moderne, mais une seconde pile à côté de Starlette), gRPC (inutile ici).
