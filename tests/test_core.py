from __future__ import annotations

import math
import sqlite3
import time

import pytest
from pydantic import ValidationError

from sovereign.config import FinanceSettings, Settings, load_settings
from sovereign.core import stats
from sovereign.core.clock import period_key
from sovereign.core.money import fmt, from_cents, to_cents, to_decimal
from sovereign.core.security import SignatureError, sign_payload, verify_webhook


def test_spend_ratio_cannot_exceed_ten_percent():
    with pytest.raises(ValidationError):
        FinanceSettings(spend_ratio="0.15")
    with pytest.raises(ValidationError):
        FinanceSettings(spend_ratio="0")
    assert FinanceSettings(spend_ratio="0.05").spend_ratio == to_decimal("0.05")


def test_env_cannot_raise_ratio(monkeypatch, tmp_path):
    monkeypatch.setenv("SOVEREIGN_SPEND_RATIO", "0.5")
    with pytest.raises(ValidationError):
        load_settings(tmp_path)


def test_settings_from_toml_and_env(monkeypatch, tmp_path):
    (tmp_path / "sovereign.toml").write_text('[finance]\ncurrency = "USD"\n[llm]\nmodel = "claude-sonnet-5-5"\n')
    monkeypatch.setenv("SOVEREIGN_WEBHOOK_SECRET_GITHUB", "s3cret")
    settings = load_settings(tmp_path)
    assert settings.finance.currency == "USD"
    assert settings.llm.model == "claude-sonnet-5-5"
    assert settings.security.webhook_secrets["github"] == "s3cret"
    assert settings.db_path == tmp_path / "sovereign.db"
    assert isinstance(Settings().llm.enabled, bool) and Settings().llm.enabled is False


def test_money_is_exact():
    assert to_cents("0.1") + to_cents("0.2") == to_cents("0.3")
    assert from_cents(12345) == to_decimal("123.45")
    assert fmt("1234567.5") == "1 234 567,50 €"
    with pytest.raises(ValueError):
        to_decimal("nan")


def test_period_keys():
    from datetime import datetime

    at = datetime(2026, 11, 17)
    assert period_key("daily", at) == "2026-11-17"
    assert period_key("weekly", at) == "2026-W47"
    assert period_key("monthly", at) == "2026-11"


def test_linear_fit_and_forecast():
    fit = stats.linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
    assert fit.slope == pytest.approx(2)
    assert fit.intercept == pytest.approx(1)
    assert fit.r2 == pytest.approx(1)
    assert fit.predict(10) == pytest.approx(21)


def test_student_t_against_tables():
    # Valeurs critiques bilatérales tabulées : t(0,975 ; 10) = 2,228 ; t(0,975 ; 30) = 2,042
    assert stats.student_t_two_sided_p(2.228, 10) == pytest.approx(0.05, abs=5e-4)
    assert stats.student_t_two_sided_p(2.042, 30) == pytest.approx(0.05, abs=5e-4)
    assert stats.student_t_two_sided_p(0.0, 5) == pytest.approx(1.0)


def test_welch_detects_real_change_and_ignores_noise():
    before = [100, 102, 98, 101, 99, 100, 103, 97]
    after = [110, 112, 108, 111, 109, 110, 113, 107]
    result = stats.welch_t_test(before, after)
    assert result.significant and result.effect == pytest.approx(10)
    noise = stats.welch_t_test(before, [101, 99, 100, 102, 98, 100, 101, 99])
    assert not noise.significant


def test_two_proportion_z():
    result = stats.two_proportion_z_test(100, 1000, 140, 1000)
    assert result.significant
    assert result.relative_effect == pytest.approx(0.4)
    assert not stats.two_proportion_z_test(100, 1000, 104, 1000).significant


def test_cusum_finds_break():
    series = [10, 10.2, 9.8, 10.1, 9.9, 10, 10.1, 9.9, 14, 14.2, 13.9, 14.1]
    index = stats.cusum_change_point(series, baseline=8)
    assert index is not None and index >= 8
    assert stats.cusum_change_point([10, 10.1, 9.9, 10, 10.2, 9.8, 10.1, 9.9]) is None


