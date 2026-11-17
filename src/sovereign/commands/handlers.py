"""Les 15 commandes, chacune reliée aux modules qui la servent réellement.

| Commande          | Modules                                                                          |
|-------------------|----------------------------------------------------------------------------------|
| /director         | boucle cognitive complète (Skills sélectionnées, conseil, simulation, exécution) |
| /strategy         | finance, mémoire (stratégies apprises), modèle du monde, objectifs, BI, bizdev   |
| /business         | business-intelligence, business-development, finance                            |
| /marketing        | marketing-strategy                                                               |
| /council          | conseil des IA + simulation Monte-Carlo                                          |
| /opportunities    | business-intelligence → business-development → innovation-creativity            |
| /projects         | analysis-optimization, intelligent-management                                    |
| /audit            | dreaming du directeur, audit_system, finances, file de tâches, validations       |
| /budget           | finance-budget (statut, prévision, dépenses)                                     |
| /skills           | registre + utilité mesurée                                                       |
| /memory           | memory-learning (recherche, leçons, adaptation)                                  |
| /roadmap          | objectifs, jalons, décisions à revoir, missions, validations                     |
| /status           | état réel : base, tâches, cycles, LLM, budget, sécurité                          |
| /improve          | analysis-optimization + auto-audit                                               |
| /report           | rapport de direction Markdown persisté                                           |
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sovereign
from sovereign.brain.council import CouncilFacts
from sovereign.brain.prioritize import prioritize
from sovereign.brain.simulation import simulate
from sovereign.commands.render import (
    insights_md,
    money,
    priorities_md,
    render_director,
    skill_output_md,
    table,
)
from sovereign.commands.router import CommandResult, CommandSpec, Section
from sovereign.core.clock import iso, parse_dt
from sovereign.core.db import dumps
from sovereign.core.ids import new_id
from sovereign.skills.base import Recommendation, SkillOutput

if TYPE_CHECKING:
    from sovereign.app import Sovereign


# -- utilitaires ---------------------------------------------------------------------------------
def _flag(options: dict[str, Any], key: str) -> bool:
    value = options.get(key, False)
    return value is True or str(value).lower() in ("1", "true", "yes", "oui")


def _run(
    app: Sovereign,
    skill: str,
    action: str,
    payload: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    caller: str = "command",
) -> SkillOutput | None:
    if skill not in app.kernel.registry.names:
        return None
    try:
        return app.kernel.run_skill(skill, action, payload or {}, caller=caller)
    except Exception as exc:
        if errors is not None:
            errors.append(f"{skill}.{action} : {type(exc).__name__}: {exc}")
        return None


def _analysis(
    app: Sovereign,
    command: str,
    title: str,
    plan: list[tuple[str, str, dict[str, Any] | None]],
    empty_hint: str,
) -> CommandResult:
    """Exécute un plan d'actions de Skills, consolide constats et recommandations (sans exécuter de décision)."""

    errors: list[str] = []
    sections: list[Section] = []
    outputs: dict[str, SkillOutput] = {}
    for skill, action, payload in plan:
        out = _run(app, skill, action, payload, errors)
        if out is None:
            continue
        outputs[f"{skill}.{action}"] = out
        sections.append(Section(title=f"{skill} · {action}", body=skill_output_md(skill, out)))
    recs = [r for out in outputs.values() for r in out.recommendations]
    priorities = prioritize(recs)
    if priorities:
        sections.insert(0, Section(title="Priorités consolidées", body=priorities_md(priorities)))
    if errors:
        sections.append(Section(title="Erreurs", body="\n".join(f"- {e}" for e in errors)))
    has_content = any(o.insights or o.recommendations or o.metrics for o in outputs.values())
    summary = (
        f"{len(outputs)} analyse(s), {sum(len(o.insights) for o in outputs.values())} constat(s), "
        f"{len(priorities)} recommandation(s)."
        if has_content
        else empty_hint
    )
    if priorities:
        summary += f" Priorité n°1 : {priorities[0].recommendation.title}."
    return CommandResult(
        command=command,
        title=title,
        summary=summary,
        sections=sections,
        data={
            "outputs": {k: v.model_dump(mode="json") for k, v in outputs.items()},
            "priorities": [p.model_dump(mode="json") for p in priorities],
            "errors": errors,
        },
        ok=not errors or bool(outputs),
    )


