"""Interface en ligne de commande.

sovereign init --demo                 # crée l'espace (~/.sovereign) et charge la démo
sovereign director "Que faire cette semaine ?" --deep
sovereign budget | strategy | business | marketing | council … | opportunities | projects | audit
sovereign skills | memory | roadmap | status | improve | report weekly
sovereign shell                       # tapez /director, /budget… en langage naturel
sovereign serve                       # API REST + MCP HTTP + planificateur + worker
sovereign mcp                         # serveur MCP stdio (Claude Code, Claude Desktop…)
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.markdown import Markdown

import sovereign
from sovereign.app import Sovereign
from sovereign.commands.handlers import COMMANDS
from sovereign.commands.router import CommandError, CommandResult
from sovereign.core.finance import Recurrence

console = Console()
cli = typer.Typer(
    name="sovereign",
    help=f"SOVEREIGN — Autonomous Business Intelligence Director. {sovereign.MOTTO}",
    no_args_is_help=True,
    rich_markup_mode="markdown",
    pretty_exceptions_enable=False,
)
HomeOpt = Annotated[Path | None, typer.Option("--home", envvar="SOVEREIGN_HOME", help="Dossier de données")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Sortie JSON")]


def _app(home: Path | None) -> Sovereign:
    return Sovereign.open(home)


def _print(result: CommandResult | dict[str, Any] | list[Any], as_json: bool) -> None:
    if isinstance(result, CommandResult):
        if as_json:
            console.print_json(result.model_dump_json())
        else:
            console.print(Markdown(result.markdown()))
        return
    if as_json or not isinstance(result, dict):
        console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    else:
        for key, value in result.items():
            console.print(f"[bold]{key}[/bold] : {value}")


def _make_command(name: str, summary: str, usage: str) -> None:
    def handler(
        ctx: typer.Context,
        words: Annotated[list[str] | None, typer.Argument(help="Arguments")] = None,
        home: HomeOpt = None,
        as_json: JsonOpt = False,
    ) -> None:
        app = _app(home)
        try:
            text = " ".join(list(words or []) + list(ctx.args))
            _print(app.commands.execute(name, text, source="cli"), as_json)
        except (CommandError, ValueError) as exc:
            console.print(f"[red]Erreur :[/red] {exc}")
            raise typer.Exit(2) from exc
        finally:
            app.close()

    handler.__doc__ = f"{summary}.  Usage : `{usage}`"
    cli.command(name=name, context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(handler)


for _spec in COMMANDS:
    _make_command(_spec.name, _spec.summary, _spec.usage)


@cli.command()
def ask(
    text: Annotated[str, typer.Argument(help="« /commande args » ou question libre")],
    home: HomeOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Communiquer naturellement : « /budget », « Que faire ? » (→ /director)."""

    app = _app(home)
    try:
        _print(app.commands.run(text, source="cli"), as_json)
    except (CommandError, ValueError) as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(2) from exc
    finally:
        app.close()


@cli.command()
def shell(home: HomeOpt = None) -> None:
    """Session interactive : tapez /director, /budget, /council … ou une question. « exit » pour quitter."""

    app = _app(home)
    console.print(
        f"[bold]SOVEREIGN[/bold] — {sovereign.MOTTO}\nCommandes : " + " ".join(f"/{s.name}" for s in COMMANDS)
    )
    try:
        while True:
            try:
                text = console.input("[bold cyan]sovereign ›[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text in ("exit", "quit", "/exit", "/quit"):
                break
            if not text:
                continue
            try:
                _print(app.commands.run(text, source="shell"), False)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__} :[/red] {exc}")
    finally:
        app.close()


@cli.command()
def init(
    home: HomeOpt = None,
    demo: Annotated[bool, typer.Option(help="Charger l'espace de démonstration")] = False,
) -> None:
    """Créer l'espace de travail (base SQLite, dossiers) et éventuellement charger la démo."""

    app = _app(home)
    try:
        console.print(f"Espace de travail : [bold]{app.kernel.settings.home_path}[/bold] (base {app.kernel.db.path})")
        if demo:
            from sovereign.workspace import DEMO_PATH, import_workspace, load_file

            report = import_workspace(app, load_file(DEMO_PATH), actor="demo")
            console.print("Démo chargée :", report)
        console.print(
            "Ensuite : `sovereign status`, `sovereign director`, `sovereign apikey create admin --scope "
            "admin`, `sovereign serve`."
        )
    finally:
        app.close()


