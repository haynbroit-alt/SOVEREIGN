"""Authentification et intégrité.

- Clés d'API à portées (scopes) : seules leurs empreintes SHA-256 sont stockées (clés à 256 bits d'entropie,
  un hachage lent n'apporte rien ici) ; comparaison en temps constant.
- Webhooks entrants : signatures HMAC-SHA256 vérifiées selon le schéma de chaque émetteur
  (générique SOVEREIGN horodaté, GitHub ``X-Hub-Signature-256``, Stripe ``Stripe-Signature``),
  avec fenêtre anti-rejeu.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass

from sovereign.core.clock import Clock, iso
from sovereign.core.db import Database
from sovereign.core.ids import new_id

SCOPES = ("read", "write", "admin", "webhook")
# admin implique write qui implique read.
_IMPLIED = {"admin": {"admin", "write", "read", "webhook"}, "write": {"write", "read"}, "read": {"read"},
            "webhook": {"webhook"}}


@dataclass(frozen=True)
class ApiPrincipal:
    key_id: str
    name: str
    scopes: frozenset[str]

    def allows(self, scope: str) -> bool:
        return scope in self.scopes


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKeyStore:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def create(self, name: str, scopes: list[str]) -> tuple[str, str]:
        unknown = set(scopes) - set(SCOPES)
        if unknown or not scopes:
            raise ValueError(f"portées invalides : {sorted(unknown) or 'aucune'}")
        raw = f"sov_{secrets.token_urlsafe(32)}"
        key_id = new_id("key")
        expanded: set[str] = set()
        for scope in scopes:
            expanded |= _IMPLIED[scope]
        self.db.execute(
            "INSERT INTO api_keys(id, name, prefix, hash, scopes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key_id, name, raw[:10], _hash(raw), " ".join(sorted(expanded)), iso(self.clock.now())),
        )
        return key_id, raw

    def verify(self, raw: str | None) -> ApiPrincipal | None:
        if not raw or not raw.startswith("sov_"):
            return None
        digest = _hash(raw)
        row = self.db.one("SELECT * FROM api_keys WHERE hash = ? AND revoked_at IS NULL", (digest,))
        if row is None or not hmac.compare_digest(row["hash"], digest):
            return None
        self.db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (iso(self.clock.now()), row["id"]))
        return ApiPrincipal(row["id"], row["name"], frozenset(row["scopes"].split()))

    def revoke(self, key_id: str) -> bool:
        cursor = self.db.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (iso(self.clock.now()), key_id)
        )
        return cursor.rowcount > 0

    def list(self) -> list[dict[str, str | None]]:
        rows = self.db.query("SELECT id, name, prefix, scopes, created_at, last_used_at, revoked_at FROM api_keys")
        return [dict(r) for r in rows]

    def any_active(self) -> bool:
        return bool(self.db.scalar("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL"))


class SignatureError(Exception):
    pass


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    """En-têtes de signature SOVEREIGN (utilisés aussi pour nos notifications sortantes)."""

    ts = int(time.time()) if timestamp is None else timestamp
    signature = _hmac_hex(secret, f"{ts}.".encode() + body)
    return {"X-Sovereign-Timestamp": str(ts), "X-Sovereign-Signature": f"sha256={signature}"}


def verify_webhook(
    source: str,
    secret: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    now: float | None = None,
    tolerance: int = 300,
) -> None:
    """Lève ``SignatureError`` si la signature est absente, invalide ou trop ancienne."""

    current = time.time() if now is None else now
    lower = {k.lower(): v for k, v in headers.items()}
    if source == "github":
        provided = lower.get("x-hub-signature-256", "")
        expected = "sha256=" + _hmac_hex(secret, body)
        if not provided or not hmac.compare_digest(provided, expected):
            raise SignatureError("signature GitHub invalide")
        return
    if source == "stripe":
        header = lower.get("stripe-signature", "")
        parts: dict[str, list[str]] = {}
        for item in header.split(","):
            key, _, value = item.strip().partition("=")
            parts.setdefault(key, []).append(value)
        timestamps = parts.get("t", [])
        if not timestamps or not timestamps[0].isdigit():
            raise SignatureError("en-tête Stripe-Signature invalide")
        ts = int(timestamps[0])
        if abs(current - ts) > tolerance:
            raise SignatureError("horodatage Stripe hors tolérance (rejeu ?)")
        expected = _hmac_hex(secret, f"{ts}.".encode() + body)
        if not any(hmac.compare_digest(sig, expected) for sig in parts.get("v1", [])):
            raise SignatureError("signature Stripe invalide")
        return
    # Schéma générique SOVEREIGN : HMAC(secret, "<timestamp>." + corps)
    ts_raw = lower.get("x-sovereign-timestamp", "")
    provided = lower.get("x-sovereign-signature", "")
    if not ts_raw.isdigit() or not provided:
        raise SignatureError("en-têtes X-Sovereign-Timestamp / X-Sovereign-Signature manquants")
    ts = int(ts_raw)
    if abs(current - ts) > tolerance:
        raise SignatureError("horodatage hors tolérance (rejeu ?)")
    expected = "sha256=" + _hmac_hex(secret, f"{ts}.".encode() + body)
    if not hmac.compare_digest(provided, expected):
        raise SignatureError("signature invalide")
