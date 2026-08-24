from __future__ import annotations

import pytest

from bh_dic.dic.errors import DicConfigurationError, DicValidationError
from bh_dic.dic.route_registry import (
    DIC_ROUTES,
    DicRouteRegistry,
    DicRouteSpec,
    RouteSensitivity,
    RouteVerificationState,
)


def test_registry_contains_every_current_page_template_once() -> None:
    routes = DIC_ROUTES.snapshot()
    assert len(routes) == 10
    assert len({route.name for route in routes}) == len(routes)
    assert len({route.template for route in routes}) == len(routes)
    assert DIC_ROUTES.get("employees.payrolls").verification is (
        RouteVerificationState.LIVE_READ_VERIFIED
    )
    assert DIC_ROUTES.get("employees.contracts").verification is (
        RouteVerificationState.LIVE_READ_VERIFIED
    )
    assert DIC_ROUTES.get("employees.balances").verification is (
        RouteVerificationState.LIVE_READ_VERIFIED
    )
    assert DIC_ROUTES.get("employees.documents").verification is (
        RouteVerificationState.LIVE_READ_VERIFIED
    )


def test_registry_renders_only_validated_employee_identifiers() -> None:
    route = DIC_ROUTES.get("employees.summary")
    assert route.render(employee_id="EMP-SYNTH-001") == (
        "/it/app/employees/info/EMP-SYNTH-001/summary"
    )
    for invalid in (None, "", "../admin", "employee/id", "x?admin=true"):
        with pytest.raises(DicValidationError):
            route.render(employee_id=invalid)

    with pytest.raises(DicValidationError):
        DIC_ROUTES.get("employees.list").render(employee_id="EMP-SYNTH-001")


def test_registry_rejects_unknown_and_duplicate_routes() -> None:
    with pytest.raises(DicValidationError, match="not authorized"):
        DIC_ROUTES.get("employees.arbitrary")
    with pytest.raises(DicConfigurationError, match="absent"):
        DIC_ROUTES.for_template("/it/app/arbitrary")

    duplicate = DicRouteSpec(
        "employees.synthetic",
        "/it/app/employees/synthetic",
        (),
        (),
        ("synthetic",),
        ("read",),
        RouteSensitivity.INTERNAL_HR,
        5_000,
        "resource_circuit_then_fail",
        RouteVerificationState.NEEDS_VALIDATION,
    )
    with pytest.raises(DicConfigurationError, match="duplicate"):
        DicRouteRegistry((duplicate, duplicate))


def test_route_spec_rejects_arbitrary_parameters_and_bad_fingerprints() -> None:
    with pytest.raises(DicConfigurationError, match="parameter"):
        DicRouteSpec(
            "employees.bad",
            "/it/app/employees/{arbitrary}",
            (),
            (),
            ("bad",),
            ("read",),
            RouteSensitivity.INTERNAL_HR,
            5_000,
            "resource_circuit_then_fail",
            RouteVerificationState.NEEDS_VALIDATION,
        )
    with pytest.raises(DicConfigurationError, match="fingerprint"):
        DicRouteSpec(
            "employees.bad_fingerprint",
            "/it/app/employees/bad-fingerprint",
            (),
            (),
            ("bad",),
            ("read",),
            RouteSensitivity.INTERNAL_HR,
            5_000,
            "resource_circuit_then_fail",
            RouteVerificationState.NEEDS_VALIDATION,
            ("not-a-fingerprint",),
        )
