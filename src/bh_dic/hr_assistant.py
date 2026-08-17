"""Deterministic request context and local Senior-HR presentation helpers.

The provider remains an intent router.  Relative dates and presentation are
resolved locally so no DIC result or employee identity has to be sent back to a
model provider.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

from bh_dic.language import BotLanguageProfile
from bh_dic.openai.schemas import IntentEnvelope
from bh_dic.security.sanitization import InputValidationError, validate_employee_id

_AGGREGATE_MARKERS = ("quanti", "conteggio", "numero", "totale", "organico")
_AGGREGATE_PATTERN = re.compile(
    rf"\b(?:{'|'.join(re.escape(marker) for marker in _AGGREGATE_MARKERS)})\b",
    re.IGNORECASE,
)
_NEXT_MONTH = re.compile(
    r"\b(?:nel\s+)?(?:prossimo\s+mese|mese\s+prossimo)\b",
    re.IGNORECASE,
)
_THIS_MONTH = re.compile(
    r"\b(?:in\s+)?questo\s+mese\b",
    re.IGNORECASE,
)
_NEGATED_REQUEST = re.compile(r"\b(?:no|non|senza)\b", re.IGNORECASE)
_NEXT_DAYS = re.compile(r"\bprossim[ioe]?\s+(\d{1,3})\s+giorn[io]\b", re.IGNORECASE)
_EXPLICIT_EMPLOYEE_ID = re.compile(
    r"(?i)\b(?:employee\s*id|dipendente\s+id|id\s+dipendente|id)\s*[:#]?\s*"
    r"([A-Za-z0-9_-]{1,64})\b"
)
_NUMERIC_EMPLOYEE_TARGET = re.compile(r"(?i)\bdipendente\s+([0-9]{1,19})\b")
_ROUTER_EMPLOYEE_PLACEHOLDER = "EMP-LOCAL-REDACTED"
_LOCAL_SEARCH = re.compile(
    r"(?is)\b(?:cerca|trova|trovami|search)\s+"
    r"(?:il\s+dipendente\s+|la\s+dipendente\s+|dipendente\s+|employee\s+)?(.+)$"
)
_ROUTER_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_/-]+")
_ISO_DATE_TOKEN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ROUTER_FILLER = frozenset(
    """
    a che chi ci con da dati dei del della delle di gli ha hanno hr i il in le mi nel non ogni
    per quale quali sono su tutti tutto un una uno
    """.split()
)
_CANONICAL_TERM_GROUPS: dict[str, frozenset[str]] = {
    "request": frozenset(
        "dimmi mostra visualizza voglio sapere puoi potresti verifica controlla evidenzia analizza "
        "analisi read show tell".split()
    ),
    "employee_records": frozenset(
        "dipendente dipendenti employee employees team elenco lista job gruppo".split()
    ),
    "employee_headcount": frozenset("quanti conteggio numero totale organico count total".split()),
    "employee_search": frozenset("cerca trova trovami search".split()),
    "employee_filter": frozenset("filter filtro".split()),
    "employee_sort": frozenset("sort ordina".split()),
    "employment_contract": frozenset("contract contracts contratto contratti".split()),
    "contract_deadline": frozenset("expiry expiring fine scade scadenza scadenze validità".split()),
    "next_period": frozenset("next prossimo prossimi".split()),
    "current_period": frozenset("current questo this".split()),
    "calendar_month": frozenset(
        "month mese gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre "
        "novembre dicembre".split()
    ),
    "calendar_days": frozenset("days giorno giorni".split()),
    "calendar_year": frozenset("anno year calendario".split()),
    "active_records": frozenset("active attivi attivo riattiva".split()),
    "inactive_records": frozenset("inactive disattiva disattivati disattivo chiudi".split()),
    "documents": frozenset("document documenti documento busta buste documents".split()),
    "upload_document": frozenset("upload carica".split()),
    "download_document": frozenset("download scarica".split()),
    "balances": frozenset("balances ferie maturazione maturazioni".split()),
    "payroll": frozenset("paga payroll payrolls".split()),
    "time_access": frozenset("time timbratura".split()),
    "access_roles": frozenset("accesso permessi role roles ruolo".split()),
    "account_connection": frozenset("account collega disconnect".split()),
    "invitation": frozenset("invita".split()),
    "create_action": frozenset("crea".split()),
    "update_action": frozenset("correggi modifica write".split()),
    "delete_action": frozenset("elimina".split()),
    "approve_action": frozenset("approva".split()),
    "reject_action": frozenset("reject respingi".split()),
    "export_action": frozenset("esporta".split()),
    "pending_actions": frozenset("pending".split()),
    "system_status": frozenset("help status stato".split()),
    "all_records": frozenset("all".split()),
}


def _canonical_router_terms() -> dict[str, str]:
    result: dict[str, str] = {}
    for canonical, terms in _CANONICAL_TERM_GROUPS.items():
        for term in terms:
            if term in result:
                raise RuntimeError("duplicate canonical HR routing term")
            result[term] = canonical
    return result


_ROUTER_CANONICAL_TERMS = _canonical_router_terms()
_CONTRACT_EXPIRY_REQUIRED_TERMS = frozenset({"employment_contract", "contract_deadline"})
_CONTRACT_EXPIRY_ALLOWED_TERMS = frozenset(
    {
        "request",
        "employee_records",
        "employment_contract",
        "contract_deadline",
        "next_period",
        "current_period",
        "calendar_month",
    }
)


class HrRequestInputError(ValueError):
    """A locally rejected request that must not be forwarded to a provider."""


def is_employee_aggregate_request(request: str) -> bool:
    """Return whether an employee-list request asks only for an aggregate."""

    return _AGGREGATE_PATTERN.search(request) is not None


def minimize_hr_router_request(request: str) -> tuple[str, str | None]:
    """Remove identity material before the already-redacted request reaches a provider.

    Only an explicitly labelled Employee ID can be restored locally after routing. Names are
    never resolved by the model and remain unsupported as authorization targets.
    """

    identifiers = {
        *(match.group(1) for match in _EXPLICIT_EMPLOYEE_ID.finditer(request)),
        *(match.group(1) for match in _NUMERIC_EMPLOYEE_TARGET.finditer(request)),
    }
    if len(identifiers) > 1:
        raise HrRequestInputError("a request can contain only one explicit Employee ID")
    explicit_employee_id = next(iter(identifiers), None)
    if explicit_employee_id is not None:
        invalid_identifier = False
        try:
            explicit_employee_id = validate_employee_id(explicit_employee_id)
        except InputValidationError:
            invalid_identifier = True
        if invalid_identifier:
            raise HrRequestInputError("explicit Employee ID has an invalid format")
    identity_replaced = _EXPLICIT_EMPLOYEE_ID.sub(
        f"employee id {_ROUTER_EMPLOYEE_PLACEHOLDER}",
        request,
    )
    identity_replaced = _NUMERIC_EMPLOYEE_TARGET.sub(
        f"employee id {_ROUTER_EMPLOYEE_PLACEHOLDER}",
        identity_replaced,
    )
    local_search = _LOCAL_SEARCH.search(identity_replaced)
    if local_search is not None:
        # The search value remains available to ``local_employee_search_query`` at the caller,
        # but even a name colliding with an HR/month word must never survive provider projection.
        identity_replaced = identity_replaced[: local_search.start(1)] + "[LOCAL_SEARCH_REDACTED]"
    projected: list[str] = []
    for token in _ROUTER_TOKEN.findall(identity_replaced):
        folded = token.casefold()
        canonical = _ROUTER_CANONICAL_TERMS.get(folded)
        if token == _ROUTER_EMPLOYEE_PLACEHOLDER:
            projected.append(token)
        elif canonical is not None:
            if not projected or projected[-1] != canonical:
                projected.append(canonical)
        elif folded in _ROUTER_FILLER:
            continue
        elif _ISO_DATE_TOKEN.fullmatch(token):
            projected.append(token)
        elif not projected or projected[-1] != "[TERM_REDACTED]":
            projected.append("[TERM_REDACTED]")
    if not projected:
        raise HrRequestInputError("request contains no routable HR terms")
    return " ".join(projected), explicit_employee_id


def local_employee_search_query(request: str) -> str | None:
    """Extract a bounded search value that stays local to the DIC browser boundary."""

    match = _LOCAL_SEARCH.search(request)
    if match is None:
        return None
    query = " ".join(match.group(1).split()).strip(" .,:;!?\"'")
    if not query or len(query) > 128:
        return None
    return query


def _month_interval(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    return first, date(year, month, monthrange(year, month)[1])


def _next_month_interval(today: date) -> tuple[date, date]:
    year = today.year + (1 if today.month == 12 else 0)
    month = 1 if today.month == 12 else today.month + 1
    return _month_interval(year, month)


def _supported_contract_interval(request: str, *, today: date) -> tuple[date, date] | None:
    if _NEXT_MONTH.search(request):
        return _next_month_interval(today)
    if _THIS_MONTH.search(request):
        return _month_interval(today.year, today.month)
    next_days = _NEXT_DAYS.search(request)
    if next_days is None:
        return None
    days = int(next_days.group(1))
    if not 1 <= days <= 366:
        return None
    return today, today + timedelta(days=days)


def local_contract_expiry_fallback_interval(
    request: str,
    projected_request: str,
    *,
    today: date,
) -> tuple[date, date] | None:
    """Recognize only the closed, locally resolvable contract-expiry read subset.

    The projected request contains canonical routing terms and no DIC result. Unknown terms,
    identity placeholders, other HR functions, and every write marker keep the request on the
    normal fail-closed provider path.
    """

    terms = frozenset(projected_request.split())
    if _NEGATED_REQUEST.search(request) is not None:
        return None
    if not _CONTRACT_EXPIRY_REQUIRED_TERMS.issubset(terms):
        return None
    if not terms.issubset(_CONTRACT_EXPIRY_ALLOWED_TERMS):
        return None
    next_month = _NEXT_MONTH.search(request) is not None
    this_month = _THIS_MONTH.search(request) is not None
    if next_month == this_month:
        return None
    interval = _supported_contract_interval(request, today=today)
    if interval is None:
        return None
    if next_month:
        required_period_terms = {"next_period", "calendar_month"}
    elif this_month:
        required_period_terms = {"current_period", "calendar_month"}
    else:
        return None
    if not required_period_terms.issubset(terms):
        return None
    return interval


def normalize_hr_intent(intent: IntentEnvelope, request: str, *, today: date) -> IntentEnvelope:
    """Apply closed local semantics that must not depend on provider inference."""

    updates: dict[str, object] = {}
    folded = request.casefold()

    if intent.function_id == "EMP-READ-001" and is_employee_aggregate_request(request):
        if "disattiv" in folded or "cessat" in folded:
            status = "inactive"
        elif "attiv" in folded:
            status = "active"
        else:
            # An unqualified total means the complete company headcount, not
            # a provider-inferred or UI-default active tab.
            status = "all"
        # Aggregate semantics are entirely local: provider pagination, sorting or filters must
        # never alter the company headcount requested by the user.
        updates["parameters"] = {"status": status}

    if intent.function_id == "EMP-CONTRACT-001":
        interval = _supported_contract_interval(request, today=today)
        if interval is not None:
            updates["date_from"], updates["date_to"] = interval

    return intent.model_copy(update=updates) if updates else intent


class SeniorHrPresenter:
    """Closed local copy variants; facts remain deterministic and typed."""

    def __init__(self, profile: BotLanguageProfile | None) -> None:
        self.profile = profile

    @property
    def enabled(self) -> bool:
        return self.profile is not None

    def employee_count(self, total: int, status: str) -> tuple[str, str]:
        if self.profile is None:
            return "Conteggio dipendenti", f"Totale nel filtro richiesto: {total}"
        if self.profile.language == "en":
            label = {
                "active": "active",
                "inactive": "inactive",
                "all": "overall",
            }.get(status, "filtered")
            noun = "employee" if total == 1 else "employees"
            description = (
                f"I checked Dipendenti in Cloud: the current {label} headcount is "
                f"**{total} {noun}**."
            )
            if self.profile.verbosity == "detailed":
                description += "\n\nThe figure comes from the current tenant-bound live view."
            return "Hotel HR headcount", description

        lead = "Certo — " if self.profile.tone in {"friendly", "empathetic"} else ""
        label = {
            "active": "dei dipendenti attivi",
            "inactive": "dei dipendenti disattivati",
            "all": "complessivo",
        }.get(status, "nel filtro richiesto")
        noun = "dipendente" if total == 1 else "dipendenti"
        description = (
            f"{lead}ho controllato Dipendenti in Cloud: l'organico {label} "
            f"risulta di **{total} {noun}**."
        )
        if self.profile.verbosity == "detailed":
            description += (
                "\n\nIl dato proviene dalla vista live del tenant verificato. "
                "Posso anche separare attivi e disattivati oppure ordinare l'elenco autorizzato."
            )
        return "Organico dell'hotel", description

    def employee_list(
        self,
        *,
        page: int,
        shown: int,
        total: int,
        has_next: bool,
    ) -> tuple[str, str]:
        if self.profile is None:
            return (
                "Dipendenti",
                f"Pagina {page}; mostrati {shown} su {total}; "
                f"pagina successiva: {'sì' if has_next else 'no'}",
            )
        if self.profile.language == "en":
            return (
                "Hotel team",
                f"I checked Dipendenti in Cloud: showing {shown} of {total} employees "
                f"on page {page}.",
            )
        lead = "Certo — " if self.profile.tone in {"friendly", "empathetic"} else ""
        description = (
            f"{lead}ho consultato l'organico live su Dipendenti in Cloud: "
            f"in questa pagina mostro **{shown} dipendenti su {total}**."
        )
        if has_next:
            description += " Sono disponibili altre pagine autorizzate."
        if self.profile.verbosity == "detailed":
            description += (
                "\n\nI dati identificativi sono minimizzati; stato, mansione, contratto e "
                "collocazione restano quelli letti dal tenant verificato."
            )
        return "Il team dell'hotel", description

    def operational_status(self, operational: bool) -> tuple[str, str]:
        if self.profile is None:
            return "Stato BH-DiC", "Stato operativo redatto."
        if self.profile.language == "en":
            return (
                "BH-DiC status",
                "I am online and ready to assist HR."
                if operational
                else "I am online, but the verified DIC session is currently unavailable.",
            )
        if operational:
            return (
                "Il tuo partner HR è operativo",
                "Sono online e la sessione tenant di Dipendenti in Cloud è verificata. "
                "Posso interpretare richieste HR autorizzate e controllare i dati live.",
            )
        return (
            "Assistente online, DIC non disponibile",
            "Resto connesso a Discord, ma la sessione tenant DIC non è al momento verificata. "
            "Le letture HR falliranno in sicurezza finché un amministratore non la ripristina.",
        )

    def contract_summary(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        found: int,
        shown: int,
        unparseable: int = 0,
    ) -> tuple[str, str]:
        interval = f"{date_from or 'inizio'} → {date_to or 'fine'}"
        if self.profile is None:
            return "Contratti e scadenze", f"Intervallo: {interval}"
        if self.profile.language == "en":
            description = (
                f"I checked the contract calendar for **{interval}**: "
                f"**{found}** expiring contract(s), {shown} shown."
            )
            if unparseable:
                description += (
                    f" {unparseable} record(s) had an unsupported date and were excluded."
                )
            return "Contract deadlines", description
        if found == 0:
            description = (
                f"Ho controllato il calendario contratti per **{interval}**: "
                "non risultano scadenze nel periodo."
            )
        else:
            description = (
                f"Ho controllato il calendario contratti per **{interval}**: "
                f"ho trovato **{found} scadenze** e ne mostro {shown}."
            )
        if self.profile.verbosity == "detailed":
            description += (
                "\n\nI dettagli individuali restano visibili solo al richiedente HR autorizzato."
            )
        if unparseable:
            description += (
                f"\n\nAttenzione: {unparseable} record con data non interpretabile "
                "sono stati esclusi dal conteggio."
            )
        return "Scadenze contrattuali", description


__all__ = [
    "HrRequestInputError",
    "SeniorHrPresenter",
    "is_employee_aggregate_request",
    "local_contract_expiry_fallback_interval",
    "local_employee_search_query",
    "minimize_hr_router_request",
    "normalize_hr_intent",
]
