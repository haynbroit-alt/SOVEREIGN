"""Identifiants : triables dans le temps (préfixe horodaté) et lisibles (préfixe métier)."""

from __future__ import annotations

import hashlib
import secrets
import time


def new_id(prefix: str) -> str:
    millis = int(time.time() * 1000)
    return f"{prefix}_{millis:011x}{secrets.token_hex(4)}"


def stable_id(prefix: str, *parts: object) -> str:
    """Identifiant déterministe (ex. une recommandation identique garde le même id d'un cycle à l'autre)."""

    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"
