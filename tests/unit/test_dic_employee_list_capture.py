from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from itertools import chain, combinations
from typing import Any
from urllib.parse import urlencode

import pytest

from bh_dic.dic.employee_list_capture import (
    EMPLOYEE_LIST_ENDPOINT_ORIGIN,
    EMPLOYEE_LIST_ENDPOINT_PATH,
    EMPLOYEE_LIST_PAGINATOR_PATH,
    MAX_EMPLOYEE_LIST_RESPONSE_BYTES,
    EmployeeListResponseCapture,
    employee_list_result_from_response,
)
from bh_dic.dic.errors import DicAuthorizationError, DicUiChangedError
from bh_dic.dic.models import EmployeeFilter, EmployeeListQuery, SortDirection

_CONTRACT_TECHNICAL_KEYS = frozenset(
    {
        "flexible_workinghours",
        "hours_alert",
        "note",
        "ongoing",
        "workinghours",
        "workinghours_list",
    }
)


@dataclass(frozen=True)
class _Request:
    method: str = "GET"


class _Response:
    def __init__(
        self,
        *,
        url: str,
        document: dict[str, Any] | None = None,
        body: bytes | None = None,
        method: str = "GET",
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        content_length: str | None = None,
        include_content_length: bool = True,
    ) -> None:
        encoded = json.dumps(document, ensure_ascii=False).encode() if body is None else body
        self.url = url
        self.request = _Request(method)
        self.status = status
        self._body = encoded
        self._headers = {"content-type": content_type}
        if include_content_length:
            self._headers["content-length"] = (
                content_length if content_length is not None else str(len(encoded))
            )

    async def body(self) -> bytes:
        return self._body

    async def header_value(self, name: str) -> str | None:
        return self._headers.get(name.casefold())


class _EventPage:
    def __init__(self) -> None:
        self.handlers: list[object] = []

    def on(self, event: str, handler: object) -> None:
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event: str, handler: object) -> None:
        assert event == "response"
        self.handlers.remove(handler)

    def emit(self, response: _Response) -> None:
        for handler in tuple(self.handlers):
            handler(response)  # type: ignore[operator]


def _query(
    *,
    employee_filter: EmployeeFilter = EmployeeFilter.ACTIVE,
    search: str | None = None,
    page: int = 1,
    sort_by: str = "name",
    direction: SortDirection = SortDirection.ASC,
) -> EmployeeListQuery:
    return EmployeeListQuery(
        query=search,
        employee_filter=employee_filter,
        page=page,
        sort_by=sort_by,  # type: ignore[arg-type]
        sort_direction=direction,
    )


def _url(
    query: EmployeeListQuery,
    *,
    origin: str = EMPLOYEE_LIST_ENDPOINT_ORIGIN,
    path: str = EMPLOYEE_LIST_ENDPOINT_PATH,
    page: int | None = None,
) -> str:
    sort_field = {"name": "full_name", "contract": "current_contract"}[query.sort_by]
    if query.sort_direction is SortDirection.DESC:
        sort_field = f"-{sort_field}"
    pairs = [
        ("search", query.query or ""),
        ("filter_type", "and"),
        ("page", str(query.page if page is None else page)),
        ("per_page", "20"),
        ("sort", sort_field),
        ("search_fields", "full_name,job_title,number,teams,email,tax_code"),
    ]
    if query.employee_filter is not EmployeeFilter.ALL:
        pairs.extend(
            (
                ("filter[0][field]", "active"),
                ("filter[0][op]", "="),
                (
                    "filter[0][value]",
                    "1" if query.employee_filter is EmployeeFilter.ACTIVE else "0",
                ),
            )
        )
    return f"{origin}{path}?{urlencode(pairs)}"


def _paginator_url(query: EmployeeListQuery, *, page: int) -> str:
    return _url(query, path=EMPLOYEE_LIST_PAGINATOR_PATH, page=page)


def _employee(employee_id: int = 101) -> dict[str, Any]:
    return {
        "id": employee_id,
        "company_id": 123_456_789,
        "active": True,
        "full_name": "Alice Example",
        "email": "alice@example.invalid",
        "tax_code": "SYNTHETIC123456",
        "number": "PAY-0001",
        "job_title": "Senior tester",
        "has_access": True,
        "invited": False,
        "current_contract": {
            "id": 501,
            "hours_type": "Full time",
            "part_time_percentage": 100,
            "permanent": False,
            "valid_from": "2026-01-01",
            "valid_to": "2026-09-30",
        },
        "current_workplace": {"id": 7, "name": "Synthetic hotel"},
        "main_role": {
            "role": {"id": 3, "name": "Employee", "category": "worker"},
            "team": {"id": 4, "name": "Front desk"},
        },
    }


