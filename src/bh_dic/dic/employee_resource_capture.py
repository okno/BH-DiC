"""Typed projections for live first-party employee resource responses."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from bh_dic.dic.errors import DicUiChangedError
from bh_dic.dic.models import ContractRecord, DocumentMetadata, DocumentQuery, MaturationRecord
from bh_dic.dic.paginated_capture import PaginatedEndpointContract

CONTRACTS_ENDPOINT = PaginatedEndpointContract(
    resource="employees.contracts",
    endpoint_path="/backend_apiV2/contracts",
    allowed_query_keys=frozenset(
        {
            "employee_id",
            "filter_type",
            "page",
            "per_page",
            "search",
            "search_fields",
        }
    ),
    employee_query_key="employee_id",
    required_item_keys=frozenset(
        {
            "id",
            "employee",
            "flexible_workinghours",
            "hours_type",
            "ongoing",
            "part_time_percentage",
            "permanent",
            "valid_from",
            "valid_to",
        }
    ),
)

MATURATIONS_ENDPOINT = PaginatedEndpointContract(
    resource="employees.maturations",
    endpoint_path="/backend_apiV2/maturations",
    allowed_query_keys=frozenset(
        {
            "employee_id",
            "filter[0][field]",
            "filter[0][op]",
            "filter[0][value]",
            "filter_type",
            "filter_validity",
            "page",
            "per_page",
            "search",
            "search_fields",
        }
    ),
    employee_query_key="employee_id",
    required_item_keys=frozenset({"id", "employee", "counter", "from", "to", "ongoing", "valid"}),
)

DOCUMENTS_ENDPOINT = PaginatedEndpointContract(
    resource="employees.documents",
    endpoint_path="/backend_apiV2/documents",
    allowed_query_keys=frozenset(
        {
            "employee_ids",
            "filter[0][field]",
            "filter[0][op]",
            "filter[0][value]",
            "filter_type",
            "filter_validity",
            "page",
            "per_page",
            "search",
            "search_fields",
            "sort",
        }
    ),
    employee_query_key="employee_ids",
    required_item_keys=frozenset(
        {
            "id",
            "employee_id",
            "title",
            "category",
            "date",
            "expiration_date",
            "requested",
        }
    ),
)


def _failure(resource: str, reason: str) -> DicUiChangedError:
    return DicUiChangedError(f"{resource} response projection failed: {reason}")


def _positive_id(value: object, *, resource: str) -> str:
    if type(value) is not int or value < 1:
        raise _failure(resource, "invalid stable identifier")
    return str(value)


def _employee_matches(value: object, employee_id: str, *, resource: str) -> None:
    expected: object = int(employee_id) if employee_id.isdigit() else employee_id
    if value != expected:
        raise _failure(resource, "employee identity mismatch")


def _bounded_text(
    value: object,
    *,
    resource: str,
    maximum: int,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _failure(resource, "invalid text field")
    return value.strip()


def _strict_bool(value: object, *, resource: str) -> bool:
    if type(value) is not bool:
        raise _failure(resource, "invalid boolean field")
    return value


def _month_year(value: object, *, resource: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        raise _failure(resource, "invalid month/year field")
    month = value.get("month")
    year = value.get("year")
    if (
        type(month) is not int
        or not 1 <= month <= 12
        or type(year) is not int
        or not 2000 <= year <= 2200
    ):
        raise _failure(resource, "invalid month/year field")
    return f"{month:02d}/{year}"


def contracts_from_items(
    items: tuple[dict[str, object], ...], *, employee_id: str
) -> tuple[ContractRecord, ...]:
    records: list[ContractRecord] = []
    identifiers: set[str] = set()
    resource = CONTRACTS_ENDPOINT.resource
    for item in items:
        employee = item["employee"]
        if not isinstance(employee, dict):
            raise _failure(resource, "invalid employee reference")
        _employee_matches(employee.get("id"), employee_id, resource=resource)
        contract_id = _positive_id(item["id"], resource=resource)
        if contract_id in identifiers:
            raise _failure(resource, "duplicate stable identifier")
        identifiers.add(contract_id)
        permanent = _strict_bool(item["permanent"], resource=resource)
        flexible = _strict_bool(item["flexible_workinghours"], resource=resource)
        ongoing = _strict_bool(item["ongoing"], resource=resource)
        percentage = item["part_time_percentage"]
        if percentage is not None and (type(percentage) is not int or not 0 <= percentage <= 100):
            raise _failure(resource, "invalid work percentage")
        start = _bounded_text(item["valid_from"], resource=resource, maximum=32)
        end = _bounded_text(item["valid_to"], resource=resource, maximum=32, optional=True)
        hours_type = _bounded_text(item["hours_type"], resource=resource, maximum=128)
        level = _bounded_text(item.get("level"), resource=resource, maximum=128, optional=True)
        note = _bounded_text(item.get("note"), resource=resource, maximum=512, optional=True)
        try:
            records.append(
                ContractRecord(
                    contract_id=contract_id,
                    employee_id=employee_id,
                    stable_identifier=True,
                    actionable=False,
                    schedule=hours_type,
                    flexibility="flessibile" if flexible else "non flessibile",
                    permanent=permanent,
                    start_date=start,
                    end_date=end,
                    ccnl_level=level,
                    work_regime=(
                        hours_type
                        if percentage is None
                        else "tempo pieno"
                        if percentage == 100
                        else f"part-time {percentage}%"
                    ),
                    description="[REDACTED]" if note else None,
                    contract_type=("tempo indeterminato" if permanent else "tempo determinato"),
                    status="in corso" if ongoing else "concluso",
                    period=f"{start} → {end or 'indeterminato'}",
                )
            )
        except ValidationError:
            raise _failure(resource, "invalid contract projection") from None
    return tuple(records)


def maturations_from_items(
    items: tuple[dict[str, object], ...], *, employee_id: str
) -> tuple[MaturationRecord, ...]:
    records: list[MaturationRecord] = []
    identifiers: set[str] = set()
    resource = MATURATIONS_ENDPOINT.resource
    for item in items:
        employee = item["employee"]
        counter = item["counter"]
        if not isinstance(employee, dict) or not isinstance(counter, dict):
            raise _failure(resource, "invalid resource reference")
        _employee_matches(employee.get("id"), employee_id, resource=resource)
        identifier = _positive_id(item["id"], resource=resource)
        if identifier in identifiers:
            raise _failure(resource, "duplicate stable identifier")
        identifiers.add(identifier)
        category = _bounded_text(counter.get("name"), resource=resource, maximum=128)
        valid = _strict_bool(item["valid"], resource=resource)
        ongoing = _strict_bool(item["ongoing"], resource=resource)
        try:
            records.append(
                MaturationRecord(
                    maturation_id=identifier,
                    employee_id=employee_id,
                    category=category or "unknown",
                    valid_from=_month_year(item["from"], resource=resource),
                    valid_to=_month_year(item["to"], resource=resource, optional=True),
                    status=("attiva" if valid and ongoing else "non attiva"),
                )
            )
        except ValidationError:
            raise _failure(resource, "invalid maturation projection") from None
    return tuple(records)


def documents_from_items(
    items: tuple[dict[str, object], ...], *, employee_id: str, query: DocumentQuery
) -> tuple[DocumentMetadata, ...]:
    records: list[DocumentMetadata] = []
    identifiers: set[str] = set()
    resource = DOCUMENTS_ENDPOINT.resource
    normalized_query = (query.query or "").strip().casefold()
    for item in items:
        _employee_matches(item["employee_id"], employee_id, resource=resource)
        identifier = _positive_id(item["id"], resource=resource)
        if identifier in identifiers:
            raise _failure(resource, "duplicate stable identifier")
        identifiers.add(identifier)
        title = _bounded_text(item["title"], resource=resource, maximum=256)
        category_value = item["category"]
        if not isinstance(category_value, dict):
            raise _failure(resource, "invalid category")
        raw_category = category_value.get("name")
        category = (
            None
            if isinstance(raw_category, str) and not raw_category.strip()
            else _bounded_text(raw_category, resource=resource, maximum=128, optional=True)
        )
        requested = _strict_bool(item["requested"], resource=resource)
        state: Literal["uploaded", "pending", "unknown"] = "pending" if requested else "uploaded"
        if normalized_query and normalized_query not in (title or "").casefold():
            continue
        if query.category and (category or "").casefold() != query.category.casefold():
            continue
        if query.state != "all" and state != query.state:
            continue
        uploaded_at = _bounded_text(item["date"], resource=resource, maximum=64)
        expiry = _bounded_text(
            item["expiration_date"], resource=resource, maximum=32, optional=True
        )
        try:
            records.append(
                DocumentMetadata(
                    document_id=identifier,
                    employee_id=employee_id,
                    stable_identifier=True,
                    actionable=False,
                    title_redacted="[REDACTED]",
                    category=category,
                    expiry_date=expiry,
                    uploaded_at=uploaded_at,
                    uploaded_by_redacted="[PERSON_REDACTED]" if item.get("creator") else None,
                    state=state,
                )
            )
        except ValidationError:
            raise _failure(resource, "invalid document projection") from None
    return tuple(records)


__all__ = [
    "CONTRACTS_ENDPOINT",
    "DOCUMENTS_ENDPOINT",
    "MATURATIONS_ENDPOINT",
    "contracts_from_items",
    "documents_from_items",
    "maturations_from_items",
]
