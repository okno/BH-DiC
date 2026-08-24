from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.paginated_capture import (
    PaginatedEndpointContract,
    collect_complete_pages,
    page_from_response,
)

CONTRACT = PaginatedEndpointContract(
    resource="employees.contracts",
    endpoint_path="/backend_apiV2/contracts",
    allowed_query_keys=frozenset(
        {"employee_id", "page", "per_page", "search_fields", "filter_type"}
    ),
    employee_query_key="employee_id",
    required_item_keys=frozenset({"id", "employee"}),
)


def _document(
    *,
    page: int = 1,
    last_page: int = 1,
    total: int = 1,
    data: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "current_page": page,
        "data": data if data is not None else [{"id": page, "employee": {"id": 123}}],
        "last_page": last_page,
        "next_page_url": (
            None
            if page == last_page
            else "https://secure.dipendentincloud.it/backend_apiV2/contracts?"
            f"employee_id=123&page={page + 1}&per_page=1"
        ),
        "per_page": 1,
        "total": total,
        "unknown_additive_field": {"future": True},
    }


class Response:
    def __init__(self, document: object, *, employee_id: str = "123") -> None:
        self.url = (
            "https://secure.dipendentincloud.it/backend_apiV2/contracts?"
            f"employee_id={employee_id}&page=1&per_page=1"
        )
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


class FetchPage:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = list(documents)
        self.requested_urls: list[str] = []

    async def evaluate(self, expression: str, argument: object = None) -> object:
        assert 'credentials: "same-origin"' in expression
        assert isinstance(argument, str)
        self.requested_urls.append(argument)
        document = self.documents.pop(0)
        return {
            "url": argument,
            "status": 200,
            "contentType": "application/json",
            "oversized": False,
            "body": json.dumps(document),
        }


@pytest.mark.asyncio
async def test_parser_accepts_additive_fields_but_requires_identity_fields() -> None:
    first = await page_from_response(Response(_document()), contract=CONTRACT, employee_id="123")
    assert first.total == 1
    assert first.items[0]["id"] == 1

    missing = _document(data=[{"id": 1, "future": "value"}])
    with pytest.raises(DicUiChangedError, match="item schema"):
        await page_from_response(Response(missing), contract=CONTRACT, employee_id="123")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"current_page":1,"current_page":1}',
        b'{"current_page":NaN}',
    ],
)
async def test_parser_rejects_duplicate_keys_and_nonfinite_numbers(body: bytes) -> None:
    response = Response(_document())
    response._body = body
    with pytest.raises(DicUiChangedError, match="response document"):
        await page_from_response(response, contract=CONTRACT, employee_id="123")


@pytest.mark.asyncio
async def test_parser_rejects_wrong_employee_origin_method_and_unknown_query() -> None:
    with pytest.raises(DicUiChangedError, match="requested employee"):
        await page_from_response(
            Response(_document(), employee_id="456"),
            contract=CONTRACT,
            employee_id="123",
        )

    wrong_origin = Response(_document())
    wrong_origin.url = wrong_origin.url.replace("secure.dipendentincloud.it", "attacker.invalid")
    with pytest.raises(DicUiChangedError, match="endpoint metadata"):
        await page_from_response(wrong_origin, contract=CONTRACT, employee_id="123")

    wrong_method = Response(_document())
    wrong_method.request = SimpleNamespace(method="POST")
    with pytest.raises(DicUiChangedError, match="response metadata"):
        await page_from_response(wrong_method, contract=CONTRACT, employee_id="123")

    unknown_query = Response(_document())
    unknown_query.url += "&scope=all"
    with pytest.raises(DicUiChangedError, match="query metadata"):
        await page_from_response(unknown_query, contract=CONTRACT, employee_id="123")


@pytest.mark.asyncio
async def test_complete_paginator_follows_only_validated_same_origin_next_urls() -> None:
    first = await page_from_response(
        Response(_document(last_page=2, total=2)),
        contract=CONTRACT,
        employee_id="123",
    )
    browser_page = FetchPage([_document(page=2, last_page=2, total=2)])
    items = await collect_complete_pages(
        browser_page,
        first,
        contract=CONTRACT,
        employee_id="123",
    )
    assert [item["id"] for item in items] == [1, 2]
    assert browser_page.requested_urls == [first.next_page_url]


@pytest.mark.asyncio
async def test_complete_paginator_fails_closed_on_changed_total_or_short_result() -> None:
    first = await page_from_response(
        Response(_document(last_page=2, total=2)),
        contract=CONTRACT,
        employee_id="123",
    )
    with pytest.raises(DicUiChangedError, match="metadata changed"):
        await collect_complete_pages(
            FetchPage([_document(page=2, last_page=2, total=3)]),
            first,
            contract=CONTRACT,
            employee_id="123",
        )

    empty_last = _document(page=2, last_page=2, total=2, data=[])
    with pytest.raises(DicUiChangedError, match="incomplete pagination"):
        await collect_complete_pages(
            FetchPage([empty_last]),
            first,
            contract=CONTRACT,
            employee_id="123",
        )


def test_contract_rejects_unsafe_endpoint_and_missing_employee_query_key() -> None:
    kwargs: dict[str, Any] = {
        "resource": "employees.synthetic",
        "allowed_query_keys": frozenset({"employee_id"}),
        "employee_query_key": "employee_id",
        "required_item_keys": frozenset({"id"}),
    }
    with pytest.raises(ValueError, match="endpoint"):
        PaginatedEndpointContract(endpoint_path="/arbitrary", **kwargs)
    with pytest.raises(ValueError, match="query key"):
        PaginatedEndpointContract(
            endpoint_path="/backend_apiV2/synthetic",
            **{**kwargs, "allowed_query_keys": frozenset({"page"})},
        )
