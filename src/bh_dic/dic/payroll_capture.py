"""Fail-closed capture of the payroll response emitted by the fixed DIC UI route."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from pydantic import SecretStr, ValidationError

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.models import PayrollMetadata

PAYROLL_ENDPOINT_PATH = "/backend_apiV2/payrolls"
PAYROLL_ENDPOINT_ORIGIN = "https://secure.dipendentincloud.it"
MAX_PAYROLL_RESPONSE_BYTES = 512 * 1024
MAX_CAPTURED_PAYROLL_RESPONSES = 8

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
_PAYROLL_KEYS = frozenset(
    {
        "attachments",
        "balance",
        "balance_aligned",
        "create_provider",
        "date",
        "description",
        "employee",
        "id",
        "month",
        "net",
        "read",
        "read_at",
        "update_provider",
        "year",
    }
)
_ATTACHMENT_KEYS = frozenset({"created_at", "filename", "id", "updated_at", "url"})
_QUERY_KEYS = frozenset(
    {"search", "filter_type", "employee_id", "page", "per_page", "year", "search_fields"}
)
_AWS_QUERY_KEYS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
    }
)


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


@dataclass(frozen=True, slots=True)
class _Captured:
    sequence: int
    response: ResponseLike


class _Mismatch(DicUiChangedError):
    pass


def _failure(reason: str) -> DicUiChangedError:
    return DicUiChangedError(f"payroll response validation failed: {reason}")


def _is_candidate(response: ResponseLike) -> bool:
    try:
        return urlsplit(response.url).path == PAYROLL_ENDPOINT_PATH
    except Exception:
        return False


def _strict_int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _failure("invalid response schema")
    return value


def _money_cents(value: object) -> int:
    """Normalize the observed JSON euro amount without rounding or string coercion."""

    if type(value) is int:
        amount = Decimal(value)
    elif type(value) is float and math.isfinite(value):
        amount = Decimal(str(value))
    else:
        raise _failure("invalid response schema")
    cents = amount * 100
    if amount < 0 or amount > 10_000_000 or cents != cents.to_integral_value():
        raise _failure("invalid response schema")
    return int(cents)


def _bounded_text(value: object, *, maximum: int, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _failure("invalid response schema")
    return value.strip()


def _query(response: ResponseLike, employee_id: str, year: int) -> None:
    try:
        parsed = urlsplit(response.url)
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except (TypeError, ValueError):
        raise _failure("invalid endpoint metadata") from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != "secure.dipendentincloud.it"
        or parsed.path != PAYROLL_ENDPOINT_PATH
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
    if values["employee_id"] != employee_id or values["year"] != str(year):
        raise _Mismatch("payroll response does not match requested employee and year")
    if (
        values["page"] != "1"
        or values["per_page"] != "20"
        or values["filter_type"].casefold() != "and"
        or values["search"]
        or not values["search_fields"]
        or len(values["search_fields"]) > 128
    ):
        raise _failure("invalid query metadata")


async def _metadata(response: ResponseLike, employee_id: str, year: int) -> None:
    _query(response, employee_id, year)
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
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", content_length) is None:
            raise _failure("invalid response size metadata")
        if int(content_length) > MAX_PAYROLL_RESPONSE_BYTES:
            raise _failure("response exceeds read limit")


def _signed_pdf(value: object) -> SecretStr | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 8_192:
        raise _failure("invalid attachment URL")
    try:
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, strict_parsing=True, max_num_fields=12)
    except (TypeError, ValueError):
        raise _failure("invalid attachment URL") from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != "s3.eu-west-1.amazonaws.com"
        or not parsed.path.casefold().endswith(".pdf")
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise _failure("unexpected attachment URL")
    query = {key: item for key, item in pairs}
    if len(query) != len(pairs) or set(query) != _AWS_QUERY_KEYS:
        raise _failure("unexpected attachment URL metadata")
    if query["X-Amz-Algorithm"] != "AWS4-HMAC-SHA256":
        raise _failure("unexpected attachment signature metadata")
    return SecretStr(value)


def _payroll(item: object, employee_id: str, year: int) -> PayrollMetadata:
    if not isinstance(item, dict) or not _PAYROLL_KEYS.issubset(item):
        raise _failure("invalid payroll schema")
    employee = item["employee"]
    if (
        not isinstance(employee, dict)
        or not employee_id.isdigit()
        or employee.get("id") != int(employee_id)
    ):
        raise _failure("payroll employee does not match request")
    record_year = _strict_int(item["year"], minimum=2000)
    if record_year != year or record_year > 2200:
        raise _failure("payroll year does not match request")
    attachments = item["attachments"]
    if not isinstance(attachments, list) or len(attachments) > 8:
        raise _failure("invalid attachment schema")
    pdf_url: SecretStr | None = None
    pdf_filename: str | None = None
    for attachment in attachments:
        if not isinstance(attachment, dict) or not _ATTACHMENT_KEYS.issubset(attachment):
            raise _failure("invalid attachment schema")
        filename = _bounded_text(attachment["filename"], maximum=255)
        if filename is not None and filename.casefold().endswith(".pdf"):
            pdf_url = _signed_pdf(attachment["url"])
            pdf_filename = filename
            break
    emitted = _bounded_text(item["date"], maximum=32, optional=True)
    if emitted is not None:
        try:
            date.fromisoformat(emitted)
        except ValueError:
            raise _failure("invalid payroll date") from None
    read_state = item["read"]
    if type(read_state) is not bool:
        raise _failure("invalid payroll schema")
    try:
        return PayrollMetadata(
            payroll_id=str(_strict_int(item["id"], minimum=1)),
            employee_id=employee_id,
            year=record_year,
            month=_strict_int(item["month"], minimum=1),
            status="letta" if read_state else "non letta",
            published_at=emitted,
            # DIC emits JSON euros as either an integer or a two-decimal number.
            net_cents=_money_cents(item["net"]),
            attachment_filename=pdf_filename,
            attachment_url=pdf_url,
        )
    except ValidationError:
        raise _failure("invalid payroll projection") from None


async def payrolls_from_response(
    response: ResponseLike,
    *,
    employee_id: str,
    year: int,
) -> tuple[PayrollMetadata, ...]:
    await _metadata(response, employee_id, year)
    try:
        raw = await response.body()
    except Exception:
        raise _failure("response body unavailable") from None
    if len(raw) > MAX_PAYROLL_RESPONSE_BYTES:
        raise _failure("response exceeds read limit")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _failure("invalid response document") from None
    if not isinstance(document, dict) or not _ROOT_KEYS.issubset(document):
        raise _failure("invalid response schema")
    if document["current_page"] != 1 or document["per_page"] != 20:
        raise _failure("unexpected pagination metadata")
    data = document["data"]
    if not isinstance(data, list) or len(data) > 20:
        raise _failure("invalid payroll collection")
    records = tuple(_payroll(item, employee_id, year) for item in data)
    ids = [record.payroll_id for record in records]
    if len(ids) != len(set(ids)):
        raise _failure("duplicate payroll identifiers")
    return records


class PayrollResponseCapture:
    def __init__(self, page: ResponseEventSource) -> None:
        self._page = page
        self._queue: asyncio.Queue[_Captured] = asyncio.Queue(MAX_CAPTURED_PAYROLL_RESPONSES)
        self._sequence = 0
        self._overflow = False
        self._closed = False

    def _on_response(self, response: ResponseLike) -> None:
        if self._closed or not _is_candidate(response):
            return
        self._sequence += 1
        try:
            self._queue.put_nowait(_Captured(self._sequence, response))
        except asyncio.QueueFull:
            self._overflow = True

    def __enter__(self) -> PayrollResponseCapture:
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
                await _metadata(captured.response, employee_id, year)
            except _Mismatch:
                continue
            return captured.response


__all__ = ["PayrollResponseCapture", "payrolls_from_response"]
