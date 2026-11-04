from __future__ import annotations

import ssl
from uuid import UUID

import pytest

from bh_dic.bridge.client import BridgeEndpoint, JsonlBridgeClient
from bh_dic.bridge.contracts import (
    BackendResult,
    BoundedJsonObject,
    BridgeErrorCode,
    BridgeErrorPayload,
    BridgeRequest,
    BridgeResponse,
    BridgeStatus,
)
from bh_dic.bridge.errors import (
    BackendUnavailableError,
    BridgeOverloadedError,
    BridgeProtocolError,
    BridgeReplayError,
)
from bh_dic.bridge.host import JsonlBridgeHost, NonceReplayCache

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")
NONCE = "A" * 32


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, request: BridgeRequest) -> BackendResult:
        self.calls += 1
        return BackendResult(
            payload=BoundedJsonObject.model_validate({"kind": "unsupported"}),
            backend="codex",
            model="synthetic-model",
        )

    async def close(self) -> None:
        return None


def _request(*, issued: int = 1_000, deadline: int = 2_000) -> BridgeRequest:
    return BridgeRequest(
        request_id=REQUEST_ID,
        nonce=NONCE,
        issued_at_ms=issued,
        deadline_ms=deadline,
        payload=BoundedJsonObject.model_validate({"task": "hr_planning"}),
    )


@pytest.mark.asyncio
async def test_host_rejects_expiry_and_replay_before_backend() -> None:
    backend = _Backend()
    host = JsonlBridgeHost(backend, clock_ms=lambda: 1_500)

    expired = await host._dispatch(_request(deadline=1_500))
    accepted = await host._dispatch(_request(deadline=2_000))
    replayed = await host._dispatch(_request(deadline=2_000))

    assert expired.error is not None
    assert expired.error.code is BridgeErrorCode.EXPIRED
    assert accepted.status is BridgeStatus.OK
    assert replayed.error is not None
    assert replayed.error.code is BridgeErrorCode.REPLAY
    assert backend.calls == 1


@pytest.mark.asyncio
async def test_nonce_cache_is_bounded_and_expires_entries() -> None:
    cache = NonceReplayCache(max_entries=1)
    assert await cache.claim("A" * 32, expires_at_ms=100, now_ms=50)
    assert not await cache.claim("B" * 32, expires_at_ms=100, now_ms=50)
    assert await cache.claim("B" * 32, expires_at_ms=200, now_ms=100)


class _Reader:
    def __init__(self, line: bytes) -> None:
        self.line = line

    async def readline(self) -> bytes:
        return self.line


class _Writer:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def test_bridge_endpoint_rejects_non_loopback_targets() -> None:
    with pytest.raises(ValueError, match="loopback"):
        BridgeEndpoint("192.0.2.10", 9443, "bridge.test", _tls_context())


@pytest.mark.asyncio
async def test_client_correlates_response_and_maps_closed_error_code() -> None:
    response = BridgeResponse(
        request_id=REQUEST_ID,
        nonce=NONCE,
        completed_at_ms=1_100,
        status=BridgeStatus.ERROR,
        error=BridgeErrorPayload(
            code=BridgeErrorCode.OVERLOADED,
            message="planner bridge has no execution slot",
        ),
    )
    writer = _Writer()

    async def connector(**_kwargs: object) -> tuple[_Reader, _Writer]:
        return _Reader(response.model_dump_json().encode() + b"\n"), writer

    client = JsonlBridgeClient(
        BridgeEndpoint("127.0.0.1", 9443, "bridge.test", _tls_context()),
        connector=connector,
        clock_ms=lambda: 1_000,
    )
    with pytest.raises(BridgeOverloadedError):
        await client.plan(
            BoundedJsonObject.model_validate({"task": "hr_planning"}),
            timeout_seconds=1,
            request_id=REQUEST_ID,
            nonce=NONCE,
        )
    assert writer.closed