# -- Skills -------------------------------------------------------------------------------------------
skill_cli = typer.Typer(help="Skills : lister, inspecter, exécuter, exporter.", no_args_is_help=True)
cli.add_typer(skill_cli, name="skill")


@skill_cli.command("run")
def skill_run(
    name: str,
    action: str,
    input: Annotated[str, typer.Option("--input", "-i", help="JSON ou @fichier.json")] = "{}",
    home: HomeOpt = None,
) -> None:
    """Exécuter une action de Skill."""

    payload = json.loads(Path(input[1:]).read_text() if input.startswith("@") else input)
    app = _app(home)
    try:
        out = app.kernel.run_skill(name, action, payload, caller="cli")
        console.print_json(out.model_dump_json())
    finally:
        app.close()


@skill_cli.command("show")
def skill_show(name: str, home: HomeOpt = None) -> None:
    """Description, actions et schémas JSON d'une Skill."""

    app = _app(home)
    try:
        _print(app.commands.execute("skills", name, source="cli"), False)
    finally:
        app.close()


@skill_cli.command("export")
def skill_export(target: Annotated[Path, typer.Argument(help="Ex. .claude/skills")]) -> None:
    """Copier les SKILL.md (format Agent Skills) vers un dossier lu par un agent (Claude Code…)."""

    from sovereign.skills.registry import SkillRegistry

    registry = SkillRegistry()
    for name in registry.names:
        destination = target / name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "SKILL.md").write_text(
            registry.manifest(name).skill_md.read_text(encoding="utf-8"), encoding="utf-8"
        )
    console.print(f"{len(registry.names)} Skills exportées vers {target}")


# -- Données ------------------------------------------------------------------------------------------
data_cli = typer.Typer(help="Importer / exporter l'espace de travail.", no_args_is_help=True)
cli.add_typer(data_cli, name="data")


@data_cli.command("import")
def data_import(path: Path, home: HomeOpt = None) -> None:
    """Importer un fichier YAML/JSON (marchés, campagnes, projets, encaissements, croyances…)."""

    from sovereign.workspace import import_workspace, load_file

    app = _app(home)
    try:
        _print(import_workspace(app, load_file(path), actor="cli-import"), True)
    finally:
        app.close()


@data_cli.command("import-csv")
def data_import_csv(
    kind: Annotated[str, typer.Argument(help="collections | campaigns | <kind> (market, segment, need…)")],
    path: Path,
    mapping: Annotated[list[str] | None, typer.Option("--map", help="champ=colonne (répétable)")] = None,
    decimal: Annotated[str, typer.Option(help="auto | comma | dot")] = "auto",
    monthfirst: Annotated[bool, typer.Option(help="Dates au format américain MM/JJ/AAAA")] = False,
    channel: Annotated[str | None, typer.Option(help="Canal par défaut des campagnes")] = None,
    home: HomeOpt = None,
) -> None:
    """Importer un export CSV réel (relevé, paiements, rapport de campagnes…) sans le retoucher."""

    from sovereign.importers import import_csv

    if decimal not in ("auto", "comma", "dot"):
        raise typer.BadParameter("decimal : auto | comma | dot")
    pairs = dict(item.split("=", 1) for item in (mapping or []) if "=" in item)
    app = _app(home)
    try:
        options: dict[str, Any] = {"decimal": decimal}
        if kind in ("collections", "encaissements", "campaigns", "campagnes"):
            options["mapping"] = pairs
        if kind in ("collections", "encaissements"):
            options["dayfirst"] = not monthfirst
        if kind in ("campaigns", "campagnes"):
            options["channel"] = channel
        report = import_csv(app, kind, path, **options)
        app.worker.run_once()
        _print(report.as_dict(), True)
    except ValueError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(2) from exc
    finally:
        app.close()


@data_cli.command("inbox")
def data_inbox(
    folder: Annotated[Path | None, typer.Argument(help="Dossier de dépôt (défaut : $SOVEREIGN_HOME/inbox)")] = None,
    home: HomeOpt = None,
) -> None:
    """Importer tous les fichiers déposés (CSV « <kind>--libellé.csv », YAML/JSON) puis les ranger."""

    from sovereign.importers import process_inbox

    app = _app(home)
    try:
        target = folder or app.kernel.settings.home_path / "inbox"
        results = process_inbox(app, target)
        app.worker.run_once()
        _print({"inbox": str(target), "files": results}, True)
    finally:
        app.close()


