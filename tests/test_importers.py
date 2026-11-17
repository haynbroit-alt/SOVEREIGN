from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from sovereign.app import Sovereign
from sovereign.importers import import_csv, parse_date, parse_number
from sovereign.interfaces.cli import cli

BANK_FR = """Date opération;Libellé;Débit;Crédit
05/11/2026;VIR CLIENT DUPONT;;1 250,00
06/11/2026;PRLV HEBERGEUR;-120,00;
08/11/2026;VIR CLIENT MARTIN;;3 400,50
08/11/2026;VIR CLIENT MARTIN;;3 400,50
""".encode("cp1252")

PAYMENTS = """id,Created date (UTC),Amount,Currency,Status,Customer Email
pay_1,2026-11-02 10:15:00,49.00,eur,Paid,a@example.test
pay_2,2026-11-03 11:00:00,49.00,eur,Failed,b@example.test
pay_3,2026-11-04 09:30:00,"1,290.00",eur,Paid,c@example.test
pay_4,2026-11-05 08:00:00,-49.00,eur,Paid,a@example.test
pay_5,2026-11-05 08:00:00,99.00,usd,Paid,d@example.test
"""

ADS = """Campaign,Day,Impr.,Clicks,Cost,Conversions,Conv. value
Recherche marque,2026-11-01,1000,80,40.50,4,320
Recherche marque,2026-11-02,1200,95,"45,30",5,410
Display relance,2026-11-01,9000,30,25.00,0,0
Total,,11200,205,110.80,9,730
"""