@pytest.mark.asyncio
async def test_client_rejects_response_nonce_mismatch() -> None:
    response = BridgeResponse(
        request_id=REQUEST_ID,
        nonce="B" * 32,
        completed_at_ms=1_100,
        status=BridgeStatus.ERROR,
        error=BridgeErrorPayload(code=BridgeErrorCode.REPLAY, message="replayed"),
    )

    async def connector(**_kwargs: object) -> tuple[_Reader, _Writer]:
        return _Reader(response.model_dump_json().encode() + b"\n"), _Writer()

    client = JsonlBridgeClient(
        BridgeEndpoint("127.0.0.1", 9443, "bridge.test", _tls_context()),
        connector=connector,
        clock_ms=lambda: 1_000,
    )
    with pytest.raises(BridgeProtocolError) as caught:
        await client.plan(
            BoundedJsonObject.model_validate({"task": "hr_planning"}),
            timeout_seconds=1,
            request_id=REQUEST_ID,
            nonce=NONCE,
        )
    assert not isinstance(caught.value, BridgeReplayError)


@pytest.mark.asyncio
async def test_client_rejects_response_time_before_request_issue() -> None:
    response = BridgeResponse(
        request_id=REQUEST_ID,
        nonce=NONCE,
        completed_at_ms=999,
        status=BridgeStatus.OK,
        result=BackendResult(
            payload=BoundedJsonObject.model_validate({"kind": "unsupported"}),
            backend="codex",
            model="synthetic-model",
        ),
    )

    async def connector(**_kwargs: object) -> tuple[_Reader, _Writer]:
        return _Reader(response.model_dump_json().encode() + b"\n"), _Writer()

    client = JsonlBridgeClient(
        BridgeEndpoint("127.0.0.1", 9443, "bridge.test", _tls_context()),
        connector=connector,
        clock_ms=lambda: 1_000,
    )
    with pytest.raises(BridgeProtocolError, match="response time"):
        await client.plan(
            BoundedJsonObject.model_validate({"task": "hr_planning"}),
            timeout_seconds=1,
            request_id=REQUEST_ID,
            nonce=NONCE,
        )


@pytest.mark.asyncio
async def test_client_circuit_opens_then_allows_one_recovery_probe() -> None:
    attempts = 0
    available = False
    monotonic = [10.0]

    async def connector(**_kwargs: object) -> tuple[_Reader, _Writer]:
        nonlocal attempts
        attempts += 1
        if not available:
            raise OSError("synthetic unavailable endpoint")
        response = BridgeResponse(
            request_id=REQUEST_ID,
            nonce=NONCE,
            completed_at_ms=1_100,
            status=BridgeStatus.OK,
            result=BackendResult(
                payload=BoundedJsonObject.model_validate({"kind": "unsupported"}),
                backend="codex",
                model="synthetic-model",
            ),
        )
        return _Reader(response.model_dump_json().encode() + b"\n"), _Writer()

    client = JsonlBridgeClient(
        BridgeEndpoint("127.0.0.1", 9443, "bridge.test", _tls_context()),
        connector=connector,
        clock_ms=lambda: 1_000,
        monotonic_seconds=lambda: monotonic[0],
        circuit_failure_threshold=2,
        circuit_reset_seconds=5,
    )
    payload = BoundedJsonObject.model_validate({"task": "hr_planning"})
    for _attempt in range(2):
        with pytest.raises(BackendUnavailableError):
            await client.plan(
                payload,
                timeout_seconds=1,
                request_id=REQUEST_ID,
                nonce=NONCE,
            )
    with pytest.raises(BackendUnavailableError, match="circuit"):
        await client.plan(
            payload,
            timeout_seconds=1,
            request_id=REQUEST_ID,
            nonce=NONCE,
        )
    assert attempts == 2

    monotonic[0] = 16.0
    available = True
    recovered = await client.plan(
        payload,
        timeout_seconds=1,
        request_id=REQUEST_ID,
        nonce=NONCE,
    )
    assert recovered.status is BridgeStatus.OK
    assert attempts == 3

    await client.plan(
        payload,
        timeout_seconds=1,
        request_id=REQUEST_ID,
        nonce=NONCE,
    )
    assert attempts == 4


def test_bounded_payload_rejects_depth_and_oversized_values() -> None:
    with pytest.raises(ValueError, match="deeply nested"):
        BoundedJsonObject.model_validate(
            {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {}}}}}}}}}}
        )
    with pytest.raises(ValueError, match="string exceeds"):
        BoundedJsonObject.model_validate({"value": "x" * 20_000})
