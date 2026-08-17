from __future__ import annotations

from datetime import date

import pytest

from bh_dic.hr_assistant import (
    HrRequestInputError,
    SeniorHrPresenter,
    is_employee_aggregate_request,
    local_contract_expiry_fallback_interval,
    local_employee_search_query,
    minimize_hr_router_request,
    normalize_hr_intent,
)
from bh_dic.language import BotLanguageProfile
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, Sensitivity


def _intent(function_id: str, *, parameters: dict[str, object] | None = None) -> IntentEnvelope:
    return IntentEnvelope(
        intent=function_id.lower().replace("-", "_"),
        function_id=function_id,
        action_class=ActionClass.READ,
        employee_id=None,
        query=None,
        parameters=parameters or {},
        date_from=None,
        date_to=None,
        requires_clarification=False,
        clarification_question=None,
        sensitivity=Sensitivity.LOW,
        confidence=1.0,
    )


@pytest.mark.parametrize(
    ("query_text", "expected"),
    [
        ("Dimmi il totale dei dipendenti", True),
        ("Qual e l'organico dell'hotel?", True),
        ("Elenca i dipendenti", False),
    ],
)
def test_aggregate_semantics_cover_natural_headcount_phrases(
    query_text: str, expected: bool
) -> None:
    assert is_employee_aggregate_request(query_text) is expected


@pytest.mark.parametrize(
    ("query_text", "status"),
    [
        ("Dimmi il totale dei dipendenti", "all"),
        ("Quanti dipendenti attivi ci sono?", "active"),
        ("Numero dei dipendenti disattivati", "inactive"),
    ],
)
def test_employee_count_filter_is_resolved_locally(query_text: str, status: str) -> None:
    normalized = normalize_hr_intent(
        _intent("EMP-READ-001", parameters={"status": "active"}),
        query_text,
        today=date(2026, 8, 17),
    )
    assert normalized.parameters["status"] == status


@pytest.mark.parametrize(
    ("today", "expected_from", "expected_to"),
    [
        (date(2026, 8, 17), date(2026, 9, 1), date(2026, 9, 30)),
        (date(2027, 12, 31), date(2028, 1, 1), date(2028, 1, 31)),
        (date(2028, 1, 15), date(2028, 2, 1), date(2028, 2, 29)),
    ],
)
def test_next_month_is_calendar_based_and_does_not_depend_on_provider_dates(
    today: date, expected_from: date, expected_to: date
) -> None:
    provider_candidate = _intent("EMP-CONTRACT-001").model_copy(
        update={"date_from": date(2035, 1, 1), "date_to": date(2035, 1, 31)}
    )
    normalized = normalize_hr_intent(
        provider_candidate,
        "Dimmi i dipendenti con contratto a scadenza nel prossimo mese",
        today=today,
    )
    assert normalized.date_from == expected_from
    assert normalized.date_to == expected_to


@pytest.mark.parametrize(
    ("request_text", "expected_from", "expected_to"),
    [
        (
            "Dimmi i dipendenti con contratto a scadenza nel prossimo mese",
            date(2026, 9, 1),
            date(2026, 9, 30),
        ),
        (
            "Mostra i contratti in scadenza in questo mese",
            date(2026, 8, 1),
            date(2026, 8, 31),
        ),
    ],
)
def test_contract_expiry_fallback_accepts_only_locally_supported_intervals(
    request_text: str,
    expected_from: date,
    expected_to: date,
) -> None:
    projected, _ = minimize_hr_router_request(request_text)

    interval = local_contract_expiry_fallback_interval(
        request_text,
        projected,
        today=date(2026, 8, 17),
    )

    assert interval == (expected_from, expected_to)


