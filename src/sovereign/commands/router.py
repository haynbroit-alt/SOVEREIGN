"""Système de commandes intelligentes.

Une commande s'écrit ``/nom arguments`` ; un texte libre sans « / » est confié au directeur (``/director``).
Toutes les interfaces (CLI, shell interactif, API REST, outils et prompts MCP) passent par ce routeur : une
commande se comporte exactement de la même façon partout. Chaque exécution est mesurée (``command_runs``) pour
évaluer l'utilité réelle des commandes.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from sovereign.core.clock import iso
from sovereign.core.telemetry import span

if TYPE_CHECKING:
    from sovereign.app import Sovereign


class Section(BaseModel):
    title: str
    body: str


class CommandResult(BaseModel):
    command: str
    title: str
    summary: str
    sections: list[Section] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True

    def markdown(self) -> str:
        parts = [f"# {self.title}", "", f"> {self.summary}", ""]
        for section in self.sections:
            parts += [f"## {section.title}", "", section.body, ""]
        return "\n".join(parts)


Handler = Callable[["Sovereign", str, dict[str, Any]], CommandResult]


@dataclass
class CommandSpec:
    name: str
    summary: str
    usage: str
    handler: Handler
    scope: Literal["read", "write"] = "read"


class CommandError(Exception):
    pass


def parse(text: str) -> tuple[str, str]:
    text = text.strip()
    if not text:
        return "status", ""
    if not text.startswith("/"):
        return "director", text
    head, _, rest = text[1:].partition(" ")
    return head.lower(), rest.strip()


def parse_options(args: str) -> tuple[str, dict[str, Any]]:
    """Sépare le texte libre des options ``--clé=valeur`` / ``--drapeau``."""

    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    free: list[str] = []
    options: dict[str, Any] = {}
    for token in tokens:
        if token.startswith("--"):
            key, sep, value = token[2:].partition("=")
            options[key.replace("-", "_")] = value if sep else True
        else:
            free.append(token)
    return " ".join(free), options


class CommandRouter:
    def __init__(self, app: Sovereign) -> None:
        from sovereign.commands.handlers import COMMANDS

        self.app = app
        self.specs: dict[str, CommandSpec] = {spec.name: spec for spec in COMMANDS}

    def list(self) -> list[CommandSpec]:
        return list(self.specs.values())

    def run(self, text: str, *, source: str = "cli", **options: Any) -> CommandResult:
        name, args = parse(text)
        return self.execute(name, args, source=source, **options)

    def execute(self, name: str, args: str = "", *, source: str = "cli", **options: Any) -> CommandResult:
        name = name.lstrip("/").lower()
        spec = self.specs.get(name)
        if spec is None:
            known = ", ".join(f"/{n}" for n in self.specs)
            raise CommandError(f"commande inconnue : /{name} (disponibles : {known})")
        free, parsed = parse_options(args)
        parsed.update(options)
        started = time.perf_counter()
        ok = False
        try:
            with span(f"command.{name}", source=source):
                result = spec.handler(self.app, free, parsed)
            ok = result.ok
            return result
        finally:
            self.app.kernel.db.execute(
                "INSERT INTO command_runs(command, ok, duration_ms, source, at) VALUES (?, ?, ?, ?, ?)",
                (
                    name,
                    int(ok),
                    round((time.perf_counter() - started) * 1000, 3),
                    source,
                    iso(self.app.kernel.clock.now()),
                ),
            )
