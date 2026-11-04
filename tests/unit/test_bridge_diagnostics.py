from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bh_dic.bridge.diagnostics as diagnostics
import bh_dic.bridge.service as bridge_service
from bh_dic.bridge.contracts import BackendResult, BoundedJsonObject
from bh_dic.bridge.diagnostics import BridgePreflightCode, BridgePreflightError
from bh_dic.bridge.errors import CodexAuthenticationRequiredError


def _settings(*, backend: str = "codex") -> SimpleNamespace:
    return SimpleNamespace(
        bridge_backend=backend,
        bridge_model="synthetic-model",
    )


class _Backend:
    def __init__(self, *, result: BackendResult | None = None) -> None:
        self.preflight = AsyncMock()
        self.plan = AsyncMock(return_value=result)
        self.close = AsyncMock()


@pytest.mark.asyncio
async def test_bridge_check_offline_validates_bootstrap_without_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    monkeypatch.setattr(diagnostics, "build_server_ssl_context", lambda _settings: object())
    monkeypatch.setattr(diagnostics, "build_planner_backend", lambda _settings: backend)

    report = await diagnostics.check_bridge_host(_settings(), live=False)  # type: ignore[arg-type]

    backend.preflight.assert_awaited_once_with()
    backend.plan.assert_not_awaited()
    backend.close.assert_awaited_once_with()
    assert report.status == "READY_OFFLINE"
    assert report.live_contacted is False
    assert report.bootstrap == "CODEX_SESSION_PRESENT_UNVERIFIED"


@pytest.mark.asyncio
async def test_bridge_check_reports_missing_isolated_login_without_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    backend.preflight.side_effect = CodexAuthenticationRequiredError("private provider detail")
    monkeypatch.setattr(diagnostics, "build_server_ssl_context", lambda _settings: object())
    monkeypatch.setattr(diagnostics, "build_planner_backend", lambda _settings: backend)

    with pytest.raises(BridgePreflightError) as captured:
        await diagnostics.check_bridge_host(_settings(), live=True)  # type: ignore[arg-type]

    assert captured.value.code is BridgePreflightCode.CODEX_AUTHENTICATION_REQUIRED
    assert captured.value.live_contacted is False
    assert "private provider detail" not in str(captured.value)
    backend.plan.assert_not_awaited()
    backend.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_bridge_check_live_requires_one_exact_schema_bound_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = BackendResult(
        payload=BoundedJsonObject.model_validate(
            {
                "kind": "single_intent",
                "intent": {
                    "intent": "employee_headcount",
                    "function_id": "EMP-READ-001",
                    "action_class": "READ",
                    "sensitivity": "LOW",
                    "confidence": 1.0,
                },
            }
        ),
        backend="codex",
        model="synthetic-model",
    )
    backend = _Backend(result=result)
    monkeypatch.setattr(diagnostics, "build_server_ssl_context", lambda _settings: object())
    monkeypatch.setattr(diagnostics, "build_planner_backend", lambda _settings: backend)

    report = await diagnostics.check_bridge_host(_settings(), live=True)  # type: ignore[arg-type]

    backend.preflight.assert_awaited_once_with()
    backend.plan.assert_awaited_once()
    backend.close.assert_awaited_once_with()
    request = backend.plan.await_args.args[0]
    assert request.payload.root["request"] == "request employee_headcount"
    assert request.payload.root["functions"][0]["function_id"] == "EMP-READ-001"
    assert report.status == "LIVE_VERIFIED"
    assert report.live_contacted is True
    assert report.tool_execution is False


@pytest.mark.asyncio
async def test_bridge_service_never_binds_when_backend_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    backend.preflight.side_effect = CodexAuthenticationRequiredError("synthetic")
    host = SimpleNamespace(start_server=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(bridge_service, "build_server_ssl_context", lambda _settings: object())
    monkeypatch.setattr(bridge_service, "build_planner_backend", lambda _settings: backend)
    monkeypatch.setattr(
        bridge_service,
        "JsonlBridgeHost",
        lambda *_args, **_kwargs: host,
    )

    with pytest.raises(CodexAuthenticationRequiredError):
        await bridge_service.serve_bridge(SimpleNamespace(bridge_max_concurrency=1))

    backend.preflight.assert_awaited_once_with()
    host.start_server.assert_not_awaited()
    host.close.assert_awaited_once_with()
