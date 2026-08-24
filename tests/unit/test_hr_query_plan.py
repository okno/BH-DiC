from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from bh_dic.openai.redaction import prepare_provider_input
from bh_dic.openai.schemas import Sensitivity
from bh_dic.query.plan import (
    DeliveryMode,
    EntityResolutionMode,
    HRQueryAction,
    HRQueryPlan,
    HRQueryStep,
    HRResource,
)
from bh_dic.query.planner import build_local_hr_query_plan

TODAY = date(2026, 8, 24)


def test_local_planner_builds_payroll_entity_resolution_without_provider() -> None:
    planned = build_local_hr_query_plan(
        "Qual è lo stipendio netto di Amin del mese di luglio?",
        today=TODAY,
    )
    assert planned is not None
    assert planned.plan.resources == (HRResource.PAYROLLS,)
    assert planned.plan.entity_resolution is EntityResolutionMode.LOCAL_SEARCH
    assert planned.plan.target_entities == ("EMPLOYEE_TARGET_1",)
    assert planned.plan.steps[0].function_id == "EMP-PAY-001"
    assert planned.legacy_intent is not None
    assert planned.legacy_intent.target_query == "Amin"
    assert planned.legacy_intent.envelope.parameters == {
        "year": 2026,
        "month": 7,
        "include_net": True,
    }


def test_compound_plan_has_ordered_read_only_steps_and_local_dates() -> None:
    planned = build_local_hr_query_plan(
        "Mostrami i dipendenti del reparto sala con contratto in scadenza nei prossimi "
        "90 giorni e indicami chi non ha una busta paga a luglio.",
        today=TODAY,
    )
    assert planned is not None
    plan = planned.plan
    assert [step.function_id for step in plan.steps] == [
        "EMP-READ-001",
        "EMP-CONTRACT-001",
        "EMP-PAY-002",
    ]
    assert plan.steps[1].depends_on == ("step_1",)
    assert plan.steps[2].depends_on == ("step_2",)
    assert plan.date_range is not None
    assert plan.date_range.date_from == TODAY
    assert plan.date_range.date_to == date(2026, 11, 22)
    assert {item.field: item.value for item in plan.filters}["group"] == "sala"
    assert plan.delivery_mode is DeliveryMode.EPHEMERAL
    assert plan.sensitivity is Sensitivity.HIGH


@pytest.mark.parametrize(
    ("utterance", "function_id", "target_query"),
    [
        ("Mostrami i documenti del dipendente Test A.", "EMP-DOC-001", "Test A"),
        ("Che ruoli ha il dipendente Test A?", "EMP-RBAC-001", "Test A"),
        ("Verifica la timbratura del dipendente Test A", "EMP-TIME-001", "Test A"),
        ("Bilancio 2025 del dipendente Test A", "EMP-BAL-001", "Test A"),
        ("Maturazioni del dipendente Test A", "EMP-MAT-001", "Test A"),
        ("Fammi vedere tutti i dati del dipendente Test A", "EMP-READ-002", "Test A"),
    ],
)
def test_daily_employee_resources_are_planned_locally(
    utterance: str, function_id: str, target_query: str
) -> None:
    planned = build_local_hr_query_plan(utterance, today=TODAY)
    assert planned is not None
    assert planned.plan.steps[0].function_id == function_id
    assert planned.plan.entity_resolution is EntityResolutionMode.LOCAL_SEARCH
    assert planned.legacy_intent is not None
    assert planned.legacy_intent.target_query == target_query


def test_contract_relative_period_is_local_and_does_not_require_an_employee() -> None:
    planned = build_local_hr_query_plan(
        "Quali contratti scadono nei prossimi tre mesi?", today=TODAY
    )
    assert planned is not None
    assert planned.plan.steps[0].function_id == "EMP-CONTRACT-001"
    assert planned.plan.clarification_required is False
    assert planned.legacy_intent is not None
    assert planned.legacy_intent.envelope.date_from == TODAY
    assert planned.legacy_intent.envelope.date_to == date(2026, 11, 24)


