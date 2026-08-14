"""Central selector registry; page objects do not embed ad-hoc selectors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from bh_dic.dic.errors import DicConfigurationError


class SelectorKind(StrEnum):
    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEST_ID = "test_id"
    TEXT = "text"
    CSS = "css"


@dataclass(frozen=True, slots=True)
class SelectorCandidate:
    kind: SelectorKind
    value: str
    name: str | None = None
    exact: bool = False


class SelectorRegistry:
    """Immutable ordered fallbacks, strongest semantic selector first."""

    def __init__(self, entries: Mapping[str, tuple[SelectorCandidate, ...]]) -> None:
        if any(not value for value in entries.values()):
            raise DicConfigurationError("each selector key requires at least one candidate")
        self._entries = MappingProxyType(dict(entries))

    def candidates(self, key: str) -> tuple[SelectorCandidate, ...]:
        try:
            return self._entries[key]
        except KeyError as exc:
            raise DicConfigurationError(f"unknown selector registry key: {key}") from exc

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self._entries)


def role(role_name: str, name: str, *, exact: bool = False) -> SelectorCandidate:
    return SelectorCandidate(SelectorKind.ROLE, role_name, name, exact)


def label(value: str, *, exact: bool = False) -> SelectorCandidate:
    return SelectorCandidate(SelectorKind.LABEL, value, exact=exact)


def placeholder(value: str) -> SelectorCandidate:
    return SelectorCandidate(SelectorKind.PLACEHOLDER, value)


def test_id(value: str) -> SelectorCandidate:
    return SelectorCandidate(SelectorKind.TEST_ID, value)


def text(value: str, *, exact: bool = False) -> SelectorCandidate:
    return SelectorCandidate(SelectorKind.TEXT, value, exact=exact)


def css(value: str) -> SelectorCandidate:
    return SelectorCandidate(SelectorKind.CSS, value)


DEFAULT_SELECTORS = SelectorRegistry(
    {
        "common.success": (
            role("status", "Operazione completata"),
            test_id("success-toast"),
            css("[role='alert'].success, .toast-success"),
        ),
        "common.confirm": (
            role("button", "Conferma", exact=True),
            test_id("confirm-action"),
        ),
        "auth.username": (
            label("Email"),
            label("Username"),
            css("input[type='email']"),
        ),
        "auth.password": (label("Password"), css("input[type='password']")),
        "auth.submit": (
            role("button", "Accedi"),
            role("button", "Login"),
            css("button[type='submit']"),
        ),
        "auth.mfa": (
            label("Codice di verifica"),
            label("Codice OTP"),
            css("input[autocomplete='one-time-code']"),
        ),
        "auth.captcha": (
            test_id("captcha"),
            css("iframe[src*='captcha'], [class*='captcha']"),
        ),
        "auth.authenticated": (
            role("navigation", "Menu principale"),
            test_id("app-sidebar"),
            css("nav a[href='/it/app/employees/list']"),
        ),
        "auth.tenant": (
            test_id("current-tenant"),
            css("[data-current-tenant-id]"),
            css("[data-tenant-id]"),
            css("[data-company-id]"),
        ),
        "employees.rows": (
            test_id("employee-row"),
            css("table tbody tr"),
        ),
        "employees.total": (
            test_id("employee-total"),
            css("[data-total-count]"),
        ),
        "employees.search": (
            role("searchbox", "Cerca"),
            placeholder("Cerca"),
            test_id("employee-search"),
        ),
        "employees.filter.active": (
            role("tab", "Attivi"),
            role("button", "Attivi"),
        ),
        "employees.filter.inactive": (
            role("tab", "Disattivati"),
            role("button", "Disattivati"),
        ),
        "employees.filter.all": (
            role("tab", "Tutti"),
            role("button", "Tutti"),
        ),
        "employees.sort.name": (
            role("columnheader", "Dipendente"),
            test_id("sort-name"),
        ),
        "employees.sort.payroll_number": (
            role("columnheader", "Matricola"),
            test_id("sort-payroll-number"),
        ),
        "employees.sort.status": (
            role("columnheader", "Stato"),
            test_id("sort-status"),
        ),
        "employees.sort.contract": (
            role("columnheader", "Contratto"),
            test_id("sort-contract"),
        ),
        "employees.next": (
            role("button", "Pagina successiva"),
            test_id("pagination-next"),
            css("button[aria-label*='successiva']"),
        ),
        "employees.new": (role("button", "Nuovo dipendente"), test_id("new-employee")),
        "employees.create_manual": (
            text("Crea manualmente"),
            test_id("create-employee-manual"),
        ),
        "employees.create_payroll": (
            text("Crea caricando una busta paga"),
            test_id("create-employee-payroll"),
        ),
        "employees.create_save": (
            role("button", "Crea dipendente"),
            role("button", "Salva"),
            test_id("create-employee-submit"),
        ),
        "row.employee_id": (
            css(":scope[data-employee-id]"),
            css("[data-employee-id]"),
            css("a[href*='/employees/info/']"),
        ),
        "row.name": (css("[data-field='name']"), css("td:nth-child(1)")),
        "row.email": (css("[data-field='email']"),),
        "row.tax_code": (css("[data-field='tax-code']"),),
        "row.job_title": (css("[data-field='job-title']"),),
        "row.group": (css("[data-field='group']"),),
        "row.payroll_number": (css("[data-field='payroll-number']"),),
        "row.contract": (css("[data-field='contract']"),),
        "row.contract_state": (
            test_id("employee-contract-state"),
            css("[data-field='contract-status']"),
            css("[data-field='contract-state']"),
            css("[data-field='contract']"),
        ),
        "row.contract_period": (css("[data-field='contract-period']"),),
        "row.schedule": (css("[data-field='schedule']"),),
        "row.workplace": (
            test_id("employee-workplace"),
            css("[data-field='workplace']"),
        ),
        "row.account_state": (css("[data-field='account-state']"),),
        "row.employee_state": (css("[data-field='employee-state']"),),
        "summary.first_name": (label("Nome"), test_id("employee-first-name")),
        "summary.last_name": (label("Cognome"), test_id("employee-last-name")),
        "summary.payroll_number": (
            label("Numero di Matricola"),
            test_id("employee-payroll-number"),
        ),
        "summary.tax_code": (label("Codice fiscale"), test_id("employee-tax-code")),
        "summary.birth_date": (label("Data di nascita"), test_id("employee-birth-date")),
        "summary.iban": (label("IBAN"), test_id("employee-iban")),
        "summary.job_title": (label("Mansione"), test_id("employee-job-title")),
        "summary.phone": (label("Numero di telefono"), test_id("employee-phone")),
        "summary.email": (label("Email aziendale"), test_id("employee-email")),
        "summary.address": (label("Indirizzo"), test_id("employee-address")),
        "summary.workplace": (label("Luogo di lavoro"), test_id("employee-workplace")),
        "summary.notes": (label("Note sul dipendente"), test_id("employee-notes")),
        "summary.state": (test_id("employee-state"), css("[data-field='employee-state']")),
        "summary.save": (role("button", "Salva modifiche"), test_id("employee-save")),
        "summary.connect": (role("button", "Collega dipendente"),),
        "summary.disconnect": (role("button", "Scollega dipendente"),),
        "summary.invite_again": (role("button", "Invita di nuovo"),),
        "summary.cancel_invite": (role("button", "Annulla invito"),),
        "summary.deactivate": (role("button", "Disattiva dipendente"),),
        "summary.activate": (role("button", "Attiva dipendente"),),
        "summary.delete": (role("button", "Elimina dipendente"),),
        "roles.groups": (test_id("employee-group"), css("[data-role='employee-group']")),
        "roles.items": (test_id("employee-role"), css("[data-role='employee-role']")),
        "roles.time.timestamping": (label("Timbratura"), test_id("role-timestamping")),
        "roles.time.attendance": (label("Foglio Presenze"), test_id("role-attendance")),
        "roles.time.shifts": (label("Gestione turni"), test_id("role-shifts")),
        "roles.time.expenses": (label("Spese e Viaggi"), test_id("role-expenses")),
        "roles.save": (role("button", "Salva modifiche"), test_id("roles-save")),
        "timestamps.rows": (test_id("timestamp-employee-row"), css("table tbody tr")),
        "timestamps.row.employee_id": (
            css(":scope[data-employee-id]"),
            css("[data-employee-id]"),
        ),
        "timestamps.row.enabled": (
            css("[data-field='timestamping-enabled'] input[type='checkbox']"),
            css("[data-field='timestamping-enabled']"),
        ),
        "contracts.rows": (test_id("contract-row"), css("table tbody tr")),
        "contracts.new": (role("button", "Nuovo"), test_id("new-contract")),
        "contracts.edit": (role("button", "Modifica"), test_id("edit-contract")),
        "contracts.delete": (role("button", "Elimina"), test_id("delete-contract")),
        "contracts.schedule": (label("Orario contratto"),),
        "contracts.flexibility": (label("Flessibilità orario"),),
        "contracts.permanent": (label("Indeterminato"),),
        "contracts.start_date": (label("Data inizio"),),
        "contracts.end_date": (label("Data fine"),),
        "contracts.ccnl_level": (label("Livello CCNL"),),
        "contracts.work_regime": (label("Regime orario"),),
        "contracts.description": (label("Descrizione"),),
        "contracts.type": (label("Tipo"),),
        "contracts.save": (role("button", "Salva"), test_id("contract-save")),
        "contract_row.id": (css(":scope[data-contract-id]"), css("[data-contract-id]")),
        "contract_row.schedule": (css("[data-field='schedule']"), css("td:nth-child(1)")),
        "contract_row.flexibility": (css("[data-field='flexibility']"),),
        "contract_row.permanent": (css("[data-field='permanent']"),),
        "contract_row.start_date": (css("[data-field='start-date']"),),
        "contract_row.end_date": (css("[data-field='end-date']"),),
        "contract_row.ccnl_level": (css("[data-field='ccnl-level']"),),
        "contract_row.work_regime": (css("[data-field='work-regime']"),),
        "contract_row.description": (css("[data-field='description']"),),
        "contract_row.type": (css("[data-field='type']"),),
        "contract_row.status": (css("[data-field='status']"),),
        "contract_row.period": (css("[data-field='period']"),),
        "maturations.rows": (test_id("maturation-row"), css("table tbody tr")),
        "maturations.new": (role("button", "Nuovo"), test_id("new-maturation")),
        "maturations.category": (label("Categoria"),),
        "maturations.valid_from": (label("Data inizio"),),
        "maturations.valid_to": (label("Data fine"),),
        "maturations.save": (role("button", "Salva"), test_id("maturation-save")),
        "maturation_row.id": (
            css(":scope[data-maturation-id]"),
            css("[data-maturation-id]"),
        ),
        "maturation_row.category": (css("[data-field='category']"), css("td:nth-child(1)")),
        "maturation_row.valid_from": (css("[data-field='valid-from']"),),
        "maturation_row.valid_to": (css("[data-field='valid-to']"),),
        "maturation_row.status": (css("[data-field='status']"),),
        "balance.year": (label("Anno"), test_id("balance-year")),
        "balance.rows": (test_id("balance-row"), css("table tbody tr")),
        "balance.correct": (role("button", "Correggi"), test_id("balance-correct")),
        "balance.category": (label("Categoria"),),
        "balance.amount": (label("Correzione"),),
        "balance.save": (role("button", "Salva"), test_id("balance-save")),
        "balance_row.category": (css("[data-field='category']"), css("td:nth-child(1)")),
        "balance_row.previous_year": (css("[data-field='previous-year']"),),
        "balance_row.previous_month": (css("[data-field='previous-month']"),),
        "balance_row.accrued": (css("[data-field='accrued']"),),
        "balance_row.used": (css("[data-field='used']"),),
        "balance_row.corrections": (css("[data-field='corrections']"),),
        "balance_row.current_residual": (css("[data-field='current-residual']"),),
        "payrolls.rows": (test_id("payroll-row"), css("table tbody tr")),
        "payrolls.year": (label("Anno"), test_id("payroll-year")),
        "payroll_row.id": (css(":scope[data-payroll-id]"), css("[data-payroll-id]")),
        "payroll_row.year": (css("[data-field='year']"),),
        "payroll_row.month": (css("[data-field='month']"),),
        "payroll_row.status": (css("[data-field='status']"),),
        "payroll_row.published_at": (css("[data-field='published-at']"),),
        "documents.rows": (test_id("document-row"), css("table tbody tr")),
        "documents.search": (role("searchbox", "Cerca"), placeholder("Cerca")),
        "documents.uploaded": (role("tab", "Caricati"),),
        "documents.pending": (role("tab", "In attesa"),),
        "documents.upload": (role("button", "Carica documento"), test_id("upload-document")),
        "documents.file": (label("File"), css("input[type='file']")),
        "documents.title": (label("Titolo"),),
        "documents.category": (label("Tipologia documento"), label("Categoria")),
        "documents.expiry": (label("Scadenza"),),
        "documents.save": (role("button", "Salva"), test_id("document-save")),
        "documents.edit": (role("button", "Modifica"), test_id("edit-document")),
        "documents.delete": (role("button", "Elimina"), test_id("delete-document")),
        "document_row.id": (css(":scope[data-document-id]"), css("[data-document-id]")),
        "document_row.title": (css("[data-field='title']"), css("td:nth-child(1)")),
        "document_row.category": (css("[data-field='category']"),),
        "document_row.expiry": (css("[data-field='expiry']"),),
        "document_row.uploaded_at": (css("[data-field='uploaded-at']"),),
        "document_row.uploaded_by": (css("[data-field='uploaded-by']"),),
        "document_row.state": (css("[data-field='state']"),),
    }
)