# -- /director ------------------------------------------------------------------------------------
def cmd_director(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    mission = args or "Analyse de situation : que dois-je savoir et décider maintenant ?"
    report = app.director.run(
        mission,
        deep=_flag(options, "deep"),
        execute=not _flag(options, "dry_run"),
        use_llm=_flag(options, "llm"),
        decide_top=int(options.get("top", 3)),
    )
    return CommandResult(
        command="director",
        title=f"Directeur — {mission}",
        summary=report.summary,
        sections=[Section(title="Rapport du directeur", body=render_director(report, header=False))],
        data=report.model_dump(mode="json"),
    )


# -- /strategy ------------------------------------------------------------------------------------
def cmd_strategy(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    kernel = app.kernel
    status = kernel.finance.status()
    finance_out = _run(app, "finance-budget", "assess")
    trend = finance_out.metrics.get("finance.collections_trend") if finance_out else None
    if status.overdrawn or (trend is not None and trend < -0.05):
        posture, why = (
            "Redressement",
            "encaissements en baisse ou enveloppe dépassée : protéger la trésorerie.",
        )
    elif status.envelope > 0 and status.utilization >= 0.8:
        posture, why = "Consolidation", "budget presque consommé : optimiser avant d'investir."
    elif trend is not None and trend > 0.05:
        posture, why = "Croissance", "encaissements en hausse : réinvestir dans ce qui fonctionne."
    elif status.collected_total == 0:
        posture, why = "Amorçage", "aucun encaissement : priorité absolue au premier revenu réel."
    else:
        posture, why = "Développement maîtrisé", "situation stable : tester et apprendre à petit budget."
    sections = [
        Section(
            title="Posture stratégique",
            body=f"**{posture}** — {why}\n\n" + (finance_out.summary if finance_out else ""),
        )
    ]
    objectives = kernel.store.list("objective")
    if objectives:
        sections.append(
            Section(
                title="Objectifs",
                body=table(
                    objectives,
                    [
                        ("title", "Objectif"),
                        ("metric", "Métrique"),
                        ("target", "Cible"),
                        ("deadline", "Échéance"),
                        ("status", "Statut"),
                    ],
                ),
            )
        )
    adapt = _run(app, "memory-learning", "adapt_strategies", {"min_samples": 2})
    strategies = getattr(adapt, "strategies", None) if adapt else None
    if strategies:
        sections.append(
            Section(
                title="Ce que l'expérience enseigne",
                body="\n".join(f"- **{s.group}** : {s.advice} — {s.reason}" for s in strategies),
            )
        )
    beliefs = [b for b in app.world.list() if b.confidence >= 0.7]
    if beliefs:
        sections.append(
            Section(
                title="Convictions fortes (modèle du monde)",
                body="\n".join(f"- {b.statement} ({b.confidence:.0%})" for b in beliefs[:10]),
            )
        )
    plan: list[tuple[str, str, dict[str, Any] | None]] = [
        (s, "assess", None)
        for s in ("business-intelligence", "business-development", "marketing-strategy")
        if s in kernel.registry.names and kernel.available_data().intersection(kernel.registry.manifest(s).data)
    ]
    analysis = _analysis(app, "strategy", "Analyse", plan, "")
    priorities = analysis.data["priorities"]
    if priorities:
        sections.append(Section(title="Axes stratégiques prioritaires", body=analysis.sections[0].body))
    summary = f"Posture : {posture}. " + (
        f"Axe n°1 : {priorities[0]['recommendation']['title']}."
        if priorities
        else "Importez marchés, segments et modèles économiques pour des axes chiffrés."
    )
    return CommandResult(
        command="strategy",
        title="Stratégie actuelle",
        summary=summary,
        sections=sections,
        data={
            "posture": posture,
            "reason": why,
            "priorities": priorities,
            "finance": status.model_dump(mode="json"),
        },
    )


# -- /business, /marketing, /projects ---------------------------------------------------------------
def cmd_business(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    plan: list[tuple[str, str, dict[str, Any] | None]] = [
        ("business-intelligence", "assess", None),
        ("business-development", "assess", None),
        ("finance-budget", "assess", None),
    ]
    if app.kernel.store.count("comparison") or app.kernel.store.count("negotiation"):
        plan.append(("negotiation-strategy", "assess", None))
    return _analysis(
        app,
        "business",
        "Analyse commerciale" + (f" — {args}" if args else ""),
        plan,
        "Aucune donnée commerciale : importez marchés, concurrents, besoins et modèles économiques "
        "(`sovereign data import`).",
    )


def cmd_marketing(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    plan: list[tuple[str, str, dict[str, Any] | None]] = [("marketing-strategy", "assess", None)]
    if app.kernel.store.count("segment"):
        plan.append(("marketing-strategy", "design_strategy", {}))
    return _analysis(
        app,
        "marketing",
        "Analyse marketing",
        plan,
        "Aucune donnée marketing : importez segments, campagnes et séries de tendances.",
    )


def cmd_projects(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    plan: list[tuple[str, str, dict[str, Any] | None]] = [
        ("analysis-optimization", "analyze_projects", {}),
        ("analysis-optimization", "detect_unused_features", {}),
        ("intelligent-management", "assess", None),
    ]
    return _analysis(
        app,
        "projects",
        "Analyse des projets",
        plan,
        "Aucun projet : importez des projets avec leurs KPI et l'usage de leurs fonctionnalités.",
    )


# -- /council -------------------------------------------------------------------------------------
def _recommendation_from(app: Sovereign, args: str, options: dict[str, Any]) -> Recommendation:
    ref = args.strip()
    if ref.startswith("apr_"):
        return Recommendation.model_validate(app.approvals.get(ref).payload["recommendation"])
    if ref.startswith("rec_"):
        stored = app.kernel.store.get("recommendation", ref)
        if stored is None:
            raise ValueError(f"recommandation inconnue : {ref}")
        return Recommendation.model_validate(stored)
    if not ref:
        raise ValueError("précisez une proposition, un identifiant rec_… ou apr_…")

    def num(key: str, default: float) -> float:
        return float(options.get(key, default))

    return Recommendation(
        title=ref,
        rationale=str(options.get("why", "Proposition soumise au conseil.")),
        skill="council",
        impact=num("impact", 6),
        effort=max(0.5, num("effort", 5)),
        confidence=num("confidence", 0.5),
        urgency=num("urgency", 5),
        risk=num("risk", 0.3),
        estimated_value=Decimal(str(options["value"])) if "value" in options else None,
        estimated_cost=Decimal(str(options["cost"])) if "cost" in options else None,
        reversible=not _flag(options, "irreversible"),
        external=_flag(options, "external"),
        tags=[t for t in str(options.get("tags", "")).split(",") if t],
    ).finalize("council")


def _council_on_skill(app: Sovereign, ref: str) -> CommandResult | None:
    """Comparaison ou négociation enregistrée → Skill 6 (negotiation-strategy) : les IA confrontent les options."""

    kernel = app.kernel
    if kernel.store.get("comparison", ref) is not None:
        out = _run(app, "negotiation-strategy", "recommend", {"comparison_id": ref})
        title = f"Conseil — comparaison {ref}"
    elif kernel.store.get("negotiation", ref) is not None:
        out = _run(app, "negotiation-strategy", "prepare_negotiation", {"negotiation_id": ref})
        title = f"Conseil — préparation de la négociation {ref}"
    else:
        return None
    if out is None:
        raise ValueError(f"analyse impossible pour {ref}")
    return CommandResult(
        command="council",
        title=title,
        summary=out.summary,
        sections=[Section(title="Analyse et confrontation des options", body=skill_output_md("negotiation", out))],
        data={"negotiation": out.model_dump(mode="json")},
    )


def cmd_council(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    on_skill = _council_on_skill(app, args.strip()) if args.strip() else None
    if on_skill is not None:
        return on_skill
    rec = _recommendation_from(app, args, options)
    available = app.kernel.finance.available()
    quick = simulate(rec, available_budget=available, runs=2000)
    facts: CouncilFacts = app.director._facts(rec, quick)
    verdict = app.council_for(_flag(options, "llm")).deliberate(rec, facts, use_llm=_flag(options, "llm"))
    positions = "\n".join(
        f"- **{p.member}** ({p.role}) — {p.stance} ({p.score:+.2f})"
        + (f" — VETO : {p.veto}" if p.veto else "")
        + "\n"
        + "\n".join(f"  - {'➕' if a.polarity == 'pour' else '➖'} {a.text}" for a in p.arguments)
        + (f"\n  - ↩ {p.rebuttal}" if p.rebuttal else "")
        for p in verdict.positions
    )
    sim = (
        f"Unité : {quick.unit} · P(succès) {quick.p_success:.0%} · espérance {quick.expected_value:g} · "
        f"P(perte) {quick.p_loss:.0%} · P5 {quick.p5:g} / P50 {quick.p50:g} / P95 {quick.p95:g} · "
        f"risque **{quick.risk_class}**" + (f" ({'; '.join(quick.reasons)})" if quick.reasons else "")
    )
    sections = [
        Section(title="Positions du conseil", body=positions),
        Section(title="Simulation (bac à sable)", body=sim),
        Section(title="Procès-verbal", body="\n".join(f"{i + 1}. {m}" for i, m in enumerate(verdict.minutes))),
    ]
    if verdict.conditions:
        sections.append(
            Section(title="Conditions / points à lever", body="\n".join(f"- {c}" for c in verdict.conditions))
        )
    recorded = None
    if _flag(options, "record"):
        recorded = app.kernel.memory.record(
            title=f"Conseil : {rec.title}",
            decision=verdict.verdict.replace("_", " "),
            rationale=verdict.minutes[-1],
            category=(rec.tags or ["conseil"])[0],
            tags=rec.tags,
            skills=[rec.skill],
            confidence=verdict.confidence,
            source="council",
        ).id
    summary = (
        f"Verdict : {verdict.verdict.replace('_', ' ')} (score {verdict.score:+.2f}, confiance "
        f"{verdict.confidence:.0%})"
        + (f" — veto : {'; '.join(verdict.vetoes)}" if verdict.vetoes else "")
        + (" — enrichi par IA." if verdict.llm_enriched else ".")
    )
    return CommandResult(
        command="council",
        title=f"Conseil des IA — {rec.title}",
        summary=summary,
        sections=sections,
        data={
            "verdict": verdict.model_dump(mode="json"),
            "simulation": quick.model_dump(mode="json"),
            "decision_id": recorded,
        },
    )


# -- /opportunities -------------------------------------------------------------------------------
def cmd_opportunities(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    kernel = app.kernel
    plan: list[tuple[str, str, dict[str, Any] | None]] = []
    if kernel.store.count("need") and kernel.store.count("market"):
        plan.append(("business-intelligence", "detect_opportunities", {}))
    else:
        plan.append(("business-intelligence", "assess", None))
    plan.append(("business-development", "rank_opportunities", {}))
    if kernel.store.count("segment") and kernel.store.count("need"):
        plan.append(("innovation-creativity", "find_white_spaces", {}))
    if kernel.store.count("idea"):
        plan.append(("innovation-creativity", "evaluate_feasibility", {}))
    result = _analysis(
        app,
        "opportunities",
        "Opportunités",
        plan,
        "Aucune opportunité détectable : importez marchés, besoins (importance/satisfaction) et segments.",
    )
    stored = kernel.store.list("opportunity")
    if stored:
        rows = sorted(stored, key=lambda o: -float(o.get("score", 0) or 0))[:15]
        result.sections.insert(
            0,
            Section(
                title="Opportunités enregistrées",
                body=table(
                    [{**o, "potential_value": money(Decimal(str(o.get("potential_value", 0))))} for o in rows],
                    [
                        ("title", "Opportunité"),
                        ("potential_value", "Potentiel annuel"),
                        ("confidence", "Confiance"),
                        ("score", "Score"),
                        ("status", "Statut"),
                    ],
                ),
            ),
        )
        result.data["opportunities"] = stored
    return result


# -- /audit ---------------------------------------------------------------------------------------
def cmd_audit(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    kernel = app.kernel
    findings = app.director.dream()
    status = kernel.finance.status()
    sections = [
        Section(
            title="Auto-audit (dreaming)",
            body="\n".join(f"- {f}" for f in findings) or "_Aucun point d'attention._",
        )
    ]
    sections.append(
        Section(title="Finances", body="\n".join(f"- {a}" for a in status.alerts) or "- Aucune alerte budgétaire.")
    )
    system = _run(app, "analysis-optimization", "audit_system", {})
    if system:
        sections.append(Section(title="Utilité réelle de SOVEREIGN", body=skill_output_md("analysis", system)))
    integrity = kernel.db.scalar("PRAGMA quick_check")
    queue = app.queue.stats()
    audit_rows = kernel.db.query("SELECT at, actor, action, entity_id FROM audit_log ORDER BY seq DESC LIMIT 10")
    sections.append(
        Section(
            title="Intégrité et traçabilité",
            body=(
                f"- Base : `{integrity}`\n- Tâches : {queue}\n- Validations en attente : {len(app.approvals.list())}\n"
                "- Dernières opérations auditées :\n"
                + "\n".join(f"  - {r['at']} · {r['actor']} · {r['action']} · {r['entity_id']}" for r in audit_rows)
            ),
        )
    )
    critical = len(findings) + len(status.alerts) + (len(system.recommendations) if system else 0)
    return CommandResult(
        command="audit",
        title="Audit général",
        sections=sections,
        summary=f"{critical} point(s) d'attention ; intégrité base : {integrity}.",
        data={
            "findings": findings,
            "finance_alerts": status.alerts,
            "queue": queue,
            "integrity": integrity,
            "system": system.model_dump(mode="json") if system else None,
        },
    )


# -- /budget --------------------------------------------------------------------------------------
def cmd_budget(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    finance = app.kernel.finance
    status = finance.status()
    cur = status.currency
    forecast = _run(app, "finance-budget", "forecast", {"months": int(options.get("months", 6))})
    lines = [
        f"| Encaissé (net) | {money(status.collected_total, cur)} |",
        f"| Enveloppe autorisée ({status.spend_ratio:.0%}) | {money(status.envelope, cur)} |",
        f"| Engagé (approuvé, non réglé) | {money(status.committed, cur)} |",
        f"| Dépensé | {money(status.spent, cur)} |",
        f"| **Disponible** | **{money(status.available, cur)}** |",
        f"| Utilisation | {status.utilization:.0%} |",
        f"| Créances (non disponibles) | {money(status.receivables, cur)} ({status.receivables_count}) |",
    ]
    sections = [Section(title="Budget", body="| Poste | Montant |\n|---|---|\n" + "\n".join(lines))]
    if status.alerts:
        sections.append(Section(title="Alertes", body="\n".join(f"- {a}" for a in status.alerts)))
    expenses = finance.expenses(limit=15)
    if expenses:
        sections.append(
            Section(
                title="Dernières dépenses",
                body=table(
                    [
                        {
                            "id": e.id,
                            "amount": money(e.actual or e.amount, cur),
                            "category": e.category,
                            "vendor": e.vendor,
                            "status": e.status,
                            "reason": e.decision_reason,
                        }
                        for e in expenses
                    ],
                    [
                        ("id", "Id"),
                        ("amount", "Montant"),
                        ("category", "Catégorie"),
                        ("vendor", "Fournisseur"),
                        ("status", "Statut"),
                        ("reason", "Motif"),
                    ],
                ),
            )
        )
    if forecast is not None:
        months = forecast.months  # type: ignore[attr-defined]
        sections.append(
            Section(
                title="Prévision",
                body=forecast.summary
                + "\n\n"
                + table(
                    [
                        {
                            "month": m.month,
                            "in": money(m.projected_collections, cur),
                            "env": money(m.projected_envelope_increase, cur),
                            "costs": money(m.projected_costs, cur),
                            "end": money(m.projected_available_end, cur),
                        }
                        for m in months
                    ],
                    [
                        ("month", "Mois"),
                        ("in", "Encaissements prévus"),
                        ("env", "Enveloppe +"),
                        ("costs", "Coûts"),
                        ("end", "Disponible fin de mois"),
                    ],
                ),
            )
        )
    sections.append(
        Section(
            title="Règle",
            body=(
                "SOVEREIGN n'a **aucun accès bancaire**. Il autorise ou refuse ; l'humain paie. "
                "Enveloppe = 10 % des encaissements nets réellement reçus."
            ),
        )
    )
    return CommandResult(
        command="budget",
        title="Budget disponible",
        summary=f"Disponible : {money(status.available, cur)} sur une enveloppe de "
        f"{money(status.envelope, cur)} ({status.utilization:.0%} utilisé).",
        sections=sections,
        data={
            "status": status.model_dump(mode="json"),
            "forecast": forecast.model_dump(mode="json") if forecast else None,
        },
    )


# -- /skills --------------------------------------------------------------------------------------
def cmd_skills(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    registry = app.kernel.registry
    utility = app.kernel.skill_utility()
    if args:
        manifest = registry.manifest(args.strip())
        skill = registry.get(manifest.name)
        actions = "\n".join(f"- `{a.name}` ({a.risk}) — {a.summary}" for a in skill.actions.values())
        return CommandResult(
            command="skills",
            title=f"Skill {manifest.name}",
            summary=manifest.description,
            sections=[
                Section(title="Actions", body=actions),
                Section(title="Utilité mesurée", body=dumps(utility[manifest.name])),
                Section(title="SKILL.md", body=manifest.body),
            ],
            data={
                "manifest": {
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "actions": manifest.actions,
                    "keywords": manifest.keywords,
                    "events": manifest.events,
                },
                "schemas": {a.name: a.json_schema() for a in skill.actions.values()},
                "utility": utility[manifest.name],
            },
        )
    rows = []
    for name in registry.names:
        m = registry.manifest(name)
        u = utility[name]
        rows.append(
            {
                "name": f"`{name}`",
                "version": m.version,
                "actions": len(m.actions),
                "runs": u["runs"],
                "reliability": f"{u['reliability']:.0%}" if u["reliability"] is not None else "—",
                "adoption": f"{u['adoption_rate']:.0%}" if u["adoption_rate"] is not None else "—",
                "success": f"{u['success_posterior']:.0%}" if u["succeeded"] + u["failed"] else "—",
                "loaded": "oui" if name in registry.loaded() else "non",
            }
        )
    descriptions = "\n".join(f"- **{n}** — {registry.manifest(n).description}" for n in registry.names)
    return CommandResult(
        command="skills",
        title="Les compétences du directeur",
        summary=f"{len(registry.names)} Skill(s) découverte(s), {len(registry.loaded())} chargée(s) en mémoire "
        "(chargement à la demande).",
        sections=[
            Section(
                title="Utilité mesurée",
                body=table(
                    rows,
                    [
                        ("name", "Skill"),
                        ("version", "Version"),
                        ("actions", "Actions"),
                        ("runs", "Exécutions"),
                        ("reliability", "Fiabilité"),
                        ("adoption", "Adoption des recos"),
                        ("success", "Succès mesuré"),
                        ("loaded", "Chargée"),
                    ],
                ),
            ),
            Section(title="Descriptions", body=descriptions),
        ],
        data={
            "skills": {
                n: {"description": registry.manifest(n).description, "utility": utility[n]} for n in registry.names
            }
        },
    )


# -- /memory --------------------------------------------------------------------------------------
def cmd_memory(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    if args:
        out = _run(app, "memory-learning", "search", {"query": args, "limit": int(options.get("limit", 10))})
        hits = out.hits if out else []  # type: ignore[attr-defined]
        body = "\n".join(
            f"- **{h.title}** ({h.status}{f', score {h.score}' if h.score is not None else ''}) — "
            f"{h.decision}" + (f"\n  - Leçon : {h.lesson}" if h.lesson else "")
            for h in hits
        )
        return CommandResult(
            command="memory",
            title=f"Mémoire — « {args} »",
            summary=f"{len(hits)} décision(s) trouvée(s).",
            sections=[Section(title="Résultats", body=body or "_Aucun résultat._")],
            data={"hits": [h.model_dump(mode="json") for h in hits]},
        )
    memory = app.kernel.memory
    recent = memory.list(limit=15)
    due = memory.due_for_review()
    lessons = _run(app, "memory-learning", "lessons", {"limit": 10})
    sections = [
        Section(
            title="Décisions récentes",
            body=table(
                [
                    {
                        "id": d.id,
                        "title": d.title,
                        "status": d.status,
                        "source": d.source,
                        "review": d.review_at.date().isoformat() if d.review_at else None,
                    }
                    for d in recent
                ],
                [
                    ("id", "Id"),
                    ("title", "Décision"),
                    ("status", "Statut"),
                    ("source", "Origine"),
                    ("review", "Revue"),
                ],
            ),
        ),
        Section(title="Revues dues", body="\n".join(f"- {d.id} — {d.title}" for d in due) or "_Aucune._"),
    ]
    lesson_items = getattr(lessons, "lessons", None) if lessons else None
    if lesson_items:
        sections.append(
            Section(
                title="Leçons capitalisées",
                body="\n".join(f"- {lesson.lesson}" for lesson in lesson_items),
            )
        )
    total = app.kernel.db.scalar("SELECT COUNT(*) FROM decisions")
    return CommandResult(
        command="memory",
        title="Mémoire des décisions",
        summary=f"{total} décision(s) mémorisée(s), {len(due)} revue(s) due(s).",
        sections=sections,
        data={"recent": [d.model_dump(mode="json") for d in recent], "due": [d.id for d in due]},
    )


# -- /roadmap -------------------------------------------------------------------------------------
def cmd_roadmap(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    kernel = app.kernel
    now = kernel.clock.now()
    items: list[dict[str, Any]] = []
    for objective in kernel.store.list("objective"):
        items.append(
            {
                "type": "Objectif",
                "title": objective.get("title"),
                "due": objective.get("deadline"),
                "status": objective.get("status", "active"),
                "detail": f"{objective.get('metric', '')} → {objective.get('target', '')}",
            }
        )
    for item in kernel.store.list("roadmap_item"):
        items.append(
            {
                "type": "Jalon",
                "title": item.get("title"),
                "due": item.get("due"),
                "status": item.get("status", "planned"),
                "detail": item.get("objective_id", ""),
            }
        )
    for decision in kernel.memory.list(status="open", limit=200):
        if decision.review_at:
            items.append(
                {
                    "type": "Revue de décision",
                    "title": decision.title,
                    "due": iso(decision.review_at),
                    "status": "à mesurer",
                    "detail": decision.id,
                }
            )
    for mission in kernel.store.list("mission"):
        if mission.get("status") in ("todo", "assigned", "in_progress"):
            items.append(
                {
                    "type": "Mission",
                    "title": mission.get("title"),
                    "due": mission.get("due"),
                    "status": mission.get("status"),
                    "detail": mission.get("assigned_to") or "non assignée",
                }
            )
    for approval in app.approvals.list("pending"):
        waiting = round((now - approval.created_at).total_seconds() / 86400)
        items.append(
            {
                "type": "Validation",
                "title": approval.title,
                "due": None,
                "status": "en attente",
                "when": f"attend depuis {waiting} j",
                "detail": approval.id,
            }
        )
    for item in items:
        due = item.get("due")
        if due:
            delta = round((parse_dt(due) - now).total_seconds() / 86400)
            item["when"] = f"en retard de {-delta} j" if delta < 0 else f"dans {delta} j"
    items.sort(key=lambda i: i.get("due") or "0000")  # validations sans échéance en tête : elles bloquent
    horizon = now + timedelta(days=int(options.get("days", 90)))
    upcoming = [i for i in items if not i.get("due") or parse_dt(i["due"]) <= horizon]
    return CommandResult(
        command="roadmap",
        title="Feuille de route",
        summary=f"{len(upcoming)} élément(s) dans les {int(options.get('days', 90))} prochains jours "
        f"({sum(1 for i in upcoming if 'retard' in str(i.get('when')))} en retard).",
        sections=[
            Section(
                title="À venir",
                body=table(
                    upcoming,
                    [
                        ("due", "Échéance"),
                        ("when", "Délai"),
                        ("type", "Type"),
                        ("title", "Élément"),
                        ("status", "Statut"),
                        ("detail", "Détail"),
                    ],
                ),
            )
        ],
        data={"items": upcoming},
    )


# -- /status --------------------------------------------------------------------------------------
def cmd_status(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    kernel = app.kernel
    settings = kernel.settings
    db_path = Path(kernel.db.path)
    size = db_path.stat().st_size if kernel.db.path != ":memory:" and db_path.exists() else 0
    queue = app.queue.stats()
    cycles = app.cycles.history(6)
    finance = kernel.finance.status()
    counts = {
        "décisions": kernel.db.scalar("SELECT COUNT(*) FROM decisions"),
        "croyances": kernel.db.scalar("SELECT COUNT(*) FROM beliefs"),
        "observations": kernel.db.scalar("SELECT COUNT(*) FROM observations"),
        "événements": kernel.db.scalar("SELECT COUNT(*) FROM events"),
        "exécutions de Skills": kernel.db.scalar("SELECT COUNT(*) FROM skill_runs"),
        "rapports": kernel.db.scalar("SELECT COUNT(*) FROM reports"),
    }
    llm = kernel.llm
    llm_state = (
        "désactivé (mode déterministe)"
        if not settings.llm.enabled
        else f"activé — {settings.llm.model}, {llm.calls} appel(s), {llm.spent} dépensés, "
        f"{llm.denied_by_budget} refusé(s) par le budget"
    )
    lines = [
        f"- Version : SOVEREIGN {sovereign.__version__} — {sovereign.MOTTO}",
        f"- Base : `{kernel.db.path}` ({size / 1024:.0f} Ko), schéma v"
        f"{kernel.db.scalar('SELECT MAX(version) FROM schema_migrations')}, intégrité "
        f"`{kernel.db.scalar('PRAGMA quick_check')}`",
        f"- Données : {kernel.store.kinds() or 'aucune'}",
        f"- Compteurs : {counts}",
        f"- Skills : {len(kernel.registry.names)} découvertes, chargées : {kernel.registry.loaded() or 'aucune'}",
        f"- File de tâches : {queue}",
        f"- Cycles dus maintenant : {app.scheduler.due() or 'aucun'}",
        f"- Validations humaines en attente : {len(app.approvals.list())}",
        f"- Budget disponible : {money(finance.available, finance.currency)} ({finance.utilization:.0%} utilisé)",
        f"- LLM : {llm_state}",
        f"- Notifications sortantes : {'configurées' if app.notifier.configured else 'non configurées'}",
        f"- Webhooks entrants : {sorted(settings.security.webhook_secrets) or 'aucun secret configuré'}",
        f"- Clés d'API actives : {sum(1 for k in kernel.api_keys.list() if not k['revoked_at'])}",
        f"- OpenTelemetry : {'export actif' if settings.otel_enabled else 'API no-op (export désactivé)'}",
    ]
    health = "dégradé" if queue["dead"] or finance.overdrawn else "opérationnel"
    return CommandResult(
        command="status",
        title="État réel du système",
        summary=f"Système {health}.",
        sections=[
            Section(title="État", body="\n".join(lines)),
            Section(
                title="Derniers cycles",
                body=table(
                    cycles,
                    [
                        ("cycle", "Cycle"),
                        ("period", "Période"),
                        ("status", "Statut"),
                        ("started_at", "Début"),
                        ("report_id", "Rapport"),
                    ],
                ),
            ),
        ],
        data={
            "health": health,
            "queue": queue,
            "counts": counts,
            "cycles": cycles,
            "due_cycles": app.scheduler.due(),
            "llm_enabled": settings.llm.enabled,
            "finance": finance.model_dump(mode="json"),
            "skills_loaded": kernel.registry.loaded(),
        },
    )


# -- /improve -------------------------------------------------------------------------------------
def cmd_improve(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    plan: list[tuple[str, str, dict[str, Any] | None]] = [
        ("analysis-optimization", "recommend_improvements", {}),
        ("analysis-optimization", "audit_system", {}),
    ]
    result = _analysis(
        app,
        "improve",
        "Améliorations nécessaires",
        plan,
        "Aucune amélioration identifiée faute de données projets ; voir l'auto-audit.",
    )
    findings = app.director.dream()
    if findings:
        result.sections.append(Section(title="Auto-audit du directeur", body="\n".join(f"- {f}" for f in findings)))
        result.data["findings"] = findings
    return result


# -- /report --------------------------------------------------------------------------------------
def cmd_report(app: Sovereign, args: str, options: dict[str, Any]) -> CommandResult:
    kernel = app.kernel
    kind = (args or "executive").strip().lower()
    now = kernel.clock.now()
    status = kernel.finance.status()
    report = app.director.run(
        f"Rapport de direction ({kind})", mode="report", execute=False, decide_top=3, skill_limit=5
    )
    approvals = app.approvals.list("pending")
    lessons = _run(app, "memory-learning", "lessons", {"limit": 5})
    md = [
        f"# Rapport de direction — {kind}",
        "",
        f"_Généré le {now:%Y-%m-%d %H:%M} UTC par SOVEREIGN — {sovereign.MOTTO}_",
        "",
        "## Synthèse",
        "",
        report.summary,
        "",
        "## Finances",
        "",
        f"- Encaissé net : {money(status.collected_total, status.currency)}",
        f"- Enveloppe (10 %) : {money(status.envelope, status.currency)} — disponible "
        f"{money(status.available, status.currency)} ({status.utilization:.0%} utilisé)",
        *[f"- ⚠ {a}" for a in status.alerts],
        "",
        "## Priorités",
        "",
        priorities_md(report.priorities, 8),
        "",
        "## Constats majeurs",
        "",
        insights_md(report.insights, 10),
        "",
    ]
    if report.forecasts:
        md += (
            ["## Anticipation", ""]
            + [f"- `{f.metric}` : {f.current:g} → {f.projected:g} à 30 jours" for f in report.forecasts]
            + [""]
        )
    md += ["## Décisions en attente de validation humaine", ""]
    md += [f"- {a.title} — {a.summary}" for a in approvals] or ["_Aucune._"]
    md += [""]
    report_lessons = getattr(lessons, "lessons", None) if lessons else None
    if report_lessons:
        md += ["## Leçons", ""] + [f"- {lesson.lesson}" for lesson in report_lessons] + [""]
    content = "\n".join(md)
    report_id = new_id("rpt")
    kernel.db.execute(
        "INSERT INTO reports(id, kind, title, content_md, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            report_id,
            f"report.{kind}",
            f"Rapport de direction — {kind}",
            content,
            dumps({"director_report": report.report_id}),
            iso(now),
        ),
    )
    path: str | None = None
    if kernel.db.path != ":memory:":
        directory = kernel.settings.reports_path
        directory.mkdir(parents=True, exist_ok=True)
        file = directory / f"{now:%Y-%m-%d}-{kind}-{report_id[-8:]}.md"
        file.write_text(content, encoding="utf-8")
        path = str(file)
    return CommandResult(
        command="report",
        title=f"Rapport de direction — {kind}",
        summary=report.summary + (f" Rapport enregistré : {path}" if path else ""),
        sections=[Section(title="Rapport", body=content.split("\n", 3)[-1])],
        data={"report_id": report_id, "path": path, "markdown": content},
    )


COMMANDS: list[CommandSpec] = [
    CommandSpec(
        "director",
        "Consulter le directeur (boucle cognitive complète)",
        "/director [question] [--deep] [--dry-run] [--llm] [--top=3]",
        cmd_director,
        "write",
    ),
    CommandSpec("strategy", "Obtenir la stratégie actuelle", "/strategy", cmd_strategy),
    CommandSpec("business", "Lancer une analyse commerciale", "/business [focus]", cmd_business),
    CommandSpec("marketing", "Demander une analyse marketing", "/marketing", cmd_marketing),
    CommandSpec(
        "council",
        "Organiser une réunion entre les IA",
        "/council <proposition | rec_… | apr_… | id de comparaison ou de négociation> "
        "[--cost=] [--value=] [--confidence=] [--impact=] "
        "[--effort=] [--risk=] [--irreversible] [--external] [--tags=a,b] [--record] [--llm]",
        cmd_council,
    ),
    CommandSpec("opportunities", "Examiner les opportunités", "/opportunities", cmd_opportunities, "write"),
    CommandSpec("projects", "Analyser les projets existants", "/projects", cmd_projects),
    CommandSpec("audit", "Effectuer un audit général", "/audit", cmd_audit),
    CommandSpec("budget", "Consulter le budget disponible", "/budget [--months=6]", cmd_budget),
    CommandSpec("skills", "Consulter les dix compétences", "/skills [nom]", cmd_skills),
    CommandSpec("memory", "Consulter les décisions mémorisées", "/memory [recherche]", cmd_memory),
    CommandSpec("roadmap", "Consulter les objectifs à venir", "/roadmap [--days=90]", cmd_roadmap),
    CommandSpec("status", "Obtenir l'état réel du système", "/status", cmd_status),
    CommandSpec("improve", "Analyser les améliorations nécessaires", "/improve", cmd_improve),
    CommandSpec(
        "report",
        "Générer un rapport de direction",
        "/report [executive|daily|weekly|monthly]",
        cmd_report,
        "write",
    ),
]
