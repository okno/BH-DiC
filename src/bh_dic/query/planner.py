"""Deterministic high-confidence planner that never receives DIC results."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from bh_dic.hr_assistant import LocalOperationalIntent, parse_local_operational_intent
from bh_dic.openai.schemas import Sensitivity
from bh_dic.query.plan import (
    DeliveryMode,
    EntityResolutionMode,
    FilterOperator,
    HRDateRange,
    HRFilter,
    HRPagination,
    HRQueryAction,
    HRQueryPlan,
    HRQueryStep,
    HRResource,
    OutputFormat,
)

_CONTRACT = re.compile(r"(?i)\bcontratt\w*\b")
_PAYROLL = re.compile(r"(?i)\b(?:bust[ae]\s+pag[ae]|cedolin\w*|payroll)\b")
_MISSING = re.compile(r"(?i)\b(?:senza|non\s+(?:ha|hanno|risulta|risultano))\b")
_NEXT_DAYS = re.compile(r"(?i)\bprossim[ei]\s+(?P<days>[1-9][0-9]{0,2})\s+giorn[oi]\b")
_GROUP = re.compile(r"(?i)\b(?:reparto|gruppo|team)\s+(?P<group>[\wÀ-ÿ'-]{2,64})\b")
_WORKFORCE_TABLE = re.compile(r"(?i)\b(?:tabella|elenc\w*|lista|stampa\w*|mostra\w*|riporta\w*)\b")
_NET_PAY = re.compile(
    r"(?i)\b(?:nett[oi]?\s+mensil[ei]|stipendi\w*\s+nett\w*|nett[oi]?|"
    r"bust[ae]\s+pag[ae]|cedolin\w*)\b"
)
_COMPLETE_EMPLOYEE_DOSSIER = re.compile(
    r"(?i)\b(?:dossier(?:\s+hr)?(?:\s+complet[oa])?|tutti\s+i\s+dati|"
    r"tutte\s+le\s+informazioni(?:\s+disponibili)?|"
    r"tutto\s+quello\s+che\s+sai)\b"
)
_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}


@dataclass(frozen=True, slots=True)
class LocalPlannedRequest:
    plan: HRQueryPlan
    legacy_intent: LocalOperationalIntent | None = None


def _payroll_period(request: str, today: date) -> tuple[int, int] | None:
    folded = request.casefold()
    for label, month in _MONTHS.items():
        if re.search(rf"\b{label}\b", folded):
            explicit_year = re.search(r"\b(20[0-9]{2}|21[0-9]{2}|2200)\b", folded)
            year = int(explicit_year.group(1)) if explicit_year else today.year
            if not explicit_year and month > today.month:
                year -= 1
            return year, month
    return None


def _compound_contract_payroll_plan(request: str, today: date) -> HRQueryPlan | None:
    if _CONTRACT.search(request) is None or _PAYROLL.search(request) is None:
        return None
    days_match = _NEXT_DAYS.search(request)
    period = _payroll_period(request, today)
    if days_match is None or period is None:
        return None
    days = int(days_match.group("days"))
    group_match = _GROUP.search(request)
    contract_filter = HRFilter(
        field="contract_end_date",
        operator=FilterOperator.BETWEEN,
        value=[today.isoformat(), (today + timedelta(days=days)).isoformat()],
    )
    payroll_filter = HRFilter(
        field="payroll",
        operator=(FilterOperator.NOT_EXISTS if _MISSING.search(request) else FilterOperator.EXISTS),
    )
    payroll_month_filter = HRFilter(
        field="payroll_month", operator=FilterOperator.EQ, value=period[1]
    )
    payroll_year_filter = HRFilter(
        field="payroll_year", operator=FilterOperator.EQ, value=period[0]
    )
    filters: list[HRFilter] = [
        contract_filter,
        payroll_filter,
        payroll_month_filter,
        payroll_year_filter,
    ]
    if group_match is not None:
        filters.append(
            HRFilter(
                field="group",
                operator=FilterOperator.EQ,
                value=group_match.group("group").casefold(),
            )
        )
    date_to = today + timedelta(days=days)
    return HRQueryPlan(
        intent="contract_expiry_payroll_comparison",
        resources=(HRResource.EMPLOYEES, HRResource.CONTRACTS, HRResource.PAYROLLS),
        filters=tuple(filters),
        date_range=HRDateRange(
            date_from=today,
            date_to=date_to,
            label=f"prossimi {days} giorni",
        ),
        joins=("employees.contracts", "employees.payrolls"),
        projection=("employee_id", "display_name", "contract_end_date", "payroll_available"),
        pagination=HRPagination(page=1, page_size=25, require_complete=True),
        delivery_mode=DeliveryMode.EPHEMERAL,
        sensitivity=Sensitivity.HIGH,
        steps=(
            HRQueryStep(
                step_id="step_1",
                function_id="EMP-READ-001",
                resource=HRResource.EMPLOYEES,
                action=HRQueryAction.READ,
            ),
            HRQueryStep(
                step_id="step_2",
                function_id="EMP-CONTRACT-001",
                resource=HRResource.CONTRACTS,
                action=HRQueryAction.FILTER,
                depends_on=("step_1",),
                filters=(contract_filter,),
            ),
            HRQueryStep(
                step_id="step_3",
                function_id="EMP-PAY-002",
                resource=HRResource.PAYROLLS,
                action=HRQueryAction.JOIN,
                depends_on=("step_2",),
                filters=(payroll_filter, payroll_month_filter, payroll_year_filter),
            ),
        ),
    )


def _workforce_contract_payroll_table(request: str, today: date) -> HRQueryPlan | None:
    """Plan the complete management table from three verified DIC resources."""

    if (
        _WORKFORCE_TABLE.search(request) is None
        or _CONTRACT.search(request) is None
        or _NET_PAY.search(request) is None
    ):
        return None
    period = _payroll_period(request, today)
    filters: tuple[HRFilter, ...] = ()
    aggregation = "latest_paid"
    if period is not None:
        filters = (
            HRFilter(field="payroll_year", operator=FilterOperator.EQ, value=period[0]),
            HRFilter(field="payroll_month", operator=FilterOperator.EQ, value=period[1]),
        )
        aggregation = "selected_period"
    return HRQueryPlan(
        intent="workforce_contract_payroll_table",
        resources=(HRResource.EMPLOYEES, HRResource.CONTRACTS, HRResource.PAYROLLS),
        filters=filters,
        joins=("employees.contracts", "employees.payrolls"),
        projection=(
            "first_name",
            "last_name",
            "employee_id",
            "employee_state",
            "contract_end_date",
            "contract_type",
            "monthly_net",
        ),
        aggregation=aggregation,
        pagination=HRPagination(page=1, page_size=100, require_complete=True),
        output_format=OutputFormat.TABLE,
        delivery_mode=DeliveryMode.EPHEMERAL,
        sensitivity=Sensitivity.HIGH,
        steps=(
            HRQueryStep(
                step_id="step_1",
                function_id="EMP-READ-001",
                resource=HRResource.EMPLOYEES,
                action=HRQueryAction.READ,
            ),
            HRQueryStep(
                step_id="step_2",
                function_id="EMP-CONTRACT-001",
                resource=HRResource.CONTRACTS,
                action=HRQueryAction.JOIN,
                depends_on=("step_1",),
            ),
            HRQueryStep(
                step_id="step_3",
                function_id="EMP-PAY-001",
                resource=HRResource.PAYROLLS,
                action=HRQueryAction.JOIN,
                depends_on=("step_1",),
                filters=filters,
                aggregation=aggregation,
            ),
        ),
    )


def _employee_hr_dossier(
    request: str,
    today: date,
) -> LocalPlannedRequest | None:
    """Plan all registered per-employee read surfaces without exposing the target."""

    if _COMPLETE_EMPLOYEE_DOSSIER.search(request) is None:
        return None
    legacy = parse_local_operational_intent(request, today=today)
    if legacy is None or legacy.envelope.function_id != "EMP-READ-002":
        return None
    legacy = LocalOperationalIntent(
        legacy.envelope.model_copy(
            update={
                "parameters": {
                    "query_plan": "employee_hr_dossier",
                    "year": today.year,
                }
            }
        ),
        target_query=legacy.target_query,
    )
    target = "EMPLOYEE_TARGET_1"
    step_specs = (
        ("EMP-READ-002", HRResource.EMPLOYEE_SUMMARY),
        ("EMP-CONTRACT-001", HRResource.CONTRACTS),
        ("EMP-RBAC-001", HRResource.ROLES),
        ("EMP-TIME-001", HRResource.TIME_ACCESS),
        ("EMP-MAT-001", HRResource.MATURATIONS),
        ("EMP-BAL-001", HRResource.BALANCES),
        ("EMP-PAY-001", HRResource.PAYROLLS),
        ("EMP-DOC-001", HRResource.DOCUMENTS),
    )
    return LocalPlannedRequest(
        HRQueryPlan(
            intent="employee_hr_dossier",
            resources=tuple(resource for _, resource in step_specs),
            target_entities=(target,),
            entity_resolution=EntityResolutionMode.LOCAL_SEARCH,
            projection=("all_authorized_registered_employee_sections",),
            pagination=HRPagination(page=1, page_size=25, require_complete=True),
            output_format=OutputFormat.TABLE,
            delivery_mode=DeliveryMode.EPHEMERAL,
            sensitivity=Sensitivity.HIGH,
            clarification_required=legacy.envelope.requires_clarification,
            clarification_question=legacy.envelope.clarification_question,
            steps=tuple(
                HRQueryStep(
                    step_id=f"step_{index}",
                    function_id=function_id,
                    resource=resource,
                    action=HRQueryAction.READ,
                    depends_on=("step_1",) if index > 1 else (),
                    target_entity=target,
                )
                for index, (function_id, resource) in enumerate(step_specs, start=1)
            ),
        ),
        legacy,
    )


_FUNCTION_RESOURCE = {
    "EMP-READ-001": HRResource.EMPLOYEES,
    "EMP-READ-002": HRResource.EMPLOYEE_SUMMARY,
    "EMP-SEARCH-001": HRResource.EMPLOYEES,
    "EMP-FILTER-001": HRResource.EMPLOYEES,
    "EMP-SORT-001": HRResource.EMPLOYEES,
    "EMP-PAGE-001": HRResource.EMPLOYEES,
    "EMP-CONTRACT-001": HRResource.CONTRACTS,
    "EMP-RBAC-001": HRResource.ROLES,
    "EMP-TIME-001": HRResource.TIME_ACCESS,
    "EMP-MAT-001": HRResource.MATURATIONS,
    "EMP-BAL-001": HRResource.BALANCES,
    "EMP-PAY-001": HRResource.PAYROLLS,
    "EMP-PAY-002": HRResource.PAYROLLS,
    "EMP-DOC-001": HRResource.DOCUMENTS,
}


def build_local_hr_query_plan(request: str, *, today: date) -> LocalPlannedRequest | None:
    compound = _compound_contract_payroll_plan(request, today)
    if compound is not None:
        return LocalPlannedRequest(compound)
    workforce_table = _workforce_contract_payroll_table(request, today)
    if workforce_table is not None:
        return LocalPlannedRequest(workforce_table)
    dossier = _employee_hr_dossier(request, today)
    if dossier is not None:
        return dossier
    legacy = parse_local_operational_intent(request, today=today)
    if legacy is None or legacy.envelope.function_id not in _FUNCTION_RESOURCE:
        return None
    resource = _FUNCTION_RESOURCE[legacy.envelope.function_id]
    target_entities = ("EMPLOYEE_TARGET_1",) if legacy.target_query else ()
    resolution = (
        EntityResolutionMode.LOCAL_SEARCH if legacy.target_query else EntityResolutionMode.NONE
    )
    return LocalPlannedRequest(
        HRQueryPlan(
            intent=legacy.envelope.intent,
            resources=(resource,),
            target_entities=target_entities,
            entity_resolution=resolution,
            pagination=HRPagination(),
            delivery_mode=DeliveryMode.EPHEMERAL,
            sensitivity=legacy.envelope.sensitivity,
            clarification_required=legacy.envelope.requires_clarification,
            clarification_question=legacy.envelope.clarification_question,
            steps=(
                HRQueryStep(
                    step_id="step_1",
                    function_id=legacy.envelope.function_id,
                    resource=resource,
                    action=HRQueryAction.READ,
                    target_entity=target_entities[0] if target_entities else None,
                ),
            ),
        ),
        legacy,
    )


__all__ = ["LocalPlannedRequest", "build_local_hr_query_plan"]
