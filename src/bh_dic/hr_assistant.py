"""Deterministic request context and local Senior-HR presentation helpers.

The provider remains an intent router.  Relative dates and presentation are
resolved locally so no DIC result or employee identity has to be sent back to a
model provider.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from bh_dic.language import BotLanguageProfile
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, Sensitivity
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
    r"(?!(?:dei|del|della|delle|di|dipendente|dipendenti)\b)"
    r"([A-Za-z0-9_-]{1,64})\b"
)
_NUMERIC_EMPLOYEE_TARGET = re.compile(r"(?i)\bdipend(?:ente|e|ete)\s+([0-9]{1,19})\b")
_ROUTER_EMPLOYEE_PLACEHOLDER = "EMP-LOCAL-REDACTED"
_LOCAL_SEARCH = re.compile(
    r"(?is)\b(?:cerca|trova|trovami|search)\s+"
    r"(?:il\s+dipendente\s+|la\s+dipendente\s+|dipendente\s+|employee\s+)?(.+)$"
)
_EMPLOYEE_TERM = re.compile(
    r"\b(?:dipend(?:ent\w*|e|ete)|employee\w*|organico)\b",
    re.IGNORECASE,
)
_LIST_MARKER = re.compile(
    r"\b(?:elenc\w*|lista|mostra\w*|stampa\w*|tabella|visualizza\w*)\b",
    re.IGNORECASE,
)
_CAPABILITIES_MARKER = re.compile(
    r"\b(?:funzioni|funzionalit[aà]|capabilit(?:y|ies)|cosa\s+(?:puoi|sa)\s+fare)\b",
    re.IGNORECASE,
)
_EXPORT_FORMAT = re.compile(
    r"\b(?P<format>xlsx|excel|foglio\s+di\s+calcolo|pdf|docx|doc|word|documento)\b",
    re.IGNORECASE,
)
_PAYROLL_TERM = re.compile(r"\b(?:bust[ae]\s+paga|cedolin[oi]|payroll\w*)\b", re.IGNORECASE)
_PAYROLL_COLLECTIVE = re.compile(
    r"\b(?:quali|chi|elenc\w*|lista|tutti\s+i\s+dipendenti|dipendenti)\b",
    re.IGNORECASE,
)
_PAYROLL_MONTH = re.compile(
    r"\b(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|"
    r"ottobre|novembre|dicembre)\b",
    re.IGNORECASE,
)
_PAYROLL_YEAR = re.compile(r"\b(20\d{2})\b")
_MONTH_NUMBER_BY_NAME = {
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
_EXPORT_ACTION = re.compile(
    r"\b(?:genera\w*|crea\w*|prepara\w*|esporta\w*|scarica\w*|fammi|produci)\b",
    re.IGNORECASE,
)
_ACTIVATE_ACTION = re.compile(
    r"\b(?:attiva|ativa|riattiva|riativa|ri-?attiva)\b",
    re.IGNORECASE,
)
_DEACTIVATE_ACTION = re.compile(
    r"\b(?:disattiva|disativa|disattivia|disattivia|rendi\s+inattiv[oa])\b",
    re.IGNORECASE,
)
_STATUS_TARGET = re.compile(
    r"(?is)\b(?:attiva|ativa|riattiva|riativa|ri-?attiva|disattiva|disativa|disattivia|"
    r"rendi\s+inattiv[oa])\b\s+(?:il\s+|la\s+|un\s+|una\s+)?"
    r"(?:dipend(?:ent\w*|e|ete)\s+)?(.+?)\s*$"
)
_MOTIVATION = re.compile(r"(?is)\b(?:motivo|motivazione)\s*[:=-]\s*(.{3,500})$")
_GENERAL_HR_TOPIC = re.compile(
    r"\b(?:hr|risorse\s+umane|ferie|permess\w*|assen\w*|malattia|maternit[aà]|paternit[aà]|"
    r"contratt\w*|ccnl|busta\s+paga|stipendi\w*|retribuz\w*|"
    r"dipend(?:ent\w*|e|ete)|employee\w*|"
    r"timbratur\w*|presenz\w*|maturazion\w*|colloqui\w*|feedback|onboarding|offboarding|"
    r"smart\s+working)\b",
    re.IGNORECASE,
)
_OPERATIONAL_HR_ACTION = re.compile(
    r"\b(?:dimmi|quanti|conteggio|numero|totale|elenc\w*|lista|mostra\w*|stampa\w*|"
    r"tabella|visualizza\w*|cerca|trova|scad\w*|genera\w*|crea\w*|esporta\w*|"
    r"attiva|ativa|riattiva|riativa|disattiva|disativa|disattivia)\b",
    re.IGNORECASE,
)
_OPERATIONAL_HR_OBJECT = re.compile(
    r"\b(?:dipend(?:ent\w*|e|ete)|employee\w*|organico|contratt\w*|document\w*|"
    r"bust[ae]\s+paga|payroll\w*|ferie|permess\w*|maturazion\w*|timbratur\w*|"
    r"presenz\w*|ruol\w*)\b",
    re.IGNORECASE,
)
_TECHNICAL_TARGET = re.compile(
    r"(?i)(?:https?://|www\.|\b(?:token|password|cookie|select|drop|curl|powershell|bash)\b)"
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


@dataclass(frozen=True, slots=True)
class LocalOperationalIntent:
    """A closed, deterministic intent whose identity material stays local."""

    envelope: IntentEnvelope
    target_query: str | None = None


def _local_envelope(
    function_id: str,
    *,
    action_class: ActionClass,
    sensitivity: Sensitivity,
    employee_id: str | None = None,
    query: str | None = None,
    parameters: dict[str, object] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    clarification: str | None = None,
) -> IntentEnvelope:
    return IntentEnvelope(
        intent=function_id.lower().replace("-", "_"),
        function_id=function_id,
        action_class=action_class,
        employee_id=employee_id,
        query=query,
        parameters=parameters or {},
        date_from=date_from,
        date_to=date_to,
        requires_clarification=clarification is not None,
        clarification_question=clarification,
        sensitivity=sensitivity,
        confidence=1.0,
    )


def is_capabilities_request(request: str) -> bool:
    """Return whether the user asks for the locally maintained capability matrix."""

    return _CAPABILITIES_MARKER.search(request) is not None


def is_general_hr_request(request: str) -> bool:
    """Recognize general HR discussion without treating ordinary channel chat as a command."""

    return _GENERAL_HR_TOPIC.search(request) is not None


def is_operational_hr_request(request: str) -> bool:
    """Recognize requests that should enter the authorized DIC coordinator."""

    if is_capabilities_request(request):
        return True
    if is_payroll_presence_request(request):
        return True
    if (
        _ACTIVATE_ACTION.search(request) is not None
        or _DEACTIVATE_ACTION.search(request) is not None
    ):
        return True
    if (
        _EXPORT_FORMAT.search(request) is not None
        and _EXPORT_ACTION.search(request) is not None
        and (
            _EMPLOYEE_TERM.search(request) is not None
            or re.search(r"\bcontratt\w*\b", request, re.IGNORECASE) is not None
        )
    ):
        return True
    return (
        _OPERATIONAL_HR_OBJECT.search(request) is not None
        and _OPERATIONAL_HR_ACTION.search(request) is not None
    )


def _requested_status(request: str) -> Literal["active", "inactive", "all"]:
    folded = request.casefold()
    if "disattiv" in folded or "inattiv" in folded or "cessat" in folded:
        return "inactive"
    if "attiv" in folded:
        return "active"
    return "all"


def _export_format(request: str) -> Literal["pdf", "docx", "xlsx"] | None:
    match = _EXPORT_FORMAT.search(request)
    if match is None:
        return None
    raw = match.group("format").casefold()
    if raw in {"xlsx", "excel", "foglio di calcolo"}:
        return "xlsx"
    if raw == "pdf":
        return "pdf"
    return "docx"


def local_payroll_presence_period(request: str, *, today: date) -> tuple[int, int] | None:
    """Resolve only a named payroll month locally; a provider never infers the period."""

    month_match = _PAYROLL_MONTH.search(request)
    if month_match is None:
        return None
    year_match = _PAYROLL_YEAR.search(request)
    year = int(year_match.group(1)) if year_match is not None else today.year
    return year, _MONTH_NUMBER_BY_NAME[month_match.group(1).casefold()]


def is_payroll_presence_request(request: str) -> bool:
    """Recognize an aggregate, read-only question about a monthly payroll presence."""

    return (
        _PAYROLL_TERM.search(request) is not None
        and _PAYROLL_COLLECTIVE.search(request) is not None
    )


def _status_target(request: str) -> tuple[str | None, str | None]:
    explicit = _EXPLICIT_EMPLOYEE_ID.search(request) or _NUMERIC_EMPLOYEE_TARGET.search(request)
    if explicit is not None:
        try:
            return validate_employee_id(explicit.group(1)), None
        except InputValidationError as exc:
            raise HrRequestInputError("explicit Employee ID has an invalid format") from exc
    target = _STATUS_TARGET.search(request)
    if target is None:
        return None, None
    value = _MOTIVATION.sub("", target.group(1)).strip(" .,:;!?\"'")
    value = re.sub(r"(?i)^con\s+id\s+", "", value).strip()
    if not value:
        return None, None
    if len(value) > 128 or _TECHNICAL_TARGET.search(value):
        raise HrRequestInputError("employee target is invalid")
    return None, " ".join(value.split())


def parse_local_operational_intent(
    request: str,
    *,
    today: date,
) -> LocalOperationalIntent | None:
    """Parse high-confidence daily HR requests without sending identity data to a model.

    This parser intentionally covers only closed operations. Anything else remains eligible for
    the existing minimized intent router; it never manufactures a DIC URL or a free-form action.
    """

    text = " ".join(request.strip().split())
    if not text:
        return None
    has_explicit_employee_id = (
        _EXPLICIT_EMPLOYEE_ID.search(text) is not None
        or _NUMERIC_EMPLOYEE_TARGET.search(text) is not None
    )

    deactivate = _DEACTIVATE_ACTION.search(text) is not None
    activate = _ACTIVATE_ACTION.search(text) is not None and not deactivate
    if activate or deactivate:
        employee_id, target_query = _status_target(text)
        motivation_match = _MOTIVATION.search(text)
        motivation = motivation_match.group(1).strip() if motivation_match is not None else None
        clarification = None
        if employee_id is None and target_query is None:
            clarification = "Indica l'Employee ID oppure un nome e cognome da cercare."
        elif deactivate and motivation is None:
            clarification = "Indica anche una motivazione, ad esempio `motivo: cessazione`."
        parameters = {"motivation": motivation} if motivation is not None else {}
        function_id = "EMP-STATUS-001" if deactivate else "EMP-STATUS-002"
        return LocalOperationalIntent(
            _local_envelope(
                function_id,
                action_class=ActionClass.PREPARE_WRITE,
                sensitivity=Sensitivity.CRITICAL if deactivate else Sensitivity.HIGH,
                employee_id=employee_id,
                parameters=parameters,
                clarification=clarification,
            ),
            target_query=target_query,
        )

    export_format = _export_format(text)
    if export_format is not None and _EXPORT_ACTION.search(text) is not None:
        if not (_EMPLOYEE_TERM.search(text) or re.search(r"\bcontratt\w*\b", text, re.I)):
            return None
        dataset = "contracts_expiring" if re.search(r"\bscad\w*\b", text, re.I) else "employees"
        date_from, date_to = (None, None)
        if dataset == "contracts_expiring":
            interval = _supported_contract_interval(text, today=today)
            if interval is not None:
                date_from, date_to = interval
        return LocalOperationalIntent(
            _local_envelope(
                "EMP-EXPORT-001",
                action_class=ActionClass.EXPORT,
                sensitivity=Sensitivity.HIGH,
                parameters={
                    "scope": "employees",
                    "format": export_format,
                    "dataset": dataset,
                    "status": _requested_status(text),
                    **({"date_from": date_from.isoformat()} if date_from else {}),
                    **({"date_to": date_to.isoformat()} if date_to else {}),
                },
            )
        )

    payroll_period = local_payroll_presence_period(text, today=today)
    if is_payroll_presence_request(text) and payroll_period is not None:
        year, month = payroll_period
        return LocalOperationalIntent(
            _local_envelope(
                "EMP-PAY-002",
                action_class=ActionClass.READ,
                sensitivity=Sensitivity.HIGH,
                parameters={"year": year, "month": month},
            )
        )

    if _EMPLOYEE_TERM.search(text) is not None:
        if _AGGREGATE_PATTERN.search(text) is not None:
            return LocalOperationalIntent(
                _local_envelope(
                    "EMP-READ-001",
                    action_class=ActionClass.READ,
                    sensitivity=Sensitivity.LOW,
                    parameters={"status": _requested_status(text), "view": "count"},
                )
            )
        if _LIST_MARKER.search(text) is not None and not has_explicit_employee_id:
            return LocalOperationalIntent(
                _local_envelope(
                    "EMP-READ-001",
                    action_class=ActionClass.READ,
                    sensitivity=Sensitivity.MEDIUM,
                    parameters={
                        "status": _requested_status(text),
                        "view": "ascii",
                        "include_all": True,
                    },
                )
            )
    return None


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

    if intent.function_id == "EMP-PAY-002":
        period = local_payroll_presence_period(request, today=today)
        if period is not None:
            year, month = period
            updates["parameters"] = {"year": year, "month": month}

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
    "is_payroll_presence_request",
    "local_contract_expiry_fallback_interval",
    "local_employee_search_query",
    "local_payroll_presence_period",
    "minimize_hr_router_request",
    "normalize_hr_intent",
]
