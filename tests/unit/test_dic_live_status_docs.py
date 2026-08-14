from __future__ import annotations

import re
from pathlib import Path

from bh_dic.policies.catalog import (
    ALL_FUNCTION_IDS,
    READ_FUNCTION_IDS,
    WRITE_FUNCTION_IDS,
)

STATUS_DOCUMENT = Path(__file__).resolve().parents[2] / "docs" / "LIVE_VERIFICATION_STATUS.md"
MATRIX_ROW = re.compile(r"^\|\s*`(EMP-[A-Z]+-[0-9]{3})`\s*\|(?P<row>.+)$", re.MULTILINE)
PARTIAL_ARTIFACT_FUNCTIONS = {"EMP-DOC-003", "EMP-EXPORT-001"}


def _matrix_rows() -> dict[str, str]:
    document = STATUS_DOCUMENT.read_text(encoding="utf-8")
    matches = MATRIX_ROW.findall(document)
    identifiers = [function_id for function_id, _ in matches]
    assert len(identifiers) == len(set(identifiers)), "duplicate Function ID in live matrix"
    return dict(matches)


def test_live_matrix_contains_exactly_the_authoritative_catalog() -> None:
    rows = _matrix_rows()

    assert set(rows) == ALL_FUNCTION_IDS


def test_live_matrix_preserves_truthful_read_and_write_statuses() -> None:
    rows = _matrix_rows()

    for function_id in READ_FUNCTION_IDS:
        row = rows[function_id]
        assert "IMPLEMENTED — TESTED_WITH_MOCK" in row
        assert "NEEDS_VALIDATION" in row
        assert "LIVE_READ_VERIFIED" not in row

    for function_id in WRITE_FUNCTION_IDS:
        row = rows[function_id]
        expected = (
            "PARTIALLY_COMPLETED — TESTED_WITH_MOCK"
            if function_id in PARTIAL_ARTIFACT_FUNCTIONS
            else "IMPLEMENTED — TESTED_WITH_MOCK"
        )
        assert expected in row
        assert "LIVE_WRITE_UNVERIFIED" in row
        assert "DISABLED_BY_DEFAULT" in row
        if function_id in PARTIAL_ARTIFACT_FUNCTIONS:
            assert "NOT_AVAILABLE" in row
