"""Locally narrow model routing to a small, closed function family.

The provider receives an already minimized bag of canonical HR terms.  It must never choose
among the whole DIC catalog when the local vocabulary already identifies the resource family:
large tool catalogs make routing less reliable and unnecessarily expose write candidates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderRoutingScope:
    """One bounded model-routing scope, containing no user or employee data."""

    family: str
    function_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class SafeRoutingFallback:
    """A deterministic read or clarification allowed after a provider tool failure."""

    function_id: str
    clarification: str | None = None


_READ_FAMILIES: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (
    ("payroll", frozenset({"payroll"}), frozenset({"EMP-PAY-001", "EMP-PAY-002"})),
    ("documents", frozenset({"documents"}), frozenset({"EMP-DOC-001"})),
    ("contracts", frozenset({"employment_contract"}), frozenset({"EMP-CONTRACT-001"})),
    ("roles", frozenset({"access_roles"}), frozenset({"EMP-RBAC-001"})),
    ("time_access", frozenset({"time_access"}), frozenset({"EMP-TIME-001"})),
    ("maturations", frozenset({"maturations"}), frozenset({"EMP-MAT-001"})),
    ("balances", frozenset({"balances"}), frozenset({"EMP-BAL-001"})),
    ("notifications", frozenset({"notifications"}), frozenset({"EMP-NOTIF-001"})),
    ("employee_search", frozenset({"employee_search"}), frozenset({"EMP-SEARCH-001"})),
    ("employee_filter", frozenset({"employee_filter"}), frozenset({"EMP-FILTER-001"})),
    ("employee_sort", frozenset({"employee_sort"}), frozenset({"EMP-SORT-001"})),
    ("employee_page", frozenset({"employee_page"}), frozenset({"EMP-PAGE-001"})),
    ("employee_summary", frozenset({"employee_summary"}), frozenset({"EMP-READ-002"})),
    ("headcount", frozenset({"employee_headcount"}), frozenset({"EMP-READ-001"})),
)

_SAFE_GENERIC_EMPLOYEE_READS = frozenset({"EMP-READ-001", "EMP-READ-002", "EMP-SEARCH-001"})
_WRITE_MARKERS = frozenset(
    {
        "upload_document",
        "download_document",
        "create_action",
        "update_action",
        "delete_action",
        "approve_action",
        "reject_action",
        "export_action",
        "account_connection",
        "invitation",
    }
)


def narrow_provider_routing_scope(
    minimized_request: str,
    visible_function_ids: frozenset[str],
) -> ProviderRoutingScope:
    """Return at most three locally relevant and policy-visible Function IDs.

    The input is the canonical, identity-free representation produced by
    :func:`minimize_hr_router_request`.  Exact write semantics stay in the deterministic local
    parser.  This fallback therefore exposes read families only; an unrecognized request gets
    the small generic employee-read family rather than every enabled write and DIC resource.
    """

    terms = frozenset(minimized_request.split())
    selected: set[str] = set()
    families: list[str] = []
    for family, markers, function_ids in _READ_FAMILIES:
        if terms.isdisjoint(markers):
            continue
        families.append(family)
        selected.update(function_ids)

    if not selected and "employee_records" in terms:
        families.append("employees")
        selected.update(_SAFE_GENERIC_EMPLOYEE_READS)
    if not selected:
        families.append("generic_employee_read")
        selected.update(_SAFE_GENERIC_EMPLOYEE_READS)

    allowed = frozenset(sorted(selected.intersection(visible_function_ids))[:3])
    return ProviderRoutingScope(
        family="+".join(families[:3]),
        function_ids=allowed,
    )


def safe_provider_failure_fallback(
    scope: ProviderRoutingScope,
    minimized_request: str,
) -> SafeRoutingFallback | None:
    """Recover only closed read families; never infer a mutation after provider failure."""

    terms = frozenset(minimized_request.split())
    if not terms.isdisjoint(_WRITE_MARKERS):
        return None
    functions = scope.function_ids
    if functions and functions.issubset({"EMP-PAY-001", "EMP-PAY-002"}):
        return SafeRoutingFallback(
            "EMP-PAY-001",
            "Indica il nome o l'Employee ID e il mese della busta paga da consultare.",
        )
    target_reads = {
        "EMP-READ-002": "Indica il nome o l'Employee ID del dipendente.",
        "EMP-RBAC-001": "Indica il nome o l'Employee ID del dipendente.",
        "EMP-TIME-001": "Indica il nome o l'Employee ID del dipendente.",
        "EMP-MAT-001": "Indica il nome o l'Employee ID del dipendente.",
        "EMP-BAL-001": "Indica il nome o l'Employee ID del dipendente e l'anno.",
        "EMP-DOC-001": "Indica il nome o l'Employee ID del dipendente.",
    }
    if len(functions) == 1:
        function_id = next(iter(functions))
        if function_id in target_reads:
            return SafeRoutingFallback(function_id, target_reads[function_id])
        if function_id == "EMP-NOTIF-001":
            return SafeRoutingFallback(function_id)
    return None


__all__ = [
    "ProviderRoutingScope",
    "SafeRoutingFallback",
    "narrow_provider_routing_scope",
    "safe_provider_failure_fallback",
]
