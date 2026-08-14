"""Canonical scalar values shared by application and adapter boundaries."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def canonical_decimal_text(
    value: object,
    *,
    max_integer_digits: int = 9,
    max_decimal_places: int = 4,
) -> str:
    """Return a bounded base-10 string; floats, exponents and locale guesses are rejected."""

    if not isinstance(value, str):
        raise ValueError("decimal value must be supplied as text")
    text = value.strip()
    unsigned = text[1:] if text.startswith("-") else text
    integer, separator, fraction = unsigned.partition(".")
    if (
        not text
        or text.startswith("+")
        or not integer.isdigit()
        or (len(integer) > 1 and integer.startswith("0"))
        or len(integer) > max_integer_digits
        or (separator and (not fraction.isdigit() or len(fraction) > max_decimal_places))
        or (not separator and fraction)
    ):
        raise ValueError("decimal value must use canonical base-10 notation")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal value") from exc
    if not number.is_finite():
        raise ValueError("decimal value must be finite")
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return "0" if canonical in {"-0", ""} else canonical
