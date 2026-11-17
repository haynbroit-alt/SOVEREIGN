"""La passerelle LLM est la seule brique qui dépense de l'argent réel : chaque chemin est testé sans réseau."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from sovereign.brain.simulation import simulate
from sovereign.core.llm import LLMGateway, actual_cost, estimate_cost, strict_schema
from sovereign.kernel import Kernel
from sovereign.skills.base import Recommendation


class Answer(BaseModel):
    verdict: str
    points: list[str]


class FakeMessages:
    def __init__(self, text: str, stop_reason: str = "end_turn", fail: bool = False, served: str | None = None) -> None:
        self.text, self.stop_reason, self.fail, self.served = text, stop_reason, fail, served
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("réseau indisponible")
        return SimpleNamespace(
            model=self.served or kwargs["model"],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
            content=[SimpleNamespace(type="text", text=self.text)],
        )


_OPEN: list[Kernel] = []


@pytest.fixture(autouse=True)
def _close_kernels():
    yield
    while _OPEN:
        _OPEN.pop().close()


def _kernel(clock, messages: FakeMessages, collected: str | None = "10000") -> Kernel:
    kernel = Kernel.ephemeral(clock=clock, llm={"enabled": True, "model": "claude-opus-5-5"})
    kernel.llm = LLMGateway(
        kernel.settings.llm,
        kernel.finance,
        client_factory=lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)),
    )
    if collected:
        kernel.finance.record_collection(collected, "2026-11-01")
    _OPEN.append(kernel)
    return kernel


def test_disabled_by_default(kernel):
    assert kernel.llm.available is False
    assert kernel.llm.structured("s", "p", Answer, purpose="x") is None


def test_successful_call_is_budgeted_and_cost_recorded(clock):
    messages = FakeMessages('{"verdict": "ok", "points": ["a"]}')
    kernel = _kernel(clock, messages)
    result = kernel.llm.structured("système", "question", Answer, purpose="test", max_tokens=1000)
    assert result == Answer(verdict="ok", points=["a"])
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    spent = [e for e in kernel.finance.expenses() if e.category == "ia"]
    assert len(spent) == 1 and spent[0].status == "spent"
    assert spent[0].actual == actual_cost("claude-opus-5-5", 1200, 300)
    assert spent[0].actual <= spent[0].amount  # le réel ne dépasse jamais l'estimation autorisée


def test_fallback_model_is_billed_at_its_own_price_within_reservation(clock):
    # Régression (audit indépendant) : le coût d'un modèle de repli était calculé au tarif du modèle demandé.
    messages = FakeMessages('{"verdict": "ok", "points": []}', served="claude-fable-5-2")
    kernel = _kernel(clock, messages)
    kernel.llm.structured("système", "question", Answer, purpose="test", max_tokens=1000)
    spent = next(e for e in kernel.finance.expenses() if e.category == "ia")
    assert spent.actual == actual_cost("claude-fable-5-2", 1200, 300)
    assert spent.actual <= spent.amount and kernel.llm.overrun == 0  # réservation au tarif le plus élevé


def test_no_budget_means_no_ai_call(clock):
    messages = FakeMessages('{"verdict": "ok", "points": []}')
    kernel = _kernel(clock, messages, collected=None)
    assert kernel.llm.structured("s", "p", Answer, purpose="x") is None
    assert messages.calls == [] and kernel.llm.denied_by_budget == 1


def test_per_call_ceiling(clock):
    messages = FakeMessages("{}")
    kernel = _kernel(clock, messages)
    kernel.llm.settings.max_cost_per_call = Decimal("0.01")
    assert kernel.llm.structured("s", "p" * 10000, Answer, purpose="x", max_tokens=64000) is None
    assert messages.calls == []


def test_refusal_and_errors_fall_back(clock):
    refused = _kernel(clock, FakeMessages("", stop_reason="refusal"))
    assert refused.llm.structured("s", "p", Answer, purpose="x") is None and refused.llm.refusals == 1
    broken = _kernel(clock, FakeMessages("", fail=True))
    assert broken.llm.structured("s", "p", Answer, purpose="x") is None
    assert all(e.status == "cancelled" for e in broken.finance.expenses() if e.category == "ia")
    invalid = _kernel(clock, FakeMessages("pas du json"))
    assert invalid.llm.structured("s", "p", Answer, purpose="x") is None


def test_strict_schema_and_costs():
    schema = strict_schema(Answer)
    assert schema["additionalProperties"] is False and schema["required"] == ["verdict", "points"]
    assert "title" not in schema
    assert estimate_cost("claude-opus-5-5", 3000, 1000) >= actual_cost("claude-opus-5-5", 1000, 1000)


def test_unknown_value_is_not_counted_as_zero():
    """Régression : un coût connu sans valeur chiffrée ne doit pas produire 100 % de pertes."""

    rec = Recommendation(
        title="Prototype", rationale="r", impact=8, effort=3, confidence=0.85, risk=0.1, estimated_cost=Decimal("800")
    ).finalize("innovation-creativity")
    result = simulate(rec, available_budget=Decimal("3000"))
    assert result.unit == "impact_points"
    assert result.p_loss < 0.5 and result.expected_value > 0
    assert "engage une dépense (paiement humain obligatoire)" in result.reasons
