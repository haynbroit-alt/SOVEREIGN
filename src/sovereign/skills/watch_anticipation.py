"""SKILL 5 — VEILLE ET ANTICIPATION.

Surveille les évolutions technologiques (registres PyPI / npm), détecte les tendances émergentes dans les
signaux de veille, repère les changements de marché (ruptures CUSUM, anomalies robustes), évalue les
risques et
prépare l'agenda des prochaines décisions.
"""

from __future__ import annotations

import email.utils
import hashlib
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sovereign.core.clock import parse_dt
from sovereign.core.stats import cusum_change_point, mean, robust_z
from sovereign.skills.base import (
    EmptyInput,
    FollowUp,
    Insight,
    Recommendation,
    Skill,
    SkillContext,
    SkillOutput,
    TargetMetric,
    action,
)

STOPWORDS = {
    # français
    "avec",
    "dans",
    "pour",
    "plus",
    "sans",
    "sont",
    "leur",
    "leurs",
    "cette",
    "ces",
    "elle",
    "elles",
    "nous",
    "vous",
    "mais",
    "comme",
    "être",
    "etre",
    "avoir",
    "fait",
    "faire",
    "tout",
    "tous",
    "toute",
    "toutes",
    "très",
    "tres",
    "aussi",
    "entre",
    "selon",
    "après",
    "apres",
    "avant",
    "depuis",
    "dont",
    "encore",
    "même",
    "meme",
    "autre",
    "autres",
    "chez",
    "lors",
    "vers",
    "sous",
    "votre",
    "notre",
    "nouveau",
    "nouvelle",
    "nouveaux",
    "nouvelles",
    "deux",
    "trois",
    "était",
    "etait",
    "sera",
    "peut",
    "peuvent",
    "ainsi",
    "alors",
    "quand",
    "année",
    "annee",
    "jour",
    "jours",
    # anglais
    "with",
    "this",
    "that",
    "from",
    "have",
    "will",
    "your",
    "their",
    "they",
    "them",
    "were",
    "been",
    "into",
    "about",
    "more",
    "than",
    "what",
    "when",
    "which",
    "while",
    "would",
    "could",
    "should",
    "there",
    "here",
    "also",
    "just",
    "over",
    "only",
    "some",
    "such",
    "these",
    "those",
    "after",
    "before",
    "news",
    "says",
    "said",
    "year",
    "years",
    "week",
    "today",
}

DEFAULT_MITIGATIONS = {
    "market": "Diversifier les segments et surveiller les indicateurs de demande chaque semaine.",
    "tech": "Planifier les mises à jour, tester les alternatives et limiter le verrouillage fournisseur.",
    "finance": "Constituer une réserve, accélérer les encaissements et geler les dépenses non essentielles.",
    "legal": "Faire valider la conformité par un professionnel et documenter les traitements.",
    "operational": "Documenter les processus critiques et prévoir une redondance des compétences.",
    "competition": "Renforcer la différenciation et la fidélisation des clients existants.",
    "reputation": "Préparer un plan de communication de crise et suivre les avis clients.",
}

Fetcher = Callable[[str], Any]


def _default_json_fetcher(url: str) -> Any:
    import httpx2

    response = httpx2.get(url, timeout=10.0, follow_redirects=True, headers={"User-Agent": "sovereign-watch"})
    response.raise_for_status()
    return response.json()


def _default_text_fetcher(url: str) -> str:
    import httpx2

    response = httpx2.get(url, timeout=10.0, follow_redirects=True, headers={"User-Agent": "sovereign-watch"})
    response.raise_for_status()
    return response.text


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _terms(signal: SignalModel) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-]{3,}", _normalize(f"{signal.title} {signal.text or ''}"))
    terms = {w for w in words if w not in STOPWORDS and not w.isdigit()}
    terms |= {f"#{_normalize(t).strip()}" for t in signal.tags if t.strip()}
    return terms


def _version_tuple(version: str) -> tuple[int, int, int] | None:
    numbers = [int(n) for n in re.findall(r"\d+", version)[:3]]
    if not numbers:
        return None
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