def _technical_contract_values() -> dict[str, object]:
    return {
        "flexible_workinghours": False,
        "hours_alert": True,
        "note": None,
        "ongoing": False,
        "workinghours": None,
        "workinghours_list": None,
    }


def _document(
    query: EmployeeListQuery, *, employees: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    data = [_employee()] if employees is None else employees
    total = len(data)
    paginator_path = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_PAGINATOR_PATH}"
    return {
        "current_page": query.page,
        "data": data,
        "first_page_url": _paginator_url(query, page=1),
        "from": 1 if data else None,
        "last_page": 1,
        "last_page_url": _paginator_url(query, page=1),
        "links": [
            {"url": None, "label": "Previous", "active": False},
            {"url": _paginator_url(query, page=1), "label": "1", "active": True},
            {"url": None, "label": "Next", "active": False},
        ],
        "next_page_url": None,
        "path": paginator_path,
        "per_page": 20,
        "prev_page_url": None,
        "to": len(data) if data else None,
        "total": total,
    }


def _second_page_document(query: EmployeeListQuery) -> dict[str, Any]:
    assert query.page == 2
    document = _document(query)
    document.update(
        {
            "from": 21,
            "last_page": 2,
            "last_page_url": _paginator_url(query, page=2),
            "links": [
                {"url": _paginator_url(query, page=1), "label": "Previous", "active": False},
                {"url": _paginator_url(query, page=2), "label": "2", "active": True},
                {"url": None, "label": "Next", "active": False},
            ],
            "prev_page_url": _paginator_url(query, page=1),
            "to": 21,
            "total": 21,
        }
    )
    return document


def _first_of_two_pages_document(query: EmployeeListQuery) -> dict[str, Any]:
    assert query.page == 1
    document = _document(query, employees=[_employee(employee_id) for employee_id in range(1, 21)])
    document.update(
        {
            "last_page": 2,
            "last_page_url": _paginator_url(query, page=2),
            "links": [
                {"url": None, "label": "Previous", "active": False},
                {"url": _paginator_url(query, page=1), "label": "1", "active": True},
                {"url": _paginator_url(query, page=2), "label": "Next", "active": False},
            ],
            "next_page_url": _paginator_url(query, page=2),
            "to": 20,
            "total": 21,
        }
    )
    return document


@pytest.mark.asyncio
async def test_passive_employee_response_maps_only_redacted_stable_projection() -> None:
    query = _query()
    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=_document(query)),
        query,
        expected_tenant_id="123456789",
    )

    assert result.total == 1
    assert result.page_size == 20
    assert result.has_next is False
    item = result.items[0]
    assert item.employee_id == "101"
    assert item.display_name is not None
    assert item.display_name.get_secret_value() == "Alice Example"
    assert item.display_name_redacted == "A. E."
    assert item.email_redacted == "a***@example.invalid"
    assert item.tax_code_redacted == "***********3456"
    assert item.payroll_number == "****0001"
    assert item.group_name == "Front desk"
    assert item.current_contract_valid_from is not None
    assert item.current_contract_valid_to is not None
    assert item.current_contract_valid_from.isoformat() == "2026-01-01"
    assert item.current_contract_valid_to.isoformat() == "2026-09-30"
    rendered = repr(result)
    assert "Alice" not in rendered
    assert "Alice" not in result.model_dump_json()
    assert "SYNTHETIC123456" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("percentage", [None, 0, 100])
async def test_current_contract_accepts_nullable_percentage_and_integer_boundaries(
    percentage: int | None,
) -> None:
    query = _query()
    document = _document(query)
    document["data"][0]["current_contract"]["part_time_percentage"] = percentage

    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=document), query
    )

    assert result.total == 1
    assert result.items[0].current_contract_valid_from == date(2026, 1, 1)
    assert result.items[0].current_contract_valid_to == date(2026, 9, 30)


