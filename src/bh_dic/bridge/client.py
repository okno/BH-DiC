"""Deadline-bounded TLS/JSONL client for the isolated planner bridge."""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from bh_dic.bridge.contracts import (
    MAX_REQUEST_LIFETIME_MS,
    MAX_WIRE_LINE_BYTES,
    BoundedJsonObject,
    BridgeErrorCode,
    BridgeRequest,
    BridgeResponse,
    BridgeStatus,
)
from bh_dic.bridge.errors import (
    BackendOutputError,
    BackendUnavailableError,
    BridgeDeadlineExceeded,
    BridgeError,
    BridgeOverloadedError,
    BridgeProtocolError,
    BridgeReplayError,
)


class AsyncLineReader(Protocol):
    async def readline(self) -> bytes: ...


class AsyncByteWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


type StreamPair = tuple[AsyncLineReader, AsyncByteWriter]


class StreamConnector(Protocol):
    def __call__(
        self,
        *,
        host: str,
        port: int,
        ssl: ssl.SSLContext,
        server_hostname: str,
        limit: int,
    ) -> Awaitable[StreamPair]: ...


def build_client_ssl_context(
    *,
    ca_path: Path,
    client_cert_path: Path,
    client_key_path: Path,
) -> ssl.SSLContext:
    """Build a TLS 1.3 mutual-auth context without ambient trust overrides."""

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cafile=str(ca_path))
        context.load_cert_chain(
            certfile=str(client_cert_path),
            keyfile=str(client_key_path),
        )
    except (OSError, ssl.SSLError):
        raise ValueError("planner bridge mTLS material is invalid") from None
    return context


@dataclass(frozen=True, slots=True)
class BridgeEndpoint:
    """One explicitly authenticated TLS endpoint."""

    host: str
    port: int
    server_hostname: str
    ssl_context: ssl.SSLContext

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.server_hostname.strip():
            raise ValueError("bridge host and TLS server hostname are required")
        normalized_host = self.host.strip().casefold()
        if normalized_host != "localhost":
            try:
                address = ipaddress.ip_address(normalized_host)
            except ValueError as exc:
                raise ValueError("bridge endpoint must remain on loopback") from exc
            if not address.is_loopback:
                raise ValueError("bridge endpoint must remain on loopback")
        if not 1 <= self.port <= 65_535:
            raise ValueError("bridge port is invalid")
        if self.ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("bridge TLS must require certificate verification")
        if not self.ssl_context.check_hostname:
            raise ValueError("bridge TLS hostname verification must remain enabled")


async def _default_connector(
    *,
    host: str,
    port: int,
    ssl: ssl.SSLContext,
    server_hostname: str,
    limit: int,
) -> StreamPair:
    reader, writer = await asyncio.open_connection(
        host=host,
        port=port,
        ssl=ssl,
        server_hostname=server_hostname,
        limit=limit,
    )
    return reader, writer


