# SOVEREIGN — notes pour les agents de code

- Python 3.12+, gestion par `uv`. Vérifications obligatoires avant commit :
  `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest -o addopts="" -q`
- Langue : code en anglais (identifiants), docstrings, messages, SKILL.md et docs en français.
- Argent : `Decimal` / centimes entiers, jamais de float. Toute dépense passe par `FinanceLedger.request_expense`.
- Une nouvelle Skill = `src/sovereign/skills/<snake>.py` (`SKILL = Classe`, action `assess` obligatoire, sorties
  héritant de `SkillOutput`) + `src/sovereign/skills/catalog/<kebab>/SKILL.md` (7 sections, metadata en chaînes) +
  `tests/skills/test_<snake>.py`. `tests/test_skill_contracts.py` vérifie la conformité.
- Ne jamais exposer en MCP une action d'approbation humaine (validations, revue de dépenses).
- Le SDK `mcp` est en 2.x (`mcp.server.mcpserver.MCPServer`), le client HTTP est `httpx2` (pas `httpx`).
