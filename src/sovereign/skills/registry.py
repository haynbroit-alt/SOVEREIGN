"""Découverte, sélection et chargement paresseux des Skills.

Chaque Skill est décrite par un dossier ``catalog/<nom>/SKILL.md`` au format ouvert *Agent Skills*
(front-matter YAML ``name`` / ``description`` / ``license`` / ``metadata``). La découverte ne lit QUE les
front-matters (quelques centaines d'octets) : le code Python d'une Skill n'est importé que lorsqu'elle est
sélectionnée pour une mission. C'est la divulgation progressive des Agent Skills, appliquée au directeur.

Les valeurs de ``metadata`` sont des chaînes (compatibilité stricte avec la spécification) ; les listes sont
séparées par des virgules.
"""

from __future__ import annotations

import importlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sovereign.skills.base import Skill, SkillError

CATALOG_DIR = Path(__file__).parent / "catalog"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED_SECTIONS = (
    "## Description",
    "## Quand l'utiliser",
    "## Actions",
    "## Entrées et sorties",
    "## Déclencheurs",
    "## Sécurité",
    "## Tests",
)


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def stems(text: str) -> set[str]:
    """Racinisation volontairement simple (FR/EN) : troncature à 6 caractères des mots longs."""

    tokens = re.findall(r"[a-z0-9]+", normalize(text))
    return {t[:6] if len(t) > 6 else t for t in tokens if len(t) > 2}


def _csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


@dataclass
class SkillManifest:
    name: str
    description: str
    version: str
    module: str
    keywords: list[str]
    events: list[str]
    actions: list[str]
    data: list[str]
    risk: str
    path: Path
    license: str | None = None
    body: str = field(default="", repr=False)

    @property
    def skill_md(self) -> Path:
        return self.path / "SKILL.md"

    def keyword_stems(self) -> list[set[str]]:
        return [stems(k) for k in self.keywords if stems(k)]