@pytest.fixture
def app(clock):
    application = Sovereign.ephemeral(clock=clock)
    yield application
    application.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 250,00", "1250.00"),
        ("3 400,50 €", "3400.50"),
        ("1,290.00", "1290.00"),
        ("$1,234.56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("12,5", "12.5"),
        ("1,234", "1234"),
        ("(45.00)", "-45.00"),
        ("-120,00", "-120.00"),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == Decimal(expected)


def test_parse_number_empty_and_forced_modes():
    assert parse_number("") is None and parse_number("--") is None and parse_number("abc") is None
    assert parse_number("1,234", "comma") == Decimal("1.234")
    assert parse_number("1.234", "dot") == Decimal("1.234")


def test_parse_dates():
    assert parse_date("05/11/2026") == date(2026, 11, 5)
    assert parse_date("11/05/2026", dayfirst=False) == date(2026, 11, 5)
    assert parse_date("2026-11-05 13:22:01") == date(2026, 11, 5)
    assert parse_date("2026/11/05") == date(2026, 11, 5)
    assert parse_date("pas une date") is None


def test_french_bank_statement_imports_only_credits_idempotently(app):
    report = import_csv(app, "collections", BANK_FR)
    assert report.imported == 3  # 2 lignes identiques le même jour = 2 virements distincts
    assert app.kernel.finance.collected_total() == Decimal("8051.00")
    assert any("montant absent" in s for s in report.skipped)  # le débit n'est pas un encaissement
    again = import_csv(app, "collections", BANK_FR)
    assert again.imported == 0 and again.duplicates == 3
    assert app.kernel.finance.collected_total() == Decimal("8051.00")


def test_payment_export_respects_status_refund_and_currency(app):
    report = import_csv(app, "collections", PAYMENTS)
    assert report.imported == 3  # pay_1, pay_3, pay_4 (remboursement)
    assert app.kernel.finance.collected_total() == Decimal("1290.00")
    reasons = " ".join(report.skipped)
    assert "Failed" in reasons and "USD" in reasons


def test_future_dated_rows_are_rejected_not_fatal(app):
    report = import_csv(app, "collections", "date,amount\n2027-01-01,100\n2026-11-01,50\n")
    assert report.imported == 1 and any("futur" in s for s in report.skipped)


def test_explicit_mapping_and_missing_columns(app):
    with pytest.raises(ValueError, match="montant"):
        import_csv(app, "collections", "jour,valeur_x\n2026-11-01,10\n")
    report = import_csv(
        app, "collections", "jour,valeur_x\n2026-11-01,10\n", mapping={"amount": "valeur_x", "date": "jour"}
    )
    assert report.imported == 1


def test_ad_report_is_aggregated_and_feeds_marketing(app):
    report = import_csv(app, "campaigns", ADS, channel="google_ads")
    assert report.imported == 2 and any("total" in s for s in report.skipped)
    campaigns = {c["name"]: c for c in app.kernel.store.list("campaign")}
    brand = campaigns["Recherche marque"]
    assert brand["spend"] == "85.80" and brand["clicks"] == 175 and brand["conversions"] == 9
    assert brand["channel"] == "google_ads"
    out = app.kernel.run_skill("marketing-strategy", "measure_campaigns", {})
    assert out.metrics["marketing.roas"] == pytest.approx(730 / 110.80, rel=1e-3)
    assert any("Display relance" in r.title for r in out.recommendations)


def test_generic_records_feed_skills(app):
    csv_text = (
        "id,name,size,pain,willingness_to_pay,reachability,competition\n"
        "s1,plombiers,9000,8,468,0.6,0.5\ns2,peintres,4000,5,300,0.3,0.7\n"
    )
    report = import_csv(app, "segment", csv_text)
    assert report.imported == 2
    out = app.kernel.run_skill("marketing-strategy", "segment_audiences", {})
    assert out.summary
    with pytest.raises(ValueError):
        import_csv(app, "Bad Kind!", csv_text)


def test_cli_import_csv(tmp_path):
    path = tmp_path / "releve.csv"
    path.write_bytes(BANK_FR)
    result = CliRunner().invoke(
        cli, ["data", "import-csv", "collections", str(path)], env={"SOVEREIGN_HOME": str(tmp_path / "home")}
    )
    assert result.exit_code == 0, result.output
    assert '"imported": 3' in result.output


def test_inbox_imports_and_files_everything(app, tmp_path):
    from sovereign.importers import process_inbox

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "collections--banque-novembre.csv").write_bytes(BANK_FR)
    (inbox / "campaigns--google.csv").write_text(ADS, encoding="utf-8")
    (inbox / "entreprise.yaml").write_text(
        "records:\n  objective:\n    - {id: o1, title: Atteindre 10 k€ par mois, deadline: 2027-06-30}\n",
        encoding="utf-8",
    )
    (inbox / "collections--casse.csv").write_text("aucune,colonne,utile\n1,2,3\n", encoding="utf-8")
    (inbox / "notes.txt").write_text("ignoré", encoding="utf-8")
    results = process_inbox(app, inbox, now_label="T1")
    by_file = {r["file"]: r for r in results}
    assert by_file["collections--banque-novembre.csv"]["imported"] == 3
    assert by_file["campaigns--google.csv"]["imported"] == 2
    assert by_file["entreprise.yaml"]["records"] == {"objective": 1}
    assert by_file["collections--casse.csv"]["status"] == "erreur"
    assert (inbox / "erreurs" / "T1-collections--casse.csv.erreur.txt").read_text(encoding="utf-8")
    assert sorted(p.name for p in (inbox / "importes").iterdir()) == [
        "T1-campaigns--google.csv",
        "T1-collections--banque-novembre.csv",
        "T1-entreprise.yaml",
    ]
    assert (inbox / "notes.txt").exists()  # les fichiers non reconnus ne sont pas touchés
    assert process_inbox(app, inbox) == []  # rien n'est retraité
    assert process_inbox(app, tmp_path / "absent") == []


def test_cli_inbox(tmp_path):
    home = tmp_path / "home"
    (home / "inbox").mkdir(parents=True)
    (home / "inbox" / "collections.csv").write_bytes(BANK_FR)
    result = CliRunner().invoke(cli, ["data", "inbox"], env={"SOVEREIGN_HOME": str(home)})
    assert result.exit_code == 0, result.output
    assert '"importé"' in result.output
