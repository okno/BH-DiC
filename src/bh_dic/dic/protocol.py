"""Adapter contract: OpenAI and Discord never receive browser primitives."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from bh_dic.dic.models import (
    BalanceCorrectionState,
    BalanceResult,
    ContractRecord,
    DicCredentials,
    DocumentMetadata,
    DocumentQuery,
    EmployeeListQuery,
    EmployeeListResult,
    EmployeeSummary,
    ExecutionResult,
    FunctionId,
    HealthStatus,
    MaturationRecord,
    OpaqueStateDigest,
    PayrollMetadata,
    PreparedAction,
    ReconciliationResult,
    RolesResult,
    SessionStatus,
    TimeAccessResult,
)


@runtime_checkable
class DipendentiInCloudAdapter(Protocol):
    """Complete deterministic boundary for the supported Employees area."""

    async def health(self) -> HealthStatus: ...

    async def ensure_authenticated(
        self, credentials: DicCredentials | None = None
    ) -> SessionStatus: ...

    async def session_status(self) -> SessionStatus: ...

    async def list_employees(self, query: EmployeeListQuery) -> EmployeeListResult: ...

    async def get_employee_summary(self, employee_id: str) -> EmployeeSummary: ...

    async def get_contracts(self, employee_id: str) -> tuple[ContractRecord, ...]: ...

    async def get_roles(self, employee_id: str) -> RolesResult: ...

    async def get_time_access(self, employee_id: str) -> TimeAccessResult: ...

    async def get_maturations(self, employee_id: str) -> tuple[MaturationRecord, ...]: ...

    async def get_balance(self, employee_id: str, year: int) -> BalanceResult: ...

    async def get_balance_correction_state(
        self, employee_id: str, year: int, month: int, category: str
    ) -> BalanceCorrectionState: ...

    async def get_payroll_metadata(
        self, employee_id: str, year: int | None = None
    ) -> tuple[PayrollMetadata, ...]: ...

    async def get_document_metadata(
        self, employee_id: str, query: DocumentQuery
    ) -> tuple[DocumentMetadata, ...]: ...

    async def get_state_digest(
        self,
        function_id: FunctionId,
        employee_id: str | None,
        parameters: Mapping[str, JsonValue],
    ) -> OpaqueStateDigest: ...

    async def execute_prepared(self, action: PreparedAction) -> ExecutionResult: ...

    async def reconcile(self, action: PreparedAction) -> ReconciliationResult: ...

    async def close(self) -> None: ...
