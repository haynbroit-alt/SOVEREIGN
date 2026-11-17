from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sovereign.app import Sovereign
from sovereign.ecosystem import overlaps, scan_ecosystem, scan_repository

# Clé factice au format réaliste, assemblée à l'exécution : le fichier source ne contient aucun secret détectable.
FAKE_KEY = "sk-" + "ant-api03-" + "Qm7xK2pL9vR4tW8nZ3cY6bH1jF5dS0gA"


def _repo(root: Path, name: str, files: dict[str, str]) -> Path:
    path = root / name
    path.mkdir(parents=True)
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "HOME": str(root),
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True, env=env)
    return path


ENGINE_DOC = "# Moteur cognitif\\nBoucle hypothèses sérendipité exploration autonome découverte heuristique\\n"


@pytest.fixture
def ecosystem(tmp_path):
    root = tmp_path / "comptes"
    _repo(
        root,
        "produit",
        {
            "README.md": "# Paie Juste\nVérifier sa fiche de paie : salaire minimum, heures supplémentaires.\n" * 20,
            "src/calcul.js": "export function brut(h, taux) {\n  return h * taux;\n}\n// TODO arrondis\n",
            "tests/calcul.test.js": "test('brut', () => {});\ntest('heures', () => {});\n",
            "package.json": json.dumps(
                {"name": "paie-juste", "dependencies": {"express": "^4.18.2"}, "devDependencies": {"vitest": "~1.2.0"}}
            ),
            ".github/workflows/pages.yml": "on: push\n",
            "netlify.toml": "[build]\n",
            "LICENSE": "MIT",
        },
    )
    _repo(
        root,
        "moteur",
        {
            "README.md": ENGINE_DOC,
            "package.json": json.dumps({"name": "cortex-engine"}),
            "index.js": "console.log(1)\n" * 600,
        },
    )
    _repo(
        root,
        "copie",
        {
            "README.md": ENGINE_DOC,
            "moteurs/cortex/package.json": json.dumps({"name": "cortex-engine"}),
            "config.py": f'API_KEY = "{FAKE_KEY}"\n',
        },
    )
    return root


def test_scan_repository_reads_facts_without_running_code(ecosystem):
    facts = scan_repository(ecosystem / "produit")
    assert facts.commits == 1 and facts.readme_title == "Paie Juste"
    assert facts.test_files == 1 and facts.test_cases == 2
    assert facts.ci_workflows == ["pages.yml"] and set(facts.deploy) == {"GitHub Pages", "Netlify"}
    assert facts.has_license and facts.todo_count == 1 and facts.package_names == ["paie-juste"]
    deps = {(d.name, d.version, d.runtime) for d in facts.dependencies}
    assert ("express", "4.18.2", True) in deps and ("vitest", "1.2.0", False) in deps


def test_secrets_detected_but_placeholders_ignored(tmp_path):
    root = tmp_path / "r"
    _repo(
        root,
        "x",
        {
            "a.py": f'API_KEY = "{FAKE_KEY}"\n',
            "b.py": 'token = "your-token-here-xxxxxxxx"\nkey = process.env.API_KEY\n',
            "c.py": 'API_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"\n',  # suite croissante : exemple
        },
    )
    facts = scan_repository(root / "x")
    assert facts.secret_findings == ["clé Anthropic dans a.py"]


def test_overlap_by_shared_package_and_distinctive_terms(ecosystem):
    facts = [scan_repository(p) for p in sorted(ecosystem.iterdir())]
    pairs = overlaps(facts)
    assert any({a, b} == {"copie", "moteur"} and score >= 0.9 for a, b, score in pairs)
    assert not any("produit" in (a, b) for a, b, _ in pairs)


