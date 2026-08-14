"""Authoritative Function ID catalog.

Every consumer (Discord, OpenAI exposure, policy, approvals and documentation)
should consume these immutable specs instead of maintaining another ID list.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from bh_dic.dic.values import canonical_decimal_text
from bh_dic.policies.roles import LogicalRole, RoleRule, ScopedRoleRule


class ActionClass(StrEnum):
    READ = "READ"
    SEARCH = "SEARCH"
    FILTER = "FILTER"
    PREPARE_WRITE = "PREPARE_WRITE"
    FILE_UPLOAD = "FILE_UPLOAD"
    EXPORT = "EXPORT"


class Sensitivity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WriteParameterKind(StrEnum):
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    DECIMAL = "DECIMAL"
    DATE = "DATE"


@dataclass(frozen=True, slots=True)
class WriteParameterSpec:
    """One catalog-authorized input accepted by every write entry point."""

    name: str
    kind: WriteParameterKind
    required: bool = True
    min_length: int | None = None
    max_length: int | None = None
    min_value: int | None = None
    max_value: int | None = None
    max_integer_digits: int | None = None
    max_decimal_places: int | None = None
    pattern: str | None = None
    choices: frozenset[str] = field(default_factory=frozenset)


# Backwards-compatible names for integrations which already import the operator
# types.  The schema is no longer operator-only: it is authoritative for every
# write path, including OpenAI candidates and attachment workflows.
OperatorParameterKind = WriteParameterKind
OperatorParameterSpec = WriteParameterSpec


class ResourceSnapshotKind(StrEnum):
    EMPLOYEE = "EMPLOYEE"
    EMPLOYEE_CREATE = "EMPLOYEE_CREATE"
    CONTRACT = "CONTRACT"
    MATURATION_COLLECTION = "MATURATION_COLLECTION"
    BALANCE_CORRECTION = "BALANCE_CORRECTION"
    ACCOUNT = "ACCOUNT"
    ROLE = "ROLE"
    DOCUMENT = "DOCUMENT"
    DOCUMENT_COLLECTION = "DOCUMENT_COLLECTION"
    EXPORT_SOURCE = "EXPORT_SOURCE"


class WriteParameterValidationError(ValueError):
    """A write candidate does not match its closed catalog schema."""


PREVIEW_FIELD_MASKS: Mapping[str, str] = MappingProxyType(
    {
        "first_name": "[PII_REDACTED]",
        "last_name": "[PII_REDACTED]",
        "name": "[PII_REDACTED]",
        "payroll_number": "[PII_REDACTED]",
        "tax_code": "[PII_REDACTED]",
        "birth_date": "[PII_REDACTED]",
        "iban": "[PII_REDACTED]",
        "phone": "[PII_REDACTED]",
        "business_email": "[PII_REDACTED]",
        "address": "[PII_REDACTED]",
        "notes": "[REDACTED]",
        "description": "[REDACTED]",
        "title": "[REDACTED]",
        "original_filename": "[REDACTED]",
        "upload_id": "[REDACTED]",
    }
)


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    function_id: str
    title: str
    action_class: ActionClass
    sensitivity: Sensitivity
    feature_flags: tuple[str, ...]
    role_rules: tuple[ScopedRoleRule, ...]
    confirmation_required: bool = False
    approvals_required: int = 0
    approver_roles: frozenset[LogicalRole] = field(default_factory=frozenset)
    requires_target: bool = False
    destructive: bool = False
    expose_to_model: bool = True
    enabled_by_default: bool = False
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    text_confirmation_template: str | None = None
    write_parameters: tuple[WriteParameterSpec, ...] = ()
    write_required_any: frozenset[str] = field(default_factory=frozenset)
    write_selectors: frozenset[str] = field(default_factory=frozenset)
    resource_snapshot: ResourceSnapshotKind | None = None
    fixed_preview_after: tuple[tuple[str, str], ...] = ()
    deletes_resource: bool = False
    always_effectful: bool = False
    operator_live_available: bool = True

    @property
    def is_write(self) -> bool:
        return self.action_class in {
            ActionClass.PREPARE_WRITE,
            ActionClass.FILE_UPLOAD,
            ActionClass.EXPORT,
        }

    def role_rule(self, scope: str) -> RoleRule | None:
        for scoped in self.role_rules:
            if scoped.scope == scope:
                return scoped.rule
        for scoped in self.role_rules:
            if scoped.scope == "default":
                return scoped.rule
        return None

    @property
    def operator_parameters(self) -> tuple[WriteParameterSpec, ...]:
        """Compatibility view; hidden operator routes consume the same write schema."""

        return self.write_parameters

    @property
    def operator_required_any(self) -> frozenset[str]:
        return self.write_required_any

    @property
    def operational_parameter_names(self) -> frozenset[str]:
        return frozenset(parameter.name for parameter in self.write_parameters) - {"motivation"}

    def preview_mask(self, field_name: str) -> str | None:
        """Return the catalog-owned placeholder for sensitive preview fields."""

        return PREVIEW_FIELD_MASKS.get(field_name)


def _rule(
    *roles: LogicalRole,
    scope: str = "default",
    all_of: tuple[LogicalRole, ...] = (),
    entitlements: tuple[str, ...] = (),
) -> ScopedRoleRule:
    return ScopedRoleRule(
        scope,
        RoleRule(
            all_of=frozenset(all_of),
            any_of=frozenset(roles),
            entitlements=frozenset(entitlements),
        ),
    )


def _read(
    function_id: str,
    title: str,
    role_rules: tuple[ScopedRoleRule, ...],
    *,
    action_class: ActionClass = ActionClass.READ,
    sensitivity: Sensitivity = Sensitivity.MEDIUM,
    target: bool = False,
) -> FunctionSpec:
    return FunctionSpec(
        function_id,
        title,
        action_class,
        sensitivity,
        ("ENABLE_READ_ACTIONS",),
        role_rules,
        requires_target=target,
        enabled_by_default=True,
    )


def _write(
    function_id: str,
    title: str,
    flag: str,
    roles: tuple[ScopedRoleRule, ...],
    *,
    approvals: int = 0,
    approver_roles: frozenset[LogicalRole] = frozenset({LogicalRole.APPROVER}),
    sensitivity: Sensitivity = Sensitivity.HIGH,
    target: bool = True,
    destructive: bool = False,
    expose: bool = True,
    action_class: ActionClass = ActionClass.PREPARE_WRITE,
    capabilities: frozenset[str] = frozenset(),
    confirmation_template: str | None = None,
    write_parameters: tuple[WriteParameterSpec, ...],
    write_required_any: frozenset[str] = frozenset(),
    write_selectors: frozenset[str] = frozenset(),
    resource_snapshot: ResourceSnapshotKind,
    fixed_preview_after: tuple[tuple[str, str], ...] = (),
    deletes_resource: bool = False,
    always_effectful: bool = False,
    operator_live_available: bool = True,
) -> FunctionSpec:
    return FunctionSpec(
        function_id,
        title,
        action_class,
        sensitivity,
        ("ENABLE_WRITE_ACTIONS", flag),
        roles,
        confirmation_required=True,
        approvals_required=approvals,
        approver_roles=approver_roles if approvals else frozenset(),
        requires_target=target,
        destructive=destructive,
        expose_to_model=expose,
        required_capabilities=capabilities,
        text_confirmation_template=confirmation_template,
        write_parameters=write_parameters,
        write_required_any=write_required_any,
        write_selectors=write_selectors,
        resource_snapshot=resource_snapshot,
        fixed_preview_after=fixed_preview_after,
        deletes_resource=deletes_resource,
        always_effectful=always_effectful,
        operator_live_available=operator_live_available,
    )


HR_READ = (_rule(LogicalRole.HR_READ),)
HR_WRITE = (_rule(LogicalRole.HR_WRITE),)
IAM = (_rule(LogicalRole.IAM_OPERATOR),)
DOCS = (_rule(LogicalRole.DOCUMENT_OPERATOR),)
APPROVERS = frozenset({LogicalRole.APPROVER, LogicalRole.SYSTEM_ADMIN})
OPTIONAL_MOTIVATION = WriteParameterSpec(
    "motivation",
    WriteParameterKind.TEXT,
    required=False,
    min_length=3,
    max_length=500,
)
REQUIRED_MOTIVATION = WriteParameterSpec(
    "motivation",
    WriteParameterKind.TEXT,
    min_length=3,
    max_length=500,
)


def _text(
    name: str,
    *,
    required: bool = False,
    max_length: int = 256,
    pattern: str | None = None,
    choices: frozenset[str] = frozenset(),
) -> WriteParameterSpec:
    return WriteParameterSpec(
        name,
        WriteParameterKind.TEXT,
        required=required,
        min_length=1,
        max_length=max_length,
        pattern=pattern,
        choices=choices,
    )


def _date(name: str, *, required: bool = False) -> WriteParameterSpec:
    return WriteParameterSpec(name, WriteParameterKind.DATE, required=required)


_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_UPLOAD_ID = r"^[0-9a-f]{32}$"
_EMPLOYEE_FIELDS = (
    _text("first_name", max_length=128),
    _text("last_name", max_length=128),
    _text("payroll_number", max_length=64),
    _text("tax_code", max_length=32),
    _date("birth_date"),
    _text("iban", max_length=64),
    _text("job_title", max_length=128),
    _text("phone", max_length=64),
    _text("business_email", max_length=320),
    _text("address", max_length=256),
    _text("workplace", max_length=128),
    _text("notes", max_length=512),
)
_EMPLOYEE_FIELD_NAMES = frozenset(parameter.name for parameter in _EMPLOYEE_FIELDS)

_SPECS = (
    _read(
        "EMP-READ-001",
        "Elenco e conteggio dipendenti",
        (
            _rule(LogicalRole.READ_ONLY, scope="aggregate"),
            _rule(LogicalRole.READ_ONLY, scope="exposure"),
            _rule(LogicalRole.HR_READ),
        ),
        sensitivity=Sensitivity.LOW,
    ),
    _read("EMP-READ-002", "Dettaglio anagrafico redatto", HR_READ, target=True),
    _read("EMP-SEARCH-001", "Ricerca dipendente", HR_READ, action_class=ActionClass.SEARCH),
    _read("EMP-FILTER-001", "Filtri elenco", HR_READ, action_class=ActionClass.FILTER),
    _read("EMP-SORT-001", "Ordinamento elenco", HR_READ, action_class=ActionClass.FILTER),
    _read("EMP-PAGE-001", "Paginazione", HR_READ, action_class=ActionClass.FILTER),
    _read("EMP-CONTRACT-001", "Consultazione contratti", HR_READ),
    _read("EMP-RBAC-001", "Consultazione gruppi e ruoli", HR_READ, target=True),
    _read("EMP-TIME-001", "Consultazione timbratura", HR_READ, target=True),
    _read("EMP-MAT-001", "Consultazione maturazioni", HR_READ, target=True),
    _read(
        "EMP-BAL-001",
        "Consultazione bilancio",
        (_rule(LogicalRole.HR_READ, entitlements=("balances:read",)),),
        sensitivity=Sensitivity.HIGH,
        target=True,
    ),
    _read("EMP-PAY-001", "Metadati buste paga", HR_READ, sensitivity=Sensitivity.HIGH, target=True),
    _read("EMP-DOC-001", "Metadati documenti", DOCS, sensitivity=Sensitivity.HIGH, target=True),
    _write(
        "EMP-UPDATE-001",
        "Modifica dati dipendente",
        "ENABLE_EMPLOYEE_UPDATE",
        HR_WRITE,
        write_parameters=(*_EMPLOYEE_FIELDS, OPTIONAL_MOTIVATION),
        write_required_any=_EMPLOYEE_FIELD_NAMES,
        resource_snapshot=ResourceSnapshotKind.EMPLOYEE,
    ),
    _write(
        "EMP-CREATE-001",
        "Creazione dipendente",
        "ENABLE_EMPLOYEE_CREATE",
        HR_WRITE,
        target=False,
        write_parameters=(
            _text(
                "creation_mode",
                max_length=16,
                choices=frozenset({"manual"}),
            ),
            _text("first_name", required=True, max_length=128),
            _text("last_name", required=True, max_length=128),
            *_EMPLOYEE_FIELDS[2:],
            OPTIONAL_MOTIVATION,
        ),
        resource_snapshot=ResourceSnapshotKind.EMPLOYEE_CREATE,
    ),
    _write(
        "EMP-CONTRACT-002",
        "Creazione o modifica contratto",
        "ENABLE_CONTRACT_WRITE",
        HR_WRITE,
        approvals=1,
        approver_roles=frozenset({LogicalRole.HR_WRITE, LogicalRole.APPROVER}),
        write_parameters=(
            _text("contract_id", max_length=128, pattern=_IDENTIFIER),
            _text("schedule", max_length=128),
            _text("flexibility", max_length=128),
            WriteParameterSpec("permanent", WriteParameterKind.BOOLEAN, required=False),
            _date("start_date"),
            _date("end_date"),
            _text("ccnl_level", max_length=128),
            _text("work_regime", max_length=128),
            _text("description", max_length=256),
            _text("contract_type", max_length=128),
            OPTIONAL_MOTIVATION,
        ),
        write_required_any=frozenset(
            {
                "schedule",
                "flexibility",
                "permanent",
                "start_date",
                "end_date",
                "ccnl_level",
                "work_regime",
                "description",
                "contract_type",
            }
        ),
        write_selectors=frozenset({"contract_id"}),
        resource_snapshot=ResourceSnapshotKind.CONTRACT,
    ),
    _write(
        "EMP-MAT-002",
        "Nuova maturazione",
        "ENABLE_MATURATION_WRITE",
        HR_WRITE,
        approvals=1,
        approver_roles=frozenset({LogicalRole.HR_WRITE, LogicalRole.APPROVER}),
        write_parameters=(
            _text("category", required=True, max_length=128),
            _date("valid_from"),
            _date("valid_to"),
            OPTIONAL_MOTIVATION,
        ),
        resource_snapshot=ResourceSnapshotKind.MATURATION_COLLECTION,
    ),
    _write(
        "EMP-BAL-002",
        "Correzione bilancio",
        "ENABLE_BALANCE_CORRECTION",
        HR_WRITE,
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        expose=False,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(
            WriteParameterSpec("year", WriteParameterKind.INTEGER, min_value=2000, max_value=2100),
            WriteParameterSpec("month", WriteParameterKind.INTEGER, min_value=1, max_value=12),
            WriteParameterSpec("category", WriteParameterKind.TEXT, min_length=1, max_length=64),
            WriteParameterSpec(
                "previous_value",
                WriteParameterKind.DECIMAL,
                max_integer_digits=9,
                max_decimal_places=4,
            ),
            WriteParameterSpec(
                "amount",
                WriteParameterKind.DECIMAL,
                max_integer_digits=9,
                max_decimal_places=4,
            ),
            REQUIRED_MOTIVATION,
        ),
        write_selectors=frozenset({"year", "month", "category", "previous_value"}),
        resource_snapshot=ResourceSnapshotKind.BALANCE_CORRECTION,
    ),
    _write(
        "EMP-CONNECT-001",
        "Collegamento dipendente",
        "ENABLE_ACCOUNT_CONNECT",
        IAM,
        approvals=1,
        approver_roles=frozenset(
            {LogicalRole.IAM_OPERATOR, LogicalRole.HR_WRITE, LogicalRole.APPROVER}
        ),
        write_parameters=(OPTIONAL_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.ACCOUNT,
        fixed_preview_after=(("account_state", "connected"),),
    ),
    _write(
        "EMP-CONNECT-002",
        "Scollegamento dipendente",
        "ENABLE_ACCOUNT_DISCONNECT",
        IAM,
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(REQUIRED_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.ACCOUNT,
        fixed_preview_after=(("account_state", "not_connected"),),
    ),
    _write(
        "EMP-INVITE-001",
        "Reinvio invito",
        "ENABLE_INVITE_ACTIONS",
        IAM,
        write_parameters=(OPTIONAL_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.ACCOUNT,
        fixed_preview_after=(("account_state", "invited"),),
        always_effectful=True,
        operator_live_available=False,
    ),
    _write(
        "EMP-INVITE-002",
        "Annullamento invito",
        "ENABLE_INVITE_ACTIONS",
        IAM,
        write_parameters=(OPTIONAL_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.ACCOUNT,
        fixed_preview_after=(("account_state", "not_connected"),),
    ),
    _write(
        "EMP-RBAC-002",
        "Modifica permessi e ruoli",
        "ENABLE_RBAC_WRITE",
        (_rule(scope="default", all_of=(LogicalRole.IAM_OPERATOR, LogicalRole.APPROVER)),),
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        expose=False,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(
            REQUIRED_MOTIVATION,
            WriteParameterSpec("role_name", WriteParameterKind.TEXT, min_length=1, max_length=128),
            WriteParameterSpec("enabled", WriteParameterKind.BOOLEAN),
        ),
        write_selectors=frozenset({"role_name"}),
        resource_snapshot=ResourceSnapshotKind.ROLE,
    ),
    _write(
        "EMP-STATUS-001",
        "Disattivazione dipendente",
        "ENABLE_STATUS_CHANGE",
        (_rule(scope="default", all_of=(LogicalRole.HR_WRITE, LogicalRole.APPROVER)),),
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(REQUIRED_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.ACCOUNT,
        fixed_preview_after=(("employee_state", "inactive"),),
    ),
    _write(
        "EMP-STATUS-002",
        "Riattivazione dipendente",
        "ENABLE_STATUS_CHANGE",
        HR_WRITE,
        approvals=1,
        approver_roles=frozenset({LogicalRole.APPROVER, LogicalRole.SYSTEM_ADMIN}),
        write_parameters=(OPTIONAL_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.ACCOUNT,
        fixed_preview_after=(("employee_state", "active"),),
    ),
    _write(
        "EMP-DOC-002",
        "Upload documento",
        "ENABLE_DOCUMENT_UPLOAD",
        DOCS,
        action_class=ActionClass.FILE_UPLOAD,
        capabilities=frozenset({"clamav"}),
        write_parameters=(
            _text("upload_id", required=True, max_length=32, pattern=_UPLOAD_ID),
            _text("category", required=True, max_length=128),
            _date("expiry_date"),
            OPTIONAL_MOTIVATION,
        ),
        write_selectors=frozenset({"upload_id"}),
        resource_snapshot=ResourceSnapshotKind.DOCUMENT_COLLECTION,
    ),
    _write(
        "EMP-DOC-004",
        "Modifica metadati documento",
        "ENABLE_DOCUMENT_UPDATE",
        DOCS,
        write_parameters=(
            _text("document_id", required=True, max_length=128, pattern=_IDENTIFIER),
            _text("category", max_length=128),
            _date("expiry_date"),
            OPTIONAL_MOTIVATION,
        ),
        write_required_any=frozenset({"category", "expiry_date"}),
        write_selectors=frozenset({"document_id"}),
        resource_snapshot=ResourceSnapshotKind.DOCUMENT,
    ),
    _write(
        "EMP-DOC-005",
        "Eliminazione documento",
        "ENABLE_DOCUMENT_DELETE",
        DOCS,
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(
            _text("document_id", required=True, max_length=128, pattern=_IDENTIFIER),
            REQUIRED_MOTIVATION,
        ),
        write_selectors=frozenset({"document_id"}),
        resource_snapshot=ResourceSnapshotKind.DOCUMENT,
        deletes_resource=True,
        operator_live_available=False,
    ),
    _write(
        "EMP-EXPORT-001",
        "Esportazione locale protetta",
        "ENABLE_EXPORT",
        (
            _rule(LogicalRole.HR_READ, LogicalRole.DOCUMENT_OPERATOR, scope="exposure"),
            _rule(LogicalRole.HR_READ, scope="employees"),
            _rule(
                LogicalRole.HR_READ,
                scope="balances",
                entitlements=("balances:read",),
            ),
            _rule(LogicalRole.DOCUMENT_OPERATOR, scope="documents"),
        ),
        action_class=ActionClass.EXPORT,
        target=False,
        write_parameters=(
            _text(
                "scope",
                required=True,
                max_length=16,
                choices=frozenset({"employees", "balances", "documents"}),
            ),
            WriteParameterSpec(
                "year",
                WriteParameterKind.INTEGER,
                required=False,
                min_value=2000,
                max_value=2100,
            ),
            OPTIONAL_MOTIVATION,
        ),
        resource_snapshot=ResourceSnapshotKind.EXPORT_SOURCE,
        always_effectful=True,
        operator_live_available=False,
    ),
    _write(
        "EMP-DOC-003",
        "Download documento in area locale protetta",
        "ENABLE_DOCUMENT_DOWNLOAD",
        DOCS,
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        expose=False,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(
            _text("document_id", required=True, max_length=128, pattern=_IDENTIFIER),
            REQUIRED_MOTIVATION,
        ),
        write_selectors=frozenset({"document_id"}),
        resource_snapshot=ResourceSnapshotKind.DOCUMENT,
        always_effectful=True,
        operator_live_available=False,
    ),
    _write(
        "EMP-DELETE-001",
        "Eliminazione definitiva dipendente",
        "ENABLE_EMPLOYEE_DELETE",
        (_rule(LogicalRole.SYSTEM_ADMIN),),
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        expose=False,
        confirmation_template="DELETE {employee_id}",
        write_parameters=(REQUIRED_MOTIVATION,),
        resource_snapshot=ResourceSnapshotKind.EMPLOYEE,
        deletes_resource=True,
    ),
    _write(
        "EMP-CONTRACT-003",
        "Eliminazione contratto",
        "ENABLE_CONTRACT_DELETE",
        HR_WRITE,
        approvals=2,
        approver_roles=APPROVERS,
        sensitivity=Sensitivity.CRITICAL,
        destructive=True,
        expose=False,
        confirmation_template="CONFIRM {employee_id}",
        write_parameters=(
            _text("contract_id", required=True, max_length=128, pattern=_IDENTIFIER),
            REQUIRED_MOTIVATION,
        ),
        write_selectors=frozenset({"contract_id"}),
        resource_snapshot=ResourceSnapshotKind.CONTRACT,
        deletes_resource=True,
        operator_live_available=False,
    ),
)


def _build_catalog() -> Mapping[str, FunctionSpec]:
    catalog: dict[str, FunctionSpec] = {}
    id_pattern = re.compile(r"^EMP-[A-Z]+-[0-9]{3}$")
    for spec in _SPECS:
        if not id_pattern.fullmatch(spec.function_id):
            raise RuntimeError(f"invalid Function ID: {spec.function_id}")
        if spec.function_id in catalog:
            raise RuntimeError(f"duplicate Function ID: {spec.function_id}")
        if spec.is_write and "ENABLE_WRITE_ACTIONS" not in spec.feature_flags:
            raise RuntimeError(f"write missing global kill switch: {spec.function_id}")
        if spec.approvals_required not in {0, 1, 2}:
            raise RuntimeError(f"invalid approval count: {spec.function_id}")
        if spec.approvals_required and not spec.approver_roles:
            raise RuntimeError(f"missing approver roles: {spec.function_id}")
        if spec.enabled_by_default == spec.is_write:
            raise RuntimeError(f"unsafe default state: {spec.function_id}")
        parameter_names = tuple(parameter.name for parameter in spec.write_parameters)
        if len(parameter_names) != len(set(parameter_names)):
            raise RuntimeError(f"duplicate write parameter: {spec.function_id}")
        if spec.is_write:
            if not spec.write_parameters or spec.resource_snapshot is None:
                raise RuntimeError(f"write missing schema or resource snapshot: {spec.function_id}")
            if spec.destructive and "motivation" not in {
                parameter.name for parameter in spec.write_parameters if parameter.required
            }:
                raise RuntimeError(f"destructive write has optional motivation: {spec.function_id}")
        elif spec.write_parameters or spec.resource_snapshot is not None:
            raise RuntimeError(f"read has write-only metadata: {spec.function_id}")
        if not spec.write_required_any.issubset(parameter_names):
            raise RuntimeError(f"invalid write parameter group: {spec.function_id}")
        if not spec.write_selectors.issubset(parameter_names):
            raise RuntimeError(f"invalid write selector group: {spec.function_id}")
        if any(name not in parameter_names for name, _value in spec.fixed_preview_after):
            operational = spec.operational_parameter_names
            fixed_names = {name for name, _value in spec.fixed_preview_after}
            if operational or fixed_names.difference({"account_state", "employee_state"}):
                raise RuntimeError(f"invalid fixed preview: {spec.function_id}")
        catalog[spec.function_id] = spec
    return MappingProxyType(catalog)


FUNCTION_CATALOG = _build_catalog()
ALL_FUNCTION_IDS = frozenset(FUNCTION_CATALOG)
READ_FUNCTION_IDS = frozenset(key for key, value in FUNCTION_CATALOG.items() if not value.is_write)
WRITE_FUNCTION_IDS = frozenset(key for key, value in FUNCTION_CATALOG.items() if value.is_write)


def get_function_spec(function_id: str) -> FunctionSpec | None:
    return FUNCTION_CATALOG.get(function_id)


def validate_write_parameters(
    spec: FunctionSpec,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize a write using the sole catalog allowlist.

    Values are never interpolated into errors. Optional values are omitted rather
    than represented by ``null`` so selectors and mutation detection stay
    deterministic across OpenAI, Discord and approval execution boundaries.
    """

    if not spec.is_write:
        raise WriteParameterValidationError("Function ID is not a write")
    if not all(isinstance(name, str) for name in parameters):
        raise WriteParameterValidationError("write parameter names must be strings")
    expected = {parameter.name: parameter for parameter in spec.write_parameters}
    unexpected = set(parameters).difference(expected)
    if unexpected:
        raise WriteParameterValidationError(f"unsupported write parameters: {sorted(unexpected)}")
    missing = {
        parameter.name
        for parameter in spec.write_parameters
        if parameter.required and parameter.name not in parameters
    }
    if missing:
        raise WriteParameterValidationError(f"missing write parameters: {sorted(missing)}")

    validated: dict[str, Any] = {}
    for name, value in parameters.items():
        parameter = expected[name]
        if value is None:
            raise WriteParameterValidationError(f"write parameter {name} cannot be null")
        if parameter.kind is WriteParameterKind.TEXT:
            if not isinstance(value, str):
                raise WriteParameterValidationError(f"write parameter {name} must be text")
            normalized = value.strip()
            if parameter.min_length is not None and len(normalized) < parameter.min_length:
                raise WriteParameterValidationError(f"write parameter {name} is too short")
            if parameter.max_length is not None and len(normalized) > parameter.max_length:
                raise WriteParameterValidationError(f"write parameter {name} is too long")
            if (
                parameter.pattern is not None
                and re.fullmatch(parameter.pattern, normalized) is None
            ):
                raise WriteParameterValidationError(f"write parameter {name} has invalid syntax")
            if parameter.choices and normalized not in parameter.choices:
                raise WriteParameterValidationError(f"write parameter {name} is not allowed")
            validated[name] = normalized
        elif parameter.kind is WriteParameterKind.DATE:
            if not isinstance(value, str):
                raise WriteParameterValidationError(f"write parameter {name} must be an ISO date")
            try:
                validated[name] = date.fromisoformat(value.strip()).isoformat()
            except ValueError as exc:
                raise WriteParameterValidationError(
                    f"write parameter {name} must be an ISO date"
                ) from exc
        elif parameter.kind is WriteParameterKind.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                raise WriteParameterValidationError(f"write parameter {name} must be an integer")
            if parameter.min_value is not None and value < parameter.min_value:
                raise WriteParameterValidationError(f"write parameter {name} is below its minimum")
            if parameter.max_value is not None and value > parameter.max_value:
                raise WriteParameterValidationError(f"write parameter {name} exceeds its maximum")
            validated[name] = value
        elif parameter.kind is WriteParameterKind.BOOLEAN:
            if not isinstance(value, bool):
                raise WriteParameterValidationError(f"write parameter {name} must be boolean")
            validated[name] = value
        elif parameter.kind is WriteParameterKind.DECIMAL:
            try:
                validated[name] = canonical_decimal_text(
                    value,
                    max_integer_digits=parameter.max_integer_digits or 9,
                    max_decimal_places=parameter.max_decimal_places or 4,
                )
            except ValueError as exc:
                raise WriteParameterValidationError(
                    f"write parameter {name} must be a canonical decimal"
                ) from exc
        else:
            raise WriteParameterValidationError(f"unsupported write parameter type for {name}")

    if spec.write_required_any and spec.write_required_any.isdisjoint(validated):
        raise WriteParameterValidationError(
            f"one write parameter is required from: {sorted(spec.write_required_any)}"
        )
    return validated
