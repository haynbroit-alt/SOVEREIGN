"""Import CSV de données réelles : relevés, exports de paiements, rapports de campagnes publicitaires.

Conçu pour les fichiers tels qu'ils sortent des outils, sans retouche :
- séparateur détecté (``,`` ``;`` tabulation), encodage UTF-8 (BOM toléré) ou Windows-1252 ;
- en-têtes reconnus par alias FR/EN insensibles aux accents et à la ponctuation (« Montant », « Amount »,
  « Crédit », « Coût », « Cost », « Impr. », « Conv. value »…), complétés par un mappage explicite ``champ=colonne`` ;
- nombres « 1 234,56 € », « 1,234.56 », « $1,234.56 », « 12,5 % » ; dates ISO, « 05/11/2026 » (jour en premier par
  défaut), « 2026/11/05 ».

Règles :
- **encaissements** : seul l'argent RÉELLEMENT reçu d'un client est importé. Si une colonne de statut existe (export
  de paiements), seules les lignes payées / réussies comptent et un montant négatif est un remboursement client.
  Sans statut (relevé bancaire), un montant négatif est un **débit**, ignoré (toute dépense passe par le garde-fou),
  sauf libellé de remboursement ; une colonne « Crédit » est utilisée à défaut de « Montant ». Les crédits qui ne
  sont pas des ventes (prêt, apport, virement interne, remboursement fiscal ou social) sont **exclus** et signalés :
  ils grossiraient à tort l'enveloppe de 10 %.
  Idempotent : réimporter le même fichier ne double rien (référence stable par ligne).
- **campagnes** : les lignes d'une même campagne (rapports quotidiens) sont agrégées.
- **enregistrements** génériques : une ligne = un document du kind demandé.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sovereign.core.finance import FinanceError
from sovereign.core.ids import stable_id

if TYPE_CHECKING:
    from sovereign.app import Sovereign

DecimalMode = Literal["auto", "comma", "dot"]

ALIASES: dict[str, dict[str, list[str]]] = {
    "collections": {
        "amount": [
            "amount",
            "montant",
            "net",
            "montant net",
            "total",
            "somme",
            "valeur",
            "gross",
            "amount paid",
            "montant encaisse",
            "encaissement",
            "recette",
        ],
        "credit": ["credit", "credits", "montant credit", "entree", "entrees"],
        "date": [
            "date",
            "created",
            "created date",
            "created utc",
            "created date utc",
            "date operation",
            "date de l operation",
            "date valeur",
            "paid at",
            "payment date",
            "date de paiement",
            "jour",
        ],
        "counterparty": [
            "customer",
            "client",
            "customer email",
            "customer name",
            "description",
            "libelle",
            "tiers",
            "payer",
            "payeur",
            "nom",
            "name",
            "contrepartie",
        ],
        "reference": [
            "id",
            "reference",
            "ref",
            "transaction id",
            "payment id",
            "numero",
            "n operation",
            "identifiant",
            "charge id",
            "payment intent",
        ],
        "status": ["status", "statut", "etat", "state", "result"],
        "currency": ["currency", "devise", "monnaie"],
    },
    "campaigns": {
        "name": ["campaign", "campagne", "campaign name", "nom de la campagne", "name", "nom"],
        "channel": ["channel", "canal", "network", "reseau", "platform", "plateforme", "source"],
        "spend": ["cost", "cout", "spend", "depense", "depenses", "amount spent", "montant depense", "budget depense"],
        "impressions": ["impressions", "impr", "impr.", "affichages"],
        "clicks": ["clicks", "clics", "link clicks", "clics sur le lien"],
        "conversions": ["conversions", "conv", "conv.", "results", "resultats", "leads", "purchases", "achats"],
        "revenue": [
            "conv value",
            "conversion value",
            "conv. value",
            "revenue",
            "revenu",
            "chiffre d affaires",
            "ca",
            "purchase conversion value",
            "valeur de conversion",
            "valeur des conversions",
        ],
    },
}
NON_REVENUE_RE = re.compile(
    r"\b(pret|prets|emprunt|deblocage|loan|apport|apports|augmentation de capital|capital social"
    r"|compte courant d associe|virement interne|vir interne|de compte a compte|entre comptes|transfert interne"
    r"|internal transfer|own account|remboursement (de )?tva|credit de tva|dgfip|impots?|urssaf|caf)\b"
)
"""Crédits qui ne sont pas des revenus commerciaux (texte normalisé, sans accents)."""
REFUND_RE = re.compile(r"\b(rembours\w*|refund\w*|avoir|chargeback|retrocession|annulation)\b")

SUCCESS_STATUSES = {
    "paid",
    "succeeded",
    "success",
    "successful",
    "captured",
    "completed",
    "complete",
    "settled",
    "paye",
    "payee",
    "encaisse",
    "encaissee",
    "reussi",
    "reussie",
    "valide",
    "validee",
    "ok",
}


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", plain)).strip()


def parse_number(raw: Any, mode: DecimalMode = "auto") -> Decimal | None:
    """Nombre « tel qu'exporté » → Decimal (None si vide ou illisible)."""

    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "-", "--", "—", "n/a", "N/A"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^\d,.\-+]", "", text.replace(" ", "").replace("\xa0", ""))
    if not text or not re.search(r"\d", text):
        return None
    if mode == "comma" or (mode == "auto" and "," in text and "." in text and text.rfind(",") > text.rfind(".")):
        text = text.replace(".", "").replace(",", ".")
    elif mode == "dot" or (mode == "auto" and "," in text and "." in text):
        text = text.replace(",", "")
    elif mode == "auto" and "," in text:
        # « 12,34 » = décimal ; « 1,234 » ou « 1,234,567 » = séparateur de milliers
        text = text.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", text) else text.replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -abs(value) if negative else value


