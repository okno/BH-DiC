from __future__ import annotations

import pytest

from bh_dic.dic.values import canonical_decimal_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.000", "0"), ("3.5000", "3.5"), ("-2.25", "-2.25")],
)
def test_canonical_decimal_text_normalizes_without_float(raw: str, expected: str) -> None:
    assert canonical_decimal_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [1.5, 1, "1e3", "1,5", "+1", "01", "NaN", "Infinity", "1.00000", ""],
)
def test_canonical_decimal_text_rejects_ambiguous_or_lossy_values(raw: object) -> None:
    with pytest.raises(ValueError):
        canonical_decimal_text(raw)
