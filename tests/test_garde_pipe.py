"""Garde-fous de fiabilité des sessions Claude : code de sortie jamais masqué, journal complet conservé."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("garde_pipe", RACINE / ".claude" / "hooks" / "garde_pipe.py")
assert _spec and _spec.loader
garde = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(garde)


def test_verification_dans_un_pipe_refusee():
    for commande in (
        "uv run pytest -q | tail -n 15",
        "python -m unittest discover -s tests 2>&1 | tail -n 5 && node relais/test-signature.mjs",
        "uv run ruff check src | grep error",
    ):
        assert garde.verdict(commande), commande


def test_formes_sures_autorisees():
    for commande in (
        "bash -o pipefail -c 'uv run pytest -q | tail -n 15'",
        ".claude/bin/journal verif uv run pytest -q",
        "git log --oneline | head -5",
        "uv run pytest -q || echo echec",
        "grep -rn test src | head",
    ):
        assert garde.verdict(commande) is None, commande


def test_sortie_du_crochet(tmp_path):
    entree = json.dumps({"tool_name": "Bash", "tool_input": {"command": "uv run pytest | tail"}})
    sortie = subprocess.run(
        ["python3", str(RACINE / ".claude" / "hooks" / "garde_pipe.py")],
        input=entree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(sortie)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_journal_garde_code_et_journal_complet(tmp_path):
    resultat = subprocess.run(
        [
            str(RACINE / ".claude" / "bin" / "journal"),
            "essai",
            "bash",
            "-c",
            'for i in $(seq 1 5000); do echo "ligne $i ERROR"; done; exit 3',
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"JOURNAL_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert resultat.returncode == 3
    premiere, derniere = resultat.stdout.splitlines()[0], resultat.stdout.splitlines()[-1]
    assert premiere.startswith("[journal] code=3") and derniere.startswith("[journal] code=3")
    assert len(resultat.stdout) < 8000
    journal = next(tmp_path.glob("essai-*.log"))
    assert journal.read_text().count("\n") == 5000
