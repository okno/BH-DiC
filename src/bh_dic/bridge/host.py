"""Bounded async host for the TLS/JSONL planner bridge."""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
import time
from collections.abc import Callable

from pydantic import ValidationError

from bh_dic.bridge.backend import PlannerBackend
from bh_dic.bridge.client import AsyncByteWriter, AsyncLineReader
from bh_dic.bridge.contracts import (
    MAX_WIRE_LINE_BYTES,
    BridgeErrorCode,
    BridgeErrorPayload,
    BridgeRequest,
    BridgeResponse,
    BridgeStatus,
)
from bh_dic.bridge.errors import BackendUnavailableError


class NonceReplayCache:
    """Bounded in-memory nonce claim store; one host process accepts a nonce once."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        if not 1 <= max_entries <= 1_000_000:
            raise ValueError("invalid replay cache capacity")
        self._max_entries = max_entries
        self._expires_by_nonce: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def claim(self, nonce: str, *, expires_at_ms: int, now_ms: int) -> bool:
        async with self._lock:
            expired = [
                candidate
                for candidate, expiry in self._expires_by_nonce.items()
                if expiry <= now_ms
            ]
            for candidate in expired:
                del self._expires_by_nonce[candidate]
            if nonce in self._expires_by_nonce:
                return False
            if len(self._expires_by_nonce) >= self._max_entries:
                return False
            self._expires_by_nonce[nonce] = expires_at_ms
            return True


class _CapacityGate:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._active = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> bool:
        async with self._lock:
            if self._active >= self._capacity:
                return False
            self._active += 1
            return True

    async def leave(self) -> None:
        async with self._lock:
            self._active -= 1


class JsonlBridgeHost:
    """Validate, de-duplicate, deadline-bound, and dispatch planner requests."""

    def __init__(
        self,
        backend: PlannerBackend,
        *,
        replay_cache: NonceReplayCache | None = None,
        clock_ms: Callable[[], int] | None = None,
        max_concurrency: int = 4,
        max_line_bytes: int = MAX_WIRE_LINE_BYTES,
        max_clock_skew_ms: int = 5_000,
    ) -> None:
        if not 1 <= max_concurrency <= 128:
            raise ValueError("invalid bridge concurrency")
        if not 1_024 <= max_line_bytes <= MAX_WIRE_LINE_BYTES:
            raise ValueError("invalid bridge line limit")
        if not 0 <= max_clock_skew_ms <= 60_000:
            raise ValueError("invalid bridge clock skew")
        self._backend = backend
        self._replay_cache = replay_cache or NonceReplayCache()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._capacity = _CapacityGate(max_concurrency)
        self._max_line_bytes = max_line_bytes
        self._max_clock_skew_ms = max_clock_skew_ms

    async def handle_connection(
        self,
        reader: AsyncLineReader,
        writer: AsyncByteWriter,
    ) -> None:
        """Handle exactly one request, then close the connection."""

        try:
            line = await reader.readline()
            if not line or len(line) > self._max_line_bytes:
                return
            try:
                request = BridgeRequest.model_validate_json(line)
            except ValidationError:
                return
            response = await self._dispatch(request)
            encoded = response.model_dump_json().encode("utf-8") + b"\n"
            if len(encoded) > self._max_line_bytes:
                response = self._error_response(
                    request,
                    BridgeErrorCode.INTERNAL_ERROR,
                    "planner response exceeded the transport limit",
                )
                encoded = response.model_dump_json().encode("utf-8") + b"\n"
            writer.write(encoded)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _dispatch(self, request: BridgeRequest) -> BridgeResponse:
        now_ms = self._clock_ms()
        if request.issued_at_ms > now_ms + self._max_clock_skew_ms:
            return self._error_response(
                request,
                BridgeErrorCode.EXPIRED,
                "planner request issue time is in the future",
            )
        if request.deadline_ms <= now_ms:
            return self._error_response(
                request,
                BridgeErrorCode.EXPIRED,
                "planner request has expired",
            )
        claimed = await self._replay_cache.claim(
            request.nonce,
            expires_at_ms=request.deadline_ms + self._max_clock_skew_ms,
            now_ms=now_ms,
        )
        if not claimed:
            return self._error_response(
                request,
                BridgeErrorCode.REPLAY,
                "planner request nonce was already used",
            )
        if not await self._capacity.enter():
            return self._error_response(
                request,
                BridgeErrorCode.OVERLOADED,
                "planner bridge has no execution slot",
            )
        try:
            remaining_seconds = max((request.deadline_ms - self._clock_ms()) / 1_000, 0.0)
            if remaining_seconds <= 0:
                return self._error_response(
                    request,
                    BridgeErrorCode.DEADLINE_EXCEEDED,
                    "planner deadline elapsed before dispatch",
                )
            try:
                async with asyncio.timeout(remaining_seconds):
                    result = await self._backend.plan(request)
            except TimeoutError:
                return self._error_response(
                    request,
                    BridgeErrorCode.DEADLINE_EXCEEDED,
                    "planner backend exceeded the deadline",
                )
            except BackendUnavailableError:
                return self._error_response(
                    request,
                    BridgeErrorCode.BACKEND_UNAVAILABLE,
                    "planner backend is unavailable",
                )
            except Exception:
                return self._error_response(
                    request,
                    BridgeErrorCode.BACKEND_FAILURE,
                    "planner backend failed safely",
                )
            if self._clock_ms() > request.deadline_ms:
                return self._error_response(
                    request,
                    BridgeErrorCode.DEADLINE_EXCEEDED,
                    "planner backend completed after the deadline",
                )
            return BridgeResponse(
                request_id=request.request_id,
                nonce=request.nonce,
                completed_at_ms=self._clock_ms(),
                status=BridgeStatus.OK,
                result=result,
            )
        finally:
            await self._capacity.leave()

    def _error_response(
        self,
        request: BridgeRequest,
        code: BridgeErrorCode,
        message: str,
    ) -> BridgeResponse:
        return BridgeResponse(
            request_id=request.request_id,
            nonce=request.nonce,
            completed_at_ms=self._clock_ms(),
            status=BridgeStatus.ERROR,
            error=BridgeErrorPayload(code=code, message=message),
        )

    async def start_server(
        self,
        *,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext,
    ) -> asyncio.Server:
        """Bind an explicit address with mandatory client-certificate verification."""

        normalized_host = host.strip().casefold()
        if normalized_host != "localhost":
            try:
                address = ipaddress.ip_address(normalized_host)
            except ValueError as exc:
                raise ValueError("planner bridge must bind a loopback address") from exc
            if not address.is_loopback:
                raise ValueError("planner bridge must bind a loopback address")
        if not 1 <= port <= 65_535:
            raise ValueError("bridge port is invalid")
        if ssl_context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("bridge server TLS must require client certificates")
        return await asyncio.start_server(
            self.handle_connection,
            host=host,
            port=port,
            ssl=ssl_context,
            limit=self._max_line_bytes + 1,
        )

    async def close(self) -> None:
        await self._backend.close()


__all__ = ["JsonlBridgeHost", "NonceReplayCache"]