def test_plan_rejects_writes_real_entity_values_and_unordered_dependencies() -> None:
    base = {
        "intent": "synthetic_read",
        "resources": (HRResource.EMPLOYEES,),
        "sensitivity": Sensitivity.LOW,
    }
    with pytest.raises(ValidationError, match="read functions"):
        HRQueryPlan(
            **base,
            steps=(
                HRQueryStep(
                    step_id="step_1",
                    function_id="EMP-STATUS-001",
                    resource=HRResource.EMPLOYEES,
                    action=HRQueryAction.READ,
                ),
            ),
        )
    with pytest.raises(ValidationError, match="opaque placeholder"):
        HRQueryPlan(
            **base,
            target_entities=("Mario Rossi",),
            steps=(
                HRQueryStep(
                    step_id="step_1",
                    function_id="EMP-READ-001",
                    resource=HRResource.EMPLOYEES,
                    action=HRQueryAction.READ,
                ),
            ),
        )
    with pytest.raises(ValidationError, match="dependencies"):
        HRQueryPlan(
            **base,
            steps=(
                HRQueryStep(
                    step_id="step_2",
                    function_id="EMP-READ-001",
                    resource=HRResource.EMPLOYEES,
                    action=HRQueryAction.READ,
                    depends_on=("step_1",),
                ),
            ),
        )


def _conversational_corpus() -> list[tuple[str, str]]:
    months = (
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    )
    rows: list[tuple[str, str]] = []
    rows.extend(
        ("count", f"{lead} il numero totale dei dipendenti")
        for lead in (
            "Dimmi",
            "Calcola",
            "Mostrami",
            "Controlla",
            "Vorrei",
            "Puoi dirmi",
            "Mi serve",
            "Recupera",
            "Verifica",
            "Stampa",
            "Indica",
            "Riporta",
        )
    )
    rows.extend(("payroll_target", f"Qual è il netto di Amin per {month}?") for month in months)
    rows.extend(
        ("payroll_presence", f"Quali dipendenti hanno una busta paga a {month}?")
        for month in months
    )
    rows.extend(
        (
            "compound",
            f"Dipendenti del reparto sala con contratto in scadenza nei prossimi {days} "
            f"giorni e senza busta paga a luglio",
        )
        for days in range(30, 151, 10)
    )
    provider_categories = (
        "documents",
        "roles",
        "timestamps",
        "balances",
        "maturations",
        "contracts",
    )
    provider_templates = (
        "Mostrami {category} del dipendente indicato",
        "Controlla {category} per questa persona",
        "Vorrei vedere {category} aggiornati",
        "Recupera {category} dal portale",
        "Confronta {category} disponibili",
        "Qual è lo stato di {category}?",
        "Apri la sezione {category}",
        "Verifica se esistono {category}",
        "Riporta tutti i {category}",
        "Dammi un riepilogo di {category}",
        "Cerca eventuali {category}",
        "Esporta i {category} autorizzati",
    )
    for category in provider_categories:
        rows.extend(
            (category, template.format(category=category)) for template in provider_templates
        )
    return rows[:120]


def test_at_least_120_natural_italian_requests_cross_a_safe_planning_boundary() -> None:
    corpus = _conversational_corpus()
    assert len(corpus) == 120
    assert len({request for _, request in corpus}) == 120
    categories = {category for category, _ in corpus}
    assert {
        "count",
        "payroll_target",
        "payroll_presence",
        "compound",
        "documents",
        "roles",
        "timestamps",
        "balances",
        "maturations",
        "contracts",
    }.issubset(categories)
    for category, request in corpus:
        planned = build_local_hr_query_plan(request, today=TODAY)
        assert planned is not None, request
        assert prepare_provider_input(request)
        expected = {
            "count": "EMP-READ-001",
            "payroll_target": "EMP-PAY-001",
            "payroll_presence": "EMP-PAY-002",
            "documents": "EMP-DOC-001",
            "roles": "EMP-RBAC-001",
            "timestamps": "EMP-TIME-001",
            "balances": "EMP-BAL-001",
            "maturations": "EMP-MAT-001",
            "contracts": "EMP-CONTRACT-001",
        }
        if category == "compound":
            assert [step.function_id for step in planned.plan.steps] == [
                "EMP-READ-001",
                "EMP-CONTRACT-001",
                "EMP-PAY-002",
            ]
            assert planned.plan.clarification_required is False
        else:
            assert planned.plan.steps[0].function_id == expected[category]
            if category in {
                "documents",
                "roles",
                "timestamps",
                "balances",
                "maturations",
                "contracts",
            }:
                assert planned.plan.clarification_required is True
                assert planned.plan.delivery_mode is DeliveryMode.EPHEMERAL
