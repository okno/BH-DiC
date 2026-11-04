"""Sanitized offline/live readiness checks for the standalone Mint bridge."""

from __future__ import annotations

import secrets
import time
from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, StrictStr

from bh_dic.bridge.contracts import BoundedJsonObject, BridgeRequest
from bh_dic.bridge.errors import (
    BackendOutputError,
    BackendUnavailableError,
    CodexAuthenticationRequiredError,
    CodexRuntimeSecurityError,
)
from bh_dic.bridge.planner import build_planner_payload
from bh_dic.bridge.service import build_planner_backend, build_server_ssl_context
from bh_dic.bridge.settings import BridgeBackendName, BridgeHostSettings
from bh_dic.query.decision import PlanningDecision, SingleIntentDecision


class BridgePreflightCode(StrEnum):
    """Bounded operator-facing failure reasons; no provider or path detail is included."""

    BACKEND_CONFIGURATION_INVALID = "BACKEND_CONFIGURATION_INVALID"
    BACKEND_LIVE_CHECK_FAILED = "BACKEND_LIVE_CHECK_FAILED"
    BACKEND_RESPONSE_INVALID = "BACKEND_RESPONSE_INVALID"
    BACKEND_STATUS_UNAVAILABLE = "BACKEND_STATUS_UNAVAILABLE"
    CODEX_AUTHENTICATION_REQUIRED = "CODEX_AUTHENTICATION_REQUIRED"
    CODEX_RUNTIME_UNSAFE = "CODEX_RUNTIME_UNSAFE"
    MTLS_INVALID = "MTLS_INVALID"


class BridgePreflightError(RuntimeError):
    """A sanitized bridge readiness failure suitable for CLI output."""

    def __init__(
        self,
        code: BridgePreflightCode,
        *,
        live_contacted: bool = False,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.live_contacted = live_contacted


class BridgeCheckReport(BaseModel):
    """Non-sensitive readiness metadata returned to a local operator."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["READY_OFFLINE", "LIVE_VERIFIED"]
    backend: BridgeBackendName
    model: StrictStr
    live_contacted: bool
    bind_scope: Literal["LOOPBACK"] = "LOOPBACK"
    mtls: Literal["VALID"] = "VALID"
    bootstrap: Literal[
        "CODEX_SESSION_PRESENT_UNVERIFIED",
        "LOCAL_BACKEND_CONFIGURED_UNVERIFIED",
        "LIVE_VERIFIED",
    ]
    store: Literal[False] = False
    tool_execution: Literal[False] = False


def _synthetic_request() -> BridgeRequest:
    payload = build_planner_payload(
        "request employee_headcount",
        frozenset({"EMP-READ-001"}),
        today=date.today(),
    )
    issued_at_ms = time.time_ns() // 1_000_000
    return BridgeRequest(
        request_id=uuid4(),
        nonce=secrets.token_urlsafe(24),
        issued_at_ms=issued_at_ms,
        deadline_ms=issued_at_ms + 30_000,
        payload=BoundedJsonObject.model_validate(payload.model_dump(mode="json")),
    )


async def check_bridge_host(
    settings: BridgeHostSettings,
    *,
    live: bool,
) -> BridgeCheckReport:
    """Validate local boundaries, then optionally run one closed synthetic inference."""

    try:
        build_server_ssl_context(settings)
    except ValueError:
        raise BridgePreflightError(BridgePreflightCode.MTLS_INVALID) from None

    try:
        backend = build_planner_backend(settings)
    except CodexRuntimeSecurityError:
        raise BridgePreflightError(BridgePreflightCode.CODEX_RUNTIME_UNSAFE) from None
    except (OSError, ValueError):
        raise BridgePreflightError(BridgePreflightCode.BACKEND_CONFIGURATION_INVALID) from None

    try:
        try:
            await backend.preflight()
        except CodexAuthenticationRequiredError:
            raise BridgePreflightError(BridgePreflightCode.CODEX_AUTHENTICATION_REQUIRED) from None
        except BackendUnavailableError:
            raise BridgePreflightError(BridgePreflightCode.BACKEND_STATUS_UNAVAILABLE) from None

        if not live:
            bootstrap: Literal[
                "CODEX_SESSION_PRESENT_UNVERIFIED",
                "LOCAL_BACKEND_CONFIGURED_UNVERIFIED",
            ] = (
                "CODEX_SESSION_PRESENT_UNVERIFIED"
                if settings.bridge_backend == "codex"
                else "LOCAL_BACKEND_CONFIGURED_UNVERIFIED"
            )
            return BridgeCheckReport(
                status="READY_OFFLINE",
                backend=settings.bridge_backend,
                model=settings.bridge_model,
                live_contacted=False,
                bootstrap=bootstrap,
            )

        try:
            result = await backend.plan(_synthetic_request())
        except BackendOutputError:
            raise BridgePreflightError(
                BridgePreflightCode.BACKEND_RESPONSE_INVALID,
                live_contacted=True,
            ) from None
        except BackendUnavailableError:
            raise BridgePreflightError(
                BridgePreflightCode.BACKEND_LIVE_CHECK_FAILED,
                live_contacted=True,
            ) from None
        except Exception:
            raise BridgePreflightError(
                BridgePreflightCode.BACKEND_LIVE_CHECK_FAILED,
                live_contacted=True,
            ) from None

        try:
            decision = PlanningDecision.model_validate(result.payload.root)
        except ValueError:
            raise BridgePreflightError(
                BridgePreflightCode.BACKEND_RESPONSE_INVALID,
                live_contacted=True,
            ) from None
        if (
            result.backend != settings.bridge_backend
            or not isinstance(decision.root, SingleIntentDecision)
            or decision.root.intent.function_id != "EMP-READ-001"
        ):
            raise BridgePreflightError(
                BridgePreflightCode.BACKEND_RESPONSE_INVALID,
                live_contacted=True,
            )
        return BridgeCheckReport(
            status="LIVE_VERIFIED",
            backend=settings.bridge_backend,
            model=result.model,
            live_contacted=True,
            bootstrap="LIVE_VERIFIED",
        )
    finally:
        await backend.close()


__all__ = [
    "BridgeCheckReport",
    "BridgePreflightCode",
    "BridgePreflightError",
    "check_bridge_host",
]