# -- modèles ---------------------------------------------------------------------------------------
class SignalModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    title: str
    source: str = "inconnu"
    url: str | None = None
    published_at: datetime
    tags: list[str] = Field(default_factory=list)
    text: str | None = None
    kind: Literal["news", "tech", "market", "regulation", "competitor"] = "news"


class RiskModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    category: Literal["market", "tech", "finance", "legal", "operational", "competition", "reputation"]
    probability: float = Field(ge=0, le=1)
    impact: float = Field(ge=1, le=10)
    velocity: float = Field(5, ge=1, le=10)
    indicator_trend: Literal["rising", "stable", "falling"] = "stable"
    mitigation: str | None = None
    owner: str | None = None


class TechDependency(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str
    ecosystem: Literal["pypi", "npm"] = "pypi"
    current_version: str
    critical: bool = False
    latest_version: str | None = None
    latest_release: date | None = None


class SeriesPoint(BaseModel):
    at: datetime | str
    value: float


class Series(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric: str
    points: list[SeriesPoint]


class SignalsInput(BaseModel):
    signals: list[SignalModel] = Field(min_length=1)


class IngestOutput(SkillOutput):
    ingested: int
    duplicates: int
    ids: list[str]


class TrendsInput(BaseModel):
    signals: list[SignalModel] = Field(default_factory=list)
    window_days: int = Field(30, ge=1, le=365)
    baseline_days: int = Field(90, ge=1, le=1095)
    min_occurrences: int = Field(3, ge=1)
    z_threshold: float = Field(2.0, gt=0)


class TermTrend(BaseModel):
    term: str
    recent: int
    baseline: int
    ratio: float
    z: float


class TrendsOutput(SkillOutput):
    recent_signals: int
    baseline_signals: int
    emerging: list[TermTrend]
    declining: list[TermTrend]


class MarketChangesInput(BaseModel):
    series: list[Series] = Field(default_factory=list)
    min_points: int = Field(8, ge=6)
    z_threshold: float = Field(3.5, gt=0)


class SeriesChange(BaseModel):
    metric: str
    points: int
    change_index: int | None
    change_at: str | None
    last_value: float
    robust_z: float
    anomaly: bool
    recent_change: float | None


class MarketChangesOutput(SkillOutput):
    series: list[SeriesChange]


class RisksInput(BaseModel):
    risks: list[RiskModel] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    id: str
    title: str
    category: str
    score: float
    level: Literal["critique", "élevé", "moyen", "faible"]
    mitigation: str
    owner: str | None


class RisksOutput(SkillOutput):
    risks: list[RiskAssessment]
    heatmap: dict[str, int]


class TechCheckInput(BaseModel):
    dependencies: list[TechDependency] = Field(default_factory=list)
    offline: bool = Field(False, description="Vrai : n'interroge pas les registres (versions déjà connues)")
    stale_days: int = Field(540, ge=30)
    persist: bool = Field(True, description="Mémorise les dernières versions trouvées (dépendances du store)")


class DependencyStatus(BaseModel):
    name: str
    ecosystem: str
    current_version: str
    latest_version: str | None
    latest_release: date | None
    lag: Literal["major", "minor", "patch", "none", "ahead", "unknown"]
    days_since_release: int | None
    stale: bool
    status: Literal["ok", "outdated", "stale", "unknown"]
    critical: bool
    error: str | None = None


class TechCheckOutput(SkillOutput):
    dependencies: list[DependencyStatus]


class FeedInput(BaseModel):
    url: str
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    max_items: int = Field(50, ge=1, le=500)
    kind: Literal["news", "tech", "market", "regulation", "competitor"] = "news"


class FeedOutput(SkillOutput):
    fetched: int
    ingested: int
    duplicates: int
    ids: list[str]
    error: str | None = None


class PrepareInput(BaseModel):
    horizon_days: int = Field(30, ge=1, le=365)


class AgendaItem(BaseModel):
    title: str
    reason: str
    source: Literal["risque", "tendance", "technologie", "revue", "marché"]
    priority: int = Field(ge=1, le=10)
    suggested_deadline: date


class PrepareOutput(SkillOutput):
    agenda: list[AgendaItem]


class AssessOutput(SkillOutput):
    signals: int
    risks: int
    dependencies: int
    monitored_series: int


# -- utilitaires de flux ------------------------------------------------------------------------------
def parse_feed(text: str, use_feedparser: bool = True) -> list[dict[str, Any]]:
    """Analyse un flux RSS 2.0 ou Atom. ``feedparser`` est utilisé s'il est installé (extra « watch »)."""

    if len(text) > 5_000_000:
        raise ValueError("flux trop volumineux")
    if use_feedparser:
        try:
            import feedparser
        except ImportError:
            feedparser = None
        if feedparser is not None:
            parsed = feedparser.parse(text)
            items = []
            for entry in parsed.entries:
                published = None
                for key in ("published_parsed", "updated_parsed"):
                    value = entry.get(key)
                    if value:
                        published = datetime(value[0], value[1], value[2], value[3], value[4], value[5], tzinfo=UTC)
                        break
                items.append(
                    {
                        "title": entry.get("title", "").strip(),
                        "url": entry.get("link"),
                        "published_at": published,
                        "text": entry.get("summary", ""),
                        "tags": [t.get("term", "") for t in entry.get("tags", []) if t.get("term")],
                    }
                )
            if items or not parsed.get("bozo"):
                return items
    root = ET.fromstring(text)  # noqa: S314 — expat moderne protège contre les bombes XML d'expansion
    items = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in root.iter("item"):
        raw_date = item.findtext("pubDate")
        published = None
        if raw_date:
            try:
                published = email.utils.parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                published = None
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "url": item.findtext("link"),
                "published_at": published,
                "text": item.findtext("description") or "",
                "tags": [c.text.strip() for c in item.findall("category") if c.text and c.text.strip()],
            }
        )
    for entry in root.iter(f"{atom}entry"):
        link = entry.find(f"{atom}link")
        raw_date = entry.findtext(f"{atom}published") or entry.findtext(f"{atom}updated")
        items.append(
            {
                "title": (entry.findtext(f"{atom}title") or "").strip(),
                "url": link.get("href") if link is not None else None,
                "published_at": parse_dt(raw_date) if raw_date else None,
                "text": entry.findtext(f"{atom}summary") or entry.findtext(f"{atom}content") or "",
                "tags": [c.get("term", "") for c in entry.findall(f"{atom}category") if c.get("term")],
            }
        )
    return items


