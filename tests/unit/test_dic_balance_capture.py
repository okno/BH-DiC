from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from bh_dic.dic.balance_capture import empty_balance_from_response
from bh_dic.dic.errors import DicUiChangedError


class Response:
    def __init__(self, document: object, *, employee_id: str = "123", year: int = 2026) -> None:
        query = urlencode(
            {
                "employees_ids": json.dumps([int(employee_id)]),
                "include_pending": "true",
                "months": json.dumps(list(range(1, 13))),
                "years": json.dumps([year]),
            }
        )
        self.url = f"https://secure.dipendentincloud.it/backend_apiV2/attendance/balance?{query}"
        self.status = 200
        self.request = SimpleNamespace(method="GET")
        self._body = json.dumps(document).encode()

    async def body(self) -> bytes:
        return self._body

    async def header_value(self, name: str) -> str | None:
        if name == "content-type":
            return "application/json; charset=utf-8"
        if name == "content-length":
            return str(len(self._body))
        return None


@pytest.mark.asyncio
async def test_balance_capture_accepts_verified_empty_state() -> None:
    assert await empty_balance_from_response(Response({"data": []}), employee_id="123", year=2026)


@pytest.mark.asyncio
async def test_balance_capture_fails_closed_on_nonempty_unknown_schema_or_identity_drift() -> None:
    with pytest.raises(DicUiChangedError, match="explicit classification"):
        await empty_balance_from_response(
            Response({"data": [{"future": "value"}]}), employee_id="123", year=2026
        )
    with pytest.raises(DicUiChangedError, match="requested employee"):
        await empty_balance_from_response(
            Response({"data": []}, employee_id="456"), employee_id="123", year=2026
        )
    with pytest.raises(DicUiChangedError, match="requested year"):
        await empty_balance_from_response(
            Response({"data": []}, year=2025), employee_id="123", year=2026
        )


@pytest.mark.asyncio
async def test_balance_capture_rejects_duplicate_json_keys() -> None:
    response = Response({"data": []})
    response._body = b'{"data":[],"data":[]}'
    with pytest.raises(DicUiChangedError, match="response document"):
        await empty_balance_from_response(response, employee_id="123", year=2026)
