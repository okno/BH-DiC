from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from bh_dic.openai.redaction import prepare_provider_input
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, Sensitivity
from bh_dic.query.decision import (
    ClarificationDecision,
    PlanningDecision,
    ReadPlanDecision,
    SingleIntentDecision,
    UnsupportedDecision,
)
from bh_dic.query.execution import QueryPlanExecutionError, registered_read_steps
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
        "Qual è lo stipendio netto di Nora del mese di luglio?",
        today=TODAY,
    )
    assert planned is not None
    assert planned.plan.resources == (HRResource.PAYROLLS,)
    assert planned.plan.entity_resolution is EntityResolutionMode.LOCAL_SEARCH
    assert planned.plan.target_entities == ("EMPLOYEE_TARGET_1",)
    assert planned.plan.steps[0].function_id == "EMP-PAY-001"
    assert planned.legacy_intent is not None
    assert planned.legacy_intent.target_query == "Nora"
    assert planned.legacy_intent.envelope.parameters == {
        "year": 2026,
        "month": 7,
        "include_net": True,
    }


def test_local_planner_understands_colloquial_payroll_without_provider() -> None:
    planned = build_local_hr_query_plan("Quanto ha preso Nora a giugno?", today=TODAY)

    assert planned is not None
    assert planned.plan.resources == (HRResource.PAYROLLS,)
    assert planned.plan.entity_resolution is EntityResolutionMode.LOCAL_SEARCH
    assert planned.legacy_intent is not None
    assert planned.legacy_intent.target_query == "Nora"
    assert planned.legacy_intent.envelope.parameters == {
        "year": 2026,
        "month": 6,
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


def test_workforce_contract_payroll_table_is_a_complete_local_plan() -> None:
    planned = build_local_hr_query_plan(
        "Stampa una tabella con nomi, cognomi, ID, tipologia e scadenza contratto e netto "
        "mensile di tutti i dipendenti",
        today=TODAY,
    )

    assert planned is not None
    assert planned.legacy_intent is None
    assert planned.plan.intent == "workforce_contract_payroll_table"
    assert planned.plan.aggregation == "latest_paid"
    assert [step.function_id for step in planned.plan.steps] == [
        "EMP-READ-001",
        "EMP-CONTRACT-001",
        "EMP-PAY-001",
    ]


def test_complete_employee_dossier_declares_every_registered_target_read() -> None:
    planned = build_local_hr_query_plan(
        "Fammi il dossier HR completo di Nora",
        today=TODAY,
    )

    assert planned is not None
    assert planned.plan.intent == "employee_hr_dossier"
    assert planned.plan.target_entities == ("EMPLOYEE_TARGET_1",)
    assert [step.function_id for step in planned.plan.steps] == [
        "EMP-READ-002",
        "EMP-CONTRACT-001",
        "EMP-RBAC-001",
        "EMP-TIME-001",
        "EMP-MAT-001",
        "EMP-BAL-001",
        "EMP-PAY-001",
        "EMP-DOC-001",
    ]
    assert planned.legacy_intent is not None
    assert planned.legacy_intent.target_query == "Nora"
    assert planned.legacy_intent.envelope.parameters == {
        "query_plan": "employee_hr_dossier",
        "year": 2026,
    }


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


def test_planning_decision_is_a_strict_discriminated_contract() -> None:
    intent = IntentEnvelope(
        intent="employee_count",
        function_id="EMP-READ-001",
        action_class=ActionClass.READ,
        sensitivity=Sensitivity.LOW,
        confidence=1.0,
    )
    single = PlanningDecision.model_validate({"kind": "single_intent", "intent": intent})
    clarification = PlanningDecision.model_validate(
        {"kind": "clarification", "question": "Quale periodo devo consultare?"}
    )
    unsupported = PlanningDecision.model_validate(
        {
            "kind": "unsupported",
            "code": "outside_catalog",
            "reason": "La funzione non appartiene al catalogo autorizzato.",
        }
    )

    assert isinstance(single.root, SingleIntentDecision)
    assert isinstance(clarification.root, ClarificationDecision)
    assert isinstance(unsupported.root, UnsupportedDecision)
    assert single.model_dump() == {"kind": "single_intent", "intent": intent.model_dump()}

    with pytest.raises(ValidationError):
        PlanningDecision.model_validate(
            {
                "kind": "clarification",
                "question": "Quale dipendente?",
                "intent": intent,
            }
        )
    with pytest.raises(ValidationError):
        PlanningDecision.model_validate(
            {
                "kind": "unsupported",
                "code": "INVALID CODE",
                "reason": "Non eseguibile.",
            }
        )


def test_planning_decision_accepts_only_non_clarifying_read_plans() -> None:
    planned = build_local_hr_query_plan(
        "Stampa una tabella con nomi, contratti e netto mensile di tutti i dipendenti",
        today=TODAY,
    )
    assert planned is not None

    decision = PlanningDecision.model_validate({"kind": "read_plan", "plan": planned.plan})

    assert isinstance(decision.root, ReadPlanDecision)
    assert decision.root.plan.intent == "workforce_contract_payroll_table"


def test_read_registry_binds_functions_to_exact_resources_and_rejects_local_ocr() -> None:
    planned = build_local_hr_query_plan(
        "Stampa una tabella con nomi, contratti e netto mensile di tutti i dipendenti",
        today=TODAY,
    )
    assert planned is not None
    bindings = registered_read_steps(planned.plan)
    assert [binding.service_method.value for binding in bindings] == [
        "list_employees",
        "get_contracts",
        "get_payroll_metadata",
    ]

    mismatched = HRQueryPlan(
        intent="synthetic_read",
        resources=(HRResource.EMPLOYEES,),
        sensitivity=Sensitivity.LOW,
        steps=(
            HRQueryStep(
                step_id="step_1",
                function_id="EMP-PAY-001",
                resource=HRResource.EMPLOYEES,
                action=HRQueryAction.READ,
                target_entity="EMPLOYEE_TARGET_1",
            ),
        ),
        target_entities=("EMPLOYEE_TARGET_1",),
    )
    with pytest.raises(QueryPlanExecutionError, match="resource"):
        registered_read_steps(mismatched)

    local_ocr = HRQueryPlan(
        intent="synthetic_ocr",
        resources=(HRResource.EMPLOYEES,),
        sensitivity=Sensitivity.HIGH,
        steps=(
            HRQueryStep(
                step_id="step_1",
                function_id="EMP-ONBOARD-001",
                resource=HRResource.EMPLOYEES,
                action=HRQueryAction.READ,
            ),
        ),
    )
    with pytest.raises(QueryPlanExecutionError, match="no registered read executor"):
        registered_read_steps(local_ocr)


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
    rows.extend(("payroll_target", f"Qual è il netto di Nora per {month}?") for month in months)
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
