from __future__ import annotations

import pytest

from bh_dic.dic.employee_resource_capture import (
    contracts_from_items,
    documents_from_items,
    maturations_from_items,
)
from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.models import DocumentQuery


def _contract(**updates: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": 10,
        "employee": {"id": 123},
        "flexible_workinghours": True,
        "hours_type": "weekly",
        "ongoing": True,
        "part_time_percentage": 80,
        "permanent": False,
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "level": "synthetic-level",
        "note": "synthetic private note",
        "future_field": "accepted",
    }
    item.update(updates)
    return item


def _maturation(**updates: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": 20,
        "employee": {"id": 123},
        "counter": {"name": "Synthetic leave"},
        "from": {"month": 1, "year": 2026},
        "to": {"month": 12, "year": 2026},
        "ongoing": True,
        "valid": True,
    }
    item.update(updates)
    return item


def _document(**updates: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": 30,
        "employee_id": 123,
        "title": "Synthetic payroll statement",
        "category": {"name": "Synthetic category"},
        "date": "2026-07-31",
        "expiration_date": None,
        "requested": False,
        "creator": {"full_name": "Synthetic Person"},
    }
    item.update(updates)
    return item


def test_contract_projection_preserves_hr_fields_and_redacts_notes() -> None:
    records = contracts_from_items((_contract(),), employee_id="123")
    assert len(records) == 1
    record = records[0]
    assert record.contract_id == "10"
    assert record.stable_identifier is True
    assert record.actionable is False
    assert record.work_regime == "part-time 80%"
    assert record.description == "[REDACTED]"
    assert record.contract_type == "tempo determinato"
    assert record.period == "2026-01-01 → 2026-12-31"


def test_maturation_projection_uses_stable_ids_and_month_year_periods() -> None:
    records = maturations_from_items((_maturation(),), employee_id="123")
    assert records[0].maturation_id == "20"
    assert records[0].valid_from == "01/2026"
    assert records[0].valid_to == "12/2026"
    assert records[0].status == "attiva"


def test_document_projection_filters_locally_and_never_returns_title_or_creator() -> None:
    records = documents_from_items(
        (_document(), _document(id=31, title="Other synthetic file", requested=True)),
        employee_id="123",
        query=DocumentQuery(query="payroll", state="uploaded", category="Synthetic category"),
    )
    assert len(records) == 1
    assert records[0].document_id == "30"
    assert records[0].title_redacted == "[REDACTED]"
    assert records[0].uploaded_by_redacted == "[PERSON_REDACTED]"
    assert records[0].actionable is False


@pytest.mark.parametrize(
    ("projection", "item"),
    [
        (contracts_from_items, _contract(employee={"id": 999})),
        (maturations_from_items, _maturation(id=0)),
    ],
)
def test_resource_projections_fail_closed_on_identity_or_identifier_drift(
    projection: object,
    item: dict[str, object],
) -> None:
    assert callable(projection)
    with pytest.raises(DicUiChangedError):
        projection((item,), employee_id="123")  # type: ignore[operator]


def test_document_projection_rejects_duplicate_ids_and_wrong_employee() -> None:
    with pytest.raises(DicUiChangedError, match="duplicate"):
        documents_from_items(
            (_document(), _document()),
            employee_id="123",
            query=DocumentQuery(),
        )
    with pytest.raises(DicUiChangedError, match="identity"):
        documents_from_items(
            (_document(employee_id=999),),
            employee_id="123",
            query=DocumentQuery(),
        )
