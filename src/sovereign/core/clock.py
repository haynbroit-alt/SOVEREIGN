"""Horloge injectable : indispensable pour tester les cycles et les échéances de façon déterministe."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Horloge contrôlable pour les tests et les simulations."""

    def __init__(self, at: datetime) -> None:
        self._at = at if at.tzinfo else at.replace(tzinfo=UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> datetime:
        self._at = self._at + timedelta(**kwargs)
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at if at.tzinfo else at.replace(tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_dt(value: str | datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def period_key(cycle: str, at: datetime) -> str:
    """Clé de période idempotente : un cycle ne s'exécute qu'une fois par période."""

    if cycle == "daily":
        return at.strftime("%Y-%m-%d")
    if cycle == "weekly":
        year, week, _ = at.isocalendar()
        return f"{year}-W{week:02d}"
    if cycle == "monthly":
        return at.strftime("%Y-%m")
    raise ValueError(f"cycle inconnu : {cycle}")
