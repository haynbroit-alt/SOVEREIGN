"""Passerelle LLM optionnelle (Claude via le SDK officiel ``anthropic``).

Règles :
- Désactivée par défaut. Tout le système fonctionne de façon déterministe sans elle.
- Chaque appel est une DÉPENSE : le coût maximal estimé doit être autorisé par le garde-fou financier
  (enveloppe de 10 %) AVANT l'appel ; le coût réel (usage de tokens) est ensuite déclaré.
- Sorties structurées (JSON Schema) validées par Pydantic ; refus (``stop_reason == "refusal"``) et erreurs
  ⇒ ``None`` et repli déterministe.
- Repli serveur en cas de refus (``fallbacks="default"``, bêta ``server-side-fallback-2026-07-01``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel

from sovereign.config import LLMSettings
from sovereign.core.money import to_decimal

log = logging.getLogger("sovereign.llm")
T = TypeVar("T", bound=BaseModel)

# Tarifs publics en USD par million de tokens (entrée, sortie) — relevés le 2026-11-17.
PRICES_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-fable-5-2": (Decimal("8"), Decimal("40")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}
# Conversion prudente : 1 USD compté comme 1 unité de la devise du registre (sur-estime le coût en EUR/GBP/CHF).
USD_RATE = Decimal("1")
_UNSUPPORTED_SCHEMA_KEYS = {
    "default",
    "title",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "examples",
}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema compatible sorties structurées : objets fermés, tous les champs requis."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: clean(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(model.model_json_schema())


def estimate_cost(model: str, input_chars: int, max_output_tokens: int) -> Decimal:
    price_in, price_out = PRICES_USD_PER_MTOK.get(model, max(PRICES_USD_PER_MTOK.values()))
    input_tokens = Decimal(input_chars) / Decimal("3") + 200  # estimation prudente
    cost = (input_tokens * price_in + Decimal(max_output_tokens) * price_out) / Decimal(1_000_000)
    return to_decimal(max(cost * USD_RATE, Decimal("0.01")))


def actual_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICES_USD_PER_MTOK.get(model, max(PRICES_USD_PER_MTOK.values()))
    cost = (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / Decimal(1_000_000)
    return to_decimal(max(cost * USD_RATE, Decimal("0.01")))


class LLMGateway:
    def __init__(
        self,
        settings: LLMSettings,
        finance: Any,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.finance = finance
        self._client_factory = client_factory
        self._client: Any = None
        self.calls = 0
        self.refusals = 0
        self.denied_by_budget = 0
        self.spent = Decimal("0")

    @property
    def available(self) -> bool:
        if not self.settings.enabled:
            return False
        if self._client is not None:
            return True
        try:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                import anthropic

                self._client = anthropic.Anthropic()
        except Exception as exc:  # SDK absent ou identifiants introuvables
            log.warning("LLM indisponible : %s", exc)
            return False
        return True

    def structured(
        self,
        system: str,
        prompt: str,
        output_model: type[T],
        *,
        purpose: str,
        max_tokens: int = 4000,
    ) -> T | None:
        if not self.available:
            return None
        model = self.settings.model
        estimate = estimate_cost(model, len(system) + len(prompt), max_tokens)
        if estimate > self.settings.max_cost_per_call:
            log.info(
                "appel LLM ignoré : coût max estimé %s > plafond %s",
                estimate,
                self.settings.max_cost_per_call,
            )
            self.denied_by_budget += 1
            return None
        decision = self.finance.request_expense(
            estimate,
            "ia",
            vendor="Anthropic",
            description=f"LLM {model} : {purpose}",
            requested_by="sovereign-llm",
        )
        if not decision.approved:
            if decision.needs_review:
                self.finance.cancel_expense(decision.expense.id, actor="sovereign-llm")
            self.denied_by_budget += 1
            log.info("appel LLM refusé par le budget : %s", decision.message)
            return None
        expense_id = decision.expense.id
        try:
            response = self._client.beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={
                    "effort": self.settings.effort,
                    "format": {"type": "json_schema", "schema": strict_schema(output_model)},
                },
            )
        except Exception as exc:
            log.warning("appel LLM en échec (%s) : repli déterministe", type(exc).__name__)
            self.finance.cancel_expense(expense_id, actor="sovereign-llm")
            return None
        self.calls += 1
        usage = getattr(response, "usage", None)
        cost = actual_cost(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
        self.finance.confirm_spent(expense_id, min(cost, decision.expense.amount), actor="sovereign-llm")
        self.spent += min(cost, decision.expense.amount)
        if getattr(response, "stop_reason", None) == "refusal":
            self.refusals += 1
            return None
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            return output_model.model_validate_json(text)
        except Exception:
            log.warning("sortie LLM non conforme au schéma : repli déterministe")
            return None