@data_cli.command("export")
def data_export(home: HomeOpt = None) -> None:
    """Exporter l'espace de travail en JSON (sortie standard)."""

    from sovereign.workspace import export_workspace

    app = _app(home)
    try:
        sys.stdout.write(json.dumps(export_workspace(app), ensure_ascii=False, indent=1, default=str) + "\n")
    finally:
        app.close()


# -- Finances -----------------------------------------------------------------------------------------
fin_cli = typer.Typer(help="Encaissements, factures, dépenses (aucun accès bancaire).", no_args_is_help=True)
cli.add_typer(fin_cli, name="finance")


@fin_cli.command("revenue")
def fin_revenue(
    amount: str,
    date: str | None = None,
    counterparty: Annotated[str | None, typer.Option("--from")] = None,
    reference: str | None = None,
    home: HomeOpt = None,
) -> None:
    """Enregistrer un encaissement RÉEL."""

    app = _app(home)
    try:
        entry = app.kernel.finance.record_collection(
            amount, date, counterparty=counterparty, reference=reference, actor="cli"
        )
        _print(entry.model_dump(mode="json"), True)
    finally:
        app.close()


@fin_cli.command("invoice")
def fin_invoice(client: str, amount: str, due: str | None = None, home: HomeOpt = None) -> None:
    """Émettre une facture (n'ouvre aucun budget avant encaissement)."""

    app = _app(home)
    try:
        _print(app.kernel.finance.issue_invoice(client, amount, due_on=due).model_dump(mode="json"), True)
    finally:
        app.close()


@fin_cli.command("collect")
def fin_collect(invoice_id: str, date: str | None = None, home: HomeOpt = None) -> None:
    """Déclarer l'encaissement d'une facture."""

    app = _app(home)
    try:
        _print(
            app.kernel.finance.collect_invoice(invoice_id, occurred_on=date, actor="cli").model_dump(mode="json"),
            True,
        )
    finally:
        app.close()


@fin_cli.command("expense")
def fin_expense(
    amount: str,
    category: str,
    vendor: str | None = None,
    description: str | None = None,
    recurring: str = "none",
    home: HomeOpt = None,
) -> None:
    """Demander une dépense : approuvée, refusée ou envoyée en revue humaine."""

    app = _app(home)
    try:
        if recurring not in ("none", "monthly", "yearly"):
            raise typer.BadParameter("recurring : none | monthly | yearly")
        decision = app.kernel.finance.request_expense(
            amount,
            category,
            vendor=vendor,
            description=description,
            recurring=cast(Recurrence, recurring),
            requested_by="cli",
        )
        _print(decision.model_dump(mode="json"), True)
    finally:
        app.close()


@fin_cli.command("confirm")
def fin_confirm(expense_id: str, actual: str | None = None, home: HomeOpt = None) -> None:
    """Déclarer le règlement (par vous) d'une dépense approuvée."""

    app = _app(home)
    try:
        _print(app.kernel.finance.confirm_spent(expense_id, actual, actor="cli").model_dump(mode="json"), True)
    finally:
        app.close()


@fin_cli.command("review")
def fin_review(
    expense_id: str,
    approve: Annotated[bool, typer.Option("--approve/--reject")],
    reason: str | None = None,
    home: HomeOpt = None,
) -> None:
    """Décision HUMAINE sur une dépense en revue."""

    app = _app(home)
    try:
        _print(
            app.kernel.finance.decide_review(expense_id, approve, os.environ.get("USER", "humain"), reason).model_dump(
                mode="json"
            ),
            True,
        )
    finally:
        app.close()


# -- Gouvernance, mémoire, modèle du monde ---------------------------------------------------------------
gov_cli = typer.Typer(help="Validations humaines (risque élevé).", no_args_is_help=True)
cli.add_typer(gov_cli, name="approvals")


@gov_cli.command("list")
def approvals_list(status: str = "pending", home: HomeOpt = None) -> None:
    """Lister les validations."""

    app = _app(home)
    try:
        _print([a.model_dump(mode="json") for a in app.approvals.list(None if status == "all" else status)], True)
    finally:
        app.close()


@gov_cli.command("approve")
def approvals_approve(approval_id: str, reason: str | None = None, home: HomeOpt = None) -> None:
    """Approuver (crée la demande de dépense / la tâche de suivi, mémorise la décision)."""

    app = _app(home)
    try:
        _print(app.approvals.decide(approval_id, True, os.environ.get("USER", "humain"), reason), True)
    finally:
        app.close()