def test_scan_ecosystem_feeds_existing_skills(ecosystem):
    app = Sovereign.ephemeral()
    tests_file = ecosystem.parent / "tests.json"
    tests_file.write_text(json.dumps({"produit": {"passed": 9, "failed": 1}}), encoding="utf-8")
    from sovereign.ecosystem import load_test_results

    result = scan_ecosystem(app, ecosystem, tests=load_test_results(tests_file))
    assert result["stored"]["project"] == 3
    risks = {r["id"] for r in app.kernel.store.list("risk")}
    assert {"risk-copie-secrets", "risk-produit-tests", "risk-moteur-sans-tests"} <= risks
    projects = app.kernel.run_skill("analysis-optimization", "analyze_projects", {})
    assert len(projects.projects) == 3
    produit = next(p for p in projects.projects if p.name == "Paie Juste")
    assert any(k.name == "tests réussis" and k.value == 0.9 for k in produit.kpis)
    watch = app.kernel.run_skill("watch-anticipation", "assess_risks", {})
    assert watch.metrics["risk.critical_count"] >= 1  # secrets exposés
    deps = app.kernel.store.list("tech_dependency")
    assert any(d["name"] == "express" and d["critical"] for d in deps)
    app.close()


def test_cli_scan(ecosystem, tmp_path):
    from typer.testing import CliRunner

    from sovereign.interfaces.cli import cli

    result = CliRunner().invoke(
        cli, ["ecosystem", "scan", str(ecosystem)], env={"SOVEREIGN_HOME": str(tmp_path / "home")}
    )
    assert result.exit_code == 0, result.output
    assert '"project": 3' in result.output


def test_assessments_join_judgement_and_drive_portfolio(ecosystem, tmp_path):
    from typer.testing import CliRunner

    from sovereign.interfaces.cli import cli

    home = {"SOVEREIGN_HOME": str(tmp_path / "home")}
    runner = CliRunner()
    assert runner.invoke(cli, ["ecosystem", "scan", str(ecosystem)], env=home).exit_code == 0
    evaluations = tmp_path / "evaluations.json"
    evaluations.write_text(
        json.dumps(
            [
                {
                    "repo": "produit",
                    "commercial_potential": 5,
                    "effort_to_mvp_weeks": 8,
                    "confidence": 0.55,
                    "maturity": "mvp",
                    "tests_run": True,
                    "tests_total": 40,
                    "tests_passed": 38,
                    "tests_failed": 2,
                    "verdict": "develop",
                },
                {
                    "repo": "moteur",
                    "commercial_potential": 2,
                    "effort_to_mvp_weeks": 10,
                    "verdict": "freeze",
                    "tests_run": True,
                    "tests_total": 10,
                    "tests_passed": 7,
                },
                {"repo": "copie", "commercial_potential": 1, "effort_to_mvp_weeks": 12, "verdict": "freeze"},
                {"repo": "absent", "commercial_potential": 9},
            ]
        ),
        encoding="utf-8",
    )
    result = runner.invoke(cli, ["ecosystem", "assess", str(evaluations), "--analyst", "audit"], env=home)
    assert result.exit_code == 0, result.output
    assert '"absent"' in result.output  # dépôt inconnu signalé, pas inventé
    app = Sovereign.open(tmp_path / "home")
    try:
        produit = app.kernel.store.get("project", "repo-produit")
        assert produit["assessment"]["analyst"] == "audit"
        assert produit["repo"]["tests_failed"] == 2  # faits mesurés par l'analyste
        assert [k["value"] for k in produit["kpis"] if k["name"] == "tests réussis"] == [0.95]
        moteur = app.kernel.store.get("project", "repo-moteur")["repo"]
        assert moteur["tests_failed"] is None and moteur["tests_not_passed"] == 3  # ignorés ≠ échecs
        out = app.kernel.run_skill("business-development", "portfolio_review", {})
        verdicts = {d.project_id: d.verdict for d in out.decisions}
        assert verdicts["repo-produit"] == "développer"
        assert verdicts["repo-copie"] == "geler"  # doublon mesuré (paquet partagé) du moteur, mieux classé
        assert next(d for d in out.decisions if d.project_id == "repo-copie").blocking == ["secrets exposés (1)"]
    finally:
        app.close()
