"""Explicit, read-only DIC coverage gate with sanitized count-only output."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from bh_dic.config import AppSettings
from bh_dic.dic.models import DocumentQuery, EmployeeFilter, EmployeeListQuery, SessionState
from bh_dic.runtime import build_runtime


async def run_live_read_coverage(settings: AppSettings) -> dict[str, object]:
    """Exercise implemented read surfaces without printing identities or payloads."""

    if settings.mock_mode:
        raise ValueError("live read coverage is unavailable in mock mode")
    runtime = await build_runtime(settings, authenticate_dic=False)
    checks: dict[str, dict[str, Any]] = {}

    async def check(name: str, operation: Any) -> object | None:
        try:
            value = await operation()
        except Exception as exc:
            checks[name] = {"state": "FAILED", "error_type": type(exc).__name__}
            return None
        count = len(value) if isinstance(value, tuple | list) else None
        checks[name] = {
            "state": "LIVE_READ_VERIFIED",
            **({"records": count} if count is not None else {}),
        }
        return cast(object, value)

    try:
        status = await runtime.adapter.session_status()
        if status.state is not SessionState.AUTHENTICATED:
            raise RuntimeError("DIC session is not authenticated")
        page = await runtime.adapter.list_employees(
            EmployeeListQuery(employee_filter=EmployeeFilter.ALL, page=1, page_size=100)
        )
        seen = {item.employee_id for item in page.items}
        expected_total = page.total
        page_number = 1
        while page.has_next:
            page_number += 1
            if page_number > 100:
                raise RuntimeError("employee pagination exceeded the gate bound")
            page = await runtime.adapter.list_employees(
                EmployeeListQuery(
                    employee_filter=EmployeeFilter.ALL,
                    page=page_number,
                    page_size=100,
                )
            )
            before = len(seen)
            seen.update(item.employee_id for item in page.items)
            if len(seen) == before:
                raise RuntimeError("employee pagination made no progress")
        if len(seen) != expected_total:
            raise RuntimeError("employee total does not match extracted identifiers")
        checks["employees.list"] = {
            "state": "LIVE_READ_VERIFIED",
            "records": len(seen),
            "complete": True,
        }
        if not seen:
            checks["employee_specific"] = {"state": "NOT_AVAILABLE_IN_AUTHORIZED_TENANT"}
        else:
            employee_id = sorted(seen)[0]
            year = datetime.now(UTC).year
            await check(
                "employees.summary", lambda: runtime.adapter.get_employee_summary(employee_id)
            )
            await check("employees.roles", lambda: runtime.adapter.get_roles(employee_id))
            await check(
                "employees.time_access", lambda: runtime.adapter.get_time_access(employee_id)
            )
            await check("employees.contracts", lambda: runtime.adapter.get_contracts(employee_id))
            await check(
                "employees.maturations", lambda: runtime.adapter.get_maturations(employee_id)
            )
            await check(
                "employees.balances", lambda: runtime.adapter.get_balance(employee_id, year)
            )
            await check(
                "employees.payrolls",
                lambda: runtime.adapter.get_payroll_metadata(employee_id, year),
            )
            await check(
                "employees.documents",
                lambda: runtime.adapter.get_document_metadata(employee_id, DocumentQuery()),
            )
        failed = sorted(
            name
            for name, value in checks.items()
            if value["state"]
            not in {
                "LIVE_READ_VERIFIED",
                "NOT_AVAILABLE_IN_AUTHORIZED_TENANT",
            }
        )
        return {
            "mode": "live_read_only",
            "tenant": "VERIFIED_BY_ADAPTER",
            "generated_at": datetime.now(UTC).isoformat(),
            "checks": checks,
            "failed": failed,
            "success": not failed,
        }
    finally:
        await runtime.close()


__all__ = ["run_live_read_coverage"]
