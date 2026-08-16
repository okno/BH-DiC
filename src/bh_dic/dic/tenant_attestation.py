"""Fail-closed attestation of the current Dipendenti in Cloud company."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Protocol, cast
from urllib.parse import urlsplit

from bh_dic.dic.errors import DicAuthorizationError

TENANT_ENDPOINT_ORIGIN = "https://secure.dipendentincloud.it"
TENANT_ENDPOINT_PATH = "/backend_apiV2/company/info"
MAX_TENANT_RESPONSE_BYTES = 64 * 1024


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


class DuplicateJsonKeyError(ValueError):
    """Internal parse sentinel; it never includes response data in its message."""


class InvalidJsonConstantError(ValueError):
    """Internal parse sentinel for non-RFC JSON numeric constants."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise InvalidJsonConstantError("non-standard JSON constant")


def _is_company_info_candidate(response: ResponseLike) -> bool:
    try:
        return urlsplit(response.url).path == TENANT_ENDPOINT_PATH
    except (TypeError, ValueError):
        return False


def _authorization_failure(reason: str) -> DicAuthorizationError:
    # Keep this error deliberately free of URLs, tenant values, and response bodies.
    return DicAuthorizationError(f"authenticated DIC tenant attestation failed: {reason}")


async def attest_tenant_response(
    response: ResponseLike,
    expected_tenant_id: str,
    *,
    max_body_bytes: int = MAX_TENANT_RESPONSE_BYTES,
) -> None:
    """Validate one first-party company-info response without returning its contents."""

    try:
        parsed = urlsplit(response.url)
        exact_origin = (
            parsed.scheme == "https"
            and parsed.netloc == "secure.dipendentincloud.it"
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
        )
    except (TypeError, ValueError):
        raise _authorization_failure("invalid endpoint metadata") from None
    if not exact_origin or parsed.path != TENANT_ENDPOINT_PATH or parsed.query or parsed.fragment:
        raise _authorization_failure("unexpected endpoint metadata")
    try:
        method = response.request.method
        status = response.status
    except Exception:
        raise _authorization_failure("response metadata unavailable") from None
    if method != "GET" or status != 200:
        raise _authorization_failure("unexpected response metadata")

    try:
        content_type = await response.header_value("content-type")
        content_length = await response.header_value("content-length")
    except Exception:
        raise _authorization_failure("response metadata unavailable") from None
    if (
        content_type is None
        or content_type.split(";", 1)[0].strip().casefold() != "application/json"
    ):
        raise _authorization_failure("unexpected response media type")
    if content_length is not None:
        try:
            declared_length = int(content_length, 10)
        except ValueError:
            raise _authorization_failure("invalid response size metadata") from None
        if declared_length < 0 or declared_length > max_body_bytes:
            raise _authorization_failure("response exceeds the attestation limit")

    try:
        body = await response.body()
    except Exception:
        raise _authorization_failure("response body unavailable") from None
    if len(body) > max_body_bytes:
        raise _authorization_failure("response exceeds the attestation limit")
    try:
        document = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        DuplicateJsonKeyError,
        InvalidJsonConstantError,
    ):
        raise _authorization_failure("invalid response document") from None

    try:
        root = cast(dict[str, object], document)
        data = root["data"]
        if not isinstance(root, dict) or not isinstance(data, dict):
            raise TypeError
        company = data["company"]
        if not isinstance(company, dict):
            raise TypeError
        observed_id = company["id"]
    except (KeyError, TypeError):
        raise _authorization_failure("required tenant field unavailable") from None
    if type(observed_id) is not int or observed_id <= 0:
        raise _authorization_failure("tenant field has an invalid type")
    if str(observed_id) != expected_tenant_id:
        raise _authorization_failure("tenant does not match configuration")


class TenantResponseCapture:
    """Capture only a company-info response emitted during one fixed navigation."""

    def __init__(self, page: ResponseEventSource) -> None:
        self._page = page
        self._queue: asyncio.Queue[ResponseLike] = asyncio.Queue(maxsize=1)
        self._closed = False

    def _on_response(self, response: ResponseLike) -> None:
        if not self._closed and self._queue.empty() and _is_company_info_candidate(response):
            self._queue.put_nowait(response)

    def __enter__(self) -> TenantResponseCapture:
        self._page.on("response", self._on_response)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._closed = True
        self._page.remove_listener("response", self._on_response)

    async def attest(self, expected_tenant_id: str, *, timeout_ms: float) -> None:
        try:
            response = await asyncio.wait_for(self._queue.get(), timeout=timeout_ms / 1000)
        except TimeoutError:
            raise _authorization_failure("company response timed out") from None
        await attest_tenant_response(response, expected_tenant_id)
