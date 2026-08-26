"""Passive, fail-closed validation of the employee-list response emitted by DIC UI.

The module never issues an HTTP request.  A caller must install
``EmployeeListResponseCapture`` around a fixed first-party navigation or UI action and pass the
captured Playwright response here.  Only the bounded projection required by
``EmployeeListResult`` leaves this boundary; clear display names are wrapped in ``SecretStr``
and are consumed only by authorized, ephemeral HR rendering.

The emitted response and its paginator metadata intentionally use different exact paths.
Every non-null paginator URL must preserve the complete validated UI query, with only its
canonical page number changed; display labels are never trusted as page identifiers.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from pydantic import SecretStr, ValidationError

from bh_dic.dic.errors import DicAuthorizationError, DicConfigurationError, DicUiChangedError
from bh_dic.dic.models import (
    AccountState,
    EmployeeFilter,
    EmployeeListItem,
    EmployeeListQuery,
    EmployeeListResult,
    EmployeeState,
    SortDirection,
)

EMPLOYEE_LIST_ENDPOINT_ORIGIN = "https://secure.dipendentincloud.it"
EMPLOYEE_LIST_ENDPOINT_PATH = "/backend_apiV2/employees"
EMPLOYEE_LIST_PAGINATOR_PATH = "/employees"
EMPLOYEE_LIST_PAGE_SIZE = 20
MAX_EMPLOYEE_LIST_RESPONSE_BYTES = 256 * 1024
MAX_CAPTURED_EMPLOYEE_RESPONSES = 32

_ROOT_KEYS = frozenset(
    {
        "current_page",
        "data",
        "first_page_url",
        "from",
        "last_page",
        "last_page_url",
        "links",
        "next_page_url",
        "path",
        "per_page",
        "prev_page_url",
        "to",
        "total",
    }
)
_EMPLOYEE_KEYS = frozenset(
    {
        "active",
        "birth_date",
        "company_id",
        "current_contract",
        "current_workingtime",
        "current_workplace",
        "email",
        "first_name",
        "full_name",
        "has_access",
        "id",
        "invited",
        "is_admin",
        "is_master",
        "job_title",
        "last_name",
        "main_role",
        "number",
        "permissions_reduced",
        "person",
        "person_id",
        "picture",
        "tax_code",
        "teams",
        "workplace_id",
    }
)
_REQUIRED_EMPLOYEE_KEYS = frozenset(
    {
        "active",
        "company_id",
        "current_contract",
        "current_workplace",
        "email",
        "full_name",
        "has_access",
        "id",
        "invited",
        "job_title",
        "main_role",
        "number",
        "tax_code",
    }
)
_CONTRACT_KEYS = frozenset(
    {"hours_type", "id", "part_time_percentage", "permanent", "valid_from", "valid_to"}
)
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
_EXTENDED_CONTRACT_KEYS = _CONTRACT_KEYS | _CONTRACT_TECHNICAL_KEYS
_WORKPLACE_KEYS = frozenset(
    {"active", "description", "id", "name", "position", "tolerance", "type"}
)
_MAIN_ROLE_KEYS = frozenset({"role", "team"})
_ROLE_KEYS = frozenset({"category", "id", "name"})
_TEAM_KEYS = frozenset({"id", "name"})
_QUERY_KEYS = frozenset(
    {
        "filter_type",
        "filter[0][field]",
        "filter[0][op]",
        "filter[0][value]",
        "page",
        "per_page",
        "search",
        "search_fields",
        "sort",
    }
)
_SORT_FIELDS: Mapping[str, str] = {"name": "full_name", "contract": "current_contract"}
_EMPLOYEE_SEARCH_FIELDS = "full_name,job_title,number,teams,email,tax_code"
_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_UNSAFE_URL_CHARACTER = re.compile(r"[\x00-\x20\x7f]")


class RequestLike(Protocol):
    @property
    def method(self) -> str: ...


class ResponseLike(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def status(self) -> int: ...

    @property
    def request(self) -> RequestLike: ...

    async def body(self) -> bytes: ...

    async def header_value(self, name: str) -> str | None: ...


class ResponseEventSource(Protocol):
    def on(self, event: str, handler: Callable[[ResponseLike], None]) -> None: ...

    def remove_listener(self, event: str, handler: Callable[[ResponseLike], None]) -> None: ...


class _DuplicateJsonKeyError(ValueError):
    pass


class _InvalidJsonConstantError(ValueError):
    pass


class _UiActionMismatch(DicUiChangedError):
    pass


@dataclass(frozen=True, slots=True)
class CapturedEmployeeResponse:
    sequence: int
    response: ResponseLike


def _failure(reason: str) -> DicUiChangedError:
    # Never include URLs, query values, response bodies, employee identifiers, or PII.
    return DicUiChangedError(f"employee list response validation failed: {reason}")


def _mismatch(reason: str) -> _UiActionMismatch:
    return _UiActionMismatch(f"employee list response validation failed: {reason}")


def _tenant_failure() -> DicAuthorizationError:
    return DicAuthorizationError("employee list tenant does not match configuration")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise _InvalidJsonConstantError


def _is_candidate(response: ResponseLike) -> bool:
    try:
        return urlsplit(response.url).path == EMPLOYEE_LIST_ENDPOINT_PATH
    except Exception:
        return False


def _strict_int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _failure("invalid response schema")
    return value


def _strict_bool(value: object) -> bool:
    if type(value) is not bool:
        raise _failure("invalid response schema")
    return value


def _bounded_text(value: object, *, maximum: int, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _failure("invalid response schema")
    return value.strip()


def _nullable_text(value: object, *, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    return _bounded_text(value, maximum=maximum)


def _strict_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise _failure("invalid contract date")
    parsed: date | None = None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        pass
    if parsed is None:
        raise _failure("invalid contract date")
    return parsed


def _strict_url_parts(
    url: object,
    *,
    expected_path: str,
    allow_query: bool,
) -> tuple[object, dict[str, str]]:
    if (
        not isinstance(url, str)
        or len(url) > 8_192
        or _PERCENT_ESCAPE.search(url)
        or _UNSAFE_URL_CHARACTER.search(url)
        or "#" in url
        or (not allow_query and "?" in url)
    ):
        raise _failure("invalid endpoint metadata")
    parsed = None
    port: int | None = None
    parse_failed = False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        parse_failed = True
    if parse_failed or parsed is None:
        raise _failure("invalid endpoint metadata")
    if (
        parsed.scheme != "https"
        or parsed.hostname != "secure.dipendentincloud.it"
        or parsed.netloc != "secure.dipendentincloud.it"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != expected_path
        or parsed.fragment
        or (not allow_query and parsed.query)
    ):
        raise _failure("unexpected endpoint metadata")
    pairs: list[tuple[str, str]] | None = None
    query_failed = False
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=16,
        )
    except (UnicodeError, ValueError):
        query_failed = True
    if query_failed or pairs is None:
        raise _failure("invalid query metadata")
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query:
            raise _failure("duplicate query metadata")
        query[key] = value
    return parsed, query


def _response_endpoint_parts(url: object) -> tuple[object, dict[str, str]]:
    return _strict_url_parts(
        url,
        expected_path=EMPLOYEE_LIST_ENDPOINT_PATH,
        allow_query=True,
    )


def _paginator_parts(url: object, *, allow_query: bool) -> tuple[object, dict[str, str]]:
    return _strict_url_parts(
        url,
        expected_path=EMPLOYEE_LIST_PAGINATOR_PATH,
        allow_query=allow_query,
    )


def _expected_sort(query: EmployeeListQuery) -> str:
    field = _SORT_FIELDS.get(query.sort_by)
    if field is None:
        raise _failure("requested sort is not live-verified")
    return field if query.sort_direction is SortDirection.ASC else f"-{field}"


def _validate_query(actual: Mapping[str, str], expected: EmployeeListQuery) -> None:
    if not actual or set(actual).difference(_QUERY_KEYS):
        raise _failure("unexpected query metadata")
    required = {"filter_type", "page", "per_page", "search", "search_fields", "sort"}
    if not required.issubset(actual):
        raise _failure("required query metadata unavailable")
    if actual["page"] != str(expected.page) or actual["per_page"] != str(EMPLOYEE_LIST_PAGE_SIZE):
        raise _mismatch("query pagination does not match UI action")
    if actual["search"] != (expected.query or ""):
        raise _mismatch("query search does not match UI action")
    if actual["sort"] != _expected_sort(expected):
        raise _mismatch("query sort does not match UI action")
    if actual["filter_type"].casefold() != "and":
        raise _failure("query filter metadata is invalid")
    if actual["search_fields"] != _EMPLOYEE_SEARCH_FIELDS:
        raise _failure("query search metadata is invalid")

    filter_keys = {key for key in actual if key.startswith("filter[")}
    if expected.employee_filter is EmployeeFilter.ALL:
        if filter_keys:
            raise _mismatch("query filter does not match UI action")
        return
    if filter_keys != {
        "filter[0][field]",
        "filter[0][op]",
        "filter[0][value]",
    }:
        raise _failure("query filter metadata is invalid")
    expected_value = "1" if expected.employee_filter is EmployeeFilter.ACTIVE else "0"
    if (
        actual["filter[0][field]"] != "active"
        or actual["filter[0][value]"] != expected_value
        or actual["filter[0][op]"] != "="
    ):
        raise _mismatch("query filter does not match UI action")


async def validate_employee_response_metadata(
    response: ResponseLike,
    expected: EmployeeListQuery,
    *,
    max_body_bytes: int = MAX_EMPLOYEE_LIST_RESPONSE_BYTES,
) -> None:
    """Validate first-party response metadata and its UI-correlated query, without reading PII."""

    response_url: object | None = None
    url_failed = False
    try:
        response_url = response.url
    except Exception:
        url_failed = True
    if url_failed or response_url is None:
        raise _failure("response metadata unavailable")
    _, query = _response_endpoint_parts(response_url)
    _validate_query(query, expected)
    metadata_failed = False
    method: object | None = None
    status: object | None = None
    content_type: object | None = None
    content_length: object | None = None
    try:
        method = response.request.method
        status = response.status
        content_type = await response.header_value("content-type")
        content_length = await response.header_value("content-length")
    except Exception:
        metadata_failed = True
    if metadata_failed:
        raise _failure("response metadata unavailable")
    if method != "GET" or status != 200:
        raise _failure("unexpected response metadata")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().casefold() != "application/json"
    ):
        raise _failure("unexpected response media type")
    if content_length is not None:
        if (
            not isinstance(content_length, str)
            or len(content_length) > 20
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", content_length) is None
        ):
            raise _failure("invalid response size metadata")
        declared_length = int(content_length, 10)
        if declared_length > max_body_bytes:
            raise _failure("response exceeds the read limit")


def _validate_paginator_path(value: object) -> None:
    _paginator_parts(value, allow_query=False)


def _query_at_page(expected: EmployeeListQuery, page: int) -> EmployeeListQuery:
    if page < 1 or page > 10_000:
        raise _failure("invalid pagination metadata")
    query: EmployeeListQuery | None = None
    query_failed = False
    try:
        query = EmployeeListQuery(
            query=expected.query,
            employee_filter=expected.employee_filter,
            sort_by=expected.sort_by,
            sort_direction=expected.sort_direction,
            page=page,
            page_size=expected.page_size,
        )
    except ValidationError:
        query_failed = True
    if query_failed or query is None:
        raise _failure("invalid pagination metadata")
    return query


def _page_from_url(
    value: object,
    expected: EmployeeListQuery,
    *,
    expected_page: int | None = None,
    last_page: int | None = None,
) -> int:
    _, query = _paginator_parts(value, allow_query=True)
    raw_page = query.get("page")
    if raw_page is None or re.fullmatch(r"[1-9][0-9]{0,4}", raw_page) is None:
        raise _failure("invalid pagination metadata")
    page = int(raw_page, 10)
    if (expected_page is not None and page != expected_page) or (
        last_page is not None and page > last_page
    ):
        raise _failure("invalid pagination metadata")
    _validate_query(query, _query_at_page(expected, page))
    return page


def _validate_links(
    value: object,
    expected: EmployeeListQuery,
    *,
    current_page: int,
    last_page: int,
) -> None:
    if not isinstance(value, list) or not 3 <= len(value) <= 64:
        raise _failure("invalid pagination schema")
    active_links = 0
    final_index = len(value) - 1
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not {"active", "label", "url"}.issubset(item):
            raise _failure("invalid pagination schema")
        active = _strict_bool(item["active"])
        _bounded_text(item["label"], maximum=128)
        url = item["url"]
        if index == 0:
            if active or (url is None) != (current_page == 1):
                raise _failure("invalid pagination metadata")
            if url is not None:
                _page_from_url(
                    url,
                    expected,
                    expected_page=current_page - 1,
                    last_page=last_page,
                )
            continue
        if index == final_index:
            if active or (url is None) != (current_page == last_page):
                raise _failure("invalid pagination metadata")
            if url is not None:
                _page_from_url(
                    url,
                    expected,
                    expected_page=current_page + 1,
                    last_page=last_page,
                )
            continue
        linked_page: int | None = None
        if url is not None:
            linked_page = _page_from_url(url, expected, last_page=last_page)
        if active:
            active_links += 1
            if linked_page != current_page:
                raise _failure("invalid pagination metadata")
    if active_links != 1:
        raise _failure("invalid pagination metadata")


def _nested_name(
    value: object,
    *,
    object_keys: frozenset[str],
    allow_none: bool = True,
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, dict):
        raise _failure("invalid response schema")
    if "name" not in value:
        raise _failure("invalid response schema")
    return _nullable_text(value["name"], maximum=128)


def _main_role_group(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _failure("invalid response schema")
    role = value.get("role")
    team = value.get("team")
    if role is not None:
        _nested_name(role, object_keys=_ROLE_KEYS)
    return _nested_name(team, object_keys=_TEAM_KEYS)


def _workplace_name(value: object) -> str | None:
    return _nested_name(value, object_keys=_WORKPLACE_KEYS)


def _contract_projection(value: object) -> tuple[str | None, bool | None, date | None, date | None]:
    if value is None:
        return None, None, None, None
    if not isinstance(value, dict):
        raise _failure("invalid current contract schema")
    contract_keys = value.keys()
    if not _CONTRACT_KEYS.issubset(contract_keys):
        raise _failure("invalid current contract schema")
    technical_present = _CONTRACT_TECHNICAL_KEYS.intersection(contract_keys)
    if technical_present and technical_present != _CONTRACT_TECHNICAL_KEYS:
        raise _failure("invalid current contract schema")
    if technical_present:
        # These discard-only fields have one bounded live-observed shape.  They never leave this
        # validation boundary; booleans are strict (integers are rejected), the optional note is
        # validated only as bounded text, and working-hours structures remain literal null.  Any
        # future partial or unknown variant remains fail-closed.
        technical_invalid = False
        try:
            _strict_bool(value["flexible_workinghours"])
            _strict_bool(value["hours_alert"])
            _strict_bool(value["ongoing"])
        except DicUiChangedError:
            technical_invalid = True
        if technical_invalid:
            raise _failure("invalid current contract schema")
        note_invalid = False
        try:
            _nullable_text(value["note"], maximum=4096)
        except DicUiChangedError:
            note_invalid = True
        if note_invalid:
            raise _failure("invalid current contract schema")
        if value["workinghours"] is not None or value["workinghours_list"] is not None:
            raise _failure("invalid current contract schema")
    _strict_int(value["id"], minimum=1)
    hours_type = _bounded_text(value["hours_type"], maximum=128)
    raw_percentage = value["part_time_percentage"]
    if raw_percentage is not None:
        percentage = _strict_int(raw_percentage)
        if percentage > 100:
            raise _failure("invalid current contract schema")
    permanent = _strict_bool(value["permanent"])
    valid_from = _strict_date(value["valid_from"])
    valid_to = _strict_date(value["valid_to"])
    if valid_from is None or (valid_to is not None and valid_to < valid_from):
        raise _failure("invalid current contract date range")
    return hours_type, permanent, valid_from, valid_to


def _employee_item(value: object, *, expected_tenant_id: str | None) -> EmployeeListItem:
    if not isinstance(value, dict) or not _REQUIRED_EMPLOYEE_KEYS.issubset(value):
        raise _failure("invalid employee schema")
    employee_id = _strict_int(value["id"], minimum=1)
    company_id = _strict_int(value["company_id"], minimum=1)
    if expected_tenant_id is not None and str(company_id) != expected_tenant_id:
        raise _tenant_failure()
    active = _strict_bool(value["active"])
    has_access = _strict_bool(value["has_access"])
    invited = _strict_bool(value["invited"])
    full_name = _bounded_text(value["full_name"], maximum=256)
    if full_name is None:
        raise _failure("invalid employee schema")
    first_name = _nullable_text(value.get("first_name"), maximum=128)
    last_name = _nullable_text(value.get("last_name"), maximum=128)
    email = _nullable_text(value["email"], maximum=320)
    tax_code = _nullable_text(value["tax_code"], maximum=32)
    number = _nullable_text(value["number"], maximum=64)
    job_title = _nullable_text(value["job_title"], maximum=128)
    hours_type, permanent, valid_from, valid_to = _contract_projection(value["current_contract"])
    account_state = (
        AccountState.CONNECTED
        if has_access
        else AccountState.INVITED
        if invited
        else AccountState.NOT_CONNECTED
    )
    contract_period = None
    if valid_from is not None:
        contract_period = (
            f"{valid_from.isoformat()} → {valid_to.isoformat() if valid_to else 'open'}"
        )
    projection: EmployeeListItem | None = None
    projection_failed = False
    try:
        projection = EmployeeListItem(
            employee_id=str(employee_id),
            display_name=SecretStr(full_name),
            first_name=SecretStr(first_name) if first_name else None,
            last_name=SecretStr(last_name) if last_name else None,
            display_name_redacted=_redact_name(full_name) or "[REDACTED]",
            email_redacted=_redact_email(email),
            tax_code_redacted=_redact_tail(tax_code),
            job_title=job_title,
            group_name=_main_role_group(value["main_role"]),
            payroll_number=_redact_tail(number),
            contract_label=hours_type,
            contract_state=(
                None if permanent is None else "permanent" if permanent else "fixed_term"
            ),
            contract_period=contract_period,
            schedule_model=hours_type,
            workplace=_workplace_name(value["current_workplace"]),
            account_state=account_state,
            employee_state=EmployeeState.ACTIVE if active else EmployeeState.INACTIVE,
            current_contract_valid_from=valid_from,
            current_contract_valid_to=valid_to,
        )
    except ValidationError:
        projection_failed = True
    if projection_failed or projection is None:
        raise _failure("invalid employee projection")
    return projection


def _redact_name(value: str | None) -> str | None:
    if not value:
        return None
    words = [part for part in value.strip().split() if part]
    return " ".join(f"{part[0].upper()}." for part in words)


def _redact_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return "[REDACTED]" if value else None
    local, domain = value.split("@", 1)
    if not local or not domain:
        return "[REDACTED]"
    return f"{local[0]}***@{domain}"


def _redact_tail(value: str | None, visible: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= visible:
        return "*" * len(value)
    visible = min(visible, len(value))
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"


def _pagination(document: dict[str, object], expected: EmployeeListQuery) -> tuple[int, int, bool]:
    current_page = _strict_int(document["current_page"], minimum=1)
    last_page = _strict_int(document["last_page"], minimum=1)
    per_page = _strict_int(document["per_page"], minimum=1)
    total = _strict_int(document["total"])
    if (
        current_page != expected.page
        or per_page != EMPLOYEE_LIST_PAGE_SIZE
        or last_page != max(1, (total + per_page - 1) // per_page)
        or current_page > last_page
    ):
        raise _failure("pagination does not match UI action")
    data = document["data"]
    if not isinstance(data, list) or len(data) > per_page:
        raise _failure("invalid pagination schema")
    offset = (current_page - 1) * per_page
    expected_count = min(per_page, max(0, total - offset))
    if len(data) != expected_count:
        raise _failure("invalid pagination bounds")
    start = document["from"]
    end = document["to"]
    if data:
        expected_start = offset + 1
        if (
            _strict_int(start, minimum=1) != expected_start
            or _strict_int(end, minimum=1) != expected_start + len(data) - 1
            or expected_start + len(data) - 1 > total
        ):
            raise _failure("invalid pagination bounds")
    elif start is not None or end is not None:
        raise _failure("invalid empty pagination bounds")
    return current_page, per_page, current_page < last_page


async def employee_list_result_from_response(
    response: ResponseLike,
    expected: EmployeeListQuery,
    *,
    max_body_bytes: int = MAX_EMPLOYEE_LIST_RESPONSE_BYTES,
    expected_tenant_id: str | None = None,
) -> EmployeeListResult:
    """Return a privacy-typed projection of one UI-emitted, strictly validated response."""

    if (
        expected_tenant_id is not None
        and re.fullmatch(r"[1-9][0-9]{0,18}", expected_tenant_id) is None
    ):
        raise DicConfigurationError("expected employee tenant has an invalid format")
    await validate_employee_response_metadata(response, expected, max_body_bytes=max_body_bytes)
    body: object | None = None
    body_failed = False
    try:
        body = await response.body()
    except Exception:
        body_failed = True
    if body_failed:
        raise _failure("response body unavailable")
    if not isinstance(body, bytes):
        raise _failure("invalid response body")
    if len(body) > max_body_bytes:
        raise _failure("response exceeds the read limit")
    document: object | None = None
    document_failed = False
    try:
        document = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        document_failed = True
    if document_failed:
        raise _failure("invalid response document")
    if not isinstance(document, dict) or not _ROOT_KEYS.issubset(document):
        raise _failure("invalid response schema")

    current_page, per_page, has_next = _pagination(document, expected)
    _validate_paginator_path(document["path"])
    _page_from_url(document["first_page_url"], expected, expected_page=1)
    last_page = _strict_int(document["last_page"], minimum=1)
    _page_from_url(document["last_page_url"], expected, expected_page=last_page)
    next_page_url = document["next_page_url"]
    prev_page_url = document["prev_page_url"]
    if next_page_url is not None:
        _page_from_url(next_page_url, expected, expected_page=current_page + 1)
    if prev_page_url is not None:
        _page_from_url(prev_page_url, expected, expected_page=current_page - 1)
    if (document["next_page_url"] is None) != (not has_next):
        raise _failure("invalid next-page metadata")
    if (document["prev_page_url"] is None) != (current_page == 1):
        raise _failure("invalid previous-page metadata")
    _validate_links(
        document["links"],
        expected,
        current_page=current_page,
        last_page=last_page,
    )

    data = document["data"]
    if not isinstance(data, list):
        raise _failure("invalid employee collection")
    items = tuple(_employee_item(item, expected_tenant_id=expected_tenant_id) for item in data)
    identifiers = [item.employee_id for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise _failure("duplicate employee identifiers")
    projection: EmployeeListResult | None = None
    projection_failed = False
    try:
        projection = EmployeeListResult(
            items=items,
            page=current_page,
            page_size=per_page,
            total=_strict_int(document["total"]),
            has_next=has_next,
        )
    except ValidationError:
        projection_failed = True
    if projection_failed or projection is None:
        raise _failure("invalid employee-list projection")
    return projection


class EmployeeListResponseCapture:
    """Capture bounded employee responses emitted after a navigation/UI sequence marker."""

    def __init__(self, page: ResponseEventSource) -> None:
        self._page = page
        self._queue: asyncio.Queue[CapturedEmployeeResponse] = asyncio.Queue(
            maxsize=MAX_CAPTURED_EMPLOYEE_RESPONSES
        )
        self._sequence = 0
        self._overflow = False
        self._closed = False

    def _on_response(self, response: ResponseLike) -> None:
        if self._closed or not _is_candidate(response):
            return
        self._sequence += 1
        captured = CapturedEmployeeResponse(self._sequence, response)
        try:
            self._queue.put_nowait(captured)
        except asyncio.QueueFull:
            self._overflow = True

    def __enter__(self) -> EmployeeListResponseCapture:
        self._page.on("response", self._on_response)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._closed = True
        self._page.remove_listener("response", self._on_response)

    def mark(self) -> int:
        return self._sequence

    async def wait_for(
        self,
        expected: EmployeeListQuery,
        *,
        after_sequence: int,
        timeout_ms: float,
    ) -> ResponseLike:
        if timeout_ms <= 0:
            raise _failure("employee response timed out")
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1_000
        while True:
            if self._overflow:
                raise _failure("employee response capture overflowed")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _failure("employee response timed out")
            captured: CapturedEmployeeResponse | None = None
            timed_out = False
            try:
                captured = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                timed_out = True
            if timed_out or captured is None:
                raise _failure("employee response timed out")
            if captured.sequence <= after_sequence:
                continue
            try:
                await validate_employee_response_metadata(captured.response, expected)
            except _UiActionMismatch:
                continue
            return captured.response


__all__ = [
    "EMPLOYEE_LIST_ENDPOINT_ORIGIN",
    "EMPLOYEE_LIST_ENDPOINT_PATH",
    "EMPLOYEE_LIST_PAGE_SIZE",
    "EMPLOYEE_LIST_PAGINATOR_PATH",
    "MAX_EMPLOYEE_LIST_RESPONSE_BYTES",
    "EmployeeListResponseCapture",
    "employee_list_result_from_response",
    "validate_employee_response_metadata",
]
