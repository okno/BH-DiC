from __future__ import annotations

import logging
import sys
from collections.abc import Callable

import pytest

from bh_dic.dic.errors import DicAuthorizationError
from bh_dic.dic.tenant_attestation import (
    MAX_TENANT_RESPONSE_BYTES,
    TenantResponseCapture,
    attest_tenant_response,
)
from bh_dic.logging import JsonFormatter

EXPECTED_TENANT = "123456789"
ENDPOINT = "https://secure.dipendentincloud.it/backend_apiV2/company/info"


class Request:
    def __init__(self, method: str = "GET") -> None:
        self.method = method


class Response:
    def __init__(
        self,
        body: bytes = b'{"data":{"company":{"id":123456789}}}',
        *,
        url: str = ENDPOINT,
        method: str = "GET",
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        content_length: str | None = None,
    ) -> None:
        self.url = url
        self.request = Request(method)
        self.status = status
        self._body = body
        self._content_type = content_type
        self._content_length = content_length
        self.body_reads = 0

    async def header_value(self, name: str) -> str | None:
        if name.casefold() == "content-type":
            return self._content_type
        if name.casefold() == "content-length":
            return self._content_length
        return None

    async def body(self) -> bytes:
        self.body_reads += 1
        return self._body


@pytest.mark.asyncio
async def test_attestation_reads_only_direct_company_id() -> None:
    response = Response(
        b'{"data":{"company":{"id":123456789,"display":"synthetic"}},"other":{"id":999999999}}'
    )

    await attest_tenant_response(response, EXPECTED_TENANT)

    assert response.body_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"url": "https://example.invalid/backend_apiV2/company/info"}, "endpoint"),
        ({"url": f"{ENDPOINT}?company=123456789"}, "endpoint"),
        ({"method": "POST"}, "response metadata"),
        ({"status": 204}, "response metadata"),
        ({"content_type": "text/json"}, "media type"),
    ],
)
async def test_attestation_rejects_wrong_response_metadata(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(DicAuthorizationError, match=message):
        await attest_tenant_response(Response(**overrides), EXPECTED_TENANT)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"company":{"id":123456789}}',
        b'{"data":{"companyId":123456789}}',
        b'{"data":{"company":{"id":"123456789"}}}',
        b'{"data":{"company":{"id":true}}}',
        b'{"data":{"company":{"id":0}}}',
        b'{"data":{"company":{"id":-1}}}',
        b'{"data":{"company":[{"id":123456789}]}}',
    ],
)
async def test_attestation_rejects_missing_or_wrongly_typed_direct_field(body: bytes) -> None:
    with pytest.raises(DicAuthorizationError):
        await attest_tenant_response(Response(body), EXPECTED_TENANT)


@pytest.mark.asyncio
async def test_attestation_rejects_tenant_mismatch_without_disclosing_value() -> None:
    secret_observed = "987654321"

    with pytest.raises(DicAuthorizationError) as caught:
        await attest_tenant_response(
            Response(b'{"data":{"company":{"id":' + secret_observed.encode() + b"}}}"),
            EXPECTED_TENANT,
        )

    assert "does not match" in str(caught.value)
    assert secret_observed not in str(caught.value)


@pytest.mark.asyncio
async def test_attestation_rejects_duplicate_keys_without_disclosing_body() -> None:
    sensitive_marker = "private-marker"
    response = Response(
        b'{"data":{"company":{"id":123456789,"id":123456789},'
        + f'"note":"{sensitive_marker}"'.encode()
        + b"}}"
    )

    with pytest.raises(DicAuthorizationError) as caught:
        await attest_tenant_response(response, EXPECTED_TENANT)

    assert "invalid response document" in str(caught.value)
    assert sensitive_marker not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
async def test_attestation_rejects_non_rfc_json_constants_anywhere(constant: bytes) -> None:
    response = Response(b'{"data":{"company":{"id":123456789}},"syntheticExtra":' + constant + b"}")

    with pytest.raises(DicAuthorizationError, match="invalid response document"):
        await attest_tenant_response(response, EXPECTED_TENANT)


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_body_read() -> None:
    response = Response(content_length=str(MAX_TENANT_RESPONSE_BYTES + 1))

    with pytest.raises(DicAuthorizationError, match="exceeds"):
        await attest_tenant_response(response, EXPECTED_TENANT)

    assert response.body_reads == 0


@pytest.mark.asyncio
async def test_actual_oversize_is_rejected_when_length_is_absent() -> None:
    response = Response(b" " * (MAX_TENANT_RESPONSE_BYTES + 1))

    with pytest.raises(DicAuthorizationError, match="exceeds"):
        await attest_tenant_response(response, EXPECTED_TENANT)


class EventSource:
    def __init__(self) -> None:
        self.handler: Callable[[Response], None] | None = None

    def on(self, event: str, handler: Callable[[Response], None]) -> None:
        assert event == "response"
        self.handler = handler

    def remove_listener(self, event: str, handler: Callable[[Response], None]) -> None:
        assert event == "response"
        assert self.handler == handler
        self.handler = None


@pytest.mark.asyncio
async def test_capture_times_out_and_removes_listener() -> None:
    source = EventSource()

    with pytest.raises(DicAuthorizationError, match="timed out"):
        with TenantResponseCapture(source) as capture:  # type: ignore[arg-type]
            await capture.attest(EXPECTED_TENANT, timeout_ms=1)

    assert source.handler is None


@pytest.mark.asyncio
async def test_capture_rejects_query_on_candidate_endpoint() -> None:
    source = EventSource()

    with pytest.raises(DicAuthorizationError, match="endpoint"):
        with TenantResponseCapture(source) as capture:  # type: ignore[arg-type]
            assert source.handler is not None
            source.handler(Response(url=f"{ENDPOINT}?unexpected=1"))
            await capture.attest(EXPECTED_TENANT, timeout_ms=100)


@pytest.mark.asyncio
async def test_json_exception_log_never_contains_response_body_or_url_data() -> None:
    body_marker = "private-response-marker"
    url_marker = "private-url-marker"
    failures = (
        Response(f'{{"data":"{body_marker}"'.encode()),
        Response(
            url=(
                f"https://secure.dipendentincloud.it:{url_marker}"
                f"{ENDPOINT[ENDPOINT.index('/', 8) :]}"
            )
        ),
    )

    for response in failures:
        try:
            await attest_tenant_response(response, EXPECTED_TENANT)
        except DicAuthorizationError:
            exception_info = sys.exc_info()
        else:
            pytest.fail("synthetic unsafe response was accepted")
        record = logging.LogRecord(
            name="bh_dic.browser",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="tenant attestation failed",
            args=(),
            exc_info=exception_info,
        )
        rendered = JsonFormatter(timezone="UTC").format(record)
        assert body_marker not in rendered
        assert url_marker not in rendered
