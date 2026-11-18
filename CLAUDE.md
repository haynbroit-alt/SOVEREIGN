# SOVEREIGN — notes pour les agents de code

- Python 3.12+, gestion par `uv`. Vérifications obligatoires avant commit (journal complet + code exact) :
  `.claude/bin/journal verif bash -o pipefail -c 'uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest -o addopts="" -q'`
- Langue : code en anglais (identifiants), docstrings, messages, SKILL.md et docs en français.
- Argent : `Decimal` / centimes entiers, jamais de float. Toute dépense passe par `FinanceLedger.request_expense`.
- Une nouvelle Skill = `src/sovereign/skills/<snake>.py` (`SKILL = Classe`, action `assess` obligatoire, sorties
  héritant de `SkillOutput`) + `src/sovereign/skills/catalog/<kebab>/SKILL.md` (7 sections, metadata en chaînes) +
  `tests/skills/test_<snake>.py`. `tests/test_skill_contracts.py` vérifie la conformité.
- Ne jamais exposer en MCP une action d'approbation humaine (validations, revue de dépenses).
- Le SDK `mcp` est en 2.x (`mcp.server.mcpserver.MCPServer`), le client HTTP est `httpx2` (pas `httpx`).

## Fiabilité avant sobriété — la preuve prime sur le plafond de sortie
- Le plafond (15 000 caractères) ne doit jamais coûter une preuve. Une sortie d'échec trop longue est coupée en
  tête et fin, sans fichier ni code garanti : toute vérification (tests, lint, types, build) passe donc par
  `.claude/bin/journal <étiquette> <commande>` — journal complet sur disque, résumé < 8 000 caractères jamais
  tronqué, code de sortie exact en première et dernière ligne, même code renvoyé.
- Jamais `vérification | tail/head/grep` sans `pipefail` : le pipe renvoie le code du filtre (0) et masque l'échec.
  Le crochet `.claude/hooks/garde_pipe.py` refuse ces commandes.
- Sortie incomplète (aperçu tronqué, ligne de bilan absente : « N passed », « All checks passed! », « OK ») :
  lire le journal complet (Grep, Read avec `offset`) avant toute conclusion.
- « Réussi » exige : code 0 ET la ligne de bilan de l'outil vue. Sinon écrire « non vérifié » et pourquoi.
- Fusion d'une PR seulement si l'API GitHub donne `conclusion: success` pour TOUS les contrôles de la tête
  actuelle (SHA vérifié, aucun `in_progress` ni manquant) ET si les vérifications locales ont rendu code 0.
  Jamais sur la foi d'un journal partiel, d'un statut agrégé ou d'une exécution sur un autre SHA sans le dire.

## Sobriété — chaque session, sans exception
- Lire ciblé : Grep/Glob d'abord, puis Read avec `offset`/`limit`. Ne jamais relire un fichier déjà lu et inchangé.
- Sorties filtrées : jamais un log entier ; une vérification passe par `.claude/bin/journal` (voir « Fiabilité »).
  Les sorties Bash sont plafonnées par `.claude/settings.json`.
- Code minimal : le plus petit diff qui résout le problème ; pas d'abstraction, d'option ni de fichier « au cas où ».
- Modèles : sous-agent haiku pour chercher, sonnet pour implémenter ou relire, opus pour décider, fable seulement
  quand la difficulté l'exige. `/clear` entre deux tâches sans lien, `/compact` en disant quoi garder, `/context`
  pour voir ce qui pèse.
- Carte (au lieu d'ouvrir les fichiers un par un) : `src/sovereign/core/` (base, registre financier, mémoire, LLM),
  `brain/` (boucle cognitive, conseil, simulation), `skills/` + `skills/catalog/` (10 Skills et SKILL.md),
  `automation/` (file, cycles, webhooks), `commands/` (15 commandes), `interfaces/` (CLI, API, MCP),
  `ecosystem.py` (observation de dépôts), `importers.py` (CSV), `workspace.py` (YAML) ; tests miroirs dans `tests/`.
- Écosystème : avant de travailler dans un autre dépôt qui n'a pas de section « Sobriété », y ajouter ce kit dans la
  même PR — les sections « Fiabilité » et « Sobriété » (avec la carte de ce dépôt), `.claude/bin/journal`,
  `.claude/hooks/garde_pipe.py` et un `.claude/settings.json` (`bashOutputMaxChars`, crochet, lecture interdite
  de ses dossiers lourds). L'environnement cloud « Default » plafonne déjà les sorties Bash.
