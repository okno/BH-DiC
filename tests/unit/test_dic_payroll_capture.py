from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import pytest

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.payroll_capture import payrolls_from_response


@dataclass(frozen=True)
class _Request:
    method: str = "GET"


class _Response:
    def __init__(self, document: dict[str, Any], *, employee_id: str = "123", year: int = 2026):
        query = urlencode(
            {
                "search": "",
                "filter_type": "and",
                "employee_id": employee_id,
                "page": "1",
                "per_page": "20",
                "year": str(year),
                "search_fields": "description",
            }
        )
        self.url = f"https://secure.dipendentincloud.it/backend_apiV2/payrolls?{query}"
        self.status = 200
        self.request = _Request()
        self._body = json.dumps(document).encode()

    async def body(self) -> bytes:
        return self._body

    async def header_value(self, name: str) -> str | None:
        return {
            "content-type": "application/json; charset=utf-8",
            "content-length": str(len(self._body)),
        }.get(name)


def _document() -> dict[str, Any]:
    signed = "https://s3.eu-west-1.amazonaws.com/private/payroll.pdf?" + urlencode(
        {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": "synthetic",
            "X-Amz-Date": "20260821T000000Z",
            "X-Amz-Expires": "300",
            "X-Amz-SignedHeaders": "host",
            "X-Amz-Signature": "a" * 64,
        }
    )
    return {
        "current_page": 1,
        "data": [
            {
                "attachments": [
                    {
                        "created_at": "2026-08-01T00:00:00Z",
                        "filename": "cedolino.pdf",
                        "id": 9,
                        "updated_at": "2026-08-01T00:00:00Z",
                        "url": signed,
                    }
                ],
                "balance": [],
                "balance_aligned": True,
                "create_provider": "synthetic",
                "date": "2026-07-31",
                "description": "Cedolino luglio",
                "employee": {"id": 123},
                "id": 77,
                "month": 7,
                "net": 1234,
                "read": True,
                "read_at": "2026-08-01T00:00:00Z",
                "update_provider": None,
                "year": 2026,
            }
        ],
        "first_page_url": "synthetic",
        "from": 1,
        "last_page": 1,
        "last_page_url": "synthetic",
        "links": [],
        "next_page_url": None,
        "path": "synthetic",
        "per_page": 20,
        "prev_page_url": None,
        "to": 1,
        "total": 1,
    }


@pytest.mark.asyncio
async def test_payroll_capture_projects_net_and_keeps_signed_url_secret() -> None:
    records = await payrolls_from_response(_Response(_document()), employee_id="123", year=2026)

    assert len(records) == 1
    assert records[0].net_cents == 123400
    assert records[0].attachment_filename == "cedolino.pdf"
    assert records[0].attachment_url is not None
    assert "X-Amz-Signature" not in repr(records[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("net", "expected_cents"),
    [(1234, 123_400), (1234.5, 123_450), (1234.56, 123_456)],
)
async def test_payroll_capture_accepts_exact_json_euro_numbers(
    net: int | float, expected_cents: int
) -> None:
    document = _document()
    document["data"][0]["net"] = net

    records = await payrolls_from_response(_Response(document), employee_id="123", year=2026)

    assert records[0].net_cents == expected_cents


@pytest.mark.asyncio
@pytest.mark.parametrize("net", [True, "1234.56", -1, 0.001, float("inf"), float("nan")])
async def test_payroll_capture_rejects_ambiguous_or_invalid_money(net: object) -> None:
    document = _document()
    document["data"][0]["net"] = net

    with pytest.raises(DicUiChangedError, match="invalid response schema"):
        await payrolls_from_response(_Response(document), employee_id="123", year=2026)


@pytest.mark.asyncio
async def test_payroll_capture_rejects_a_different_employee() -> None:
    with pytest.raises(DicUiChangedError, match="does not match requested employee"):
        await payrolls_from_response(_Response(_document()), employee_id="124", year=2026)


@pytest.mark.asyncio
async def test_payroll_capture_accepts_ignored_additive_fields() -> None:
    document = _document()
    document["future_root"] = {"ignored": True}
    document["data"][0]["future_payroll"] = "ignored"
    document["data"][0]["attachments"][0]["future_attachment"] = 1
    records = await payrolls_from_response(_Response(document), employee_id="123", year=2026)
    assert records[0].payroll_id == "77"