def parse_date(raw: Any, dayfirst: bool = True) -> date | None:
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    match = re.match(r"^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})", text)
    if not match:
        return None
    a, b, c = (int(x) for x in match.groups())
    try:
        if a > 31:  # AAAA/MM/JJ
            return date(a, b, c)
        year = c + 2000 if c < 100 else c
        return date(year, b, a) if dayfirst else date(year, a, b)
    except ValueError:
        return None


@dataclass
class ImportReport:
    kind: str
    rows: int = 0
    imported: int = 0
    duplicates: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "rows": self.rows,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "skipped": len(self.skipped),
            "skipped_examples": self.skipped[:10],
            "warnings": self.warnings,
            "columns": self.columns,
        }


def read_csv(source: str | Path | bytes) -> list[dict[str, str]]:
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).exists()):
        raw = Path(source).read_bytes()
    else:
        raw = str(source).encode("utf-8")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


def _resolve(headers: list[str], kind: str, mapping: dict[str, str]) -> dict[str, str]:
    """Champ interne → nom de colonne réel."""

    by_norm = {norm(h): h for h in headers}
    resolved: dict[str, str] = {}
    for fld, column in mapping.items():
        if column in headers:
            resolved[fld] = column
        elif norm(column) in by_norm:
            resolved[fld] = by_norm[norm(column)]
        else:
            raise ValueError(f"colonne « {column} » introuvable (colonnes : {', '.join(headers)})")
    for fld, aliases in ALIASES.get(kind, {}).items():
        if fld in resolved:
            continue
        for alias in aliases:
            if norm(alias) in by_norm:
                resolved[fld] = by_norm[norm(alias)]
                break
    return resolved


