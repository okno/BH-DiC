"""Versioned allowlist of deterministic DIC navigation routes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from bh_dic.dic.errors import DicConfigurationError, DicValidationError

_EMPLOYEE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


class RouteVerificationState(StrEnum):
    DISCOVERED = "DISCOVERED"
    LIVE_READ_VERIFIED = "LIVE_READ_VERIFIED"
    DEGRADED_SCHEMA = "DEGRADED_SCHEMA"
    DISABLED = "DISABLED"
    NEEDS_VALIDATION = "NEEDS_VALIDATION"


class RouteSensitivity(StrEnum):
    AUTH = "AUTH"
    INTERNAL_HR = "INTERNAL_HR"
    PERSONAL = "PERSONAL"
    HIGHLY_CONFIDENTIAL = "HIGHLY_CONFIDENTIAL"
    SECURITY_IAM = "SECURITY_IAM"


RecoveryStrategy = Literal[
    "reauthenticate",
    "refresh_once_then_fail",
    "resource_circuit_then_fail",
]


@dataclass(frozen=True, slots=True)
class DicRouteSpec:
    name: str
    template: str
    required_controls: tuple[str, ...]
    optional_controls: tuple[str, ...]
    readable_resources: tuple[str, ...]
    operations: tuple[Literal["read", "write"], ...]
    sensitivity: RouteSensitivity
    timeout_ms: int
    recovery: RecoveryStrategy
    verification: RouteVerificationState
    fingerprints_sha256: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.]{1,79}", self.name):
            raise DicConfigurationError("invalid DIC route name")
        if not self.template.startswith("/it/") or "?" in self.template or "#" in self.template:
            raise DicConfigurationError("invalid DIC route template")
        if set(re.findall(r"{([^{}]+)}", self.template)).difference({"employee_id"}):
            raise DicConfigurationError("unsupported DIC route parameter")
        if not 1_000 <= self.timeout_ms <= 120_000:
            raise DicConfigurationError("invalid DIC route timeout")
        if not self.readable_resources:
            raise DicConfigurationError("DIC route must declare a readable resource")
        if any(_FINGERPRINT.fullmatch(item) is None for item in self.fingerprints_sha256):
            raise DicConfigurationError("invalid DIC route fingerprint")

    @property
    def accepts_employee_id(self) -> bool:
        return "{employee_id}" in self.template

    def render(self, *, employee_id: str | None = None) -> str:
        if not self.accepts_employee_id:
            if employee_id is not None:
                raise DicValidationError("this DIC route does not accept employee_id")
            return self.template
        if employee_id is None:
            raise DicValidationError("employee_id is required by this route")
        if _EMPLOYEE_ID.fullmatch(employee_id) is None:
            raise DicValidationError("invalid employee identifier")
        return self.template.format(employee_id=employee_id)


_ROUTES = (
    DicRouteSpec(
        "auth.login",
        "/it/login",
        ("auth.username", "auth.password", "auth.submit"),
        ("auth.mfa", "auth.captcha"),
        ("session",),
        ("read",),
        RouteSensitivity.AUTH,
        30_000,
        "reauthenticate",
        RouteVerificationState.LIVE_READ_VERIFIED,
    ),
    DicRouteSpec(
        "employees.list",
        "/it/app/employees/list",
        ("employees.container", "employees.rows"),
        ("employees.search", "employees.next"),
        ("employees",),
        ("read", "write"),
        RouteSensitivity.PERSONAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        (
            "00a7a28e2db11182ea78cac953f1ed8ed8c1cedd62a34b0b4d6588a0dbcac529",
            "6976b305169cb86c068c6584761c989efb1c74a5954e1a8ac92f476b1cbeed82",
        ),
    ),
    DicRouteSpec(
        "employees.summary",
        "/it/app/employees/info/{employee_id}/summary",
        ("summary.first_name", "summary.last_name", "summary.state"),
        ("summary.job_title", "summary.workplace"),
        ("employee_summary",),
        ("read", "write"),
        RouteSensitivity.HIGHLY_CONFIDENTIAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        ("b402a6e032f6a93d92d92dde511c2cea4b92d8b87b61ed2b989b21668e4c6e03",),
    ),
    DicRouteSpec(
        "employees.roles",
        "/it/app/employees/info/{employee_id}/roles",
        ("roles.groups", "roles.items"),
        (
            "roles.time.timestamping",
            "roles.time.attendance",
            "roles.time.shifts",
            "roles.time.expenses",
        ),
        ("roles", "time_access", "permissions"),
        ("read", "write"),
        RouteSensitivity.SECURITY_IAM,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        ("36cfc1bb04b4afc99e103b96eb70a282ad611d83378e3e274b9fcee9578faedf",),
    ),
    DicRouteSpec(
        "timestamps.employees",
        "/it/app/settings/timestamps/employees",
        ("timestamps.rows",),
        (),
        ("timestamp_access", "workplaces"),
        ("read", "write"),
        RouteSensitivity.SECURITY_IAM,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        ("f435af95db058e3494460c8a1f74ce3ec5e0270af62fec5f4b5171d14e108b1e",),
    ),
    DicRouteSpec(
        "employees.contracts",
        "/it/app/employees/info/{employee_id}/contracts",
        ("contracts.container",),
        ("contracts.rows",),
        ("contracts", "working_times"),
        ("read", "write"),
        RouteSensitivity.PERSONAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        ("701fd4eccadeaf8ebe675bcadbe78807c5254bb85bde936e40a9ff84fd171138",),
    ),
    DicRouteSpec(
        "employees.maturations",
        "/it/app/employees/info/{employee_id}/maturations",
        ("maturations.container",),
        ("maturations.rows",),
        ("maturations", "maturation_history"),
        ("read", "write"),
        RouteSensitivity.PERSONAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        ("65c6845f23b0e9508ebfbccab022f826bd2753879c790253f985c0846f50a38d",),
    ),
    DicRouteSpec(
        "employees.balances",
        "/it/app/employees/info/{employee_id}/counters",
        ("balance.year_selector", "balance.year_current"),
        ("balance.rows", "balance.month"),
        ("counters", "balances", "corrections"),
        ("read", "write"),
        RouteSensitivity.HIGHLY_CONFIDENTIAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
        ("6b14f0968884b7feb7be1ce46208632117ee01b31245ac9ff7375c381cfe3f5a",),
    ),
    DicRouteSpec(
        "employees.payrolls",
        "/it/app/employees/info/{employee_id}/payrolls",
        ("payrolls.year_selector",),
        ("payrolls.rows",),
        ("payrolls", "payroll_attachments"),
        ("read",),
        RouteSensitivity.HIGHLY_CONFIDENTIAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
    ),
    DicRouteSpec(
        "employees.documents",
        "/it/app/employees/info/{employee_id}/documents/list",
        ("documents.container",),
        ("documents.rows", "documents.search"),
        ("documents", "document_categories", "document_attachments"),
        ("read", "write"),
        RouteSensitivity.HIGHLY_CONFIDENTIAL,
        30_000,
        "resource_circuit_then_fail",
        RouteVerificationState.LIVE_READ_VERIFIED,
    ),
)


class DicRouteRegistry:
    def __init__(self, routes: tuple[DicRouteSpec, ...] = _ROUTES) -> None:
        by_name: dict[str, DicRouteSpec] = {}
        by_template: dict[str, DicRouteSpec] = {}
        for route in routes:
            if route.name in by_name or route.template in by_template:
                raise DicConfigurationError("duplicate DIC route registration")
            by_name[route.name] = route
            by_template[route.template] = route
        self._by_name: Mapping[str, DicRouteSpec] = MappingProxyType(by_name)
        self._by_template: Mapping[str, DicRouteSpec] = MappingProxyType(by_template)

    def get(self, name: str) -> DicRouteSpec:
        try:
            return self._by_name[name]
        except KeyError:
            raise DicValidationError("DIC route is not authorized") from None

    def for_template(self, template: str) -> DicRouteSpec:
        try:
            return self._by_template[template]
        except KeyError:
            raise DicConfigurationError("page route is absent from the DIC registry") from None

    def snapshot(self) -> tuple[DicRouteSpec, ...]:
        return tuple(self._by_name[name] for name in sorted(self._by_name))


DIC_ROUTES = DicRouteRegistry()
