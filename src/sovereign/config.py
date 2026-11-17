"""Configuration de SOVEREIGN.

Sources (par ordre de priorité croissante) :
1. valeurs par défaut ci-dessous ;
2. fichier ``sovereign.toml`` dans ``SOVEREIGN_HOME`` ;
3. variables d'environnement ``SOVEREIGN_*``.

Règle de sécurité non négociable : le ratio de dépense autorisé ne peut pas dépasser
10 % des revenus réellement encaissés. Une configuration plus élevée est refusée.
"""

from __future__ import annotations

import os
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_SPEND_RATIO = Decimal("0.10")


class LLMSettings(BaseModel):
    """Raisonnement LLM optionnel. Désactivé par défaut : le système est 100 % fonctionnel sans."""

    enabled: bool = False
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_cost_per_call: Decimal = Decimal("0.50")
    # Les cycles automatiques n'appellent jamais d'IA sauf autorisation explicite.
    allow_in_cycles: bool = False


class FinanceSettings(BaseModel):
    currency: str = "EUR"
    spend_ratio: Decimal = MAX_SPEND_RATIO
    # Au-delà de cette part du budget restant, une dépense part en revue humaine.
    review_threshold_share: Decimal = Decimal("0.50")
    alert_thresholds: list[Decimal] = Field(default_factory=lambda: [Decimal("0.80"), Decimal("1.00")])

    @field_validator("spend_ratio")
    @classmethod
    def _cap_ratio(cls, value: Decimal) -> Decimal:
        if value <= 0 or value > MAX_SPEND_RATIO:
            raise ValueError(
                f"spend_ratio doit être dans ]0, {MAX_SPEND_RATIO}] — la règle des 10 % est non négociable"
            )
        return value


class AutomationSettings(BaseModel):
    daily_hour: int = 7  # heure locale (UTC par défaut) à partir de laquelle le cycle quotidien est dû
    weekly_weekday: int = 0  # 0 = lundi
    monthly_day: int = 1
    worker_poll_seconds: float = 2.0
    scheduler_tick_seconds: float = 60.0
    task_lease_seconds: int = 300
    notify_webhook_url: str | None = None
    notify_webhook_secret: str | None = None


class SecuritySettings(BaseModel):
    # Secrets HMAC des webhooks entrants, par source (ex. {"github": "...", "stripe": "whsec_..."}).
    webhook_secrets: dict[str, str] = Field(default_factory=dict)
    # Si vrai, l'API exige une clé même en local (recommandé dès qu'elle est exposée).
    require_api_key: bool = True
    webhook_tolerance_seconds: int = 300


class Settings(BaseModel):
    home: Path = Field(default_factory=lambda: Path(os.environ.get("SOVEREIGN_HOME", "~/.sovereign")))
    db_filename: str = "sovereign.db"
    reports_dirname: str = "reports"
    timezone: str = "UTC"
    llm: LLMSettings = Field(default_factory=LLMSettings)
    finance: FinanceSettings = Field(default_factory=FinanceSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    otel_enabled: bool = False

    @property
    def home_path(self) -> Path:
        return self.home.expanduser()

    @property
    def db_path(self) -> Path:
        return self.home_path / self.db_filename

    @property
    def reports_path(self) -> Path:
        return self.home_path / self.reports_dirname


_ENV_MAP: dict[str, tuple[str, ...]] = {
    "SOVEREIGN_DB_FILENAME": ("db_filename",),
    "SOVEREIGN_TIMEZONE": ("timezone",),
    "SOVEREIGN_OTEL": ("otel_enabled",),
    "SOVEREIGN_LLM_ENABLED": ("llm", "enabled"),
    "SOVEREIGN_LLM_MODEL": ("llm", "model"),
    "SOVEREIGN_LLM_EFFORT": ("llm", "effort"),
    "SOVEREIGN_LLM_MAX_COST": ("llm", "max_cost_per_call"),
    "SOVEREIGN_CURRENCY": ("finance", "currency"),
    "SOVEREIGN_SPEND_RATIO": ("finance", "spend_ratio"),
    "SOVEREIGN_NOTIFY_WEBHOOK_URL": ("automation", "notify_webhook_url"),
    "SOVEREIGN_NOTIFY_WEBHOOK_SECRET": ("automation", "notify_webhook_secret"),
    "SOVEREIGN_REQUIRE_API_KEY": ("security", "require_api_key"),
}


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = data
    for key in path[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[path[-1]] = value


def load_settings(home: str | Path | None = None, **overrides: Any) -> Settings:
    """Charge la configuration depuis le fichier TOML puis l'environnement."""

    base_home = Path(home or os.environ.get("SOVEREIGN_HOME", "~/.sovereign")).expanduser()
    data: dict[str, Any] = {"home": base_home}
    toml_path = base_home / "sovereign.toml"
    if toml_path.exists():
        with toml_path.open("rb") as handle:
            data.update(tomllib.load(handle))
        data["home"] = base_home
    for env_name, path in _ENV_MAP.items():
        if env_name in os.environ:
            _set_path(data, path, os.environ[env_name])
    for env_name, value in os.environ.items():
        if env_name.startswith("SOVEREIGN_WEBHOOK_SECRET_"):
            source = env_name.removeprefix("SOVEREIGN_WEBHOOK_SECRET_").lower()
            data.setdefault("security", {}).setdefault("webhook_secrets", {})[source] = value
    data.update(overrides)
    return Settings.model_validate(data)
