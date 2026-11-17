from __future__ import annotations

import pytest

from sovereign.app import Sovereign
from sovereign.commands.router import CommandError, parse, parse_options

ALL = [
    "director",
    "strategy",
    "business",
    "marketing",
    "council",
    "opportunities",
    "projects",
    "audit",
    "budget",
    "skills",
    "memory",
    "roadmap",
    "status",
    "improve",
    "report",
]


@pytest.fixture
def app(clock):
    application = Sovereign.ephemeral(clock=clock)
    application.kernel.finance.record_collection("12000", "2026-11-01", reference="c1")
    yield application
    application.close()


def test_the_fifteen_commands_exist():
    app = Sovereign.ephemeral()
    assert sorted(app.commands.specs) == sorted(ALL)


def test_parsing():
    assert parse("/budget") == ("budget", "")
    assert parse("Que faire ?") == ("director", "Que faire ?")
    assert parse("") == ("status", "")
    free, opts = parse_options("Lancer X --cost=500 --record --tags=a,b")
    assert free == "Lancer X" and opts == {"cost": "500", "record": True, "tags": "a,b"}


@pytest.mark.parametrize("name", ALL)
def test_every_command_runs_on_a_fresh_workspace(app, name):
    args = "Ouvrir un second bureau --cost=100" if name == "council" else ""
    result = app.commands.execute(name, args)
    assert result.ok and result.summary and result.command == name
    assert result.markdown().startswith(f"# {result.title}")
    assert app.kernel.db.scalar("SELECT COUNT(*) FROM command_runs WHERE command = ?", (name,)) == 1


def test_unknown_command(app):
    with pytest.raises(CommandError):
        app.commands.run("/nope")


def test_budget_command_is_real(app):
    result = app.commands.run("/budget")
    assert result.data["status"]["envelope"] == "1200.00"
    assert "aucun accès bancaire" in result.markdown()


def test_council_by_recommendation_id_and_record(app):
    app.kernel.finance.issue_invoice("Retard", "2000", "2026-09-01", "2026-10-01")
    out = app.kernel.run_skill("finance-budget", "assess")
    rec_id = out.recommendations[0].id
    result = app.commands.run(f"/council {rec_id} --record")
    assert result.data["decision_id"]
    assert result.data["verdict"]["recommendation_id"] == rec_id


def test_council_budget_veto_in_command(app):
    result = app.commands.run("/council Campagne TV nationale --cost=50000 --value=80000 --tags=marketing")
    assert result.data["verdict"]["vetoes"]


def test_free_text_goes_to_director(app):
    result = app.commands.run("Comment optimiser le budget de dépenses ?")
    assert result.command == "director"
    assert result.data["selected"][0]["name"] == "finance-budget"


def test_memory_search_and_skills_detail(app):
    app.kernel.memory.record("Tarification annuelle", "Passer à l'annuel", category="pricing")
    assert app.commands.run("/memory tarification").data["hits"]
    detail = app.commands.run("/skills finance-budget")
    assert "request_expense" in detail.data["schemas"]


def test_report_is_persisted(app, tmp_path):
    result = app.commands.run("/report weekly")
    assert result.data["report_id"]
    assert "Rapport de direction — weekly" in result.data["markdown"]
