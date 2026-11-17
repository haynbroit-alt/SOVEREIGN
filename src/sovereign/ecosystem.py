"""OBSERVER un écosystème de dépôts de code (ex. tous les dépôts GitHub d'un compte).

Transforme chaque dépôt cloné en FAITS mesurables, puis en données que les Skills existantes savent analyser :

- ``project``         santé du dépôt (KPI : tests, automatisation, activité, documentation, tests réussis)
                      → ``analysis-optimization`` (santé, points faibles, recommandations)
- ``tech_dependency`` dépendances déclarées (npm, PyPI) → ``watch-anticipation`` (retards, maintenance)
- ``risk``            secrets exposés, tests en échec, code sans tests, dispersion entre projets qui se
                      recouvrent → ``watch-anticipation`` (carte des risques) et le directeur

Sécurité : l'observateur ne fait QUE lire (``git``, fichiers suivis). Il n'exécute jamais le code des dépôts —
SOVEREIGN détient la base financière et d'éventuelles clés d'API. Les résultats de tests viennent de la CI des
dépôts ou d'un fichier d'audit (``--tests``), jamais d'une exécution dans le processus de SOVEREIGN.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import combinations, pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sovereign.skills.registry import stems

if TYPE_CHECKING:
    from sovereign.app import Sovereign

SOURCE_EXT = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".sh",
    ".css",
    ".html",
    ".vue",
    ".svelte",
}
IGNORED_DIRS = ("node_modules/", "vendor/", "dist/", "build/", ".venv/", "__pycache__/", "coverage/")
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.[a-z]+$|(^|/)test_[^/]+\.py$", re.I)
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("clé AWS", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("clé Anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("clé OpenAI", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("jeton GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("jeton Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("clé Google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("clé Stripe", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}")),
    ("clé privée", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "secret en clair",
        re.compile(r"""(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['"]([^'"\s]{16,})['"]"""),
    ),
]
PLACEHOLDER_RE = re.compile(r"(?i)(x{4,}|your|example|exemple|changeme|placeholder|dummy|<|\$\{|process\.env|test)")


def _is_fake(value: str) -> bool:
    """Valeur factice : marqueur explicite, ou suite croissante de 6 caractères (« abcdef », « 123456 »).

    Une vraie clé aléatoire ne contient pratiquement jamais une telle suite ; les exemples et les tests, si.
    """

    if PLACEHOLDER_RE.search(value):
        return True
    run, low = 1, value.lower()
    for previous, current in pairwise(low):
        run = run + 1 if current.isalnum() and ord(current) == ord(previous) + 1 else 1
        if run >= 6:
            return True
    return False


@dataclass
class Dependency:
    ecosystem: str
    name: str
    version: str
    runtime: bool


@dataclass
class RepoFacts:
    name: str
    path: str
    remote: str | None
    commits: int
    first_commit: str | None
    last_commit: str | None
    days_since_last_commit: int | None
    active_days_30: int
    tracked_files: int
    source_files: int
    loc: int
    languages: dict[str, int]
    test_files: int
    test_cases: int
    ci_workflows: list[str]
    deploy: list[str]
    readme_title: str
    readme_chars: int
    has_license: bool
    todo_count: int
    dependencies: list[Dependency] = field(default_factory=list)
    secret_findings: list[str] = field(default_factory=list)
    package_names: list[str] = field(default_factory=list)
    tests_passed: int | None = None
    tests_failed: int | None = None
    profile: set[str] = field(default_factory=set, repr=False)

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "depot"


def _git(path: Path, *args: str) -> str:
    # Arguments fixés par le code (lecture seule : ls-files, log, remote) ; aucune entrée utilisateur interprétée.
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(path), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return result.stdout


def _version(spec: str) -> str | None:
    match = re.search(r"\d+(?:\.\d+){0,3}", spec or "")
    return match.group(0) if match else None


def _dependencies(path: Path, files: list[str]) -> tuple[list[Dependency], list[str]]:
    deps: list[Dependency] = []
    names: list[str] = []
    for rel in files:
        file = path / rel
        base = rel.rsplit("/", 1)[-1]
        try:
            if base == "package.json" and "node_modules/" not in rel:
                data = json.loads(file.read_text(encoding="utf-8"))
                if data.get("name"):
                    names.append(str(data["name"]))
                for section, runtime in (("dependencies", True), ("devDependencies", False)):
                    for name, spec in (data.get(section) or {}).items():
                        version = _version(str(spec))
                        if version:
                            deps.append(Dependency("npm", name, version, runtime))
            elif base == "pyproject.toml":
                data = tomllib.loads(file.read_text(encoding="utf-8"))
                project = data.get("project") or {}
                if project.get("name"):
                    names.append(str(project["name"]))
                for spec in project.get("dependencies") or []:
                    name = re.split(r"[\s<>=!~;\[]", spec, maxsplit=1)[0]
                    version = _version(spec[len(name) :])
                    if name and version:
                        deps.append(Dependency("pypi", name, version, True))
            elif re.fullmatch(r"requirements[^/]*\.txt", base):
                runtime = "dev" not in base and "test" not in base
                for raw_line in file.read_text(encoding="utf-8").splitlines():
                    line = raw_line.split("#", 1)[0].strip()
                    if not line or line.startswith("-"):
                        continue
                    name = re.split(r"[\s<>=!~;\[]", line, maxsplit=1)[0]
                    version = _version(line[len(name) :])
                    if name and version:
                        deps.append(Dependency("pypi", name, version, runtime))
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue
    unique = {(d.ecosystem, d.name): d for d in deps}
    return list(unique.values()), names


def scan_repository(path: str | Path, now: datetime | None = None) -> RepoFacts:
    """Faits d'un dépôt cloné : lecture seule, aucun code exécuté."""

    root = Path(path)
    now = now or datetime.now(UTC)
    files = [f for f in _git(root, "ls-files").splitlines() if f]
    dates = [d for d in _git(root, "log", "--format=%cI").splitlines() if d]
    parsed = sorted(datetime.fromisoformat(d) for d in dates)
    last = parsed[-1] if parsed else None
    active_30 = len({d.date() for d in parsed if (now - d).days <= 30})
    languages: dict[str, int] = {}
    loc = source_files = todo = test_cases = 0
    secrets: list[str] = []
    profile_text: list[str] = []
    for rel in files:
        if rel.startswith(IGNORED_DIRS) or any(f"/{d}" in f"/{rel}" for d in IGNORED_DIRS):
            continue
        file = root / rel
        ext = file.suffix.lower()
        try:
            if file.stat().st_size > 1_000_000:
                continue
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ext in SOURCE_EXT and not rel.endswith(".min.js"):
            lines = sum(1 for line in text.splitlines() if line.strip())
            loc += lines
            source_files += 1
            languages[ext.lstrip(".")] = languages.get(ext.lstrip("."), 0) + lines
            todo += len(re.findall(r"\b(TODO|FIXME|XXX)\b", text))
            if TEST_RE.search(rel):
                test_cases += len(re.findall(r"^\s*(?:async\s+)?def test_|\b(?:it|test)\s*\(", text, re.M))
        if ext in (".md", ".txt") or rel.endswith(("package.json", "pyproject.toml")):
            profile_text.append(text[:20000])
        if not any(rel.endswith(s) for s in ("lock", "lock.json", ".lock", ".min.js", ".svg", ".map")):
            finding = next(
                (
                    label
                    for label, pattern in SECRET_PATTERNS
                    for match in pattern.finditer(text)
                    if not _is_fake(match.group(match.lastindex or 0))
                ),
                None,
            )
            if finding:  # un constat par fichier suffit : le plus spécifique (ordre des motifs)
                secrets.append(f"{finding} dans {rel}")
    readme = next((f for f in files if re.fullmatch(r"(?i)readme(\.[a-z]+)?", f)), None)
    readme_text = (root / readme).read_text(encoding="utf-8", errors="ignore") if readme else ""
    title = next((line.lstrip("# ").strip() for line in readme_text.splitlines() if line.startswith("#")), "")
    deps, package_names = _dependencies(root, files)
    workflows = [f.rsplit("/", 1)[-1] for f in files if f.startswith(".github/workflows/")]
    deploy = sorted(
        {
            label
            for label, hit in (
                ("GitHub Pages", any("pages" in w.lower() for w in workflows)),
                ("Netlify", "netlify.toml" in files),
                ("Vercel", "vercel.json" in files),
                ("Docker", any(f.rsplit("/", 1)[-1] == "Dockerfile" for f in files)),
                ("systemd", any(f.endswith(".service") for f in files)),
                ("Fly.io", "fly.toml" in files),
                ("Cloudflare", "wrangler.toml" in files),
            )
            if hit
        }
    )
    remote = _git(root, "remote", "get-url", "origin").strip() or None
    return RepoFacts(
        name=root.name,
        path=str(root),
        remote=remote,
        commits=len(parsed),
        first_commit=parsed[0].date().isoformat() if parsed else None,
        last_commit=last.date().isoformat() if last else None,
        days_since_last_commit=(now - last).days if last else None,
        active_days_30=active_30,
        tracked_files=len(files),
        source_files=source_files,
        loc=loc,
        languages=dict(sorted(languages.items(), key=lambda kv: -kv[1])),
        test_files=sum(1 for f in files if TEST_RE.search(f)),
        test_cases=test_cases,
        ci_workflows=workflows,
        deploy=deploy,
        readme_title=title,
        readme_chars=len(readme_text),
        has_license=any(re.fullmatch(r"(?i)(licen[cs]e|copying)(\.[a-z]+)?", f) for f in files),
        todo_count=todo,
        dependencies=deps,
        secret_findings=sorted(set(secrets)),
        package_names=package_names,
        profile=stems(" ".join([title, *profile_text, *package_names])),
    )


def overlaps(facts: list[RepoFacts], threshold: float = 0.14) -> list[tuple[str, str, float]]:
    """Recouvrements entre dépôts.

    Le vocabulaire commun (mots présents dans la plupart des dépôts) ne discrimine pas : deux longs documents en
    français partagent beaucoup de mots. On ne garde donc que les termes DISTINCTIFS (présents dans 2 dépôts au
    moins et au plus 30 % d'entre eux, à la manière d'un IDF), puis on mesure leur similarité de Jaccard. Un nom
    de paquet partagé est une preuve directe de duplication.
    """

    document_frequency = Counter(term for f in facts for term in f.profile)
    ceiling = max(2, int(0.3 * len(facts)))
    distinctive = {f.name: {t for t in f.profile if 2 <= document_frequency[t] <= ceiling} for f in facts}
    pairs: list[tuple[str, str, float]] = []
    for a, b in combinations(facts, 2):
        union = distinctive[a.name] | distinctive[b.name]
        score = len(distinctive[a.name] & distinctive[b.name]) / len(union) if union else 0.0
        if set(a.package_names) & set(b.package_names):
            score = max(score, 0.9)
        if score >= threshold:
            pairs.append((a.name, b.name, round(score, 3)))
    return sorted(pairs, key=lambda p: -p[2])


def to_records(
    facts: RepoFacts, overlap_with: list[str], scores: dict[str, float] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Traduit les faits en enregistrements analysables par les Skills existantes."""

    kpis: list[dict[str, Any]] = []
    if facts.source_files:
        kpis.append(
            {
                "name": "tests / fichiers source",
                "value": round(facts.test_files / facts.source_files, 3),
                "target": 0.3,
                "direction": "higher",
                "weight": 1.5,
            }
        )
        kpis.append(
            {
                "name": "automatisation CI",
                "value": 1.0 if facts.ci_workflows else 0.0,
                "target": 1.0,
                "direction": "higher",
                "weight": 1.0,
            }
        )
    kpis.append(
        {
            "name": "documentation (caractères README)",
            "value": facts.readme_chars,
            "target": 1500,
            "direction": "higher",
            "weight": 0.5,
        }
    )
    if facts.days_since_last_commit is not None:
        kpis.append(
            {
                "name": "jours depuis le dernier commit",
                "value": max(1, facts.days_since_last_commit),
                "target": 14,
                "direction": "lower",
                "weight": 1.0,
            }
        )
    if facts.tests_passed is not None and facts.tests_failed is not None and facts.tests_passed + facts.tests_failed:
        kpis.append(
            {
                "name": "tests réussis",
                "value": round(facts.tests_passed / (facts.tests_passed + facts.tests_failed), 3),
                "target": 1.0,
                "direction": "higher",
                "weight": 2.0,
            }
        )
    status = "actif" if (facts.days_since_last_commit or 0) <= 30 else "dormant"
    project = {
        "id": f"repo-{facts.slug}",
        "name": facts.readme_title or facts.name,
        "status": status,
        "kpis": kpis,
        "source": "ecosystem-scan",
        "repo": {k: v for k, v in asdict(facts).items() if k not in ("dependencies", "profile")},
        "overlaps_with": overlap_with,
        "overlaps": [{"repo": name, "score": (scores or {}).get(name, 0.0)} for name in overlap_with],
    }
    deps = [
        {
            "id": f"dep-{facts.slug}-{d.ecosystem}-{re.sub(r'[^a-z0-9]+', '-', d.name.lower())}",
            "name": d.name,
            "ecosystem": d.ecosystem,
            "current_version": d.version,
            "critical": d.runtime,
            "project": facts.slug,
        }
        for d in facts.dependencies
    ]
    risks: list[dict[str, Any]] = []
    if facts.secret_findings:
        risks.append(
            {
                "id": f"risk-{facts.slug}-secrets",
                "title": f"Secrets exposés dans {facts.name}",
                "category": "legal",
                "probability": 0.9,
                "impact": 9,
                "velocity": 9,
                "indicator_trend": "rising",
                "owner": facts.name,
                "mitigation": "Révoquer les clés, les retirer de l'historique, utiliser des secrets d'environnement.",
            }
        )
    if facts.tests_failed:
        risks.append(
            {
                "id": f"risk-{facts.slug}-tests",
                "title": f"Tests en échec dans {facts.name}",
                "category": "tech",
                "probability": 0.8,
                "impact": 5,
                "velocity": 5,
                "owner": facts.name,
                "mitigation": "Corriger les tests avant toute nouvelle fonctionnalité.",
            }
        )
    if facts.loc >= 500 and facts.test_files == 0:
        risks.append(
            {
                "id": f"risk-{facts.slug}-sans-tests",
                "title": f"{facts.loc} lignes sans aucun test ({facts.name})",
                "category": "tech",
                "probability": 0.6,
                "impact": 4,
                "velocity": 4,
                "owner": facts.name,
                "mitigation": "Tests sur les calculs et parcours critiques.",
            }
        )
    return {"project": [project], "tech_dependency": deps, "risk": risks}


def load_test_results(path: str | Path) -> dict[str, tuple[int, int]]:
    """Fichier JSON ``{"depot": {"passed": 10, "failed": 2}}`` (CI ou audit externe)."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: (int(v.get("passed", 0)), int(v.get("failed", 0))) for name, v in data.items()}


def scan_ecosystem(
    app: Sovereign,
    root: str | Path,
    *,
    tests: dict[str, tuple[int, int]] | None = None,
    exclude: tuple[str, ...] = (),
    include: tuple[str | Path, ...] = (),
) -> dict[str, Any]:
    """Observe tous les dépôts d'un dossier (plus ``include``) et enregistre les faits dans l'espace de travail."""

    base = Path(root)
    repos = sorted(p for p in base.iterdir() if (p / ".git").exists() and p.name not in exclude)
    repos += [Path(p) for p in include if (Path(p) / ".git").exists() and Path(p) not in repos]
    now = app.kernel.clock.now()
    facts = [scan_repository(repo, now) for repo in repos]
    for item in facts:
        if tests and item.name in tests:
            item.tests_passed, item.tests_failed = tests[item.name]
    pairs = overlaps(facts)
    stored = {"project": 0, "tech_dependency": 0, "risk": 0}
    for item in facts:
        scores = {b if a == item.name else a: score for a, b, score in pairs if item.name in (a, b)}
        for kind, records in to_records(item, list(scores), scores).items():
            for record in records:
                app.kernel.store.put(kind, record["id"], record)
                stored[kind] += 1
    clusters = _clusters(pairs)
    for index, cluster in enumerate(clusters, start=1):
        if len(cluster) >= 3:
            app.kernel.store.put(
                "risk",
                f"risk-dispersion-{index}",
                {
                    "id": f"risk-dispersion-{index}",
                    "category": "operational",
                    "probability": 0.7,
                    "impact": 6,
                    "velocity": 6,
                    "indicator_trend": "rising",
                    "title": f"Dispersion : {len(cluster)} projets se recouvrent ({', '.join(sorted(cluster))})",
                    "mitigation": "Choisir un projet canonique, fusionner ou geler les autres.",
                },
            )
            stored["risk"] += 1
    app.kernel.events.publish("ecosystem.scanned", {"repos": len(facts), "overlaps": len(pairs)}, source="ecosystem")
    return {
        "repos": [_summary(f) for f in facts],
        "overlaps": pairs,
        "clusters": [sorted(c) for c in clusters],
        "stored": stored,
    }


_ASSESSMENT_FIELDS = (
    "purpose",
    "maturity",
    "commercial_potential",
    "effort_to_mvp_weeks",
    "confidence",
    "verdict",
    "verdict_reason",
    "overlaps_with",
    "complementary_to_sovereign",
    "security_issues",
    "correctness_issues",
    "deadline",
    "role",
)


def apply_assessments(app: Sovereign, assessments: list[dict[str, Any]], *, analyst: str = "ia") -> dict[str, Any]:
    """Joint le jugement d'analystes (IA ou humains) aux projets observés, sans écraser les faits mesurés.

    Le jugement est rangé à part (``assessment``) : la revue de portefeuille calcule SON verdict et signale les
    désaccords. Les résultats de tests exécutés par l'analyste deviennent un KPI mesuré.
    """

    projects = app.kernel.store.list("project")
    by_repo = {str((p.get("repo") or {}).get("name", "")).lower(): p for p in projects}
    applied, unknown = [], []
    for item in assessments:
        project = by_repo.get(str(item.get("repo", "")).lower())
        if project is None:
            unknown.append(item.get("repo"))
            continue
        assessment = {k: item[k] for k in _ASSESSMENT_FIELDS if item.get(k) is not None}
        assessment["analyst"] = analyst
        project["assessment"] = assessment
        total, passed = item.get("tests_total"), item.get("tests_passed")
        if item.get("tests_run") and isinstance(total, int) and isinstance(passed, int) and total > 0:
            repo = project.setdefault("repo", {})
            failed = item.get("tests_failed")
            if failed is None and "tests_skipped" in item:
                failed = total - passed - int(item["tests_skipped"])
            # Sans détail, « non réussi » peut être un test ignoré : jamais compté comme un échec.
            repo["tests_passed"], repo["tests_failed"] = passed, failed
            repo["tests_not_passed"] = total - passed
            project["kpis"] = [k for k in project.get("kpis", []) if k.get("name") != "tests réussis"] + [
                {
                    "name": "tests réussis",
                    "value": round(passed / total, 3),
                    "target": 1.0,
                    "direction": "higher",
                    "weight": 2.0,
                }
            ]
        app.kernel.store.put("project", project["id"], project)
        applied.append(project["id"])
    app.kernel.events.publish("ecosystem.assessed", {"projects": len(applied)}, source="ecosystem")
    return {"applied": applied, "unknown": unknown}


def _clusters(pairs: list[tuple[str, str, float]]) -> list[set[str]]:
    groups: list[set[str]] = []
    for a, b, _ in pairs:
        hits = [g for g in groups if a in g or b in g]
        merged = {a, b}.union(*hits) if hits else {a, b}
        groups = [g for g in groups if g not in hits] + [merged]
    return sorted(groups, key=lambda g: -len(g))


def _summary(f: RepoFacts) -> dict[str, Any]:
    return {
        "repo": f.name,
        "titre": f.readme_title,
        "commits": f.commits,
        "dernier_commit": f.last_commit,
        "loc": f.loc,
        "langages": list(f.languages)[:3],
        "fichiers_test": f.test_files,
        "cas_de_test": f.test_cases,
        "ci": f.ci_workflows,
        "deploiement": f.deploy,
        "dependances": len(f.dependencies),
        "secrets": f.secret_findings,
        "licence": f.has_license,
        "todo": f.todo_count,
        "tests": None if f.tests_passed is None else {"réussis": f.tests_passed, "échecs": f.tests_failed},
    }
