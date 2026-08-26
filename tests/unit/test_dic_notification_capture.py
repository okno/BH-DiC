from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.notification_capture import NOTIFICATIONS_ENDPOINT, notifications_from_items
from bh_dic.dic.paginated_capture import page_from_response


def _item(*, notification_id: int = 7, read: bool = False) -> dict[str, object]:
    return {
        "additional_text": None,
        "created_at": "2026-08-26T10:15:00+00:00",
        "employee": {"id": 99, "discarded": "personal"},
        "id": notification_id,
        "parameters": [],
        "read": read,
        "received": True,
        "recipient_id": 1,
        "recipient_type": None,
        "resource_id": None,
        "sender": {"id": 2, "discarded": "personal"},
        "sender_id": 2,
        "sent": True,
        "text": "Nuovo cedolino disponibile",
        "type": {"id": "payroll_available", "label": "discarded"},
        "updated_at": "2026-08-26T10:15:00+00:00",
    }


class Response:
    def __init__(self, document: object) -> None:
        query = urlencode(
            {
                "page": "1",
                "per_page": "20",
                "sort": '{"active":"created_at","direction":"desc"}',
            }
        )
        self.url = f"https://secure.dipendentincloud.it/backend_apiV2/notifications?{query}"
        self.status = 200
        self.request = SimpleNamespace(method="GET")
        self._body = json.dumps(document).encode()

    async def body(self) -> bytes:
        return self._body

    async def header_value(self, name: str) -> str | None:
        if name == "content-type":
            return "application/json"
        if name == "content-length":
            return str(len(self._body))
        return None


def _document(item: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "current_page": 1,
        "data": [item or _item()],
        "last_page": 1,
        "next_page_url": None,
        "per_page": 20,
        "total": 1,
    }


@pytest.mark.asyncio
async def test_notification_endpoint_has_no_employee_scope_and_projects_minimal_fields() -> None:
    first = await page_from_response(
        Response(_document()),
        contract=NOTIFICATIONS_ENDPOINT,
        employee_id=None,
    )
    records = notifications_from_items(first.items)

    assert records[0].notification_id == 7
    assert records[0].text == "Nuovo cedolino disponibile"
    assert records[0].notification_type == "payroll_available"
    assert records[0].read is False
    assert "employee" not in records[0].model_dump()
    assert "sender" not in records[0].model_dump()


@pytest.mark.asyncio
async def test_notification_endpoint_rejects_wrong_scope_and_invalid_schema() -> None:
    response = Response(_document())
    with pytest.raises(DicUiChangedError, match="employee metadata"):
        await page_from_response(
            response,
            contract=NOTIFICATIONS_ENDPOINT,
            employee_id="EMP-PRIVATE-1",
        )

    invalid = _item()
    invalid["read"] = "false"
    first = await page_from_response(
        Response(_document(invalid)),
        contract=NOTIFICATIONS_ENDPOINT,
        employee_id=None,
    )
    with pytest.raises(DicUiChangedError, match="notification schema"):
        notifications_from_items(first.items)


def test_notification_projection_rejects_duplicate_ids_and_bad_timestamps() -> None:
    with pytest.raises(DicUiChangedError, match="duplicate"):
        notifications_from_items((_item(), _item()))

    invalid = _item()
    invalid["created_at"] = "not-a-date"
    with pytest.raises(DicUiChangedError, match="timestamp"):
        notifications_from_items((invalid,))