class JsonlBridgeClient:
    """Send exactly one expiring request per mutually authenticated connection."""

    def __init__(
        self,
        endpoint: BridgeEndpoint,
        *,
        connector: StreamConnector = _default_connector,
        clock_ms: Callable[[], int] | None = None,
        monotonic_seconds: Callable[[], float] | None = None,
        max_line_bytes: int = MAX_WIRE_LINE_BYTES,
        circuit_failure_threshold: int = 3,
        circuit_reset_seconds: float = 30.0,
    ) -> None:
        if not 1_024 <= max_line_bytes <= MAX_WIRE_LINE_BYTES:
            raise ValueError("invalid bridge line limit")
        if not 1 <= circuit_failure_threshold <= 20:
            raise ValueError("invalid bridge circuit failure threshold")
        if not 1.0 <= circuit_reset_seconds <= 300.0:
            raise ValueError("invalid bridge circuit reset interval")
        self._endpoint = endpoint
        self._connector = connector
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._monotonic_seconds = monotonic_seconds or time.monotonic
        self._max_line_bytes = max_line_bytes
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_reset_seconds = circuit_reset_seconds
        self._circuit_failures = 0
        self._circuit_open_until: float | None = None
        self._circuit_half_open = False
        self._circuit_lock = asyncio.Lock()

    async def plan(
        self,
        payload: BoundedJsonObject,
        *,
        timeout_seconds: float,
        request_id: UUID | None = None,
        nonce: str | None = None,
    ) -> BridgeResponse:
        if not 0.05 <= timeout_seconds <= MAX_REQUEST_LIFETIME_MS / 1_000:
            raise ValueError("bridge timeout is outside the protocol bounds")
        half_open_probe = await self._begin_circuit_attempt()
        try:
            response = await self._plan_once(
                payload,
                timeout_seconds=timeout_seconds,
                request_id=request_id,
                nonce=nonce,
            )
        except asyncio.CancelledError:
            await self._cancel_circuit_attempt(half_open_probe=half_open_probe)
            raise
        except BridgeReplayError:
            # The authenticated peer is reachable; replay rejection is not an outage.
            await self._record_circuit_success()
            raise
        except BridgeError:
            await self._record_circuit_failure(half_open_probe=half_open_probe)
            raise
        except Exception:
            await self._cancel_circuit_attempt(half_open_probe=half_open_probe)
            raise
        await self._record_circuit_success()
        return response

    async def _plan_once(
        self,
        payload: BoundedJsonObject,
        *,
        timeout_seconds: float,
        request_id: UUID | None,
        nonce: str | None,
    ) -> BridgeResponse:
        issued_at_ms = self._clock_ms()
        request = BridgeRequest(
            request_id=request_id or uuid4(),
            nonce=nonce or secrets.token_urlsafe(32),
            issued_at_ms=issued_at_ms,
            deadline_ms=issued_at_ms + int(timeout_seconds * 1_000),
            payload=payload,
        )
        wire = request.model_dump_json().encode("utf-8") + b"\n"
        if len(wire) > self._max_line_bytes:
            raise BridgeProtocolError("serialized bridge request exceeds the line limit")

        writer: AsyncByteWriter | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                reader, writer = await self._connector(
                    host=self._endpoint.host,
                    port=self._endpoint.port,
                    ssl=self._endpoint.ssl_context,
                    server_hostname=self._endpoint.server_hostname,
                    limit=self._max_line_bytes + 1,
                )
                writer.write(wire)
                await writer.drain()
                response_line = await reader.readline()
        except TimeoutError:
            raise BridgeDeadlineExceeded("planner bridge deadline exceeded") from None
        except (OSError, ssl.SSLError):
            raise BackendUnavailableError("planner bridge is unavailable") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ssl.SSLError):
                    pass

        if not response_line or len(response_line) > self._max_line_bytes:
            raise BridgeProtocolError("planner bridge returned an invalid line")
        try:
            response = BridgeResponse.model_validate_json(response_line)
        except ValidationError:
            raise BridgeProtocolError("planner bridge returned an invalid response") from None
        if response.request_id != request.request_id or response.nonce != request.nonce:
            raise BridgeProtocolError("planner bridge response correlation failed")
        if response.completed_at_ms < request.issued_at_ms:
            raise BridgeProtocolError("planner bridge response time is invalid")
        if response.completed_at_ms > request.deadline_ms:
            raise BridgeDeadlineExceeded("planner bridge completed after the deadline")
        if response.status is BridgeStatus.ERROR:
            if response.error is None:
                raise BridgeProtocolError("planner bridge omitted error details")
            code = response.error.code
            if code in {BridgeErrorCode.EXPIRED, BridgeErrorCode.DEADLINE_EXCEEDED}:
                raise BridgeDeadlineExceeded("planner bridge deadline was not satisfied")
            if code is BridgeErrorCode.REPLAY:
                raise BridgeReplayError("planner bridge rejected a replayed request")
            if code is BridgeErrorCode.OVERLOADED:
                raise BridgeOverloadedError("planner bridge is overloaded")
            if code is BridgeErrorCode.BACKEND_UNAVAILABLE:
                raise BackendUnavailableError("planner backend is unavailable")
            if code is BridgeErrorCode.BACKEND_FAILURE:
                raise BackendOutputError("planner backend failed safely")
            raise BridgeProtocolError("planner bridge returned an internal error")
        return response

    async def _begin_circuit_attempt(self) -> bool:
        now = self._monotonic_seconds()
        async with self._circuit_lock:
            if self._circuit_open_until is None:
                return False
            if now < self._circuit_open_until or self._circuit_half_open:
                raise BackendUnavailableError("planner bridge circuit is open")
            self._circuit_half_open = True
            return True

    async def _record_circuit_failure(self, *, half_open_probe: bool) -> None:
        async with self._circuit_lock:
            self._circuit_failures += 1
            self._circuit_half_open = False
            if half_open_probe or self._circuit_failures >= self._circuit_failure_threshold:
                self._circuit_open_until = self._monotonic_seconds() + self._circuit_reset_seconds

    async def _record_circuit_success(self) -> None:
        async with self._circuit_lock:
            self._circuit_failures = 0
            self._circuit_open_until = None
            self._circuit_half_open = False

    async def _cancel_circuit_attempt(self, *, half_open_probe: bool) -> None:
        if not half_open_probe:
            return
        async with self._circuit_lock:
            self._circuit_half_open = False
            self._circuit_open_until = self._monotonic_seconds() + self._circuit_reset_seconds

    async def close(self) -> None:
        """The one-request-per-connection client owns no persistent resource."""


__all__ = [
    "AsyncByteWriter",
    "AsyncLineReader",
    "BridgeEndpoint",
    "JsonlBridgeClient",
    "StreamConnector",
    "StreamPair",
    "build_client_ssl_context",
]