def import_collections(
    app: Sovereign,
    source: str | Path | bytes,
    *,
    mapping: dict[str, str] | None = None,
    decimal: DecimalMode = "auto",
    dayfirst: bool = True,
    actor: str = "csv",
) -> ImportReport:
    rows = read_csv(source)
    report = ImportReport(kind="collections", rows=len(rows))
    if not rows:
        return report
    columns = _resolve(list(rows[0]), "collections", mapping or {})
    report.columns = columns
    if "amount" not in columns and "credit" not in columns:
        raise ValueError("aucune colonne de montant (Montant / Amount / Crédit) — précisez --map amount=<colonne>")
    if "date" not in columns:
        raise ValueError("aucune colonne de date — précisez --map date=<colonne>")
    finance = app.kernel.finance
    seen: Counter[str] = Counter()
    for index, row in enumerate(rows, start=2):
        status = norm(row.get(columns.get("status", ""), "")) if "status" in columns else ""
        if "status" in columns and status and status not in SUCCESS_STATUSES:
            report.skipped.append(f"ligne {index} : statut « {row[columns['status']]} » (non encaissé)")
            continue
        amount = parse_number(row.get(columns["amount"]), decimal) if "amount" in columns else None
        if amount is None and "credit" in columns:
            amount = parse_number(row.get(columns["credit"]), decimal)
        if amount is None or amount == 0:
            report.skipped.append(f"ligne {index} : montant absent ou nul")
            continue
        text = norm(" ".join(str(v) for v in row.values() if v))
        if amount > 0 and NON_REVENUE_RE.search(text):
            report.skipped.append(
                f"ligne {index} : crédit non commercial exclu (prêt, apport, virement interne, remboursement fiscal) "
                "— s'il s'agit d'une vraie vente : sovereign finance revenue"
            )
            continue
        if amount < 0 and "status" not in columns and not REFUND_RE.search(text):
            report.skipped.append(f"ligne {index} : débit ignoré (les dépenses passent par le garde-fou des 10 %)")
            continue
        day = parse_date(row.get(columns["date"]), dayfirst)
        if day is None:
            report.skipped.append(f"ligne {index} : date illisible « {row.get(columns['date'])} »")
            continue
        currency = row.get(columns["currency"], "").strip().upper() if "currency" in columns else ""
        if currency and currency != finance.currency:
            report.skipped.append(f"ligne {index} : devise {currency} ≠ {finance.currency} (pas de conversion)")
            continue
        counterparty = row.get(columns["counterparty"]) if "counterparty" in columns else None
        reference = row.get(columns["reference"]) if "reference" in columns else ""
        if not reference:
            key = f"{day}|{amount}|{counterparty}"
            seen[key] += 1
            reference = stable_id("csv", key, seen[key])
        else:
            reference = f"csv:{reference}"
        try:
            if amount > 0:
                entry = finance.record_collection(
                    amount,
                    day,
                    counterparty=counterparty or None,
                    description="Import CSV",
                    reference=reference,
                    source="csv",
                    actor=actor,
                )
            else:
                entry = finance.record_refund(
                    -amount,
                    day,
                    counterparty=counterparty or None,
                    description="Import CSV (remboursement)",
                    reference=reference,
                    source="csv",
                    actor=actor,
                )
        except FinanceError as exc:
            report.skipped.append(f"ligne {index} : {exc}")
            continue
        if entry.duplicate:
            report.duplicates += 1
        else:
            report.imported += 1
    if "status" not in columns:
        report.warnings.append(
            "Aucune colonne de statut : les crédits sont considérés encaissés (sauf prêts, apports, virements "
            "internes, remboursements fiscaux) et les débits ignorés."
        )
    return report


def import_campaigns(
    app: Sovereign,
    source: str | Path | bytes,
    *,
    mapping: dict[str, str] | None = None,
    decimal: DecimalMode = "auto",
    channel: str | None = None,
    actor: str = "csv",
) -> ImportReport:
    rows = read_csv(source)
    report = ImportReport(kind="campaigns", rows=len(rows))
    if not rows:
        return report
    columns = _resolve(list(rows[0]), "campaigns", mapping or {})
    report.columns = columns
    if "name" not in columns or "spend" not in columns:
        raise ValueError("colonnes « campagne » et « coût » requises — précisez --map name=<col> spend=<col>")
    totals: dict[tuple[str, str], dict[str, Decimal]] = {}
    for index, row in enumerate(rows, start=2):
        name = row.get(columns["name"], "").strip()
        if not name or norm(name) in ("total", "totaux", "grand total"):
            report.skipped.append(f"ligne {index} : ligne de total ou sans nom")
            continue
        chan = (row.get(columns["channel"], "") if "channel" in columns else "") or channel or "inconnu"
        bucket = totals.setdefault(
            (name, chan), {k: Decimal(0) for k in ("spend", "impressions", "clicks", "conversions", "revenue")}
        )
        for fld in bucket:
            if fld in columns:
                value = parse_number(row.get(columns[fld]), decimal)
                bucket[fld] += value or Decimal(0)
    for (name, chan), values in totals.items():
        record_id = stable_id("cmp", norm(name), norm(chan))
        app.kernel.store.put(
            "campaign",
            record_id,
            {
                "id": record_id,
                "name": name,
                "channel": chan,
                "spend": str(values["spend"]),
                "impressions": int(values["impressions"]),
                "clicks": int(values["clicks"]),
                "conversions": int(values["conversions"]),
                "revenue": str(values["revenue"]),
                "source": actor,
            },
        )
        report.imported += 1
    if "revenue" not in columns:
        report.warnings.append("Pas de colonne de revenu : le ROAS ne pourra pas être calculé.")
    return report


