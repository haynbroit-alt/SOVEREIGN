from __future__ import annotations

from datetime import date

import pytest

from sovereign.skills.watch_anticipation import parse_feed

SKILL = "watch-anticipation"

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Veille</title>
<item><title>Les agents IA autonomes arrivent en PME</title><link>https://ex.org/a</link>
<pubDate>Tue, 10 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Analyse&lt;/p&gt;</description>
<category>ia</category></item>
<item><title>Nouvelle régulation des données</title><link>https://ex.org/b</link>
<pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Edge computing en hausse</title><link href="https://ex.org/c"/>
<updated>2026-11-12T08:00:00Z</updated><summary>Résumé</summary><category term="tech"/></entry>
</feed>"""


def _signal(title, day, tags=(), url=None):
    return {
        "title": title,
        "source": "test",
        "published_at": f"{day}T10:00:00Z",
        "tags": list(tags),
        "url": url,
    }


def test_ingest_deduplicates(kernel):
    signals = [
        _signal("Hausse du SaaS vertical", "2026-11-10", url="https://ex.org/1"),
        _signal("Hausse du SaaS vertical (copie)", "2026-11-11", url="https://ex.org/1"),
    ]
    out = kernel.run_skill(SKILL, "ingest_signals", {"signals": signals})
    assert out.ingested == 1 and out.duplicates == 1 and len(set(out.ids)) == 1
    assert kernel.store.count("signal") == 1


def test_emerging_and_declining_terms(kernel):
    signals = [_signal(f"Montée de l'automatisation agentique {i}", f"2026-11-{10 + i:02d}") for i in range(5)]
    signals.append(_signal("Une remarque agentique isolée", "2026-09-15"))
    signals += [_signal(f"Blockchain partout épisode {i}", f"2026-09-{10 + i:02d}") for i in range(12)]
    kernel.run_skill(SKILL, "ingest_signals", {"signals": signals})
    out = kernel.run_skill(SKILL, "detect_trends", {})
    emerging = {t.term: t for t in out.emerging}
    assert "agentique" in emerging
    assert emerging["agentique"].recent == 5 and emerging["agentique"].baseline == 1
    assert emerging["agentique"].z == pytest.approx((5 - 0.5) / 0.5**0.5, abs=1e-3)
    assert "blockchain" in {t.term for t in out.declining}
    assert out.recent_signals == 5 and out.baseline_signals == 13
    assert out.recommendations and out.recommendations[0].follow_up.skill == "business-intelligence"


def test_market_change_detection_from_input_and_observations(kernel):
    stable = [{"at": f"2026-10-{d:02d}", "value": 100 + (d % 3)} for d in range(1, 13)]
    jump = [{"at": f"2026-10-{d:02d}", "value": 100.0 + (d % 2)} for d in range(1, 9)]
    jump += [{"at": f"2026-10-{d:02d}", "value": 140.0 + (d % 2)} for d in range(9, 13)]
    spike = [{"at": f"2026-10-{d:02d}", "value": 50.0 + (d % 2)} for d in range(1, 12)] + [
        {"at": "2026-10-12", "value": 120.0}
    ]
    out = kernel.run_skill(
        SKILL,
        "detect_market_changes",
        {
            "series": [
                {"metric": "stable", "points": stable},
                {"metric": "rupture", "points": jump},
                {"metric": "pic", "points": spike},
            ]
        },
    )
    by_metric = {s.metric: s for s in out.series}
    assert by_metric["stable"].change_index is None and not by_metric["stable"].anomaly
    assert by_metric["rupture"].change_index is not None and by_metric["rupture"].recent_change > 0.2
    assert by_metric["pic"].anomaly
    for i in range(10):
        kernel.db.execute(
            "INSERT INTO observations(metric, value, at, source) VALUES (?, ?, ?, 'test')",
            ("ventes.jour", 10.0 + i % 2, f"2026-11-{i + 1:02d}T00:00:00+00:00"),
        )
    assessed = kernel.run_skill(SKILL, "assess")
    assert assessed.monitored_series == 1


def test_risk_scoring_and_levels(kernel):
    kernel.store.put(
        "risk",
        "r1",
        {
            "id": "r1",
            "title": "Concurrent low-cost",
            "category": "competition",
            "probability": 0.8,
            "impact": 9,
            "velocity": 8,
            "indicator_trend": "rising",
        },
    )
    kernel.store.put(
        "risk",
        "r2",
        {"id": "r2", "title": "Panne hébergeur", "category": "tech", "probability": 0.2, "impact": 5},
    )
    out = kernel.run_skill(SKILL, "assess_risks", {})
    first, second = out.risks
    assert first.id == "r1" and first.score == pytest.approx(0.8 * 9 * 1.3 * 1.2, abs=1e-3)
    assert first.level == "critique" and second.level == "faible"
    assert "fidélisation" in first.mitigation  # mitigation par défaut de la catégorie
    assert out.heatmap["critique"] == 1 and out.metrics["risk.critical_count"] == 1
    assert out.recommendations[0].title.startswith("Traiter le risque")


def test_check_technologies_with_fake_registries(kernel):
    skill = kernel.registry.get(SKILL)
    calls = []

    def fetcher(url):
        calls.append(url)
        if "pypi" in url and "fastapi" in url:
            return {
                "info": {"version": "0.163.1"},
                "releases": {"0.163.1": [{"upload_time_iso_8601": "2026-11-15T10:00:00Z"}]},
            }
        if "npmjs" in url:
            return {"dist-tags": {"latest": "4.2.1"}, "time": {"4.2.1": "2024-01-01T00:00:00.000Z"}}
        raise ConnectionError("hors ligne")

    skill.fetcher = fetcher
    out = kernel.run_skill(
        SKILL,
        "check_technologies",
        {
            "dependencies": [
                {"name": "fastapi", "ecosystem": "pypi", "current_version": "0.110.0", "critical": True},
                {"name": "leftpad", "ecosystem": "npm", "current_version": "4.2.1"},
                {"name": "ghost", "ecosystem": "pypi", "current_version": "1.0.0"},
            ]
        },
    )
    deps = {d.name: d for d in out.dependencies}
    assert deps["fastapi"].lag == "minor" and deps["fastapi"].status == "outdated"
    assert deps["fastapi"].latest_release == date(2026, 11, 15)
    assert deps["leftpad"].lag == "none" and deps["leftpad"].stale and deps["leftpad"].status == "stale"
    assert deps["ghost"].status == "unknown" and "ConnectionError" in deps["ghost"].error
    assert len(calls) == 3
    assert any("fastapi" in r.title for r in out.recommendations)
    assert any("alternative" in r.title for r in out.recommendations)


def test_versions_persisted_then_used_offline(kernel):
    skill = kernel.registry.get(SKILL)
    kernel.store.put(
        "tech_dependency",
        "t1",
        {"id": "t1", "name": "mcp", "ecosystem": "pypi", "current_version": "1.9.0", "critical": True},
    )
    skill.fetcher = lambda url: {
        "info": {"version": "2.3.0"},
        "releases": {"2.3.0": [{"upload_time": "2026-10-02T10:00:00"}]},
    }
    out = kernel.run_skill(SKILL, "check_technologies", {})
    assert out.dependencies[0].lag == "major"
    assert kernel.store.get("tech_dependency", "t1")["latest_version"] == "2.3.0"

    def boom(url):
        raise AssertionError("aucun appel réseau attendu hors ligne")

    skill.fetcher = boom
    offline = kernel.run_skill(SKILL, "check_technologies", {"offline": True})
    assert offline.dependencies[0].latest_version == "2.3.0"
    agenda = kernel.run_skill(SKILL, "prepare_decisions", {})
    assert any(item.source == "technologie" for item in agenda.agenda)


def test_version_ahead_is_ok(kernel):
    kernel.registry.get(SKILL).fetcher = lambda url: {"info": {"version": "2.9"}, "releases": {}}
    out = kernel.run_skill(SKILL, "check_technologies", {"dependencies": [{"name": "x", "current_version": "3.0.0"}]})
    assert out.dependencies[0].lag == "ahead" and out.dependencies[0].status == "ok"


@pytest.mark.parametrize("use_feedparser", [True, False])
def test_parse_rss_and_atom(use_feedparser):
    rss = parse_feed(RSS, use_feedparser=use_feedparser)
    assert [i["title"] for i in rss] == [
        "Les agents IA autonomes arrivent en PME",
        "Nouvelle régulation des données",
    ]
    assert rss[0]["published_at"].year == 2026 and "ia" in rss[0]["tags"]
    atom = parse_feed(ATOM, use_feedparser=use_feedparser)
    assert atom[0]["url"] == "https://ex.org/c" and atom[0]["tags"] == ["tech"]


def test_fetch_feed_ingests_and_handles_errors(kernel):
    skill = kernel.registry.get(SKILL)
    skill.text_fetcher = lambda url: RSS
    out = kernel.run_skill(SKILL, "fetch_feed", {"url": "https://ex.org/feed", "tags": ["marché"]})
    assert out.fetched == 2 and out.ingested == 2 and out.error is None
    stored = kernel.store.get("signal", out.ids[0])
    assert "<p>" not in stored["text"] and "marché" in stored["tags"]
    again = kernel.run_skill(SKILL, "fetch_feed", {"url": "https://ex.org/feed"})
    assert again.duplicates == 2

    def broken(url):
        raise TimeoutError("trop long")

    skill.text_fetcher = broken
    failed = kernel.run_skill(SKILL, "fetch_feed", {"url": "https://ex.org/feed"})
    assert failed.ingested == 0 and "TimeoutError" in failed.error


def test_prepare_decisions_prioritizes(kernel, clock):
    kernel.store.put(
        "risk",
        "r1",
        {
            "id": "r1",
            "title": "Fuite de données",
            "category": "legal",
            "probability": 0.7,
            "impact": 10,
            "velocity": 7,
        },
    )
    decision = kernel.memory.record("Tarif Pro", "49 €", review_at="2026-11-10T00:00:00Z")
    out = kernel.run_skill(SKILL, "prepare_decisions", {"horizon_days": 30})
    assert out.agenda[0].source == "risque" and out.agenda[0].priority == 10
    assert out.agenda[0].suggested_deadline == date(2026, 11, 24)
    assert any(decision.title in item.title for item in out.agenda)
    assert out.recommendations


def test_assess_empty_workspace(kernel):
    out = kernel.run_skill(SKILL, "assess")
    assert "Aucune donnée de veille" in out.summary and out.signals == 0


def test_assess_never_uses_network(kernel):
    skill = kernel.registry.get(SKILL)

    def boom(url):
        raise AssertionError("assess ne doit pas appeler le réseau")

    skill.fetcher = boom
    kernel.store.put("tech_dependency", "t1", {"id": "t1", "name": "lib", "current_version": "1.0.0"})
    out = kernel.run_skill(SKILL, "assess")
    assert out.dependencies == 1 and "dépendance" in out.summary


def test_invalid_signal_rejected(kernel):
    with pytest.raises(Exception):  # noqa: B017 — erreur de validation Pydantic
        kernel.run_skill(SKILL, "ingest_signals", {"signals": []})
