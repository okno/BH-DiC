from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bh_dic.live_read_coverage as coverage_module
from bh_dic.config import AppSettings
from bh_dic.dic.models import (
    EmployeeListItem,
    EmployeeListResult,
    SessionState,
    SessionStatus,
)


def _settings() -> AppSettings:
    return AppSettings.model_construct(mock_mode=False)


def _page(
    page: int, identifiers: tuple[str, ...], *, total: int, has_next: bool
) -> EmployeeListResult:
    return EmployeeListResult(
        items=tuple(
            EmployeeListItem(employee_id=identifier, display_name_redacted="S. E.")
            for identifier in identifiers
        ),
        page=page,
        page_size=100,
        total=total,
        has_next=has_next,
    )


@pytest.mark.asyncio
async def test_live_read_gate_pages_all_employees_and_emits_only_counts(monkeypatch) -> None:
    adapter = SimpleNamespace(
        session_status=AsyncMock(return_value=SessionStatus(state=SessionState.AUTHENTICATED)),
        list_employees=AsyncMock(
            side_effect=(
                _page(1, ("EMP-SYNTH-001",), total=2, has_next=True),
                _page(2, ("EMP-SYNTH-002",), total=2, has_next=False),
            )
        ),
        get_employee_summary=AsyncMock(return_value=object()),
        get_roles=AsyncMock(return_value=()),
        get_time_access=AsyncMock(return_value=object()),
        get_contracts=AsyncMock(return_value=()),
        get_maturations=AsyncMock(return_value=()),
        get_balance=AsyncMock(return_value=object()),
        get_payroll_metadata=AsyncMock(return_value=()),
        get_document_metadata=AsyncMock(return_value=()),
    )
    runtime = SimpleNamespace(adapter=adapter, close=AsyncMock())
    monkeypatch.setattr(coverage_module, "build_runtime", AsyncMock(return_value=runtime))

    result = await coverage_module.run_live_read_coverage(_settings())

    assert result["success"] is True
    assert result["tenant"] == "VERIFIED_BY_ADAPTER"
    assert result["checks"]["employees.list"] == {  # type: ignore[index]
        "state": "LIVE_READ_VERIFIED",
        "records": 2,
        "complete": True,
    }
    serialized = str(result)
    assert "EMP-SYNTH" not in serialized
    assert [call.args[0].page for call in adapter.list_employees.await_args_list] == [1, 2]
    runtime.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_read_gate_fails_closed_and_still_closes_runtime(monkeypatch) -> None:
    adapter = SimpleNamespace(
        session_status=AsyncMock(return_value=SessionStatus(state=SessionState.EXPIRED))
    )
    runtime = SimpleNamespace(adapter=adapter, close=AsyncMock())
    monkeypatch.setattr(coverage_module, "build_runtime", AsyncMock(return_value=runtime))

    with pytest.raises(RuntimeError, match="not authenticated"):
        await coverage_module.run_live_read_coverage(_settings())

    runtime.close.assert_awaited_once()
