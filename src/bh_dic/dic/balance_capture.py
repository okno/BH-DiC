"""Strict event-driven capture for the employee balance empty/non-empty state."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.models import BalanceLine
from bh_dic.dic.paginated_capture import (
    DIC_ENDPOINT_ORIGIN,
    MAX_CAPTURED_RESPONSES,
    MAX_RESPONSE_BYTES,
    ResponseEventSource,
    ResponseLike,
    strict_json_loads,
)
from bh_dic.dic.values import canonical_decimal_text

BALANCE_ENDPOINT_PATH = "/backend_apiV2/attendance/balance"
COUNTERS_ENDPOINT_PATH = "/backend_apiV2/counters"
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
        value = raw
    if isinstance(value, str):
        parts = value.split("|")
        return (
            1 <= len(parts) <= 12
            and all(part.isdigit() and 1 <= int(part) <= 12 for part in parts)
            and len(set(parts)) == len(parts)
        )
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


async def _read_document(response: ResponseLike, *, resource: str) -> object:
    try:
        body = await response.body()
    except Exception:
        raise _failure(f"{resource} response body unavailable") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise _failure(f"{resource} response exceeds read limit")
    try:
        return strict_json_loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _failure(f"invalid {resource} response document") from None


async def _validate_counter_metadata(response: ResponseLike) -> None:
    try:
        parsed = urlsplit(response.url)
        content_type = await response.header_value("content-type")
        content_length = await response.header_value("content-length")
        method = response.request.method
        status = response.status
    except Exception:
        raise _failure("counter response metadata unavailable") from None
    if (
        f"{parsed.scheme}://{parsed.netloc}" != DIC_ENDPOINT_ORIGIN
        or parsed.path != COUNTERS_ENDPOINT_PATH
        or parsed.query
        or parsed.fragment
        or method != "GET"
        or status != 200
    ):
        raise _failure("unexpected counter response metadata")
    if (
        not isinstance(content_type, str)
        or content_type.split(";", 1)[0].strip().casefold() != "application/json"
    ):
        raise _failure("unexpected counter response media type")
    if content_length is not None and (
        not content_length.isdigit() or int(content_length) > MAX_RESPONSE_BYTES
    ):
        raise _failure("invalid counter response size metadata")


async def counters_from_response(response: ResponseLike) -> dict[int, str]:
    await _validate_counter_metadata(response)
    document = await _read_document(response, resource="counter")
    if not isinstance(document, dict) or set(document) != {"data"}:
        raise _failure("invalid counter response schema")
    data = document["data"]
    if not isinstance(data, list) or len(data) > 100:
        raise _failure("invalid counter collection")
    counters: dict[int, str] = {}
    required = {"id", "name", "active", "auto_maturation"}
    for item in data:
        if not isinstance(item, dict) or not required.issubset(item):
            raise _failure("invalid counter item schema")
        identifier = item["id"]
        name = item["name"]
        if type(identifier) is not int or identifier < 1 or identifier in counters:
            raise _failure("invalid counter identifier")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise _failure("invalid counter name")
        if not _binary_state(item["active"]) or not _binary_state(item["auto_maturation"]):
            raise _failure("invalid counter state")
        counters[identifier] = name.strip()
    return counters


def _decimal(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _failure("invalid balance numeric field")
    if isinstance(value, float) and not math.isfinite(value):
        raise _failure("invalid balance numeric field")
    try:
        return canonical_decimal_text(str(value))
    except ValueError:
        raise _failure("invalid balance numeric field") from None


def _binary_state(value: object) -> bool:
    return type(value) in {int, bool} and value in {0, 1}


async def balance_lines_from_response(
    response: ResponseLike,
    *,
    employee_id: str,
    year: int,
    counters: dict[int, str],
) -> tuple[BalanceLine, ...]:
    """Project the observed employee/year/month/counter balance contract."""

    await _validate_metadata(response, employee_id, year)
    document = await _read_document(response, resource="balance")
    if not isinstance(document, dict) or set(document) != {"data"}:
        raise _failure("invalid response schema")
    data = document["data"]
    if data == []:
        return ()
    if not isinstance(data, dict) or set(data) != {employee_id}:
        raise _failure("invalid balance collection")
    employee_data = data[employee_id]
    if not isinstance(employee_data, dict) or set(employee_data) != {str(year)}:
        raise _failure("invalid balance year collection")
    months = employee_data[str(year)]
    if not isinstance(months, dict) or len(months) > 12:
        raise _failure("invalid balance month collection")
    lines: list[BalanceLine] = []
    required = {
        "balance",
        "correction",
        "counter_id",
        "maturation",
        "projection",
        "residue",
        "utilization",
    }
    seen: set[tuple[int, int]] = set()
    for raw_month, raw_items in sorted(
        months.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else 99
    ):
        if not isinstance(raw_month, str) or not raw_month.isdigit():
            raise _failure("invalid balance month")
        month = int(raw_month)
        if not 1 <= month <= 12 or not isinstance(raw_items, list) or len(raw_items) > 100:
            raise _failure("invalid balance month")
        for item in raw_items:
            if not isinstance(item, dict) or not required.issubset(item):
                raise _failure("invalid balance item schema")
            counter_id = item["counter_id"]
            if type(counter_id) is not int or counter_id not in counters:
                raise _failure("unknown balance counter")
            identity = (month, counter_id)
            if identity in seen:
                raise _failure("duplicate balance item")
            seen.add(identity)
            lines.append(
                BalanceLine(
                    category=counters[counter_id],
                    counter_id=str(counter_id),
                    month=month,
                    balance=_decimal(item["balance"]),
                    maturation=_decimal(item["maturation"]),
                    utilization=_decimal(item["utilization"]),
                    projection=_decimal(item["projection"]),
                    accrued=_decimal(item["maturation"]),
                    used=_decimal(item["utilization"]),
                    corrections=_decimal(item["correction"]),
                    current_residual=_decimal(item["residue"]),
                )
            )
            if len(lines) > 1_200:
                raise _failure("balance collection exceeds read limit")
    return tuple(lines)


class _ResponseMismatch(DicUiChangedError):
    pass


class BalanceResponseCapture:
    def __init__(self, page: ResponseEventSource) -> None:
        self._page = page
        self._queue: asyncio.Queue[_Captured] = asyncio.Queue(MAX_CAPTURED_RESPONSES)
        self._counter_queue: asyncio.Queue[_Captured] = asyncio.Queue(MAX_CAPTURED_RESPONSES)
        self._sequence = 0
        self._counter_sequence = 0
        self._overflow = False
        self._closed = False

    def _on_response(self, response: ResponseLike) -> None:
        if self._closed:
            return
        try:
            path = urlsplit(response.url).path
        except Exception:
            path = ""
        if path not in {BALANCE_ENDPOINT_PATH, COUNTERS_ENDPOINT_PATH}:
            return
        if path == COUNTERS_ENDPOINT_PATH:
            self._counter_sequence += 1
            try:
                self._counter_queue.put_nowait(_Captured(self._counter_sequence, response))
            except asyncio.QueueFull:
                self._overflow = True
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

    def counter_mark(self) -> int:
        return self._counter_sequence

    async def wait_for_counters(self, *, after_sequence: int, timeout_ms: float) -> ResponseLike:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1_000
        while True:
            if self._overflow:
                raise _failure("response capture overflowed")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _failure("counter response timed out")
            try:
                captured = await asyncio.wait_for(self._counter_queue.get(), timeout=remaining)
            except TimeoutError:
                raise _failure("counter response timed out") from None
            if captured.sequence <= after_sequence:
                continue
            await _validate_counter_metadata(captured.response)
            return captured.response

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


__all__ = [
    "BalanceResponseCapture",
    "balance_lines_from_response",
    "counters_from_response",
]