@gov_cli.command("reject")
def approvals_reject(approval_id: str, reason: str | None = None, home: HomeOpt = None) -> None:
    """Refuser (la décision de refus est mémorisée)."""

    app = _app(home)
    try:
        _print(app.approvals.decide(approval_id, False, os.environ.get("USER", "humain"), reason), True)
    finally:
        app.close()


belief_cli = typer.Typer(help="Modèle du monde : croyances testables.", no_args_is_help=True)
cli.add_typer(belief_cli, name="belief")


@belief_cli.command("add")
def belief_add(
    statement: str,
    topic: str = "general",
    metric: str | None = None,
    comparator: Annotated[str | None, typer.Option(help="lt, le, gt, ge, between")] = None,
    threshold: float | None = None,
    high: float | None = None,
    confidence: float = 0.6,
    home: HomeOpt = None,
) -> None:
    """Ajouter une croyance (testable si métrique + comparateur + seuil)."""

    app = _app(home)
    try:
        belief = app.world.add(
            statement,
            topic=topic,
            metric=metric,
            comparator=comparator,  # type: ignore[arg-type]
            threshold=threshold,
            threshold_high=high,
            confidence=confidence,
        )
        _print(belief.model_dump(mode="json"), True)
    finally:
        app.close()


@belief_cli.command("list")
def belief_list(home: HomeOpt = None) -> None:
    """Lister les croyances et leur confiance."""

    app = _app(home)
    try:
        _print([b.model_dump(mode="json", exclude={"evidence"}) for b in app.world.list()], True)
    finally:
        app.close()


@cli.command("outcome")
def outcome(
    decision_id: str,
    actual: float | None = None,
    status: str | None = None,
    notes: str = "",
    home: HomeOpt = None,
) -> None:
    """Mesurer le résultat réel d'une décision (alimente l'apprentissage)."""

    app = _app(home)
    try:
        out = app.kernel.run_skill(
            "memory-learning",
            "record_outcome",
            {"decision_id": decision_id, "actual": actual, "status": status, "notes": notes},
            caller="cli",
        )
        app.worker.run_once()
        _print(out.model_dump(mode="json"), True)
    finally:
        app.close()


@cli.command("event")
def event(event_type: str, payload: str = "{}", home: HomeOpt = None) -> None:
    """Publier un événement (mode continu : les règles réagissent)."""

    app = _app(home)
    try:
        published = app.kernel.events.publish(event_type, json.loads(payload), source="cli")
        processed = app.worker.run_once()
        _print({"event": published.id if published else None, "tasks_processed": processed}, True)
    finally:
        app.close()


# -- Automatisation -----------------------------------------------------------------------------------
@cli.command("cycle")
def cycle(
    name: Annotated[str, typer.Argument(help="daily | weekly | monthly")],
    force: bool = False,
    home: HomeOpt = None,
) -> None:
    """Exécuter un cycle proactif maintenant (idempotent par période)."""

    app = _app(home)
    try:
        _print(app.cycles.run(name, force=force), True)
        app.worker.run_once()
    finally:
        app.close()


@cli.command("tick")
def tick(home: HomeOpt = None, run: bool = True) -> None:
    """Planificateur : enfile les cycles dus puis traite la file (pour cron / GitHub Actions)."""

    app = _app(home)
    try:
        enqueued = app.scheduler.tick()
        processed = app.worker.run_once() if run else 0
        _print({"enqueued": enqueued, "processed": processed, "queue": app.queue.stats()}, True)
    finally:
        app.close()


@cli.command("worker")
def worker(once: bool = False, home: HomeOpt = None) -> None:
    """Traiter la file de tâches durables (en continu, ou une passe avec --once)."""

    app = _app(home)
    try:
        if once:
            _print({"processed": app.worker.run_once()}, True)
            return
        stop = threading.Event()
        console.print("Worker démarré (Ctrl+C pour arrêter).")
        try:
            app.worker.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
    finally:
        app.close()