@pytest.mark.asyncio
async def test_current_contract_accepts_complete_technical_variant_without_projection() -> None:
    query = _query()
    document = _document(query)
    document["data"][0]["current_contract"].update(_technical_contract_values())

    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=document), query
    )

    assert result.total == 1
    assert result.items[0].current_contract_valid_from == date(2026, 1, 1)
    assert result.items[0].current_contract_valid_to == date(2026, 9, 30)
    serialized = result.model_dump_json()
    for technical_key in _CONTRACT_TECHNICAL_KEYS:
        assert technical_key not in serialized


@pytest.mark.asyncio
async def test_current_contract_accepts_bounded_note_without_projection() -> None:
    query = _query()
    document = _document(query)
    document["data"][0]["current_contract"].update(_technical_contract_values())
    document["data"][0]["current_contract"]["note"] = "PRIVATE_CONTRACT_NOTE_MARKER"

    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=document), query
    )

    assert result.total == 1
    serialized = result.model_dump_json()
    assert "PRIVATE_CONTRACT_NOTE_MARKER" not in serialized
    for technical_key in _CONTRACT_TECHNICAL_KEYS:
        assert technical_key not in serialized


@pytest.mark.asyncio
async def test_current_contract_validates_base_and_extended_shapes_per_employee_record() -> None:
    query = _query()
    base_employee = _employee(101)
    extended_employee = _employee(102)
    extended_employee["current_contract"].update(_technical_contract_values())
    document = _document(query, employees=[base_employee, extended_employee])

    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=document), query
    )

    assert tuple(item.employee_id for item in result.items) == ("101", "102")
    serialized = result.model_dump_json()
    for technical_key in _CONTRACT_TECHNICAL_KEYS:
        assert technical_key not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("flexible_workinghours", 1),
        ("flexible_workinghours", "PRIVATE_TECHNICAL_VALUE_MARKER"),
        ("hours_alert", 0),
        ("hours_alert", None),
        ("ongoing", 1.0),
        ("ongoing", "false"),
        ("note", False),
        ("note", "x" * 4097),
        ("workinghours", False),
        ("workinghours_list", []),
    ],
)
async def test_current_contract_rejects_non_exact_technical_value_shapes(
    field: str,
    invalid_value: object,
) -> None:
    query = _query()
    document = _document(query)
    document["data"][0]["current_contract"].update(_technical_contract_values())
    document["data"][0]["current_contract"][field] = invalid_value

    with pytest.raises(
        DicUiChangedError,
        match=r"invalid (?:response|current contract) schema",
    ) as captured:
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "PRIVATE_TECHNICAL_VALUE_MARKER" not in repr(captured.value)


@pytest.mark.asyncio
async def test_current_contract_rejects_every_nonempty_partial_technical_key_variant() -> None:
    query = _query()
    proper_subsets = chain.from_iterable(
        combinations(sorted(_CONTRACT_TECHNICAL_KEYS), size)
        for size in range(1, len(_CONTRACT_TECHNICAL_KEYS))
    )

    for subset in proper_subsets:
        document = _document(query)
        private_marker = "PRIVATE_PARTIAL_VARIANT_MARKER"
        document["data"][0]["current_contract"].update({key: private_marker for key in subset})
        with pytest.raises(
            DicUiChangedError,
            match="invalid current contract schema",
        ) as captured:
            await employee_list_result_from_response(
                _Response(url=_url(query), document=document), query
            )
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert private_marker not in repr(captured.value)


@pytest.mark.asyncio
async def test_current_contract_rejects_unknown_extended_key_without_private_leakage() -> None:
    query = _query()
    document = _document(query)
    private_marker = "PRIVATE_UNKNOWN_CONTRACT_MARKER"
    document["data"][0]["current_contract"].update(
        {key: private_marker for key in _CONTRACT_TECHNICAL_KEYS}
    )
    document["data"][0]["current_contract"]["unknown_private_field"] = private_marker

    with pytest.raises(
        DicUiChangedError,
        match="invalid current contract schema",
    ) as captured:
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert private_marker not in repr(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("percentage", "reason"),
    [
        (True, "invalid response schema"),
        ("PRIVATE_CONTRACT_PERCENTAGE_MARKER", "invalid response schema"),
        (50.0, "invalid response schema"),
        (-1, "invalid response schema"),
        (101, "invalid current contract schema"),
        ([], "invalid response schema"),
        ({}, "invalid response schema"),
    ],
)
async def test_current_contract_rejects_invalid_percentage_without_private_exception_chain(
    percentage: object,
    reason: str,
) -> None:
    query = _query()
    document = _document(query)
    document["data"][0]["current_contract"]["part_time_percentage"] = percentage

    with pytest.raises(DicUiChangedError, match=reason) as captured:
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "PRIVATE_CONTRACT_PERCENTAGE_MARKER" not in repr(captured.value)


