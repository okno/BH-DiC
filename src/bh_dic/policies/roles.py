"""Logical roles and reusable role predicates."""

from __future__ import annotations

from collections.abc import Iterable, Set
from dataclasses import dataclass, field
from enum import StrEnum


class LogicalRole(StrEnum):
    READ_ONLY = "READ_ONLY"
    HR_READ = "HR_READ"
    HR_WRITE = "HR_WRITE"
    IAM_OPERATOR = "IAM_OPERATOR"
    DOCUMENT_OPERATOR = "DOCUMENT_OPERATOR"
    APPROVER = "APPROVER"
    SECURITY_ADMIN = "SECURITY_ADMIN"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


_IMPLIED_ROLES: dict[LogicalRole, frozenset[LogicalRole]] = {
    LogicalRole.HR_READ: frozenset({LogicalRole.READ_ONLY}),
    LogicalRole.HR_WRITE: frozenset({LogicalRole.HR_READ, LogicalRole.READ_ONLY}),
}


def normalize_roles(values: Iterable[LogicalRole | str]) -> frozenset[LogicalRole]:
    """Return known roles plus only the deliberately narrow HR hierarchy."""

    roles = {value if isinstance(value, LogicalRole) else LogicalRole(value) for value in values}
    changed = True
    while changed:
        changed = False
        for role in tuple(roles):
            for implied in _IMPLIED_ROLES.get(role, frozenset()):
                if implied not in roles:
                    roles.add(implied)
                    changed = True
    return frozenset(roles)


@dataclass(frozen=True, slots=True)
class RoleRule:
    """A role expression: every ``all_of`` and one optional ``any_of``."""

    all_of: frozenset[LogicalRole] = field(default_factory=frozenset)
    any_of: frozenset[LogicalRole] = field(default_factory=frozenset)
    entitlements: frozenset[str] = field(default_factory=frozenset)

    def matches(
        self,
        roles: Set[LogicalRole],
        entitlements: Set[str] = frozenset(),
    ) -> bool:
        expanded = normalize_roles(roles)
        return (
            self.all_of.issubset(expanded)
            and (not self.any_of or not self.any_of.isdisjoint(expanded))
            and self.entitlements.issubset(entitlements)
        )


@dataclass(frozen=True, slots=True)
class ScopedRoleRule:
    """Role rule selected by a deterministic operation scope."""

    scope: str
    rule: RoleRule