@pytest.mark.parametrize(
    "request_text",
    [
        "Dimmi i dipendenti nel prossimo mese",
        "Dimmi i contratti in scadenza a settembre 2026",
        "Modifica i contratti in scadenza nel prossimo mese",
        "Elimina i contratti in scadenza nel prossimo mese",
        "Dimmi i contratti in scadenza nel prossimo mese del reparto Segreto",
        "Dimmi il bilancio e i contratti in scadenza nel prossimo mese",
        "Dimmi i contratti in scadenza nei prossimi 0 giorni",
        "Dimmi i contratti in scadenza nei prossimi 30 giorni",
        "Dimmi i contratti in scadenza nei prossimi 367 giorni",
        "Dimmi i contratti in scadenza nel prossimo mese e in questo mese",
        "Non dirmi i contratti in scadenza nel prossimo mese",
        "Dimmi i dipendenti senza contratti in scadenza nel prossimo mese",
        "No, dimmi i contratti in scadenza nel prossimo mese",
    ],
)
def test_contract_expiry_fallback_rejects_ambiguous_unsupported_or_write_requests(
    request_text: str,
) -> None:
    projected, _ = minimize_hr_router_request(request_text)

    assert (
        local_contract_expiry_fallback_interval(
            request_text,
            projected,
            today=date(2026, 8, 17),
        )
        is None
    )


def test_presenter_changes_copy_without_changing_facts() -> None:
    profile = BotLanguageProfile(tone="friendly", address_style="tu", verbosity="detailed")
    title, description = SeniorHrPresenter(profile).employee_count(42, "all")
    assert title == "Organico dell'hotel"
    assert "42" in description
    assert "Dipendenti in Cloud" in description
    assert "Posso anche" in description

    default_title, default_description = SeniorHrPresenter(None).employee_count(42, "all")
    assert default_title == "Conteggio dipendenti"
    assert default_description == "Totale nel filtro richiesto: 42"


def test_router_projection_keeps_hr_intent_but_removes_names_ids_and_unknown_terms() -> None:
    projected, employee_id = minimize_hr_router_request(
        "Mostra il contratto di Mario Rossi, employee id EMP-PRIVATE-987654, reparto Segreto"
    )

    assert employee_id == "EMP-PRIVATE-987654"
    assert "employment_contract" in projected
    assert "EMP-LOCAL-REDACTED" in projected
    assert "Mario" not in projected
    assert "Rossi" not in projected
    assert "EMP-PRIVATE-987654" not in projected
    assert "Segreto" not in projected
    assert "[TERM_REDACTED]" in projected


def test_router_projection_preserves_exact_supported_example_phrases() -> None:
    total, _ = minimize_hr_router_request("Dimmi il totale dei dipendenti")
    expiries, _ = minimize_hr_router_request(
        "Dimmi i dipendenti con contratto a scadenza nel prossimo mese"
    )

    assert total.casefold() == "request employee_headcount employee_records"
    assert expiries.casefold() == (
        "request employee_records employment_contract contract_deadline next_period calendar_month"
    )


def test_employee_search_value_is_extracted_for_local_dic_use_only() -> None:
    request = "Cerca il dipendente Mario Rossi"
    projected, _ = minimize_hr_router_request(request)

    assert local_employee_search_query(request) == "Mario Rossi"
    assert "Mario" not in projected
    assert "Rossi" not in projected


@pytest.mark.parametrize("colliding_name", ["Maggio", "Marzo", "Fine", "Ruolo"])
def test_local_search_names_never_survive_vocabulary_collisions(colliding_name: str) -> None:
    request = f"Cerca il dipendente {colliding_name}"
    projected, _ = minimize_hr_router_request(request)

    assert local_employee_search_query(request) == colliding_name
    assert colliding_name.casefold() not in projected.casefold()
    assert "employee_search" in projected


@pytest.mark.parametrize("invalid_id", ["_invalid", "-invalid"])
def test_explicit_employee_id_is_validated_locally_before_routing(invalid_id: str) -> None:
    with pytest.raises(HrRequestInputError, match="invalid format"):
        minimize_hr_router_request(f"Mostra employee id {invalid_id}")


@pytest.mark.parametrize(
    ("request_text", "expected_id"),
    [
        ("Mostra il dipendente 1565", "1565"),
        ("Mostra il contratto del dipendente 101", "101"),
    ],
)
def test_numeric_employee_targets_are_restored_locally_but_never_forwarded(
    request_text: str,
    expected_id: str,
) -> None:
    projected, employee_id = minimize_hr_router_request(request_text)

    assert employee_id == expected_id
    assert expected_id not in projected
    assert "EMP-LOCAL-REDACTED" in projected


def test_unlabelled_numbers_are_redacted_from_provider_projection() -> None:
    projected, employee_id = minimize_hr_router_request("Mostra i dipendenti del gruppo 1565")

    assert employee_id is None
    assert "1565" not in projected
    assert "[TERM_REDACTED]" in projected
