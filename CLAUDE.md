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

## Sobriété — chaque session, sans exception
- Lire ciblé : Grep/Glob d'abord, puis Read avec `offset`/`limit`. Ne jamais relire un fichier déjà lu et inchangé.
- Sorties filtrées : jamais un log entier. Tests `-q` + `| tail -n 15`, `grep -E "error|FAIL"`, `head`. Les sorties
  Bash sont plafonnées par `.claude/settings.json` ; relancer filtré plutôt que tout réafficher.
- Code minimal : le plus petit diff qui résout le problème ; pas d'abstraction, d'option ni de fichier « au cas où ».
- Modèles : sous-agent haiku pour chercher, sonnet pour implémenter ou relire, opus pour décider, fable seulement
  quand la difficulté l'exige. `/clear` entre deux tâches sans lien, `/compact` en disant quoi garder, `/context`
  pour voir ce qui pèse.
- Carte (au lieu d'ouvrir les fichiers un par un) : `src/sovereign/core/` (base, registre financier, mémoire, LLM),
  `brain/` (boucle cognitive, conseil, simulation), `skills/` + `skills/catalog/` (10 Skills et SKILL.md),
  `automation/` (file, cycles, webhooks), `commands/` (15 commandes), `interfaces/` (CLI, API, MCP),
  `ecosystem.py` (observation de dépôts), `importers.py` (CSV), `workspace.py` (YAML) ; tests miroirs dans `tests/`.
