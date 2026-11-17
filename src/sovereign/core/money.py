"""Argent : toujours en Decimal / centimes entiers. Jamais de float pour un montant."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    try:
        result = Decimal(str(value)) if not isinstance(value, Decimal) else value
    except InvalidOperation as exc:
        raise ValueError(f"montant invalide : {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"montant invalide : {value!r}")
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def to_cents(value: Decimal | int | float | str) -> int:
    return int((to_decimal(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(CENT)


def fmt(value: Decimal | int | float, currency: str = "EUR") -> str:
    amount = to_decimal(value)
    sign = "-" if amount < 0 else ""
    integer, _, frac = f"{abs(amount):.2f}".partition(".")
    groups: list[str] = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    symbol = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "CAD": "$CA"}.get(currency, currency)
    return f"{sign}{' '.join(groups)},{frac} {symbol}"
