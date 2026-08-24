from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from bh_dic.dic.balance_capture import balance_lines_from_response, counters_from_response
from bh_dic.dic.errors import DicUiChangedError


class Response:
    def __init__(
        self,
        document: object,
        *,
        employee_id: str = "123",
        year: int = 2026,
        path: str = "/backend_apiV2/attendance/balance",
    ) -> None:
        query = urlencode(
            {
                "employees_ids": json.dumps([int(employee_id)]),
                "include_pending": "true",
                "months": "|".join(str(month) for month in range(1, 13)),
                "years": json.dumps([year]),
            }
        )
        suffix = "" if path == "/backend_apiV2/counters" else f"?{query}"
        self.url = f"https://secure.dipendentincloud.it{path}{suffix}"
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
    assert (
        await balance_lines_from_response(
            Response({"data": []}), employee_id="123", year=2026, counters={}
        )
        == ()
    )


@pytest.mark.asyncio
async def test_balance_capture_projects_monthly_counter_values() -> None:
    counters = await counters_from_response(
        Response(
            {
                "data": [
                    {
                        "id": 7,
                        "name": "Ferie",
                        "active": 1,
                        "auto_maturation": 1,
                        "color": "ignored",
                    }
                ]
            },
            path="/backend_apiV2/counters",
        )
    )
    lines = await balance_lines_from_response(
        Response(
            {
                "data": {
                    "123": {
                        "2026": {
                            "7": [
                                {
                                    "balance": 3.5,
                                    "correction": 1,
                                    "counter_id": 7,
                                    "maturation": 2.5,
                                    "projection": 6.0,
                                    "residue": 4.0,
                                    "utilization": 2,
                                }
                            ]
                        }
                    }
                }
            }
        ),
        employee_id="123",
        year=2026,
        counters=counters,
    )
    assert len(lines) == 1
    assert lines[0].category == "Ferie"
    assert lines[0].month == 7
    assert lines[0].current_residual == "4"
    assert lines[0].projection == "6"


@pytest.mark.asyncio
async def test_balance_capture_fails_closed_on_identity_or_year_drift() -> None:
    with pytest.raises(DicUiChangedError, match="requested employee"):
        await balance_lines_from_response(
            Response({"data": []}, employee_id="456"),
            employee_id="123",
            year=2026,
            counters={},
        )
    with pytest.raises(DicUiChangedError, match="requested year"):
        await balance_lines_from_response(
            Response({"data": []}, year=2025),
            employee_id="123",
            year=2026,
            counters={},
        )


@pytest.mark.asyncio
async def test_balance_capture_rejects_duplicate_json_keys() -> None:
    response = Response({"data": []})
    response._body = b'{"data":[],"data":[]}'
    with pytest.raises(DicUiChangedError, match="response document"):
        await balance_lines_from_response(response, employee_id="123", year=2026, counters={})