def import_records(
    app: Sovereign, kind: str, source: str | Path | bytes, *, decimal: DecimalMode = "auto", actor: str = "csv"
) -> ImportReport:
    """Une ligne = un enregistrement (en-têtes normalisés en snake_case, nombres convertis)."""

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", kind):
        raise ValueError("kind invalide (minuscules, chiffres, _)")
    rows = read_csv(source)
    report = ImportReport(kind=kind, rows=len(rows))
    for index, row in enumerate(rows, start=2):
        record: dict[str, Any] = {}
        for header, value in row.items():
            key = norm(header).replace(" ", "_")
            if not key:
                continue
            number = parse_number(value, decimal) if re.fullmatch(r"[\s\d,.\-+()%€$£]+", value or "") else None
            if number is not None:
                record[key] = (
                    int(number)
                    if number == number.to_integral_value() and "." not in value and "," not in value
                    else float(number)
                )
            else:
                record[key] = value
        if not any(v not in ("", None) for v in record.values()):
            report.skipped.append(f"ligne {index} : vide")
            continue
        record_id = str(record.get("id") or stable_id(kind, *sorted(map(str, record.items()))))
        record["id"] = record_id
        existed = app.kernel.store.get(kind, record_id) is not None
        app.kernel.store.put(kind, record_id, record)
        if existed:
            report.duplicates += 1
        else:
            report.imported += 1
    return report


def import_csv(app: Sovereign, kind: str, source: str | Path | bytes, **options: Any) -> ImportReport:
    if kind in ("collections", "encaissements"):
        report = import_collections(app, source, **options)
    elif kind in ("campaigns", "campagnes"):
        report = import_campaigns(app, source, **options)
    else:
        options.pop("mapping", None)
        options.pop("dayfirst", None)
        options.pop("channel", None)
        report = import_records(app, kind, source, **options)
    app.kernel.events.publish("workspace.csv_imported", report.as_dict(), source="csv")
    return report


INBOX_SUFFIXES = (".csv", ".yaml", ".yml", ".json")


def process_inbox(app: Sovereign, inbox: str | Path, *, now_label: str | None = None) -> list[dict[str, Any]]:
    """Importe les fichiers déposés dans une boîte de dépôt puis les range dans ``inbox/importes/``.

    Convention de nommage des CSV : ``<kind>.csv`` ou ``<kind>--<libellé>.csv`` (ex. ``collections--banque.csv``,
    ``campaigns--google.csv``, ``segment.csv``). Les fichiers YAML/JSON suivent le format d'espace de travail.
    Un fichier en erreur est déplacé dans ``inbox/erreurs/`` avec un fichier ``.erreur.txt`` explicatif : il n'est
    jamais réessayé en boucle et rien n'est perdu.
    """

    from sovereign.workspace import import_workspace, load_file

    folder = Path(inbox)
    if not folder.is_dir():
        return []
    label = now_label or app.kernel.clock.now().strftime("%Y%m%dT%H%M%S")
    done_dir, error_dir = folder / "importes", folder / "erreurs"
    results: list[dict[str, Any]] = []
    for file in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in INBOX_SUFFIXES):
        try:
            if file.suffix.lower() == ".csv":
                kind = file.stem.split("--", 1)[0].strip().lower()
                outcome: dict[str, Any] = import_csv(app, kind, file.read_bytes()).as_dict()
            else:
                outcome = {"kind": "workspace", **import_workspace(app, load_file(file), actor=f"inbox:{file.name}")}
            target = done_dir / f"{label}-{file.name}"
            status = "importé"
        except Exception as exc:  # un fichier défectueux ne bloque pas les autres
            outcome = {"error": f"{type(exc).__name__}: {exc}"}
            target = error_dir / f"{label}-{file.name}"
            status = "erreur"
        target.parent.mkdir(parents=True, exist_ok=True)
        file.rename(target)
        if status == "erreur":
            target.with_suffix(target.suffix + ".erreur.txt").write_text(outcome["error"] + "\n", encoding="utf-8")
        results.append({"file": file.name, "status": status, "moved_to": str(target.relative_to(folder)), **outcome})
    return results