@pytest.mark.asyncio
async def test_malformed_employee_json_is_detached_from_private_exception_context() -> None:
    query = _query()
    private_marker = "PRIVATE_EMPLOYEE_JSON_MARKER"
    response = _Response(
        url=_url(query),
        body=f'{{"full_name":"{private_marker}"'.encode(),
    )

    with pytest.raises(DicUiChangedError, match="invalid response document") as caught:
        await employee_list_result_from_response(response, query)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_marker not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate_url", "body", "content_type", "method"),
    [
        (lambda url: url.replace("https://", "http://"), None, "application/json", "GET"),
        (
            lambda url: url.replace("secure.dipendentincloud.it", "evil.invalid"),
            None,
            "application/json",
            "GET",
        ),
        (
            lambda url: url.replace(
                "secure.dipendentincloud.it", "secure.dipendentincloud.it.evil.invalid"
            ),
            None,
            "application/json",
            "GET",
        ),
        (lambda url: url.replace("https://", "https://user@"), None, "application/json", "GET"),
        (
            lambda url: url.replace("secure.dipendentincloud.it", "secure.dipendentincloud.it:443"),
            None,
            "application/json",
            "GET",
        ),
        (
            lambda url: url.replace(EMPLOYEE_LIST_ENDPOINT_PATH, "/employees/extra"),
            None,
            "application/json",
            "GET",
        ),
        (
            lambda url: url.removeprefix(EMPLOYEE_LIST_ENDPOINT_ORIGIN),
            None,
            "application/json",
            "GET",
        ),
        (lambda url: url + "#", None, "application/json", "GET"),
        (lambda url: url + "#fragment", None, "application/json", "GET"),
        (lambda url: url + "&unexpected=1", None, "application/json", "GET"),
        (lambda url: url + "&page=1", None, "application/json", "GET"),
        (lambda url: url, b'{"current_page":1,"current_page":1}', "application/json", "GET"),
        (lambda url: url, b'{"current_page":NaN}', "application/json", "GET"),
        (lambda url: url, None, "text/html", "GET"),
        (lambda url: url, None, "application/json", "POST"),
    ],
)
async def test_response_boundary_rejects_endpoint_query_json_and_media_mismatches_without_pii(
    mutate_url: Any,
    body: bytes | None,
    content_type: str,
    method: str,
) -> None:
    query = _query()
    response = _Response(
        url=mutate_url(_url(query)),
        document=_document(query),
        body=body,
        content_type=content_type,
        method=method,
    )
    with pytest.raises(DicUiChangedError) as captured:
        await employee_list_result_from_response(response, query)
    message = str(captured.value)
    assert "Alice" not in message
    assert "example.invalid" not in message
    assert EMPLOYEE_LIST_ENDPOINT_PATH not in message


