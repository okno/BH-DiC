"""Strict event-driven capture for the employee balance empty/non-empty state."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.paginated_capture import (
    DIC_ENDPOINT_ORIGIN,
    MAX_CAPTURED_RESPONSES,
    MAX_RESPONSE_BYTES,
    ResponseEventSource,
    ResponseLike,
    strict_json_loads,
)

BALANCE_ENDPOINT_PATH = "/backend_apiV2/attendance/balance"
_QUERY_KEYS = frozenset({"employees_ids", "include_pending", "months", "years"})


@dataclass(frozen=True, slots=True)
class _Captured:
    sequence: int
    response: ResponseLike


def _failure(reason: str) -> DicUiChangedError:
    return DicUiChangedError(f"employees.balances response validation failed: {reason}")


def _single_identifier(raw: str, employee_id: str) -> bool:
    try:
        value: object = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        value = raw
    expected: object = int(employee_id) if employee_id.isdigit() else employee_id
    return (
        value == expected or value == [expected] or value == employee_id or value == [employee_id]
    )


def _contains_year(raw: str, year: int) -> bool:
    try:
        value: object = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        value = raw
    return value == year or value == str(year) or value == [year] or value == [str(year)]


def _valid_months(raw: str) -> bool:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(value, int) and not isinstance(value, bool):
        return 1 <= value <= 12
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 12
        and all(type(item) is int and 1 <= item <= 12 for item in value)
        and len(set(value)) == len(value)
    )


async def _validate_metadata(response: ResponseLike, employee_id: str, year: int) -> None:
    try:
        parsed = urlsplit(response.url)
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (TypeError, ValueError):
        raise _failure("invalid endpoint metadata") from None
    if (
        f"{parsed.scheme}://{parsed.netloc}" != DIC_ENDPOINT_ORIGIN
        or parsed.path != BALANCE_ENDPOINT_PATH
        or parsed.fragment
    ):
        raise _failure("unexpected endpoint metadata")
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise _failure("duplicate query metadata")
        values[key] = value
    if set(values) != _QUERY_KEYS:
        raise _failure("unexpected query metadata")
    if not _single_identifier(values["employees_ids"], employee_id):
        raise _ResponseMismatch("response does not match requested employee")
    if not _contains_year(values["years"], year):
        raise _ResponseMismatch("response does not match requested year")
    if not _valid_months(values["months"]):
        raise _failure("invalid month metadata")
    if values["include_pending"].casefold() not in {"0", "1", "false", "true"}:
        raise _failure("invalid pending-state metadata")
    try:
        content_type = await response.header_value("content-type")
        content_length = await response.header_value("content-length")
        method = response.request.method
        status = response.status
    except Exception:
        raise _failure("response metadata unavailable") from None
    if method != "GET" or status != 200:
        raise _failure("unexpected response metadata")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().casefold() != "application/json"
    ):
        raise _failure("unexpected response media type")
    if content_length is not None:
        if not content_length.isdigit() or int(content_length) > MAX_RESPONSE_BYTES:
            raise _failure("invalid response size metadata")


async def empty_balance_from_response(
    response: ResponseLike, *, employee_id: str, year: int
) -> bool:
    """Return true only for the observed, schema-valid empty balance state."""

    await _validate_metadata(response, employee_id, year)
    try:
        body = await response.body()
    except Exception:
        raise _failure("response body unavailable") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise _failure("response exceeds read limit")
    try:
        document = strict_json_loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _failure("invalid response document") from None
    if not isinstance(document, dict) or set(document) != {"data"}:
        raise _failure("invalid response schema")
    data = document["data"]
    if not isinstance(data, list):
        raise _failure("invalid balance collection")
    if data:
        raise _failure("non-empty balance schema requires explicit classification")
    return True


class _ResponseMismatch(DicUiChangedError):
    pass


class BalanceResponseCapture:
    def __init__(self, page: ResponseEventSource) -> None:
        self._page = page
        self._queue: asyncio.Queue[_Captured] = asyncio.Queue(MAX_CAPTURED_RESPONSES)
        self._sequence = 0
        self._overflow = False
        self._closed = False

    def _on_response(self, response: ResponseLike) -> None:
        if self._closed:
            return
        try:
            candidate = urlsplit(response.url).path == BALANCE_ENDPOINT_PATH
        except Exception:
            candidate = False
        if not candidate:
            return
        self._sequence += 1
        try:
            self._queue.put_nowait(_Captured(self._sequence, response))
        except asyncio.QueueFull:
            self._overflow = True

    def __enter__(self) -> BalanceResponseCapture:
        self._page.on("response", self._on_response)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._closed = True
        self._page.remove_listener("response", self._on_response)

    def mark(self) -> int:
        return self._sequence

    async def wait_for(
        self,
        employee_id: str,
        year: int,
        *,
        after_sequence: int,
        timeout_ms: float,
    ) -> ResponseLike:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1_000
        while True:
            if self._overflow:
                raise _failure("response capture overflowed")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _failure("response timed out")
            try:
                captured = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except TimeoutError:
                raise _failure("response timed out") from None
            if captured.sequence <= after_sequence:
                continue
            try:
                await _validate_metadata(captured.response, employee_id, year)
            except _ResponseMismatch:
                continue
            return captured.response


__all__ = ["BalanceResponseCapture", "empty_balance_from_response"]
