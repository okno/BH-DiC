"""Bounded first-party JSON capture and complete pagination for DIC resources."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from bh_dic.dic.errors import DicUiChangedError

DIC_ENDPOINT_ORIGIN = "https://secure.dipendentincloud.it"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CAPTURED_RESPONSES = 16
MAX_PAGES = 500
MAX_RECORDS = 5_000

_FETCH_JSON_PAGE = """
async url => {
  const response = await fetch(url, {
    method: "GET",
    credentials: "same-origin",
    headers: {Accept: "application/json"},
    redirect: "error"
  });
  const contentType = response.headers.get("content-type") || "";
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null && Number(contentLength) > 2097152) {
    return {url: response.url, status: response.status, contentType, oversized: true};
  }
  const body = await response.text();
  return {
    url: response.url,
    status: response.status,
    contentType,
    oversized: body.length > 2097152,
    body: body.length > 2097152 ? null : body
  };
}
"""

_FETCH_JSON_PAGE_WITH_SESSION_HEADERS = """
async ({url, authorization, deviceId}) => {
  const response = await fetch(url, {
    method: "GET",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      Authorization: authorization,
      "X-Device-Id": deviceId
    },
    redirect: "error"
  });
  const contentType = response.headers.get("content-type") || "";
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null && Number(contentLength) > 2097152) {
    return {url: response.url, status: response.status, contentType, oversized: true};
  }
  const body = await response.text();
  return {
    url: response.url,
    status: response.status,
    contentType,
    oversized: body.length > 2097152,
    body: body.length > 2097152 ? null : body
  };
}
"""


class RequestLike(Protocol):
    @property
    def method(self) -> str: ...

    async def header_value(self, name: str) -> str | None: ...


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


class PageEvaluator(Protocol):
    async def evaluate(self, expression: str, argument: object = None) -> object: ...


@dataclass(frozen=True, slots=True)
class PaginatedEndpointContract:
    resource: str
    endpoint_path: str
    allowed_query_keys: frozenset[str]
    employee_query_key: str | None
    required_item_keys: frozenset[str]
    paginator_path: str | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.]{1,79}", self.resource) is None:
            raise ValueError("invalid paginated resource name")
        if not self.endpoint_path.startswith("/backend_apiV2/"):
            raise ValueError("invalid paginated endpoint path")
        if (
            self.employee_query_key is not None
            and self.employee_query_key not in self.allowed_query_keys
        ):
            raise ValueError("employee query key must be allowed")
        if self.paginator_path is not None and (
            not self.paginator_path.startswith("/")
            or self.paginator_path.startswith("//")
            or any(character in self.paginator_path for character in ("?", "#", "\\"))
            or len(self.paginator_path) > 256
        ):
            raise ValueError("invalid paginator endpoint path")
        if not self.required_item_keys:
            raise ValueError("paginated resource requires item keys")


@dataclass(frozen=True, slots=True)
class PaginatedJsonPage:
    items: tuple[dict[str, object], ...] = field(repr=False)
    current_page: int
    last_page: int
    per_page: int
    total: int
    request_url: str = field(repr=False)
    next_page_url: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _Captured:
    sequence: int
    response: ResponseLike


class _ResponseMismatch(DicUiChangedError):
    pass


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def strict_json_loads(raw: bytes | str) -> object:
    """Decode first-party JSON without accepting duplicate keys or non-finite numbers."""

    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    return json.loads(
        text,
        parse_constant=_reject_non_finite,
        object_pairs_hook=_unique_object,
    )


def _failure(resource: str, reason: str) -> DicUiChangedError:
    return DicUiChangedError(f"{resource} response validation failed: {reason}")


def _strict_int(value: object, *, resource: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _failure(resource, "invalid pagination schema")
    return value


def _employee_query_matches(raw: str, employee_id: str) -> bool:
    if raw == employee_id:
        return True
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if isinstance(decoded, list) and decoded == [employee_id]:
        return True
    if isinstance(decoded, list) and employee_id.isdigit() and decoded == [int(employee_id)]:
        return True
    return False


def _validate_url(
    url: str,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
    *,
    expected_page: int | None = None,
    paginator: bool = False,
) -> dict[str, str]:
    try:
        parsed = urlsplit(url)
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
        )
    except (TypeError, ValueError):
        raise _failure(contract.resource, "invalid endpoint metadata") from None
    expected_path = (
        contract.paginator_path
        if paginator and contract.paginator_path is not None
        else contract.endpoint_path
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "secure.dipendentincloud.it"
        or parsed.path != expected_path
        or parsed.fragment
    ):
        raise _failure(contract.resource, "unexpected endpoint metadata")
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise _failure(contract.resource, "duplicate query metadata")
        values[key] = value
    if not set(values).issubset(contract.allowed_query_keys):
        raise _failure(contract.resource, "unexpected query metadata")
    if contract.employee_query_key is not None:
        if employee_id is None:
            raise _failure(contract.resource, "employee metadata is required")
        employee_value = values.get(contract.employee_query_key)
        if employee_value is None or not _employee_query_matches(employee_value, employee_id):
            raise _ResponseMismatch("response does not match requested employee")
    elif employee_id is not None:
        raise _failure(contract.resource, "unexpected employee metadata")
    if expected_page is not None:
        page_value = values.get("page")
        if page_value is None or page_value != str(expected_page):
            raise _failure(contract.resource, "unexpected page metadata")
    return values


async def _validate_response_metadata(
    response: ResponseLike,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
) -> None:
    _validate_url(response.url, contract, employee_id)
    try:
        content_type = await response.header_value("content-type")
        content_length = await response.header_value("content-length")
        method = response.request.method
        status = response.status
    except Exception:
        raise _failure(contract.resource, "response metadata unavailable") from None
    if method != "GET" or status != 200:
        raise _failure(contract.resource, "unexpected response metadata")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().casefold() != "application/json"
    ):
        raise _failure(contract.resource, "unexpected response media type")
    if content_length is not None:
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", content_length) is None:
            raise _failure(contract.resource, "invalid response size metadata")
        if int(content_length) > MAX_RESPONSE_BYTES:
            raise _failure(contract.resource, "response exceeds read limit")


def _page_from_document(
    document: object,
    *,
    request_url: str,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
) -> PaginatedJsonPage:
    required_root = {
        "current_page",
        "data",
        "last_page",
        "next_page_url",
        "per_page",
        "total",
    }
    if not isinstance(document, dict) or not required_root.issubset(document):
        raise _failure(contract.resource, "invalid response schema")
    current_page = _strict_int(document["current_page"], resource=contract.resource, minimum=1)
    last_page = _strict_int(document["last_page"], resource=contract.resource, minimum=1)
    per_page = _strict_int(document["per_page"], resource=contract.resource, minimum=1)
    total = _strict_int(document["total"], resource=contract.resource)
    if current_page > last_page or last_page > MAX_PAGES or per_page > 100 or total > MAX_RECORDS:
        raise _failure(contract.resource, "pagination bounds exceeded")
    data = document["data"]
    if not isinstance(data, list) or len(data) > per_page:
        raise _failure(contract.resource, "invalid collection schema")
    items: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict) or not contract.required_item_keys.issubset(item):
            raise _failure(contract.resource, "invalid item schema")
        items.append(item)
    raw_next = document["next_page_url"]
    if raw_next is not None and (not isinstance(raw_next, str) or len(raw_next) > 2_048):
        raise _failure(contract.resource, "invalid next-page metadata")
    if current_page < last_page and raw_next is None:
        raise _failure(contract.resource, "missing next page")
    if current_page == last_page and raw_next is not None:
        raise _failure(contract.resource, "unexpected next page")
    if raw_next is not None:
        _validate_url(
            raw_next,
            contract,
            employee_id,
            expected_page=current_page + 1,
            paginator=True,
        )
    return PaginatedJsonPage(
        items=tuple(items),
        current_page=current_page,
        last_page=last_page,
        per_page=per_page,
        total=total,
        request_url=request_url,
        next_page_url=raw_next,
    )


async def page_from_response(
    response: ResponseLike,
    *,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
) -> PaginatedJsonPage:
    await _validate_response_metadata(response, contract, employee_id)
    try:
        body = await response.body()
    except Exception:
        raise _failure(contract.resource, "response body unavailable") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise _failure(contract.resource, "response exceeds read limit")
    try:
        document = strict_json_loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _failure(contract.resource, "invalid response document") from None
    return _page_from_document(
        document,
        request_url=response.url,
        contract=contract,
        employee_id=employee_id,
    )


def _page_from_fetch(
    raw: object,
    *,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
    expected_page: int,
    paginator: bool = True,
) -> PaginatedJsonPage:
    if not isinstance(raw, Mapping):
        raise _failure(contract.resource, "next-page response unavailable")
    url = raw.get("url")
    status = raw.get("status")
    content_type = raw.get("contentType")
    body = raw.get("body")
    if raw.get("oversized") is True:
        raise _failure(contract.resource, "response exceeds read limit")
    if (
        not isinstance(url, str)
        or status != 200
        or not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().casefold() != "application/json"
        or not isinstance(body, str)
        or len(body.encode("utf-8")) > MAX_RESPONSE_BYTES
    ):
        raise _failure(contract.resource, "unexpected next-page response")
    _validate_url(
        url,
        contract,
        employee_id,
        expected_page=expected_page,
        paginator=paginator,
    )
    try:
        document = strict_json_loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _failure(contract.resource, "invalid response document") from None
    page = _page_from_document(
        document,
        request_url=url,
        contract=contract,
        employee_id=employee_id,
    )
    if page.current_page != expected_page:
        raise _failure(contract.resource, "pagination did not advance")
    return page


async def fetch_paginated_page(
    page: PageEvaluator,
    url: str,
    *,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
    expected_page: int,
    paginator: bool = True,
    request_headers: Mapping[str, str] | None = None,
) -> PaginatedJsonPage:
    """Fetch one exact first-party page and validate it before projection."""

    _validate_url(
        url,
        contract,
        employee_id,
        expected_page=expected_page,
        paginator=paginator,
    )
    expression = _FETCH_JSON_PAGE
    argument: object = url
    if request_headers is not None:
        if set(request_headers) != {"authorization", "x-device-id"}:
            raise _failure(contract.resource, "invalid session header capability")
        authorization = request_headers["authorization"]
        device_id = request_headers["x-device-id"]
        if (
            not authorization
            or len(authorization) > 8_192
            or not device_id
            or len(device_id) > 512
            or any(character in authorization or character in device_id for character in "\r\n\0")
        ):
            raise _failure(contract.resource, "invalid session header capability")
        expression = _FETCH_JSON_PAGE_WITH_SESSION_HEADERS
        argument = {
            "url": url,
            "authorization": authorization,
            "deviceId": device_id,
        }
    try:
        raw = await page.evaluate(expression, argument)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise _failure(contract.resource, "next-page request failed") from None
    return _page_from_fetch(
        raw,
        contract=contract,
        employee_id=employee_id,
        expected_page=expected_page,
        paginator=paginator,
    )


async def collect_complete_pages(
    page: PageEvaluator,
    first: PaginatedJsonPage,
    *,
    contract: PaginatedEndpointContract,
    employee_id: str | None,
    request_headers: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    pages = [first]
    base_query = _validate_url(
        first.request_url,
        contract,
        employee_id,
        expected_page=first.current_page,
    )
    base_query.pop("page", None)
    while pages[-1].current_page < pages[-1].last_page:
        current = pages[-1]
        if current.next_page_url is None:
            raise _failure(contract.resource, "missing next page")
        next_query = _validate_url(
            current.next_page_url,
            contract,
            employee_id,
            expected_page=current.current_page + 1,
            paginator=True,
        )
        next_query.pop("page", None)
        if next_query != base_query:
            raise _failure(contract.resource, "pagination query changed")
        next_parts = urlsplit(current.next_page_url)
        canonical_next_url = f"{DIC_ENDPOINT_ORIGIN}{contract.endpoint_path}?{next_parts.query}"
        following = await fetch_paginated_page(
            page,
            canonical_next_url,
            contract=contract,
            employee_id=employee_id,
            expected_page=current.current_page + 1,
            paginator=False,
            request_headers=request_headers,
        )
        if following.total != first.total or following.last_page != first.last_page:
            raise _failure(contract.resource, "pagination metadata changed")
        pages.append(following)
    items = tuple(item for current in pages for item in current.items)
    if len(items) != first.total:
        raise _failure(contract.resource, "incomplete pagination")
    return items


class PaginatedResponseCapture:
    def __init__(self, page: ResponseEventSource, contract: PaginatedEndpointContract) -> None:
        self._page = page
        self._contract = contract
        self._queue: asyncio.Queue[_Captured] = asyncio.Queue(MAX_CAPTURED_RESPONSES)
        self._sequence = 0
        self._overflow = False
        self._closed = False

    def _on_response(self, response: ResponseLike) -> None:
        if self._closed:
            return
        try:
            candidate = urlsplit(response.url).path == self._contract.endpoint_path
        except Exception:
            candidate = False
        if not candidate:
            return
        self._sequence += 1
        try:
            self._queue.put_nowait(_Captured(self._sequence, response))
        except asyncio.QueueFull:
            self._overflow = True

    def __enter__(self) -> PaginatedResponseCapture:
        self._page.on("response", self._on_response)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._closed = True
        self._page.remove_listener("response", self._on_response)

    def mark(self) -> int:
        return self._sequence

    async def wait_for(
        self,
        employee_id: str | None,
        *,
        after_sequence: int,
        timeout_ms: float,
    ) -> ResponseLike:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1_000
        while True:
            if self._overflow:
                raise _failure(self._contract.resource, "response capture overflowed")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _failure(self._contract.resource, "response timed out")
            try:
                captured = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                raise _failure(self._contract.resource, "response timed out") from None
            if captured.sequence <= after_sequence:
                continue
            try:
                await _validate_response_metadata(captured.response, self._contract, employee_id)
            except _ResponseMismatch:
                continue
            return captured.response


__all__ = [
    "PaginatedEndpointContract",
    "PaginatedJsonPage",
    "PaginatedResponseCapture",
    "collect_complete_pages",
    "fetch_paginated_page",
    "page_from_response",
    "strict_json_loads",
]