@pytest.mark.asyncio
async def test_response_and_paginator_paths_are_not_interchangeable() -> None:
    query = _query()
    response_at_paginator_path = _url(query).replace(
        EMPLOYEE_LIST_ENDPOINT_PATH, EMPLOYEE_LIST_PAGINATOR_PATH
    )
    with pytest.raises(DicUiChangedError, match="unexpected endpoint metadata"):
        await employee_list_result_from_response(
            _Response(url=response_at_paginator_path, document=_document(query)), query
        )

    document = _document(query)
    document["path"] = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_ENDPOINT_PATH}"
    with pytest.raises(DicUiChangedError, match="unexpected endpoint metadata"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["path", "first_page_url", "last_page_url", "next_page_url", "prev_page_url", "links"],
)
async def test_every_paginator_url_field_rejects_the_response_endpoint_path(field: str) -> None:
    query = _query()
    document = _document(query)
    endpoint = f"{EMPLOYEE_LIST_ENDPOINT_ORIGIN}{EMPLOYEE_LIST_ENDPOINT_PATH}"
    if field == "path":
        document[field] = endpoint
    elif field == "links":
        document[field][1]["url"] = f"{endpoint}?page=1"
    else:
        page = {"first_page_url": 1, "last_page_url": 1, "next_page_url": 2, "prev_page_url": 0}[
            field
        ]
        document[field] = f"{endpoint}?page={page}"

    with pytest.raises(DicUiChangedError, match="unexpected endpoint metadata"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_path",
    [
        "/employees",
        "http://secure.dipendentincloud.it/employees",
        "https://user@secure.dipendentincloud.it/employees",
        "https://secure.dipendentincloud.it:443/employees",
        "https://secure.dipendentincloud.it.evil.invalid/employees",
        "https://secure.dipendentincloud.it/employees/extra",
        "https://secure.dipendentincloud.it/employees-lookalike",
        "https://secure.dipendentincloud.it/employees#",
        "https://secure.dipendentincloud.it/employees#fragment",
        "https://secure.dipendentincloud.it/employees?",
        "https://secure.dipendentincloud.it/employees?page=1",
    ],
)
async def test_paginator_path_rejects_non_exact_origin_path_and_forbidden_components(
    invalid_path: str,
) -> None:
    query = _query()
    document = _document(query)
    document["path"] = invalid_path

    with pytest.raises(DicUiChangedError, match="endpoint metadata"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["first_page_url", "last_page_url", "next_page_url", "prev_page_url", "links"],
)
async def test_every_non_null_paginator_url_rejects_extra_query_metadata(field: str) -> None:
    query = _query(page=2) if field == "prev_page_url" else _query()
    document = _second_page_document(query) if query.page == 2 else _document(query)
    if field == "links":
        document["links"][1]["url"] = f"{_paginator_url(query, page=1)}&unexpected=accepted"
    else:
        page = {"first_page_url": 1, "last_page_url": 1, "next_page_url": 2, "prev_page_url": 1}[
            field
        ]
        document[field] = f"{_paginator_url(query, page=page)}&unexpected=accepted"

    with pytest.raises(DicUiChangedError, match="query metadata"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )


@pytest.mark.asyncio
async def test_paginator_urls_preserve_full_ui_query_and_do_not_trust_labels() -> None:
    query = _query(
        employee_filter=EmployeeFilter.INACTIVE,
        search="synthetic",
        sort_by="contract",
        direction=SortDirection.DESC,
    )
    document = _document(query)
    document["links"][1]["label"] = "untrusted display text"

    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=document), query
    )

    assert result.total == 1


@pytest.mark.asyncio
async def test_paginator_url_rejects_a_changed_preserved_ui_query_value() -> None:
    query = _query(search="synthetic")
    document = _document(query)
    document["links"][1]["url"] = str(document["links"][1]["url"]).replace(
        "search=synthetic", "search=other"
    )

    with pytest.raises(DicUiChangedError, match="search does not match"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )


