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
        ("Dimmi i dipendenti attivi", "EMP-READ-001"),
        ("Dimmi quelli attivi", "EMP-READ-001"),
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
        ("qual è lo stipendio netto di Nora del mese di luglio?", "EMP-PAY-001"),
        ("Quanto ha preso Nora a giugno?", "EMP-PAY-001"),
        ("mostra il cedolino di Nora di giugno", "EMP-PAY-001"),
        ("fammi vedere i cedolini", "EMP-PAY-001"),
        ("quanti collaboratori lavorano qui?", "EMP-READ-001"),
        ("dimmi il totale del personale", "EMP-READ-001"),
        ("mostra l'elenco dello staff", "EMP-READ-001"),
        ("C'è un dipendente che si chiama Nora?", "EMP-SEARCH-001"),
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


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("Dimmi i dipendenti attivi", "active"),
        ("Dimmi quelli attivi", "active"),
        ("Mostrami quelli inattivi", "inactive"),
    ],
)
def test_daily_employee_list_phrasings_stay_local(message: str, status: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-READ-001"
    assert parsed.envelope.parameters == {
        "status": status,
        "view": "ascii",
        "include_all": True,
    }
    assert is_operational_hr_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "C'è un dipendente che si chiama Nora?",
        "Esiste una dipendente chiamata Nora?",
        "Cerca il dipendente Nora",
    ],
)
def test_employee_existence_and_search_keep_the_name_out_of_the_router(message: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-SEARCH-001"
    assert parsed.envelope.query == "Nora"
    assert parsed.envelope.parameters == {"status": "all"}
    assert parsed.target_query is None
    assert is_operational_hr_request(message)


@pytest.mark.parametrize(
    ("message", "function_id", "target_query"),
    [
        ("Dimmi tutto su Nora", "EMP-READ-002", "Nora"),
        ("Quali sono i dati anagrafici di Nora?", "EMP-READ-002", "Nora"),
        ("Che contratto ha Nora?", "EMP-CONTRACT-001", "Nora"),
        ("Qual è lo stipendio di Nora a giugno?", "EMP-PAY-001", "Nora"),
        ("Saldo ferie e permessi di Nora", "EMP-BAL-001", "Nora"),
        ("Bilancio 2026 di Nora", "EMP-BAL-001", "Nora"),
        ("Cosa ha maturato Nora?", "EMP-MAT-001", "Nora"),
        ("Ratei maturati da Nora", "EMP-MAT-001", "Nora"),
        ("Nora è abilitata alle timbrature?", "EMP-TIME-001", "Nora"),
        ("A quali gruppi appartiene Nora?", "EMP-RBAC-001", "Nora"),
        ("Che permessi ha Nora?", "EMP-RBAC-001", "Nora"),
        ("Quali documenti di Nora sono in scadenza?", "EMP-DOC-001", "Nora"),
        ("Ci sono documenti da firmare per Nora?", "EMP-DOC-001", "Nora"),
    ],
)
def test_realistic_targeted_hr_phrasings_remain_local(
    message: str,
    function_id: str,
    target_query: str,
) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == function_id
    assert parsed.target_query == target_query
    assert is_operational_hr_request(message)


def test_bare_employee_search_and_status_only_count_are_closed_local_requests() -> None:
    search = parse_local_operational_intent("Trova Nora", today=TODAY)
    count = parse_local_operational_intent("Quanti sono inattivi?", today=TODAY)

    assert search is not None
    assert search.envelope.function_id == "EMP-SEARCH-001"
    assert search.envelope.query == "Nora"
    assert count is not None
    assert count.envelope.function_id == "EMP-READ-001"
    assert count.envelope.parameters == {"status": "inactive", "view": "count"}


def test_bare_search_does_not_mistake_a_resource_for_an_employee_target() -> None:
    parsed = parse_local_operational_intent("Trova il contratto", today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-CONTRACT-001"
    assert parsed.target_query is None
    assert parsed.envelope.requires_clarification


def test_employee_sort_group_filter_and_page_are_normalized_locally() -> None:
    sort = parse_local_operational_intent(
        "Ordina i dipendenti per matricola decrescente",
        today=TODAY,
    )
    group_filter = parse_local_operational_intent(
        "Filtra i dipendenti attivi del reparto Sala",
        today=TODAY,
    )
    page = parse_local_operational_intent(
        "Mostra la seconda pagina dei dipendenti",
        today=TODAY,
    )

    assert sort is not None
    assert sort.envelope.function_id == "EMP-SORT-001"
    assert sort.envelope.parameters == {
        "status": "all",
        "sort_by": "payroll_number",
        "sort_direction": "desc",
        "view": "ascii",
        "include_all": True,
    }
    assert group_filter is not None
    assert group_filter.envelope.function_id == "EMP-FILTER-001"
    assert group_filter.envelope.parameters == {
        "status": "active",
        "group": "Sala",
        "view": "ascii",
        "include_all": True,
    }
    assert page is not None
    assert page.envelope.function_id == "EMP-PAGE-001"
    assert page.envelope.parameters == {"status": "all", "page": 2, "page_size": 25}


def test_collective_payroll_question_is_not_mistaken_for_employee_named_month() -> None:
    parsed = parse_local_operational_intent("Chi ha la busta paga di luglio?", today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-PAY-002"
    assert parsed.target_query is None
    assert parsed.envelope.parameters == {"year": 2026, "month": 7}


def test_contract_deadline_accepts_bounded_within_days_phrase() -> None:
    parsed = parse_local_operational_intent(
        "Chi ha il contratto in scadenza entro 30 giorni?",
        today=TODAY,
    )

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-CONTRACT-001"
    assert parsed.envelope.date_from == TODAY
    assert parsed.envelope.date_to == date(2026, 9, 19)
    assert not parsed.envelope.requires_clarification


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
        "qual è lo stipendio netto di Nora del mese di luglio?",
        today=TODAY,
    )
    previous = parse_local_operational_intent(
        "dimmi lo stipendio netto di Nora",
        today=TODAY,
    )

    assert named is not None
    assert named.envelope.function_id == "EMP-PAY-001"
    assert named.target_query == "Nora"
    assert named.envelope.employee_id is None
    assert named.envelope.parameters == {"year": 2026, "month": 7, "include_net": True}
    assert previous is not None
    assert previous.envelope.parameters == {"year": 2026, "month": 7, "include_net": True}


@pytest.mark.parametrize(
    "message",
    [
        "Quanto ha preso Nora a giugno?",
        "Quanto ha percepito Nora nel mese di giugno?",
        "Quanto è stata pagata Nora a giugno?",
        "Quanto hanno pagato Nora a giugno?",
        "Quanto ha preso di netto Nora a giugno?",
        "Quanto ha preso Nora giugno?",
        "Quanto prende Nora a giugno?",
    ],
)
def test_colloquial_payroll_questions_keep_identity_local(message: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-PAY-001"
    assert parsed.target_query == "Nora"
    assert parsed.envelope.parameters == {"year": 2026, "month": 6, "include_net": True}
    assert is_operational_hr_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "qual è il netto dell'ultimo mese pagato di Nora?",
        "mostra l'ultima busta paga di Nora",
        "qual è l'ultimo cedolino di Nora?",
        "trova la busta paga più recente di Nora",
    ],
)
def test_latest_paid_payroll_is_not_rewritten_to_previous_calendar_month(message: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-PAY-001"
    assert parsed.target_query == "Nora"
    assert parsed.envelope.parameters == {"latest_paid": True, "include_net": True}


def test_generic_payroll_request_extracts_target_or_asks_a_specific_question() -> None:
    targeted = parse_local_operational_intent(
        "mostra il cedolino di Nora di giugno",
        today=TODAY,
    )
    missing_target = parse_local_operational_intent("fammi vedere i cedolini", today=TODAY)

    assert targeted is not None
    assert targeted.target_query == "Nora"
    assert targeted.envelope.parameters == {"year": 2026, "month": 6, "include_net": True}
    assert missing_target is not None
    assert missing_target.envelope.function_id == "EMP-PAY-001"
    assert missing_target.envelope.requires_clarification
    assert "Employee ID" in (missing_target.envelope.clarification_question or "")


@pytest.mark.parametrize(
    ("message", "function_id"),
    [
        ("documenti di Nora", "EMP-DOC-001"),
        ("ruoli di Nora", "EMP-RBAC-001"),
        ("permessi di Nora", "EMP-RBAC-001"),
        ("timbrature di Nora", "EMP-TIME-001"),
        ("bilancio di Nora", "EMP-BAL-001"),
        ("maturazioni di Nora", "EMP-MAT-001"),
        ("contratto di Nora", "EMP-CONTRACT-001"),
    ],
)
def test_resource_of_name_phrasings_resolve_locally(
    message: str,
    function_id: str,
) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == function_id
    assert parsed.target_query == "Nora"


@pytest.mark.parametrize(
    ("message", "function_id"),
    [
        ("ferie e permessi maturati da Nora", "EMP-BAL-001"),
        ("quante ferie restano a Nora?", "EMP-BAL-001"),
        ("Nora può timbrare?", "EMP-TIME-001"),
        ("quali permessi ha Nora sul portale?", "EMP-RBAC-001"),
        ("ci sono documenti in scadenza per Nora?", "EMP-DOC-001"),
        ("mostra tutte le informazioni disponibili su Nora", "EMP-READ-002"),
        ("fammi il dossier HR completo di Nora", "EMP-READ-002"),
        ("dimmi tutto quello che sai su Nora", "EMP-READ-002"),
    ],
)
def test_colloquial_resource_questions_keep_the_employee_name_local(
    message: str,
    function_id: str,
) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == function_id
    assert parsed.target_query == "Nora"
    assert not parsed.envelope.requires_clarification
    assert is_operational_hr_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "leggi gli avvisi non letti",
        "fammi vedere le notifiche nuove",
        "mostra gli alert da leggere",
    ],
)
def test_notification_synonyms_request_only_unread_items(message: str) -> None:
    parsed = parse_local_operational_intent(message, today=TODAY)

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-NOTIF-001"
    assert parsed.envelope.parameters == {"unread_only": True}
    assert is_operational_hr_request(message)


def test_notification_state_change_synonym_remains_a_confirmed_write() -> None:
    parsed = parse_local_operational_intent(
        "segna la notifica 123 come letta",
        today=TODAY,
    )

    assert parsed is not None
    assert parsed.envelope.function_id == "EMP-NOTIF-002"
    assert parsed.envelope.action_class.value == "PREPARE_WRITE"
    assert parsed.envelope.parameters == {"notification_id": 123, "read": True}
    assert is_operational_hr_request("segna la notifica 123 come letta")


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