def parse_skill_md(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillError(f"{path} : front-matter YAML manquant")
    _, front, body = text.split("---", 2)
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        raise SkillError(f"{path} : front-matter invalide")
    return data, body.strip()


def load_manifest(directory: Path) -> SkillManifest:
    front, body = parse_skill_md(directory / "SKILL.md")
    name = str(front.get("name", ""))
    description = str(front.get("description", "")).strip()
    if not NAME_RE.match(name) or len(name) > 64:
        raise SkillError(f"{directory} : nom de Skill invalide « {name} »")
    if name != directory.name:
        raise SkillError(f"{directory} : le nom « {name} » doit correspondre au dossier")
    if not description or len(description) > 1024:
        raise SkillError(f"{directory} : description absente ou > 1024 caractères")
    meta = front.get("metadata") or {}
    if not isinstance(meta, dict):
        raise SkillError(f"{directory} : metadata doit être un dictionnaire")
    for key, value in meta.items():
        if not isinstance(value, str):
            raise SkillError(f"{directory} : metadata.{key} doit être une chaîne (spécification Agent Skills)")
    module = meta.get("module", "")
    if not module.startswith("sovereign.skills."):
        raise SkillError(f"{directory} : metadata.module doit pointer vers sovereign.skills.*")
    return SkillManifest(
        name=name,
        description=description,
        version=meta.get("version", "0.0.0"),
        module=module,
        keywords=_csv(meta.get("keywords")),
        events=_csv(meta.get("events")),
        actions=_csv(meta.get("actions")),
        data=_csv(meta.get("data")),
        risk=meta.get("risk", "read_only"),
        path=directory,
        license=front.get("license"),
        body=body,
    )


@dataclass
class Selection:
    name: str
    score: float
    reasons: list[str]


class SkillRegistry:
    def __init__(self, catalog_dir: Path | None = None) -> None:
        self.catalog_dir = catalog_dir or CATALOG_DIR
        self.manifests: dict[str, SkillManifest] = {}
        self._instances: dict[str, Skill] = {}
        self.discover()

    def discover(self) -> list[SkillManifest]:
        self.manifests = {}
        for directory in sorted(p for p in self.catalog_dir.iterdir() if (p / "SKILL.md").is_file()):
            manifest = load_manifest(directory)
            self.manifests[manifest.name] = manifest
        return list(self.manifests.values())

    @property
    def names(self) -> list[str]:
        return sorted(self.manifests)

    def manifest(self, name: str) -> SkillManifest:
        try:
            return self.manifests[name]
        except KeyError as exc:
            raise SkillError(f"Skill inconnue « {name} » (disponibles : {', '.join(self.names)})") from exc

    def loaded(self) -> list[str]:
        return sorted(self._instances)

    def get(self, name: str) -> Skill:
        """Charge (une seule fois) l'implémentation et vérifie sa conformité au SKILL.md."""

        if name in self._instances:
            return self._instances[name]
        manifest = self.manifest(name)
        module = importlib.import_module(manifest.module)
        skill_cls = getattr(module, "SKILL", None)
        if skill_cls is None or not (isinstance(skill_cls, type) and issubclass(skill_cls, Skill)):
            raise SkillError(f"{manifest.module} doit exposer SKILL = <classe Skill>")
        instance = skill_cls()
        if instance.name != manifest.name:
            raise SkillError(f"{manifest.module} : nom {instance.name} ≠ SKILL.md {manifest.name}")
        if instance.version != manifest.version:
            raise SkillError(f"{manifest.name} : version code {instance.version} ≠ SKILL.md {manifest.version}")
        declared, implemented = set(manifest.actions), set(instance.actions)
        if declared != implemented:
            raise SkillError(
                f"{manifest.name} : actions SKILL.md {sorted(declared)} ≠ implémentation {sorted(implemented)}"
            )
        if "assess" not in implemented:
            raise SkillError(f"{manifest.name} : l'action 'assess' est obligatoire")
        self._instances[name] = instance
        return instance

    def select(
        self,
        text: str = "",
        *,
        events: Iterable[str] = (),
        available_data: Iterable[str] = (),
        priors: dict[str, float] | None = None,
        limit: int = 3,
        min_score: float = 1.0,
    ) -> list[Selection]:
        """Sélectionne les Skills pertinentes pour une mission, sans en charger le code.

        Score = correspondances de mots-clés (2 pts pour une expression complète) + événements déclencheurs
        (3 pts) + données disponibles (0,5 pt par type, même sans mot-clé) + a priori appris de l'utilité passée
        (±1 pt).
        """

        text_stems = stems(text)
        event_list = list(events)
        data_set = set(available_data)
        results: list[Selection] = []
        for manifest in self.manifests.values():
            score = 0.0
            reasons: list[str] = []
            matched = [
                kw
                for kw, kw_stems in zip(manifest.keywords, manifest.keyword_stems(), strict=False)
                if kw_stems and kw_stems <= text_stems
            ]
            if matched:
                score += sum(2.0 if " " in kw else 1.5 for kw in matched)
                reasons.append("mots-clés : " + ", ".join(matched[:5]))
            name_hit = stems(manifest.name.replace("-", " ")) & text_stems
            if name_hit:
                score += 1.0
            for event in event_list:
                if any(_glob(pattern, event) for pattern in manifest.events):
                    score += 3.0
                    reasons.append(f"événement : {event}")
            # Les données disponibles comptent même sans mot-clé : une Skill qui a de quoi analyser la situation
            # n'est pas ignorée parce que la mission est formulée autrement (les mots-clés restent prépondérants).
            hits = data_set.intersection(manifest.data)
            if hits:
                score += 0.5 * len(hits)
                reasons.append("données : " + ", ".join(sorted(hits)))
            if score > 0 and priors and manifest.name in priors:
                adjust = (priors[manifest.name] - 0.5) * 2.0
                score += adjust
                reasons.append(f"utilité apprise : {priors[manifest.name]:.2f}")
            if score >= min_score:
                results.append(Selection(manifest.name, round(score, 3), reasons))
        results.sort(key=lambda s: (-s.score, s.name))
        return results[:limit]


def _glob(pattern: str, value: str) -> bool:
    import fnmatch

    return fnmatch.fnmatchcase(value, pattern)