@pytest.mark.asyncio
async def test_link_boundaries_reject_swaps_wrong_pages_and_null_contradictions() -> None:
    first_query = _query()
    second_query = _query(page=2)
    invalid_documents: list[tuple[EmployeeListQuery, dict[str, Any]]] = []

    swapped_first = _first_of_two_pages_document(first_query)
    swapped_first["links"][0]["url"] = _paginator_url(first_query, page=2)
    swapped_first["links"][-1]["url"] = None
    invalid_documents.append((first_query, swapped_first))

    wrong_next = _first_of_two_pages_document(first_query)
    wrong_next["links"][-1]["url"] = _paginator_url(first_query, page=1)
    invalid_documents.append((first_query, wrong_next))

    missing_next = _first_of_two_pages_document(first_query)
    missing_next["links"][-1]["url"] = None
    invalid_documents.append((first_query, missing_next))

    swapped_second = _second_page_document(second_query)
    swapped_second["links"][0]["url"] = None
    swapped_second["links"][-1]["url"] = _paginator_url(second_query, page=1)
    invalid_documents.append((second_query, swapped_second))

    wrong_previous = _second_page_document(second_query)
    wrong_previous["links"][0]["url"] = _paginator_url(second_query, page=2)
    invalid_documents.append((second_query, wrong_previous))

    missing_previous = _second_page_document(second_query)
    missing_previous["links"][0]["url"] = None
    invalid_documents.append((second_query, missing_previous))

    active_boundary = _first_of_two_pages_document(first_query)
    active_boundary["links"][-1]["active"] = True
    invalid_documents.append((first_query, active_boundary))

    too_short = _document(first_query)
    too_short["links"] = too_short["links"][1:]
    invalid_documents.append((first_query, too_short))

    for query, document in invalid_documents:
        with pytest.raises(DicUiChangedError, match="pagination"):
            await employee_list_result_from_response(
                _Response(url=_url(query), document=document), query
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", [" ", "\x00", "\n", "\r", "\t", "\x7f"])
@pytest.mark.parametrize("target", ["response", "paginator"])
async def test_employee_urls_reject_lexically_unsafe_characters(
    unsafe: str,
    target: str,
) -> None:
    query = _query()
    response_url = _url(query)
    document = _document(query)
    if target == "response":
        response_url = f"{unsafe}{response_url}"
    else:
        document["path"] = f"{unsafe}{document['path']}"

    with pytest.raises(DicUiChangedError, match="endpoint metadata"):
        await employee_list_result_from_response(
            _Response(url=response_url, document=document), query
        )


@pytest.mark.asyncio
async def test_paginator_page_number_is_bounded_and_has_no_private_exception_chain() -> None:
    query = _query()
    document = _document(query)
    document["links"][1]["url"] = _paginator_url(query, page=1).replace(
        "page=1", f"page={'9' * 5_000}"
    )

    with pytest.raises(DicUiChangedError, match="pagination metadata") as captured:
        await employee_list_result_from_response(
            _Response(url=_url(query), document=document), query
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_response_boundary_rejects_oversize_and_closed_schema_mismatches() -> None:
    query = _query()
    oversized = _Response(
        url=_url(query),
        body=b"{}",
        content_length=str(MAX_EMPLOYEE_LIST_RESPONSE_BYTES + 1),
    )
    with pytest.raises(DicUiChangedError, match="read limit"):
        await employee_list_result_from_response(oversized, query)

    oversized_without_header = _Response(
        url=_url(query),
        body=b" " * (MAX_EMPLOYEE_LIST_RESPONSE_BYTES + 1),
        include_content_length=False,
    )
    with pytest.raises(DicUiChangedError, match="read limit"):
        await employee_list_result_from_response(oversized_without_header, query)

    extra_root = _document(query)
    extra_root["unexpected"] = True
    assert (
        await employee_list_result_from_response(
            _Response(url=_url(query), document=extra_root), query
        )
    ).total == 1

    invalid_contract = _document(query)
    invalid_contract["data"][0]["current_contract"]["valid_to"] = "30/09/2026"
    with pytest.raises(DicUiChangedError, match="contract date"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=invalid_contract), query
        )

    iso_week_contract = _document(query)
    iso_week_contract["data"][0]["current_contract"]["valid_to"] = "2026-W01-1"
    with pytest.raises(DicUiChangedError, match="contract date"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=iso_week_contract), query
        )

    extra_employee_field = _document(query)
    extra_employee_field["data"][0]["unexpected"] = "ignored additive value"
    assert (
        await employee_list_result_from_response(
            _Response(url=_url(query), document=extra_employee_field), query
        )
    ).items[0].employee_id == str(extra_employee_field["data"][0]["id"])

    partial_page = _document(query)
    partial_page["total"] = 2
    partial_page["to"] = 1
    with pytest.raises(DicUiChangedError, match="pagination bounds"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=partial_page), query
        )

    wrong_active_link = _document(query)
    wrong_active_link["links"][1]["active"] = False
    with pytest.raises(DicUiChangedError, match="pagination metadata"):
        await employee_list_result_from_response(
            _Response(url=_url(query), document=wrong_active_link), query
        )


@pytest.mark.asyncio
async def test_employee_projection_binds_tenant_and_never_exposes_short_identifiers() -> None:
    query = _query()
    short_identifier = _document(query)
    short_identifier["data"][0]["number"] = "007"
    result = await employee_list_result_from_response(
        _Response(url=_url(query), document=short_identifier),
        query,
        expected_tenant_id="123456789",
    )
    assert result.items[0].payroll_number == "***"

    with pytest.raises(DicAuthorizationError, match="tenant") as captured:
        await employee_list_result_from_response(
            _Response(url=_url(query), document=_document(query)),
            query,
            expected_tenant_id="987654321",
        )
    assert "123456789" not in str(captured.value)
    assert "987654321" not in str(captured.value)

    without_contract = _document(query)
    without_contract["data"][0]["current_contract"] = None
    no_contract_result = await employee_list_result_from_response(
        _Response(url=_url(query), document=without_contract), query
    )
    assert no_contract_result.items[0].current_contract_valid_from is None
    assert no_contract_result.items[0].current_contract_valid_to is None


