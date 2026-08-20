"""Fail-closed feature flags with a runtime kill-switch layer."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from types import MappingProxyType
from typing import Protocol

DEFAULT_FEATURE_FLAGS: Mapping[str, bool] = MappingProxyType(
    {
        "ENABLE_READ_ACTIONS": True,
        "ENABLE_DIC_RECONNECT": False,
        "ENABLE_WRITE_ACTIONS": False,
        "ENABLE_LIVE_WRITE_TESTS": False,
        "ENABLE_EMPLOYEE_CREATE": False,
        "ENABLE_EMPLOYEE_UPDATE": False,
        "ENABLE_SENSITIVE_PROFILE_UPDATE": False,
        "ENABLE_CONTRACT_WRITE": False,
        "ENABLE_CONTRACT_DELETE": False,
        "ENABLE_MATURATION_WRITE": False,
        "ENABLE_BALANCE_CORRECTION": False,
        "ENABLE_INVITE_ACTIONS": False,
        "ENABLE_ACCOUNT_CONNECT": False,
        "ENABLE_ACCOUNT_DISCONNECT": False,
        "ENABLE_RBAC_WRITE": False,
        "ENABLE_STATUS_CHANGE": False,
        "ENABLE_DOCUMENT_UPLOAD": False,
        "ENABLE_DOCUMENT_DOWNLOAD": False,
        "ENABLE_DOCUMENT_UPDATE": False,
        "ENABLE_DOCUMENT_DELETE": False,
        "ENABLE_EXPORT": False,
        "ENABLE_EMPLOYEE_DELETE": False,
    }
)


class FeatureFlags(Protocol):
    def enabled(self, name: str) -> bool: ...


class RuntimeFeatureFlags:
    """Configuration baseline AND runtime overrides.

    A runtime value can immediately disable a feature but can never enable a
    feature disabled in the validated process configuration. This prevents a
    database/admin override from bypassing the deployment safety baseline.
    """

    def __init__(
        self,
        baseline: Mapping[str, bool] | None = None,
        runtime_overrides: Mapping[str, bool] | None = None,
    ) -> None:
        self._baseline = dict(DEFAULT_FEATURE_FLAGS if baseline is None else baseline)
        self._runtime = dict(runtime_overrides or {})
        self._lock = RLock()

    def enabled(self, name: str) -> bool:
        with self._lock:
            baseline = self._baseline.get(name, False)
            runtime = self._runtime.get(name, True)
            return bool(baseline and runtime)

    def set_runtime(self, name: str, enabled: bool) -> None:
        if name not in self._baseline:
            raise KeyError(f"unknown feature flag: {name}")
        with self._lock:
            self._runtime[name] = bool(enabled)

    def clear_runtime(self, name: str) -> None:
        with self._lock:
            self._runtime.pop(name, None)

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return {name: self.enabled(name) for name in self._baseline}