def test_robust_z_and_beta():
    assert stats.robust_z(100, [10, 11, 9, 10, 10.5]) > 10
    posterior = stats.BetaPosterior(8, 2)
    assert posterior.mean == pytest.approx(0.75)
    assert 0 < posterior.stdev < 0.2
    assert stats.sigmoid(stats.logit(0.3)) == pytest.approx(0.3)
    assert math.isclose(stats.percentile([1, 2, 3, 4], 50), 2.5)


def test_webhook_signatures():
    body = b'{"type":"payment"}'
    headers = sign_payload("secret", body, timestamp=int(time.time()))
    verify_webhook("generic", "secret", body, headers)
    with pytest.raises(SignatureError):
        verify_webhook("generic", "wrong", body, headers)
    with pytest.raises(SignatureError):
        verify_webhook("generic", "secret", body, sign_payload("secret", body, timestamp=1))  # rejeu
    import hashlib
    import hmac

    gh = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    verify_webhook("github", "k", body, {"X-Hub-Signature-256": gh})
    ts = int(time.time())
    sig = hmac.new(b"whsec", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    verify_webhook("stripe", "whsec", body, {"Stripe-Signature": f"t={ts},v1={sig}"})
    with pytest.raises(SignatureError):
        verify_webhook("stripe", "whsec", body, {"Stripe-Signature": f"t={ts},v1=deadbeef"})


def test_api_keys(kernel):
    key_id, raw = kernel.api_keys.create("ci", ["write"])
    principal = kernel.api_keys.verify(raw)
    assert principal and principal.allows("read") and principal.allows("write") and not principal.allows("admin")
    assert kernel.api_keys.verify(raw + "x") is None
    assert kernel.api_keys.revoke(key_id)
    assert kernel.api_keys.verify(raw) is None
    with pytest.raises(ValueError):
        kernel.api_keys.create("bad", ["root"])
    stored = kernel.db.scalar("SELECT hash FROM api_keys WHERE id = ?", (key_id,))
    assert raw not in stored  # seule l'empreinte est stockée


def test_event_bus_dedup_and_dispatch(kernel):
    seen = []
    kernel.events.subscribe("finance.*", lambda e: seen.append(e.type))
    assert kernel.events.publish("finance.test", {"a": 1}, dedup_key="k1") is not None
    assert kernel.events.publish("finance.test", {"a": 1}, dedup_key="k1") is None
    kernel.events.publish("other.thing")
    assert seen == ["finance.test"]
    assert [e.type for e in kernel.events.recent("finance.*")] == ["finance.test"]


def test_failing_subscriber_does_not_break_bus(kernel):
    kernel.events.subscribe("*", lambda e: 1 / 0)
    assert kernel.events.publish("x.y") is not None


def test_record_store(kernel):
    kernel.store.put("market", "m1", {"name": "A"})
    kernel.store.put("market", "m1", {"name": "B"})
    assert kernel.store.get("market", "m1") == {"name": "B"}
    assert kernel.store.count("market") == 1
    assert kernel.store.kinds() == {"market": 1}
    assert kernel.store.delete("market", "m1")


def test_migrations_are_idempotent(tmp_path):
    from sovereign.core.db import MIGRATIONS, Database

    db = Database(tmp_path / "x.db")
    assert db.migrate() == len(MIGRATIONS)
    db.close()
    db2 = Database(tmp_path / "x.db")
    assert db2.scalar("SELECT COUNT(*) FROM schema_migrations") == len(MIGRATIONS)
    with pytest.raises(sqlite3.IntegrityError):
        db2.execute("INSERT INTO audit_log(at, actor, action, entity) VALUES ('t','a','b','c')")
        db2.execute("UPDATE audit_log SET actor = 'x'")
    db2.close()
