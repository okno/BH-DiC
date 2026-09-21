from __future__ import annotations

import pytest

from bh_dic.hr_assistant import minimize_hr_router_request
from bh_dic.query.routing_scope import (
    narrow_provider_routing_scope,
    safe_provider_failure_fallback,
)

VISIBLE = frozenset(
    {
        "EMP-READ-001",
        "EMP-READ-002",
        "EMP-SEARCH-001",
        "EMP-FILTER-001",
        "EMP-SORT-001",
        "EMP-PAGE-001",
        "EMP-CONTRACT-001",
        "EMP-RBAC-001",
        "EMP-TIME-001",
        "EMP-MAT-001",
        "EMP-BAL-001",
        "EMP-PAY-001",
        "EMP-PAY-002",
        "EMP-NOTIF-001",
        "EMP-DOC-001",
        "EMP-UPDATE-001",
        "EMP-DELETE-001",
    }
)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Mostra i cedolini disponibili", {"EMP-PAY-001", "EMP-PAY-002"}),
        ("Apri la sezione documenti", {"EMP-DOC-001"}),
        ("Controlla i contratti", {"EMP-CONTRACT-001"}),
        ("Quali permessi ha il personale?", {"EMP-RBAC-001"}),
        ("Verifica le timbrature", {"EMP-TIME-001"}),
        ("Mostra maturazioni e ratei", {"EMP-MAT-001"}),
        ("Dammi il saldo ferie", {"EMP-BAL-001"}),
        ("Leggi le notifiche", {"EMP-NOTIF-001"}),
        ("Quanti dipendenti ci sono?", {"EMP-READ-001"}),
    ],
)
def test_provider_scope_exposes_only_the_relevant_read_family(
    utterance: str,
    expected: set[str],
) -> None:
    minimized, _ = minimize_hr_router_request(utterance)

    scope = narrow_provider_routing_scope(minimized, VISIBLE)

    assert scope.function_ids == frozenset(expected)
    assert len(scope.function_ids) <= 3
    assert "EMP-UPDATE-001" not in scope.function_ids
    assert "EMP-DELETE-001" not in scope.function_ids


def test_provider_scope_intersects_policy_visibility() -> None:
    minimized, _ = minimize_hr_router_request("Mostra i cedolini disponibili")

    scope = narrow_provider_routing_scope(minimized, frozenset({"EMP-PAY-002"}))

    assert scope.function_ids == frozenset({"EMP-PAY-002"})


def test_unknown_request_never_reopens_the_full_catalog() -> None:
    minimized, _ = minimize_hr_router_request("Analizza la situazione del personale")

    scope = narrow_provider_routing_scope(minimized, VISIBLE)

    assert scope.family == "generic_employee_read"
    assert scope.function_ids == frozenset({"EMP-READ-001", "EMP-READ-002", "EMP-SEARCH-001"})


def test_payroll_tool_failure_has_a_safe_local_clarification() -> None:
    minimized, _ = minimize_hr_router_request("Mostra i cedolini disponibili")
    scope = narrow_provider_routing_scope(minimized, VISIBLE)

    fallback = safe_provider_failure_fallback(scope, minimized)

    assert fallback is not None
    assert fallback.function_id == "EMP-PAY-001"
    assert fallback.clarification is not None


def test_write_markers_never_receive_a_local_failure_fallback() -> None:
    minimized, _ = minimize_hr_router_request("Elimina i documenti del dipendente")
    scope = narrow_provider_routing_scope(minimized, VISIBLE)

    assert safe_provider_failure_fallback(scope, minimized) is None
