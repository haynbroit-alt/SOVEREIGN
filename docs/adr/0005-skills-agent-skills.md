# ADR 0005 — Skills au format ouvert Agent Skills, chargement paresseux, sélection par pertinence

- **Statut** : accepté — 2026-11-17

## Décision

- Chaque Skill = un dossier `src/sovereign/skills/catalog/<nom>/SKILL.md` au format **Agent Skills**
  (front-matter `name` = nom du dossier, `description` ≤ 1 024 caractères, `license`, `metadata` dont toutes
  les valeurs sont des chaînes) + une implémentation Python `src/sovereign/skills/<nom_snake>.py`.
- La **découverte** ne lit que les front-matters ; le code n'est importé qu'à la première utilisation
  (divulgation progressive, comme les Agent Skills). `/skills` montre quelles Skills sont réellement chargées.
- La **sélection** pour une mission combine mots-clés (racinisation FR/EN), événements déclencheurs, données
  disponibles et **utilité apprise** (a posteriori bêta des résultats mesurés). Seules les Skills pertinentes
  (3 à 5) sont exécutées.
- Le **registre vérifie la conformité** code ↔ SKILL.md au chargement (nom, version, liste exacte des actions,
  action `assess` obligatoire) ; `tests/test_skill_contracts.py` vérifie les 7 sections documentaires
  obligatoires et que chaque action est documentée.
- Interopérabilité : `sovereign skill export .claude/skills` copie les SKILL.md pour des agents comme Claude Code.

## Contrat commun

Toute sortie hérite de `SkillOutput` (`summary`, `insights`, `recommendations`, `metrics`) : le cerveau central
récolte uniformément constats, recommandations chiffrées (impact, effort, confiance, urgence, risque, valeur,
coût, réversibilité, caractère externe, suivi exécutable) et métriques (alimentent le modèle du monde).
