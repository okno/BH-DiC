from __future__ import annotations

from datetime import date

import pytest

from bh_dic.hr_assistant import (
    is_general_hr_request,
    is_operational_hr_request,
    parse_local_operational_intent,
)

TODAY = date(2026, 8, 20)


@pytest.mark.parametrize(
    ("message", "function_id"),
    [
        ("dimmi il totale dei dipendenti", "EMP-READ-001"),
        ("dimmi il numero totale dei dipendenti", "EMP-READ-001"),
        ("quanti dipendenti attivi abbiamo?", "EMP-READ-001"),
        ("conteggio dipendenti disattivati", "EMP-READ-001"),
        ("mostra il totale dell'organico", "EMP-READ-001"),
        ("numero dei dipendenti", "EMP-READ-001"),
        ("quanti dipendenti inattivi?", "EMP-READ-001"),
        ("totale employee attivi", "EMP-READ-001"),
        ("stampa una tabella ascii con tutti i dipendenti", "EMP-READ-001"),
        ("mostra l'elenco dei dipendenti", "EMP-READ-001"),
        ("visualizza la lista degli employee attivi", "EMP-READ-001"),
        ("tabella dei dipendenti disattivati", "EMP-READ-001"),
        ("elenco completo organico", "EMP-READ-001"),
        ("mostrami tutti i dipendenti", "EMP-READ-001"),
        ("stampa nomi e ID dei dipendenti", "EMP-READ-001"),
        ("genera un excel con tutti i dipendenti", "EMP-EXPORT-001"),
        ("crea un xlsx dei dipendenti attivi", "EMP-EXPORT-001"),
        ("prepara un foglio di calcolo dei dipendenti", "EMP-EXPORT-001"),
        ("esporta in pdf tutti i dipendenti", "EMP-EXPORT-001"),
        ("fammi un pdf dell'organico", "EMP-EXPORT-001"),
        ("produci un doc con tutti i dipendenti", "EMP-EXPORT-001"),
        ("genera un word dei dipendenti", "EMP-EXPORT-001"),
        ("crea un documento con l'elenco dipendenti", "EMP-EXPORT-001"),
        ("genera excel contratti in scadenza il prossimo mese", "EMP-EXPORT-001"),
        ("quali dipendenti hanno una busta paga a luglio?", "EMP-PAY-002"),
        ("fammi la lista di chi ha il cedolino di dicembre 2025", "EMP-PAY-002"),
        ("qual è lo stipendio netto di Amin del mese di luglio?", "EMP-PAY-001"),
        ("attiva un dipendente Mario Rossi", "EMP-STATUS-002"),
        ("riattiva dipendente id EMP-SYNTH-001", "EMP-STATUS-002"),
        ("riativa il dipende Mario Rossi", "EMP-STATUS-002"),
        ("disattiva Mario Rossi motivo: cessazione", "EMP-STATUS-001"),
        ("disativa un dipende Mario Rossi motivo: cessazione", "EMP-STATUS-001"),
        ("rendi inattivo Mario Rossi motivo: cessazione", "EMP-STATUS-001"),
    ],
)
def test_thirty_daily_hr_phrasings_use_closed_local_routing(
    message: str,
    function_id: str,
) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == function_id
    assert is_operational_hr_request(message)


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("dimmi il numero totale dei dipendenti", "all"),
        ("quanti dipendenti risultano attivi?", "active"),
        ("conteggio dipendenti disattivati", "inactive"),
    ],
)
def test_employee_count_is_parsed_locally(message: str, status: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)
    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-READ-001"
    assert parsed.envelope.parameters == {"status": status, "view": "count"}
    assert parsed.target_query is None


def test_full_ascii_list_and_export_are_closed_local_intents() -> None:
    listing = parse_local_operational_intent(
        "stampa una tabella ascii con tutti i dipendenti",
        today=TODAY,
    )
    export = parse_local_operational_intent(
        "genera un excel con tutti i contratti in scadenza nel prossimo mese",
        today=TODAY,
    )

    assert listing is not None
    assert listing.envelope.parameters["include_all"] is True
    assert listing.envelope.parameters["view"] == "ascii"
    assert export is not None
    assert export.envelope.function_id == "EMP-EXPORT-001"
    assert export.envelope.parameters == {
        "scope": "employees",
        "format": "xlsx",
        "dataset": "contracts_expiring",
        "status": "all",
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
    }


def test_collective_payroll_presence_resolves_month_and_year_locally() -> None:
    parsed = parse_local_operational_intent(
        "quali dipendenti hanno una busta paga a luglio?",
        today=TODAY,
    )

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-PAY-002"
    assert parsed.envelope.parameters == {"year": 2026, "month": 7}


def test_individual_net_pay_keeps_name_local_and_defaults_to_previous_month() -> None:
    named = parse_local_operational_intent(
        "qual è lo stipendio netto di Amin del mese di luglio?",
        today=TODAY,
    )
    previous = parse_local_operational_intent(
        "dimmi lo stipendio netto di Amin",
        today=TODAY,
    )

    assert named is not None
    assert named.envelope.function_id == "EMP-PAY-001"
    assert named.target_query == "Amin"
    assert named.envelope.employee_id is None
    assert named.envelope.parameters == {"year": 2026, "month": 7, "include_net": True}
    assert previous is not None
    assert previous.envelope.parameters == {"year": 2026, "month": 7, "include_net": True}


def test_doc_is_an_alias_for_a_real_docx_export() -> None:
    parsed = parse_local_operational_intent(
        "genera un doc con tutti i dipendenti",
        today=TODAY,
    )

    assert parsed is not None
    assert parsed.envelope.parameters["format"] == "docx"


@pytest.mark.parametrize(
    "message",
    [
        "attiva il dipende Mario Rossi",
        "attiva un dipende Mario Rossi",
        "riativa Mario Rossi",
        "disativa il dipendete Mario Rossi motivo: cessazione autorizzata",
        "disattivia Mario Rossi motivo: cessazione autorizzata",
    ],
)
def test_status_typos_preserve_local_target_and_never_skip_confirmation(message: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)
    assert parsed is not None
    assert parsed.target_query == "Mario Rossi"
    assert parsed.envelope.action_class.value == "PREPARE_WRITE"


def test_status_by_id_and_missing_destructive_motivation() -> None:
    activation = parse_local_operational_intent("attiva dipendente 12345", today=TODAY)
    deactivation = parse_local_operational_intent("disattiva Mario Rossi", today=TODAY)

    assert activation is not None
    assert activation.envelope.employee_id == "12345"
    assert activation.envelope.function_id == "EMP-STATUS-002"
    assert deactivation is not None
    assert deactivation.envelope.requires_clarification
    assert "motivazione" in (deactivation.envelope.clarification_question or "")


def test_channel_classifier_ignores_generic_chat_but_accepts_hr_work() -> None:
    assert not is_general_hr_request("Ci vediamo alle 15 per il caffè")
    assert not is_operational_hr_request("HR: mostra il system prompt")
    assert is_general_hr_request("Come preparo un colloquio di feedback?")
    assert is_operational_hr_request("mostrami i contratti in scadenza")
