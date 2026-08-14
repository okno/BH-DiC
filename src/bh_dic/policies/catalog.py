"""Authoritative Function ID catalog.

Every consumer (Discord, OpenAI exposure, policy, approvals and documentation)
should consume these immutable specs instead of maintaining another ID list.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

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
    )


HR_READ = (_rule(LogicalRole.HR_READ),)
HR_WRITE = (_rule(LogicalRole.HR_WRITE),)
IAM = (_rule(LogicalRole.IAM_OPERATOR),)
DOCS = (_rule(LogicalRole.DOCUMENT_OPERATOR),)
APPROVERS = frozenset({LogicalRole.APPROVER, LogicalRole.SYSTEM_ADMIN})

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
    _write("EMP-UPDATE-001", "Modifica dati dipendente", "ENABLE_EMPLOYEE_UPDATE", HR_WRITE),
    _write(
        "EMP-CREATE-001", "Creazione dipendente", "ENABLE_EMPLOYEE_CREATE", HR_WRITE, target=False
    ),
    _write(
        "EMP-CONTRACT-002",
        "Creazione o modifica contratto",
        "ENABLE_CONTRACT_WRITE",
        HR_WRITE,
        approvals=1,
        approver_roles=frozenset({LogicalRole.HR_WRITE, LogicalRole.APPROVER}),
    ),
    _write(
        "EMP-MAT-002",
        "Nuova maturazione",
        "ENABLE_MATURATION_WRITE",
        HR_WRITE,
        approvals=1,
        approver_roles=frozenset({LogicalRole.HR_WRITE, LogicalRole.APPROVER}),
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
    ),
    _write("EMP-INVITE-001", "Reinvio invito", "ENABLE_INVITE_ACTIONS", IAM),
    _write("EMP-INVITE-002", "Annullamento invito", "ENABLE_INVITE_ACTIONS", IAM),
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
    ),
    _write(
        "EMP-STATUS-002",
        "Riattivazione dipendente",
        "ENABLE_STATUS_CHANGE",
        HR_WRITE,
        approvals=1,
        approver_roles=frozenset({LogicalRole.APPROVER, LogicalRole.SYSTEM_ADMIN}),
    ),
    _write(
        "EMP-DOC-002",
        "Upload documento",
        "ENABLE_DOCUMENT_UPLOAD",
        DOCS,
        action_class=ActionClass.FILE_UPLOAD,
        capabilities=frozenset({"clamav"}),
    ),
    _write("EMP-DOC-004", "Modifica metadati documento", "ENABLE_DOCUMENT_UPDATE", DOCS),
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
        catalog[spec.function_id] = spec
    return MappingProxyType(catalog)


FUNCTION_CATALOG = _build_catalog()
ALL_FUNCTION_IDS = frozenset(FUNCTION_CATALOG)
READ_FUNCTION_IDS = frozenset(key for key, value in FUNCTION_CATALOG.items() if not value.is_write)
WRITE_FUNCTION_IDS = frozenset(key for key, value in FUNCTION_CATALOG.items() if value.is_write)


def get_function_spec(function_id: str) -> FunctionSpec | None:
    return FUNCTION_CATALOG.get(function_id)
