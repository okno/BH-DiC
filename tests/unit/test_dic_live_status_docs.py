from __future__ import annotations

import re
from pathlib import Path

from bh_dic.policies.catalog import (
    ALL_FUNCTION_IDS,
    FUNCTION_CATALOG,
    READ_FUNCTION_IDS,
    WRITE_FUNCTION_IDS,
)

STATUS_DOCUMENT = Path(__file__).resolve().parents[2] / "docs" / "LIVE_VERIFICATION_STATUS.md"
MATRIX_ROW = re.compile(r"^\|\s*`(EMP-[A-Z]+-[0-9]{3})`\s*\|(?P<row>.+)$", re.MULTILINE)
PARTIALLY_COMPLETED_FUNCTIONS = {
    "EMP-CREATE-001",
    "EMP-CONTRACT-003",
    "EMP-DOC-003",
    "EMP-DOC-005",
    "EMP-EXPORT-001",
    "EMP-INVITE-001",
}
LIVE_NOT_AVAILABLE_FUNCTIONS = PARTIALLY_COMPLETED_FUNCTIONS - {"EMP-CREATE-001"}
LIVE_READ_VERIFIED_FUNCTIONS = {"EMP-READ-001", "EMP-CONTRACT-001"}


def _matrix_rows() -> dict[str, str]:
    document = STATUS_DOCUMENT.read_text(encoding="utf-8")
    matches = MATRIX_ROW.findall(document)
    identifiers = [function_id for function_id, _ in matches]
    assert len(identifiers) == len(set(identifiers)), "duplicate Function ID in live matrix"
    return dict(matches)


def test_live_matrix_contains_exactly_the_authoritative_catalog() -> None:
    rows = _matrix_rows()

    assert set(rows) == ALL_FUNCTION_IDS
    assert len(ALL_FUNCTION_IDS) == 32
    assert len(READ_FUNCTION_IDS) == 13
    assert len(WRITE_FUNCTION_IDS) == 19
    assert len(PARTIALLY_COMPLETED_FUNCTIONS) == 6
    assert len(LIVE_NOT_AVAILABLE_FUNCTIONS) == 5


def test_live_matrix_preserves_truthful_read_and_write_statuses() -> None:
    rows = _matrix_rows()

    for function_id in READ_FUNCTION_IDS:
        row = rows[function_id]
        assert "IMPLEMENTED — TESTED_WITH_MOCK" in row
        assert "NEEDS_VALIDATION" in row
        if function_id in LIVE_READ_VERIFIED_FUNCTIONS:
            assert "LIVE_READ_VERIFIED" in row
            assert "soltanto" in row
        else:
            assert "LIVE_READ_VERIFIED" not in row

    for function_id in WRITE_FUNCTION_IDS:
        row = rows[function_id]
        assert "TESTED_WITH_MOCK" in row
        if function_id in PARTIALLY_COMPLETED_FUNCTIONS:
            assert "PARTIALLY_COMPLETED" in row
            assert "IMPLEMENTED" not in row
        else:
            assert "IMPLEMENTED" in row
            assert "PARTIALLY_COMPLETED" not in row
        assert "LIVE_WRITE_UNVERIFIED" in row
        assert "DISABLED_BY_POLICY" in row
        assert "DISABLED_BY_DEFAULT" in row
        if function_id in LIVE_NOT_AVAILABLE_FUNCTIONS:
            assert "NOT_AVAILABLE" in row
        else:
            assert "NOT_AVAILABLE" not in row


def test_live_matrix_matches_catalog_operator_live_availability() -> None:
    rows = _matrix_rows()
    unavailable = {
        function_id
        for function_id, spec in FUNCTION_CATALOG.items()
        if spec.is_write and not spec.operator_live_available
    }

    assert unavailable == LIVE_NOT_AVAILABLE_FUNCTIONS
    assert all("NOT_AVAILABLE" in rows[function_id] for function_id in unavailable)


def test_create_status_documents_the_live_verifiable_subset() -> None:
    row = _matrix_rows()["EMP-CREATE-001"]

    assert "subset verificabile" in row
    for unsupported_field in ("birth_date", "iban", "phone", "address", "notes"):
        assert unsupported_field in row