@cli.command("serve")
def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    home: HomeOpt = None,
    background: Annotated[bool, typer.Option(help="Planificateur + worker intégrés")] = True,
) -> None:
    """API REST + MCP HTTP (/mcp) + planificateur + worker dans un seul processus."""

    import uvicorn

    from sovereign.interfaces.api import create_api

    app = _app(home)
    if app.kernel.settings.security.require_api_key and not app.kernel.api_keys.any_active():
        console.print(
            "[yellow]Aucune clé d'API : créez-en une avec `sovereign apikey create admin --scope admin`.[/yellow]"
        )
    stop = threading.Event()
    threads = []
    if background:
        threads = [
            threading.Thread(target=app.scheduler.run_forever, args=(stop,), daemon=True),
            threading.Thread(target=app.worker.run_forever, args=(stop,), daemon=True),
        ]
        for thread in threads:
            thread.start()
    try:
        uvicorn.run(create_api(app, mcp_host=host), host=host, port=port, log_level="info")
    finally:
        stop.set()
        app.close()


@cli.command("mcp")
def mcp(home: HomeOpt = None) -> None:
    """Serveur MCP sur stdio (à déclarer dans Claude Code / Claude Desktop)."""

    from sovereign.interfaces.mcp_server import build_mcp

    app = _app(home)
    try:
        build_mcp(app).run("stdio")
    finally:
        app.close()


eco_cli = typer.Typer(help="Écosystème de dépôts de code (ex. comptes GitHub).", no_args_is_help=True)
cli.add_typer(eco_cli, name="ecosystem")


@eco_cli.command("scan")
def ecosystem_scan(
    root: Annotated[Path, typer.Argument(help="Dossier contenant les dépôts clonés")],
    include: Annotated[list[Path] | None, typer.Option("--include", help="Dépôt supplémentaire")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Dépôt à ignorer")] = None,
    tests: Annotated[Path | None, typer.Option(help="JSON {dépôt: {passed, failed}} (CI ou audit)")] = None,
    home: HomeOpt = None,
) -> None:
    """Observer chaque dépôt (lecture seule, aucun code exécuté) et l'enregistrer pour les Skills."""

    from sovereign.ecosystem import load_test_results, scan_ecosystem

    app = _app(home)
    try:
        result = scan_ecosystem(
            app,
            root,
            tests=load_test_results(tests) if tests else None,
            exclude=tuple(exclude or ()),
            include=tuple(include or ()),
        )
        app.worker.run_once()
        _print(result, True)
    finally:
        app.close()


@eco_cli.command("assess")
def ecosystem_assess(
    files: Annotated[list[Path], typer.Argument(help="JSON : liste d'évaluations {repo, commercial_potential, …}")],
    analyst: Annotated[str, typer.Option(help="Auteur des évaluations")] = "ia",
    home: HomeOpt = None,
) -> None:
    """Joindre des évaluations d'analystes (IA ou humains) aux dépôts observés, sans écraser les faits."""

    from sovereign.ecosystem import apply_assessments

    app = _app(home)
    try:
        items: list[dict[str, Any]] = []
        for file in files:
            data = json.loads(file.read_text(encoding="utf-8"))
            items.extend(data if isinstance(data, list) else [data])
        _print(apply_assessments(app, items, analyst=analyst), True)
    finally:
        app.close()


key_cli = typer.Typer(help="Clés d'API à portées (read, write, admin, webhook).", no_args_is_help=True)
cli.add_typer(key_cli, name="apikey")


@key_cli.command("create")
def key_create(
    name: str,
    scope: Annotated[list[str], typer.Option("--scope")] = ["read"],  # noqa: B006
    home: HomeOpt = None,
) -> None:
    """Créer une clé (affichée UNE seule fois)."""

    app = _app(home)
    try:
        key_id, raw = app.kernel.api_keys.create(name, scope)
        console.print(f"Clé {key_id} ({', '.join(scope)}) — conservez-la, elle ne sera plus affichée :\n{raw}")
    finally:
        app.close()


@key_cli.command("list")
def key_list(home: HomeOpt = None) -> None:
    """Lister les clés (sans leur valeur)."""

    app = _app(home)
    try:
        _print(app.kernel.api_keys.list(), True)
    finally:
        app.close()


@key_cli.command("revoke")
def key_revoke(key_id: str, home: HomeOpt = None) -> None:
    """Révoquer une clé."""

    app = _app(home)
    try:
        _print({"revoked": app.kernel.api_keys.revoke(key_id)}, True)
    finally:
        app.close()


@cli.command("version")
def version() -> None:
    """Version."""

    console.print(f"SOVEREIGN {sovereign.__version__} — {sovereign.MOTTO}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
