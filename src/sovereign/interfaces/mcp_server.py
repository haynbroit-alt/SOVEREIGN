"""Serveur MCP (Model Context Protocol, SDK officiel ``mcp`` 2.x, spécification 2026-07-28).

Expose SOVEREIGN à n'importe quel client MCP (Claude Code, Claude Desktop, autres agents) :
- **outils** : les 15 commandes, l'exécution d'une action de Skill, les écritures financières et mémorielles ;
- **ressources** : catalogue des Skills, SKILL.md, état, dernier rapport, décisions récentes ;
- **prompts** : briefing du directeur, réunion du conseil, revue hebdomadaire (slash-commands côté client).

Sécurité : AUCUN outil MCP ne peut approuver une validation humaine ni trancher une dépense en revue — un agent
IA ne doit jamais valider ses propres propositions. Ces décisions passent par la CLI ou l'API avec une clé admin.
Transports : stdio (``sovereign mcp``) et HTTP Streamable monté sur ``/mcp`` par ``sovereign serve`` (clé d'API).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

import sovereign

if TYPE_CHECKING:
    from sovereign.app import Sovereign

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

INSTRUCTIONS = (
    "SOVEREIGN est un directeur général IA (business, marketing, stratégie, management, coordination d'IA). "
    "Commencez par `status` ou `director`. Les montants sont en chaînes décimales. SOVEREIGN n'a aucun accès "
    "bancaire : il autorise ou refuse des dépenses dans la limite de 10 % des revenus encaissés ; les "
    "validations humaines se font hors MCP."
)


def build_mcp(app: Sovereign) -> MCPServer:
    server: MCPServer = MCPServer(
        name="sovereign",
        title="SOVEREIGN — Autonomous Business Intelligence Director",
        description=sovereign.MOTTO,
        instructions=INSTRUCTIONS,
        version=sovereign.__version__,
    )

    def command(name: str, args: str = "", **options: Any) -> str:
        result = app.commands.execute(name, args, source="mcp", **{k: v for k, v in options.items() if v})
        return result.markdown()

    # -- les 15 commandes -------------------------------------------------------------------------
    @server.tool(
        name="director",
        annotations=WRITE,
        description="Consulter le directeur : boucle cognitive complète sur une question ou une mission. "
        "dry_run=true pour ne rien exécuter.",
    )
    def director(question: str = "", deep: bool = False, dry_run: bool = False) -> str:
        return command("director", question, deep=deep, dry_run=dry_run)

    @server.tool(name="strategy", annotations=READ, description="Stratégie actuelle : posture, objectifs, axes.")
    def strategy() -> str:
        return command("strategy")

    @server.tool(name="business", annotations=READ, description="Analyse commerciale (marchés, modèles, finances).")
    def business(focus: str = "") -> str:
        return command("business", focus)

    @server.tool(name="marketing", annotations=READ, description="Analyse marketing (segments, campagnes, tendances).")
    def marketing() -> str:
        return command("marketing")

    @server.tool(
        name="council",
        annotations=READ,
        description="Réunion du conseil des IA sur une proposition (texte, rec_… ou apr_…). Montants en "
        "chaînes décimales.",
    )
    def council(
        proposal: str,
        cost: str = "",
        value: str = "",
        confidence: float = 0.5,
        impact: float = 6,
        effort: float = 5,
        risk: float = 0.3,
        irreversible: bool = False,
        external: bool = False,
    ) -> str:
        options: dict[str, Any] = {
            "confidence": confidence,
            "impact": impact,
            "effort": effort,
            "risk": risk,
            "irreversible": irreversible,
            "external": external,
        }
        if cost:
            options["cost"] = cost
        if value:
            options["value"] = value
        return command("council", proposal, **options)

    @server.tool(name="opportunities", annotations=WRITE, description="Détecter et classer les opportunités.")
    def opportunities() -> str:
        return command("opportunities")

    @server.tool(name="projects", annotations=READ, description="Analyser les projets existants.")
    def projects() -> str:
        return command("projects")

    @server.tool(name="audit", annotations=READ, description="Audit général (auto-audit, finances, intégrité).")
    def audit() -> str:
        return command("audit")

    @server.tool(name="budget", annotations=READ, description="Budget disponible (règle des 10 %) et prévision.")
    def budget(months: int = 6) -> str:
        return command("budget", months=months)

    @server.tool(name="skills", annotations=READ, description="Les dix compétences et leur utilité mesurée.")
    def skills(name: str = "") -> str:
        return command("skills", name)

    @server.tool(name="memory", annotations=READ, description="Décisions mémorisées (recherche plein texte).")
    def memory(query: str = "") -> str:
        return command("memory", query)

    @server.tool(name="roadmap", annotations=READ, description="Objectifs, jalons et échéances à venir.")
    def roadmap(days: int = 90) -> str:
        return command("roadmap", days=days)

    @server.tool(name="status", annotations=READ, description="État réel du système.")
    def status() -> str:
        return command("status")

    @server.tool(name="improve", annotations=READ, description="Améliorations nécessaires (projets et système).")
    def improve() -> str:
        return command("improve")

    @server.tool(name="report", annotations=WRITE, description="Générer un rapport de direction persisté.")
    def report(kind: str = "executive") -> str:
        return command("report", kind)

    # -- Skills et écritures ------------------------------------------------------------------------
    @server.tool(
        name="skill_run",
        annotations=WRITE,
        description="Exécuter une action d'une Skill avec une entrée JSON (schémas : ressource "
        "sovereign://skills/{name}).",
    )
    def skill_run(skill: str, action: str, input: dict[str, Any] | None = None) -> dict[str, Any]:
        out = app.kernel.run_skill(skill, action, input or {}, caller="mcp")
        return out.model_dump(mode="json")

    @server.tool(
        name="record_revenue",
        annotations=WRITE,
        description="Enregistrer un encaissement RÉEL (argent reçu). Idempotent sur reference.",
    )
    def record_revenue(amount: str, counterparty: str = "", reference: str = "", date: str = "") -> dict[str, Any]:
        entry = app.kernel.finance.record_collection(
            amount,
            date or None,
            counterparty=counterparty or None,
            reference=reference or None,
            source="mcp",
            actor="mcp",
        )
        return entry.model_dump(mode="json")

    @server.tool(
        name="request_expense",
        annotations=WRITE,
        description="Demander une dépense : approuvée, refusée ou en revue humaine. N'effectue aucun paiement.",
    )
    def request_expense(amount: str, category: str, vendor: str = "", description: str = "") -> dict[str, Any]:
        decision = app.kernel.finance.request_expense(
            amount, category, vendor=vendor or None, description=description or None, requested_by="mcp"
        )
        return decision.model_dump(mode="json")

    @server.tool(name="record_decision", annotations=WRITE, description="Mémoriser une décision importante.")
    def record_decision(
        title: str,
        decision: str,
        rationale: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        review_in_days: int | None = None,
    ) -> dict[str, Any]:
        out = app.kernel.run_skill(
            "memory-learning",
            "record_decision",
            {
                "title": title,
                "decision": decision,
                "rationale": rationale,
                "category": category,
                "tags": tags or [],
                "review_in_days": review_in_days,
            },
            caller="mcp",
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="pending_approvals",
        annotations=READ,
        description="Lister les validations humaines en attente (lecture seule : l'approbation est "
        "réservée aux humains, via CLI ou API admin).",
    )
    def pending_approvals() -> list[dict[str, Any]]:
        return [a.model_dump(mode="json") for a in app.approvals.list("pending")]

    # -- ressources ---------------------------------------------------------------------------------
    @server.resource(
        "sovereign://skills",
        name="skills",
        mime_type="application/json",
        description="Catalogue des Skills (front-matters) et utilité mesurée",
    )
    def skills_catalog() -> str:
        registry = app.kernel.registry
        utility = app.kernel.skill_utility()
        return json.dumps(
            {
                n: {
                    "description": registry.manifest(n).description,
                    "actions": registry.manifest(n).actions,
                    "utility": utility[n],
                }
                for n in registry.names
            },
            ensure_ascii=False,
            default=str,
        )

    @server.resource(
        "sovereign://skills/{name}",
        name="skill",
        mime_type="text/markdown",
        description="SKILL.md d'une Skill et schémas JSON de ses actions",
    )
    def skill_doc(name: str) -> str:
        manifest = app.kernel.registry.manifest(name)
        skill = app.kernel.registry.get(name)
        schemas = json.dumps({a: s.json_schema() for a, s in skill.actions.items()}, ensure_ascii=False, indent=1)
        return manifest.skill_md.read_text(encoding="utf-8") + f"\n\n## Schémas JSON\n\n```json\n{schemas}\n```\n"

    @server.resource(
        "sovereign://status", name="status", mime_type="application/json", description="État réel du système"
    )
    def status_resource() -> str:
        return json.dumps(app.commands.execute("status", source="mcp").data, ensure_ascii=False, default=str)

    @server.resource(
        "sovereign://reports/latest",
        name="latest-report",
        mime_type="text/markdown",
        description="Dernier rapport produit",
    )
    def latest_report() -> str:
        row = app.kernel.db.one("SELECT content_md FROM reports ORDER BY created_at DESC, rowid DESC LIMIT 1")
        return row["content_md"] if row else "_Aucun rapport._"

    @server.resource(
        "sovereign://memory/recent",
        name="recent-decisions",
        mime_type="application/json",
        description="Décisions récentes",
    )
    def recent_decisions() -> str:
        return json.dumps([d.model_dump(mode="json") for d in app.kernel.memory.list(limit=30)], ensure_ascii=False)

    # -- prompts ------------------------------------------------------------------------------------
    @server.prompt(
        name="director_briefing",
        title="Briefing du directeur",
        description="Point de situation et décisions de la journée",
    )
    def director_briefing() -> str:
        return (
            "Appelle l'outil `status`, puis `director` (question : « Que dois-je savoir et décider "
            "aujourd'hui ? »). Résume en français : situation, 3 priorités chiffrées, décisions en attente de "
            "validation humaine, risques. Ne présente aucune estimation comme un fait."
        )

    @server.prompt(
        name="council_meeting",
        title="Réunion du conseil",
        description="Soumettre une proposition au conseil des IA",
    )
    def council_meeting(proposal: str) -> str:
        return (
            f"Soumets la proposition suivante au conseil avec l'outil `council` : « {proposal} ». Si un coût "
            "ou une valeur sont connus, renseigne-les. Restitue les positions, les vetos, la simulation et "
            "le verdict, puis indique ce qui manquerait pour changer la décision."
        )

    @server.prompt(
        name="weekly_review",
        title="Revue hebdomadaire",
        description="Évaluer les résultats et réviser la stratégie",
    )
    def weekly_review() -> str:
        return (
            "Appelle `memory`, `projects`, `budget` puis `director` avec deep=true et la question « Revue "
            "hebdomadaire : qu'avons-nous appris et que faut-il changer ? ». Synthétise les résultats mesurés, "
            "les erreurs répétées et les ajustements de stratégie."
        )

    return server