class WatchAnticipationSkill(Skill):
    name = "watch-anticipation"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        # Injectables (les tests remplacent ces fonctions : aucun appel réseau en test).
        self.fetcher: Fetcher = _default_json_fetcher
        self.text_fetcher: Callable[[str], str] = _default_text_fetcher

    # -- accès espace de travail ---------------------------------------------------------------
    @staticmethod
    def _stored_signals(ctx: SkillContext) -> list[SignalModel]:
        return [SignalModel.model_validate(s) for s in ctx.store.list("signal")]

    @staticmethod
    def _stored_series(ctx: SkillContext, min_points: int) -> list[Series]:
        series = [Series.model_validate(s) for s in ctx.store.list("metric_series")]
        known = {s.metric for s in series}
        grouped: dict[str, list[SeriesPoint]] = defaultdict(list)
        for row in ctx.db.query("SELECT metric, value, at FROM observations ORDER BY at, seq"):
            grouped[row["metric"]].append(SeriesPoint(at=row["at"], value=row["value"]))
        for metric, points in grouped.items():
            if metric not in known and len(points) >= min_points:
                series.append(Series(metric=metric, points=points))
        return series

    def _store_signal(self, ctx: SkillContext, signal: SignalModel) -> tuple[str, bool]:
        signal_id = signal.id or "sig_" + hashlib.sha256((signal.url or signal.title).encode()).hexdigest()[:16]
        if ctx.store.get("signal", signal_id) is not None:
            return signal_id, False
        record = signal.model_copy(update={"id": signal_id})
        ctx.store.put("signal", signal_id, record.model_dump(mode="json"))
        return signal_id, True

    # -- actions --------------------------------------------------------------------------------
    @action("Diagnostic de veille : tendances, risques, dépendances, séries surveillées")
    def assess(self, ctx: SkillContext, data: EmptyInput) -> AssessOutput:
        signals = self._stored_signals(ctx)
        risks = ctx.store.list("risk")
        deps = ctx.store.list("tech_dependency")
        series = self._stored_series(ctx, 8)
        out = AssessOutput(
            signals=len(signals),
            risks=len(risks),
            dependencies=len(deps),
            monitored_series=len(series),
            summary="",
        )
        if not (signals or risks or deps or series):
            out.summary = (
                "Aucune donnée de veille : importez des « signal », « risk », « tech_dependency » "
                "ou des séries « metric_series » (ou utilisez ingest_signals / fetch_feed)."
            )
            return out
        parts: list[str] = []
        if signals:
            trends = self.detect_trends(ctx, TrendsInput(signals=signals))
            parts.append(f"{len(trends.emerging)} tendance(s) émergente(s)")
            out.metrics["watch.emerging_terms"] = float(len(trends.emerging))
            self._absorb(out, trends)
        if risks:
            assessed = self.assess_risks(ctx, RisksInput())
            parts.append(f"{assessed.heatmap.get('critique', 0)} risque(s) critique(s)")
            self._absorb(out, assessed)
        if deps:
            tech = self.check_technologies(ctx, TechCheckInput(offline=True, persist=False))
            outdated = sum(1 for d in tech.dependencies if d.status in ("outdated", "stale"))
            parts.append(f"{outdated} dépendance(s) à surveiller")
            self._absorb(out, tech)
        if series:
            changes = self.detect_market_changes(ctx, MarketChangesInput(series=series))
            flagged = sum(1 for c in changes.series if c.anomaly or c.change_index is not None)
            parts.append(f"{flagged} série(s) en rupture ou anomalie")
            self._absorb(out, changes)
        out.summary = "Veille : " + ", ".join(parts) + "."
        return out

    @staticmethod
    def _absorb(target: SkillOutput, source: SkillOutput) -> None:
        target.insights.extend(source.insights)
        target.recommendations.extend(source.recommendations)
        target.metrics.update(source.metrics)

    @action("Enregistrer des signaux de veille (dédoublonnés par URL ou titre)", risk="write")
    def ingest_signals(self, ctx: SkillContext, data: SignalsInput) -> IngestOutput:
        ids: list[str] = []
        duplicates = 0
        for signal in data.signals:
            signal_id, created = self._store_signal(ctx, signal)
            ids.append(signal_id)
            duplicates += 0 if created else 1
        ingested = len(ids) - duplicates
        return IngestOutput(
            ingested=ingested,
            duplicates=duplicates,
            ids=ids,
            summary=f"{ingested} signal(aux) enregistré(s), {duplicates} doublon(s) ignoré(s).",
        )

    @action("Détecter les tendances émergentes et en déclin (fréquence récente vs référence, z de Poisson)")
    def detect_trends(self, ctx: SkillContext, data: TrendsInput) -> TrendsOutput:
        signals = data.signals or self._stored_signals(ctx)
        now = ctx.clock.now()
        recent_start = now - timedelta(days=data.window_days)
        baseline_start = recent_start - timedelta(days=data.baseline_days)
        recent: Counter[str] = Counter()
        baseline: Counter[str] = Counter()
        n_recent = n_base = 0
        for signal in signals:
            published = parse_dt(signal.published_at)
            if recent_start < published <= now:
                recent.update(_terms(signal))
                n_recent += 1
            elif baseline_start < published <= recent_start:
                baseline.update(_terms(signal))
                n_base += 1
        scale = data.window_days / data.baseline_days
        emerging: list[TermTrend] = []
        declining: list[TermTrend] = []
        for term in set(recent) | set(baseline):
            r, b = recent[term], baseline[term]
            expected = max(b * scale, 0.5)
            z = (r - expected) / math.sqrt(expected)
            ratio = (r + 1) / (b * scale + 1)
            trend = TermTrend(term=term, recent=r, baseline=b, ratio=round(ratio, 3), z=round(z, 3))
            if z >= data.z_threshold and r >= data.min_occurrences:
                emerging.append(trend)
            elif z <= -data.z_threshold and b >= data.min_occurrences:
                declining.append(trend)
        emerging.sort(key=lambda t: (-t.z, t.term))
        declining.sort(key=lambda t: (t.z, t.term))
        out = TrendsOutput(
            recent_signals=n_recent,
            baseline_signals=n_base,
            emerging=emerging[:20],
            declining=declining[:20],
            summary=(
                f"{len(emerging)} terme(s) émergent(s), {len(declining)} en déclin "
                f"({n_recent} signaux récents / {n_base} de référence)."
            ),
        )
        out.metrics["watch.emerging_terms"] = float(len(emerging))
        for trend in emerging[:5]:
            out.insights.append(
                Insight(
                    kind="trend",
                    severity="info",
                    title=f"Tendance émergente : {trend.term}",
                    detail=(f"{trend.recent} mention(s) récentes contre {trend.baseline} en référence (z={trend.z})."),
                    value=trend.z,
                )
            )
        if emerging:
            top = emerging[0]
            out.recommendations.append(
                Recommendation(
                    title=f"Évaluer l'opportunité liée à la tendance « {top.term} »",
                    rationale=f"Hausse statistiquement marquée des mentions (z={top.z}, ×{top.ratio}).",
                    impact=min(10.0, 4 + top.z),
                    effort=2,
                    confidence=min(0.85, 0.4 + 0.05 * top.recent),
                    urgency=min(10.0, 3 + top.z),
                    risk=0.1,
                    horizon_days=30,
                    tags=["veille", "tendance"],
                    evidence=[f"{t.term}: {t.recent} vs {t.baseline}" for t in emerging[:5]],
                    follow_up=FollowUp(skill="business-intelligence", action="assess"),
                )
            )
        return out

    @action("Détecter les changements de marché : ruptures (CUSUM), anomalies (z robuste), variations")
    def detect_market_changes(self, ctx: SkillContext, data: MarketChangesInput) -> MarketChangesOutput:
        series = data.series or self._stored_series(ctx, data.min_points)
        results: list[SeriesChange] = []
        for item in series:
            points = sorted(item.points, key=lambda p: parse_dt(p.at))
            if len(points) < data.min_points:
                continue
            values = [p.value for p in points]
            index = cusum_change_point(values)
            z = robust_z(values[-1], values[:-1])
            z_value = 0.0 if math.isnan(z) else (math.copysign(99.0, z) if math.isinf(z) else z)
            k = max(2, len(values) // 4)
            previous = values[-2 * k : -k]
            recent_mean, previous_mean = mean(values[-k:]), mean(previous) if previous else None
            change = (recent_mean - previous_mean) / abs(previous_mean) if previous_mean not in (None, 0) else None
            results.append(
                SeriesChange(
                    metric=item.metric,
                    points=len(values),
                    change_index=index,
                    change_at=str(points[index].at) if index is not None else None,
                    last_value=values[-1],
                    robust_z=round(z_value, 3),
                    anomaly=abs(z_value) >= data.z_threshold,
                    recent_change=round(change, 4) if change is not None else None,
                )
            )
        out = MarketChangesOutput(series=results, summary="")
        flagged = [r for r in results if r.anomaly or r.change_index is not None]
        out.summary = f"{len(results)} série(s) analysée(s), {len(flagged)} en rupture ou anomalie."
        for flagged_change in flagged:
            what = "anomalie" if flagged_change.anomaly else "rupture"
            detail = (
                f"Dernière valeur {flagged_change.last_value} (z robuste {flagged_change.robust_z})"
                + (
                    f", rupture détectée au point {flagged_change.change_index} ({flagged_change.change_at})"
                    if flagged_change.change_index is not None
                    else ""
                )
                + (
                    f", variation récente {flagged_change.recent_change:+.1%}"
                    if flagged_change.recent_change is not None
                    else ""
                )
                + "."
            )
            out.insights.append(
                Insight(
                    kind="anomaly",
                    severity="warning" if not flagged_change.anomaly else "critical",
                    title=f"{what.capitalize()} sur {flagged_change.metric}",
                    detail=detail,
                    metric=flagged_change.metric,
                    value=flagged_change.last_value,
                )
            )
            out.recommendations.append(
                Recommendation(
                    title=f"Analyser la {what} de « {flagged_change.metric} »",
                    rationale=detail,
                    impact=6 if flagged_change.anomaly else 5,
                    effort=2,
                    confidence=0.75 if flagged_change.anomaly else 0.6,
                    urgency=8 if flagged_change.anomaly else 6,
                    risk=0.1,
                    tags=["veille", "marché"],
                    evidence=[detail],
                    follow_up=FollowUp(skill="analysis-optimization", action="assess"),
                )
            )
        out.metrics["watch.series_flagged"] = float(len(flagged))
        return out

    @action("Évaluer les risques : score probabilité × impact × vitesse × tendance, carte et mitigations")
    def assess_risks(self, ctx: SkillContext, data: RisksInput) -> RisksOutput:
        risks = data.risks or [RiskModel.model_validate(r) for r in ctx.store.list("risk")]
        factor = {"rising": 1.2, "stable": 1.0, "falling": 0.8}
        assessed: list[RiskAssessment] = []
        for risk in risks:
            score = risk.probability * risk.impact * (1 + (risk.velocity - 5) / 10) * factor[risk.indicator_trend]
            level: Literal["critique", "élevé", "moyen", "faible"] = (
                "critique" if score >= 6 else "élevé" if score >= 4 else "moyen" if score >= 2 else "faible"
            )
            assessed.append(
                RiskAssessment(
                    id=risk.id,
                    title=risk.title,
                    category=risk.category,
                    score=round(score, 3),
                    level=level,
                    mitigation=risk.mitigation or DEFAULT_MITIGATIONS[risk.category],
                    owner=risk.owner,
                )
            )
        assessed.sort(key=lambda r: (-r.score, r.id))
        heatmap = Counter(r.level for r in assessed)
        out = RisksOutput(
            risks=assessed,
            heatmap={k: heatmap.get(k, 0) for k in ("critique", "élevé", "moyen", "faible")},
            summary=(
                f"{len(assessed)} risque(s) : {heatmap.get('critique', 0)} critique(s), "
                f"{heatmap.get('élevé', 0)} élevé(s)."
                if assessed
                else "Aucun risque enregistré."
            ),
        )
        if assessed:
            out.metrics["risk.top_score"] = assessed[0].score
            out.metrics["risk.critical_count"] = float(heatmap.get("critique", 0))
        for assessed_risk in assessed:
            if assessed_risk.level in ("critique", "élevé"):
                out.insights.append(
                    Insight(
                        kind="risk",
                        severity="critical" if assessed_risk.level == "critique" else "warning",
                        title=f"Risque {assessed_risk.level} : {assessed_risk.title}",
                        detail=f"Score {assessed_risk.score} ({assessed_risk.category}).",
                        value=assessed_risk.score,
                    )
                )
            if assessed_risk.level == "critique":
                out.recommendations.append(
                    Recommendation(
                        title=f"Traiter le risque « {assessed_risk.title} »",
                        expected_metric=TargetMetric(metric="risk.critical_count", direction="decrease"),
                        rationale=assessed_risk.mitigation,
                        impact=min(10.0, assessed_risk.score),
                        effort=4,
                        confidence=0.7,
                        urgency=min(10.0, 2 + assessed_risk.score),
                        risk=0.1,
                        tags=["risque", assessed_risk.category],
                        evidence=[f"score={assessed_risk.score}"],
                    )
                )
        return out

    @action("Vérifier les versions des technologies suivies (PyPI / npm) : retard et maintenance", risk="write")
    def check_technologies(self, ctx: SkillContext, data: TechCheckInput) -> TechCheckOutput:
        from_store = not data.dependencies
        deps = data.dependencies or [TechDependency.model_validate(d) for d in ctx.store.list("tech_dependency")]
        today = ctx.clock.now().date()
        results: list[DependencyStatus] = []
        for dep in deps:
            latest, released, error = dep.latest_version, dep.latest_release, None
            if not data.offline:
                try:
                    latest, released = self._lookup(dep)
                except Exception as exc:  # réseau, format inattendu : jamais d'exception vers l'appelant
                    error = f"{type(exc).__name__}: {exc}"[:200]
            current_t, latest_t = _version_tuple(dep.current_version), _version_tuple(latest or "")
            if current_t is None or latest_t is None:
                lag: Literal["major", "minor", "patch", "none", "ahead", "unknown"] = "unknown"
            elif latest_t[0] > current_t[0]:
                lag = "major"
            elif latest_t == current_t:
                lag = "none"
            elif latest_t < current_t:
                lag = "ahead"
            elif latest_t[1] > current_t[1]:
                lag = "minor"
            else:
                lag = "patch"
            days = (today - released).days if released else None
            stale = days is not None and days > data.stale_days
            status: Literal["ok", "outdated", "stale", "unknown"] = (
                "unknown"
                if lag == "unknown"
                else "stale"
                if stale
                else "outdated"
                if lag in ("major", "minor", "patch")
                else "ok"
            )
            results.append(
                DependencyStatus(
                    name=dep.name,
                    ecosystem=dep.ecosystem,
                    current_version=dep.current_version,
                    latest_version=latest,
                    latest_release=released,
                    lag=lag,
                    days_since_release=days,
                    stale=stale,
                    status=status,
                    critical=dep.critical,
                    error=error,
                )
            )
            if from_store and data.persist and not data.offline and error is None and dep.id:
                record = dep.model_copy(update={"latest_version": latest, "latest_release": released})
                ctx.store.put("tech_dependency", dep.id, record.model_dump(mode="json"))
        out = TechCheckOutput(dependencies=results, summary="")
        outdated = [r for r in results if r.status == "outdated"]
        stale_items = [r for r in results if r.status == "stale"]
        unknown = [r for r in results if r.status == "unknown"]
        out.summary = (
            f"{len(results)} dépendance(s) : {len(outdated)} en retard, {len(stale_items)} à maintenance "
            f"douteuse, {len(unknown)} indéterminée(s)."
        )
        out.metrics["tech.outdated"] = float(len(outdated))
        out.metrics["tech.stale"] = float(len(stale_items))
        for dep_status in results:
            if dep_status.status == "stale":
                out.insights.append(
                    Insight(
                        kind="risk",
                        severity="warning" if not dep_status.critical else "critical",
                        title=f"Maintenance douteuse : {dep_status.name}",
                        detail=f"Dernière version publiée il y a {dep_status.days_since_release} jours.",
                    )
                )
                out.recommendations.append(
                    Recommendation(
                        title=f"Évaluer une alternative maintenue à {dep_status.name}",
                        rationale=(
                            f"Aucune publication depuis {dep_status.days_since_release} jours : risque d'abandon."
                        ),
                        impact=7 if dep_status.critical else 4,
                        effort=5,
                        confidence=0.6,
                        urgency=6 if dep_status.critical else 3,
                        risk=0.2,
                        tags=["technologie", "veille"],
                    )
                )
            elif dep_status.status == "outdated" and (dep_status.lag == "major" or dep_status.critical):
                out.recommendations.append(
                    Recommendation(
                        title=(
                            f"Mettre à jour {dep_status.name} {dep_status.current_version} "
                            f"→ {dep_status.latest_version}"
                        ),
                        expected_metric=TargetMetric(metric="tech.outdated", direction="decrease"),
                        rationale=f"Retard {dep_status.lag}"
                        + (" sur une dépendance critique." if dep_status.critical else "."),
                        impact=6 if dep_status.critical else 4,
                        effort=4 if dep_status.lag == "major" else 2,
                        confidence=0.7,
                        urgency=7 if dep_status.critical else 4,
                        risk=0.25 if dep_status.lag == "major" else 0.1,
                        tags=["technologie", "maintenance"],
                    )
                )
        return out

    def _lookup(self, dep: TechDependency) -> tuple[str, date | None]:
        if dep.ecosystem == "pypi":
            payload = self.fetcher(f"https://pypi.org/pypi/{dep.name}/json")
            version = str(payload["info"]["version"])
            files = payload.get("releases", {}).get(version) or []
            stamp = files[0].get("upload_time_iso_8601") or files[0].get("upload_time") if files else None
            return version, parse_dt(stamp).date() if stamp else None
        payload = self.fetcher(f"https://registry.npmjs.org/{dep.name}")
        version = str(payload["dist-tags"]["latest"])
        stamp = payload.get("time", {}).get(version)
        return version, parse_dt(stamp.replace("Z", "+00:00")).date() if stamp else None

    @action("Lire un flux RSS/Atom et enregistrer ses entrées comme signaux", risk="write")
    def fetch_feed(self, ctx: SkillContext, data: FeedInput) -> FeedOutput:
        try:
            items = parse_feed(self.text_fetcher(data.url))
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:200]
            return FeedOutput(
                fetched=0,
                ingested=0,
                duplicates=0,
                ids=[],
                error=message,
                summary=f"Flux illisible ({message}).",
            )
        ids: list[str] = []
        duplicates = 0
        now = ctx.clock.now()
        source = data.source or data.url
        for item in items[: data.max_items]:
            if not item["title"]:
                continue
            signal = SignalModel(
                title=item["title"],
                source=source,
                url=item.get("url"),
                published_at=item.get("published_at") or now,
                tags=[*data.tags, *item.get("tags", [])],
                text=re.sub(r"<[^>]+>", " ", item.get("text") or "")[:2000],
                kind=data.kind,
            )
            signal_id, created = self._store_signal(ctx, signal)
            ids.append(signal_id)
            duplicates += 0 if created else 1
        ingested = len(ids) - duplicates
        return FeedOutput(
            fetched=len(items),
            ingested=ingested,
            duplicates=duplicates,
            ids=ids,
            summary=f"{len(items)} entrée(s) lue(s), {ingested} nouvelle(s), {duplicates} doublon(s).",
        )

    @action("Préparer l'agenda des prochaines décisions (risques, tendances, technologies, revues dues)")
    def prepare_decisions(self, ctx: SkillContext, data: PrepareInput) -> PrepareOutput:
        today = ctx.clock.now().date()
        horizon = timedelta(days=data.horizon_days)
        agenda: list[AgendaItem] = []
        if ctx.store.count("risk"):
            for risk in self.assess_risks(ctx, RisksInput()).risks:
                if risk.level in ("critique", "élevé"):
                    agenda.append(
                        AgendaItem(
                            title=f"Décider du traitement du risque « {risk.title} »",
                            reason=f"Risque {risk.level} (score {risk.score}). Piste : {risk.mitigation}",
                            source="risque",
                            priority=10 if risk.level == "critique" else 7,
                            suggested_deadline=today + (timedelta(days=7) if risk.level == "critique" else horizon / 2),
                        )
                    )
        signals = self._stored_signals(ctx)
        if signals:
            for trend in self.detect_trends(ctx, TrendsInput(signals=signals)).emerging[:3]:
                agenda.append(
                    AgendaItem(
                        title=f"Se positionner sur la tendance « {trend.term} »",
                        reason=f"{trend.recent} mentions récentes vs {trend.baseline} (z={trend.z}).",
                        source="tendance",
                        priority=min(9, 4 + int(trend.z)),
                        suggested_deadline=today + horizon,
                    )
                )
        if ctx.store.count("tech_dependency"):
            tech = self.check_technologies(ctx, TechCheckInput(offline=True, persist=False))
            for dep in tech.dependencies:
                if dep.critical and dep.status in ("outdated", "stale"):
                    agenda.append(
                        AgendaItem(
                            title=f"Planifier la mise à niveau de {dep.name}",
                            reason=(
                                f"Dépendance critique {dep.status} ({dep.current_version} → {dep.latest_version})."
                            ),
                            source="technologie",
                            priority=6,
                            suggested_deadline=today + horizon,
                        )
                    )
        for decision in ctx.memory.due_for_review():
            agenda.append(
                AgendaItem(
                    title=f"Mesurer le résultat de « {decision.title} »",
                    reason="Date de revue atteinte : mesurer pour apprendre.",
                    source="revue",
                    priority=7,
                    suggested_deadline=today + timedelta(days=3),
                )
            )
        agenda.sort(key=lambda i: (-i.priority, i.suggested_deadline, i.title))
        out = PrepareOutput(
            agenda=agenda, summary=f"{len(agenda)} décision(s) à préparer sous {data.horizon_days} jours."
        )
        for item in agenda[:5]:
            out.recommendations.append(
                Recommendation(
                    title=item.title,
                    rationale=item.reason,
                    impact=min(10.0, float(item.priority)),
                    effort=2,
                    confidence=0.7,
                    urgency=min(10.0, float(item.priority)),
                    risk=0.1,
                    horizon_days=(item.suggested_deadline - today).days,
                    tags=["agenda", item.source],
                )
            )
        return out


SKILL = WatchAnticipationSkill