@pytest.mark.asyncio
async def test_employee_response_normalizes_non_bytes_and_json_integer_failures() -> None:
    query = _query()

    class NonBytesResponse(_Response):
        async def body(self) -> Any:
            return "not bytes"

    with pytest.raises(DicUiChangedError, match="invalid response body"):
        await employee_list_result_from_response(
            NonBytesResponse(url=_url(query), document=_document(query)), query
        )

    huge_integer = b'{"value":' + (b"9" * 5_000) + b"}"
    with pytest.raises(DicUiChangedError, match="invalid response document"):
        await employee_list_result_from_response(
            _Response(url=_url(query), body=huge_integer), query
        )


@pytest.mark.asyncio
async def test_response_boundary_validates_filter_sort_and_pagination_correspondence() -> None:
    expected = _query(
        employee_filter=EmployeeFilter.INACTIVE,
        search="synthetic",
        sort_by="contract",
        direction=SortDirection.DESC,
    )
    wrong_filter_url = _url(expected).replace(
        "filter%5B0%5D%5Bvalue%5D=0", "filter%5B0%5D%5Bvalue%5D=1"
    )
    with pytest.raises(DicUiChangedError, match="filter does not match"):
        await employee_list_result_from_response(
            _Response(url=wrong_filter_url, document=_document(expected)), expected
        )

    wrong_filter_op_url = _url(expected).replace(
        "filter%5B0%5D%5Bop%5D=%3D", "filter%5B0%5D%5Bop%5D=eq"
    )
    with pytest.raises(DicUiChangedError, match="filter does not match"):
        await employee_list_result_from_response(
            _Response(url=wrong_filter_op_url, document=_document(expected)), expected
        )

    wrong_search_fields_url = _url(expected).replace(
        "full_name%2Cjob_title%2Cnumber%2Cteams%2Cemail%2Ctax_code", "full_name"
    )
    with pytest.raises(DicUiChangedError, match="search metadata"):
        await employee_list_result_from_response(
            _Response(url=wrong_search_fields_url, document=_document(expected)), expected
        )

    duplicate_ids = _document(expected, employees=[_employee(), _employee()])
    duplicate_ids["total"] = 2
    duplicate_ids["to"] = 2
    with pytest.raises(DicUiChangedError, match="duplicate employee"):
        await employee_list_result_from_response(
            _Response(url=_url(expected), document=duplicate_ids), expected
        )

    pagination = _document(expected)
    pagination["current_page"] = 2
    with pytest.raises(DicUiChangedError, match="pagination"):
        await employee_list_result_from_response(
            _Response(url=_url(expected), document=pagination), expected
        )


@pytest.mark.asyncio
async def test_capture_ignores_pre_marker_and_intermediate_query_then_accepts_match() -> None:
    page = _EventPage()
    expected = _query(employee_filter=EmployeeFilter.ALL)
    stale = _query()
    with EmployeeListResponseCapture(page) as capture:
        page.emit(_Response(url=_url(stale), document=_document(stale)))
        mark = capture.mark()
        page.emit(_Response(url=_url(stale), document=_document(stale)))
        matching = _Response(url=_url(expected), document=_document(expected))
        page.emit(matching)
        observed = await capture.wait_for(expected, after_sequence=mark, timeout_ms=100)
    assert observed is matching
    assert not page.handlers


@pytest.mark.asyncio
async def test_capture_fails_closed_on_same_path_wrong_origin_and_bounded_queue() -> None:
    page = _EventPage()
    expected = _query()
    with EmployeeListResponseCapture(page) as capture:
        mark = capture.mark()
        page.emit(
            _Response(
                url=_url(expected, origin="https://evil.invalid"),
                document=_document(expected),
            )
        )
        page.emit(_Response(url=_url(expected), document=_document(expected)))
        with pytest.raises(DicUiChangedError, match="endpoint metadata"):
            await capture.wait_for(expected, after_sequence=mark, timeout_ms=100)

    with EmployeeListResponseCapture(page) as capture:
        mark = capture.mark()
        for _ in range(33):
            page.emit(_Response(url=_url(expected), document=_document(expected)))
        with pytest.raises(DicUiChangedError, match="overflowed"):
            await capture.wait_for(expected, after_sequence=mark, timeout_ms=100)
