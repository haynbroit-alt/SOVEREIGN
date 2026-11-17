"""Rendu Markdown des résultats (CLI, rapports, MCP). Les interfaces JSON utilisent les modèles bruts."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sovereign.core.money import fmt

if TYPE_CHECKING:
    from sovereign.brain.director import DirectorReport
    from sovereign.brain.prioritize import Priority
    from sovereign.skills.base import Insight, SkillOutput

_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
_ROUTE = {
    "execute": "▶ exécutée (suivi automatique)",
    "mission": "▶ confiée aux équipes d'agents",
    "human_review": "✋ revue humaine",
    "none": "— non exécutée",
    "already_handled": "✓ déjà traitée",
}


def money(value: Decimal | float | int | None, currency: str = "EUR") -> str:
    return "—" if value is None else fmt(value, currency)


def insights_md(insights: list[Insight], limit: int = 12) -> str:
    if not insights:
        return "_Aucun constat._"
    order = {"critical": 0, "warning": 1, "info": 2}
    ranked = sorted(insights, key=lambda i: order.get(i.severity, 3))[:limit]
    lines = [f"- {_ICON.get(i.severity, '•')} **{i.title}**" + (f" — {i.detail}" if i.detail else "")
             + (f" _({i.skill})_" if i.skill else "") for i in ranked]
    if len(insights) > limit:
        lines.append(f"- … {len(insights) - limit} autre(s) constat(s)")
    return "\n".join(lines)


def priorities_md(priorities: list[Priority], limit: int = 10) -> str:
    if not priorities:
        return "_Aucune recommandation._"
    lines = ["| # | Recommandation | Score | Impact | Effort | Confiance | Urgence | ROI | Skill |",
             "|---|---|---|---|---|---|---|---|---|"]
    for p in priorities[:limit]:
        r = p.recommendation
        roi = f"{p.roi:.0%}" if p.roi is not None else "—"
        lines.append(f"| {p.rank} | {r.title} | {p.score:.2f} | {r.impact:.0f} | {r.effort:.0f} | "
                     f"{r.confidence:.0%} | {r.urgency:.0f} | {roi} | {r.skill} |")
    return "\n".join(lines)


def skill_output_md(name: str, out: SkillOutput, limit: int = 6) -> str:
    parts = [out.summary or "_(pas de synthèse)_"]
    if out.insights:
        parts.append(insights_md(out.insights, limit))
    if out.recommendations:
        parts.append("**Recommandations :**\n" + "\n".join(
            f"- {r.title} _(impact {r.impact:.0f}/10, confiance {r.confidence:.0%})_ — {r.rationale}"
            for r in out.recommendations[:limit]))
    return "\n\n".join(parts)


def director_md(report: DirectorReport) -> str:
    return render_director(report)


def render_director(report: DirectorReport, header: bool = True) -> str:
    lines = [f"# Directeur — {report.mission}", "", f"> {report.summary}", ""] if header else []
    lines += [f"_{report.at:%Y-%m-%d %H:%M} UTC · mode {report.mode} · mission {report.mission_id}_", "",
              "## Boucle cognitive", ""]
    for phase in report.phases:
        lines.append(f"- **{phase.name}** — {phase.summary}")
    lines += ["", "## Skills mobilisées", ""]
    for s in report.selected:
        reasons = "; ".join(s.get("reasons") or [])
        lines.append(f"- `{s['name']}`" + (f" — {reasons}" if reasons else ""))
    if report.confrontations:
        lines += ["", "## Faits vs croyances", ""]
        for c in report.confrontations:
            mark = "✅" if c.outcome == "confirmation" else "❌"
            lines.append(f"- {mark} {c.statement} — observé {c.metric} = {c.observed:g} (attendu {c.expected}) ; "
                         f"confiance {c.prior:.0%} → {c.posterior:.0%}")
    if report.hypotheses:
        lines += ["", "## Hypothèses à vérifier", ""]
        lines += [f"- {h.statement} (plausibilité {h.plausibility:.0%}) → test : `{h.test_skill}.{h.test_action}`"
                  for h in report.hypotheses]
    lines += ["", "## Constats", "", insights_md(report.insights)]
    if report.forecasts:
        lines += ["", "## Anticipation (30 jours)", ""]
        lines += [f"- `{f.metric}` : {f.current:g} → {f.projected:g} (pente {f.slope_per_day:+.4g}/jour, "
                  f"r² {f.r2:.2f}, {f.points} points)" for f in report.forecasts]
    lines += ["", "## Priorités", "", priorities_md(report.priorities)]
    if report.decisions:
        lines += ["", "## Décisions du conseil", ""]
        for d in report.decisions:
            title = d.priority.recommendation.title
            if d.verdict is None:
                lines.append(f"- **{title}** — {_ROUTE[d.route]} ({d.note})")
                continue
            unit = "€" if d.simulation and d.simulation.unit == "money" else "pts d'impact"
            sim = (f" ; simulation : E={d.simulation.expected_value:g} {unit}, "
                   f"P(perte)={d.simulation.p_loss:.0%}, risque {d.simulation.risk_class}") if d.simulation else ""
            lines.append(f"- **{title}** — verdict **{d.verdict.verdict.replace('_', ' ')}** "
                         f"(score {d.verdict.score:+.2f}, confiance {d.verdict.confidence:.0%}){sim} → "
                         f"{_ROUTE[d.route]}" + (f" — {d.note}" if d.note else ""))
            for vote in d.verdict.minutes[1:-1]:
                if not vote.startswith("Réfutation"):  # le détail des réfutations est dans /council
                    lines.append(f"  - {vote}")
    if report.measured:
        lines += ["", "## Résultats mesurés", ""]
        lines += [f"- {m['title']} → {m['status']} (score {m['score']})" for m in report.measured]
    if report.learned:
        lines += ["", "## Apprentissage", ""] + [f"- {item}" for item in report.learned]
    if report.audit:
        lines += ["", "## Dreaming — auto-audit et anticipation", ""] + [f"- {item}" for item in report.audit]
    if report.skill_errors:
        lines += ["", "## Erreurs", ""] + [f"- {e}" for e in report.skill_errors]
    return "\n".join(lines) + "\n"


def table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_Aucune donnée._"
    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "|" + "---|" * len(columns)
    body = ["| " + " | ".join(str(row.get(key, "—")) if row.get(key) is not None else "—" for key, _ in columns)
            + " |" for row in rows]
    return "\n".join([header, sep, *body])
