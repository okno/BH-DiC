"""Closed catalog and dynamic exposure for OpenAI function tools."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from bh_dic.openai.schemas import ActionClass
from bh_dic.policies.catalog import FUNCTION_CATALOG


@dataclass(frozen=True, slots=True)
class IntentTool:
    name: str
    description: str
    function_ids: tuple[str, ...]
    action_class: ActionClass
    intent: str

    def __post_init__(self) -> None:
        unknown = set(self.function_ids).difference(FUNCTION_CATALOG)
        if unknown:
            raise RuntimeError(f"OpenAI tool references unknown Function ID(s): {sorted(unknown)}")


TOOL_CATALOG: tuple[IntentTool, ...] = (
    IntentTool(
        "list_employees",
        "Elenca o conta dipendenti autorizzati.",
        ("EMP-READ-001",),
        ActionClass.READ,
        "list_employees",
    ),
    IntentTool(
        "get_employee_summary",
        "Legge il riepilogo redatto di un dipendente.",
        ("EMP-READ-002",),
        ActionClass.READ,
        "employee_summary",
    ),
    IntentTool(
        "search_employees",
        "Cerca un dipendente con criteri consentiti.",
        ("EMP-SEARCH-001",),
        ActionClass.SEARCH,
        "search_employees",
    ),
    IntentTool(
        "filter_employees",
        "Applica il filtro Attivi, Disattivati o Tutti.",
        ("EMP-FILTER-001",),
        ActionClass.FILTER,
        "filter_employees",
    ),
    IntentTool(
        "sort_employees",
        "Ordina l'elenco dipendenti.",
        ("EMP-SORT-001",),
        ActionClass.FILTER,
        "sort_employees",
    ),
    IntentTool(
        "paginate_employees",
        "Seleziona una pagina dell'elenco.",
        ("EMP-PAGE-001",),
        ActionClass.FILTER,
        "paginate_employees",
    ),
    IntentTool(
        "get_contracts",
        "Consulta contratti e scadenze.",
        ("EMP-CONTRACT-001",),
        ActionClass.READ,
        "get_contracts",
    ),
    IntentTool(
        "get_roles", "Consulta gruppi e ruoli.", ("EMP-RBAC-001",), ActionClass.READ, "get_roles"
    ),
    IntentTool(
        "get_timestamp_status",
        "Consulta lo stato della timbratura.",
        ("EMP-TIME-001",),
        ActionClass.READ,
        "get_timestamp_status",
    ),
    IntentTool(
        "get_maturations",
        "Consulta i ratei di maturazione.",
        ("EMP-MAT-001",),
        ActionClass.READ,
        "get_maturations",
    ),
    IntentTool(
        "get_balances",
        "Consulta il bilancio autorizzato.",
        ("EMP-BAL-001",),
        ActionClass.READ,
        "get_balances",
    ),
    IntentTool(
        "get_payroll_metadata",
        "Consulta solo metadati minimizzati delle buste paga.",
        ("EMP-PAY-001",),
        ActionClass.READ,
        "get_payroll_metadata",
    ),
    IntentTool(
        "find_employees_with_payroll",
        "Trova i dipendenti con una busta paga disponibile in uno specifico mese e anno.",
        ("EMP-PAY-002",),
        ActionClass.READ,
        "find_employees_with_payroll",
    ),
    IntentTool(
        "get_document_metadata",
        "Consulta solo metadati autorizzati dei documenti.",
        ("EMP-DOC-001",),
        ActionClass.READ,
        "get_document_metadata",
    ),
    IntentTool(
        "prepare_employee_update",
        "Prepara, senza eseguire, una modifica anagrafica.",
        ("EMP-UPDATE-001",),
        ActionClass.PREPARE_WRITE,
        "prepare_employee_update",
    ),
    IntentTool(
        "prepare_employee_create",
        "Prepara, senza eseguire, la creazione di un dipendente.",
        ("EMP-CREATE-001",),
        ActionClass.PREPARE_WRITE,
        "prepare_employee_create",
    ),
    IntentTool(
        "prepare_contract_change",
        "Prepara creazione o modifica contratto.",
        ("EMP-CONTRACT-002",),
        ActionClass.PREPARE_WRITE,
        "prepare_contract_change",
    ),
    IntentTool(
        "prepare_invite_action",
        "Prepara un'azione invito o collegamento account.",
        ("EMP-CONNECT-001", "EMP-CONNECT-002", "EMP-INVITE-001", "EMP-INVITE-002"),
        ActionClass.PREPARE_WRITE,
        "prepare_invite_action",
    ),
    IntentTool(
        "prepare_status_change",
        "Prepara attivazione o disattivazione.",
        ("EMP-STATUS-001", "EMP-STATUS-002"),
        ActionClass.PREPARE_WRITE,
        "prepare_status_change",
    ),
    IntentTool(
        "prepare_role_change",
        "Prepara una modifica di ruoli senza eseguirla.",
        ("EMP-RBAC-002",),
        ActionClass.PREPARE_WRITE,
        "prepare_role_change",
    ),
    IntentTool(
        "prepare_document_upload",
        "Prepara un upload già quarantinato e scansionato.",
        ("EMP-DOC-002",),
        ActionClass.FILE_UPLOAD,
        "prepare_document_upload",
    ),
    IntentTool(
        "prepare_document_change",
        "Prepara modifica o eliminazione metadati documento.",
        ("EMP-DOC-004", "EMP-DOC-005"),
        ActionClass.PREPARE_WRITE,
        "prepare_document_change",
    ),
    IntentTool(
        "prepare_destructive_action",
        "Prepara un'azione critica disabilitata per default.",
        ("EMP-DOC-003", "EMP-DELETE-001", "EMP-CONTRACT-003", "EMP-BAL-002"),
        ActionClass.PREPARE_WRITE,
        "prepare_destructive_action",
    ),
    IntentTool(
        "prepare_maturation_change",
        "Prepara una nuova maturazione.",
        ("EMP-MAT-002",),
        ActionClass.PREPARE_WRITE,
        "prepare_maturation_change",
    ),
    IntentTool(
        "prepare_export",
        "Prepara un export locale protetto.",
        ("EMP-EXPORT-001",),
        ActionClass.EXPORT,
        "prepare_export",
    ),
)

_BY_NAME = {tool.name: tool for tool in TOOL_CATALOG}


def tool_by_name(name: str) -> IntentTool | None:
    return _BY_NAME.get(name)


def _parameters_schema(function_ids: list[str]) -> dict[str, Any]:
    nullable_string: dict[str, Any] = {"type": ["string", "null"]}
    write_contracts: list[str] = []
    for function_id in function_ids:
        spec = FUNCTION_CATALOG.get(function_id)
        if spec is None or not spec.is_write:
            continue
        fields = ", ".join(
            f"{parameter.name}:{parameter.kind.value.lower()}{'!' if parameter.required else '?'}"
            for parameter in spec.write_parameters
        )
        required_any = (
            f"; at least one of {', '.join(sorted(spec.write_required_any))}"
            if spec.write_required_any
            else ""
        )
        write_contracts.append(f"{function_id} => {fields}{required_any}")
    parameter_description = (
        "Oggetto JSON con schema chiuso; ! obbligatorio, ? opzionale. "
        + " | ".join(write_contracts)
        if write_contracts
        else "Oggetto JSON piccolo con soli parametri necessari alla lettura."
    )
    return {
        "type": "object",
        "properties": {
            "function_id": {"type": "string", "enum": function_ids},
            "employee_id": {**nullable_string, "maxLength": 64},
            "query": {**nullable_string, "maxLength": 500},
            "parameters_json": {
                **nullable_string,
                "maxLength": 4_000,
                "description": parameter_description,
            },
            "date_from": {
                **nullable_string,
                "description": (
                    "Data assoluta ISO YYYY-MM-DD; null per un periodo relativo di lettura, "
                    "risolto localmente."
                ),
            },
            "date_to": {
                **nullable_string,
                "description": (
                    "Data assoluta ISO YYYY-MM-DD; null per un periodo relativo di lettura, "
                    "risolto localmente."
                ),
            },
            "requires_clarification": {"type": "boolean"},
            "clarification_question": {**nullable_string, "maxLength": 300},
            "sensitivity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "function_id",
            "employee_id",
            "query",
            "parameters_json",
            "date_from",
            "date_to",
            "requires_clarification",
            "clarification_question",
            "sensitivity",
            "confidence",
        ],
        "additionalProperties": False,
    }


def build_openai_tools(allowed_function_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Return only tools whose individual function IDs are locally authorized."""

    allowed = frozenset(
        function_id
        for function_id in allowed_function_ids
        if function_id in FUNCTION_CATALOG and FUNCTION_CATALOG[function_id].expose_to_model
    )
    result: list[dict[str, Any]] = []
    for tool in TOOL_CATALOG:
        visible_ids = [item for item in tool.function_ids if item in allowed]
        if not visible_ids:
            continue
        result.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": _parameters_schema(visible_ids),
                "strict": True,
            }
        )
    result.append(
        {
            "type": "function",
            "name": "unsupported_request",
            "description": "Usa questo tool se nessuna funzione esposta soddisfa la richiesta.",
            "parameters": _parameters_schema(["UNSUPPORTED"]),
            "strict": True,
        }
    )
    return result


def exposed_tool_names(allowed_function_ids: Iterable[str]) -> frozenset[str]:
    return frozenset(tool["name"] for tool in build_openai_tools(allowed_function_ids))
