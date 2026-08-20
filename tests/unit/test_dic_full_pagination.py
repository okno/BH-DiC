from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bh_dic.dic.errors import DicValidationError
from bh_dic.dic.models import EmployeeListItem, EmployeeListQuery, EmployeeListResult
from bh_dic.dic.protocol import DipendentiInCloudAdapter
from bh_dic.policies.feature_flags import DEFAULT_FEATURE_FLAGS, RuntimeFeatureFlags
from bh_dic.services.dic_service import DicService


def _service(results: list[EmployeeListResult]) -> tuple[DicService, AsyncMock]:
    adapter = AsyncMock(spec=DipendentiInCloudAdapter)
    adapter.list_employees.side_effect = results
    flags = RuntimeFeatureFlags(dict(DEFAULT_FEATURE_FLAGS))
    return DicService(adapter, flags), adapter


def _result(
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
async def test_list_all_employees_reads_every_page_without_losing_rows() -> None:
    service, adapter = _service(
        [
            _result(1, ("EMP-SYNTH-001", "EMP-SYNTH-002"), total=3, has_next=True),
            _result(2, ("EMP-SYNTH-003",), total=3, has_next=False),
        ]
    )

    result = await service.list_all_employees(EmployeeListQuery())

    assert tuple(item.employee_id for item in result.items) == (
        "EMP-SYNTH-001",
        "EMP-SYNTH-002",
        "EMP-SYNTH-003",
    )
    assert result.total == 3
    assert not result.has_next
    assert [call.args[0].page for call in adapter.list_employees.await_args_list] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "results",
    [
        [
            _result(1, ("EMP-SYNTH-001",), total=2, has_next=True),
            _result(2, ("EMP-SYNTH-001",), total=2, has_next=False),
        ],
        [
            _result(1, ("EMP-SYNTH-001",), total=2, has_next=True),
            _result(2, ("EMP-SYNTH-002",), total=3, has_next=False),
        ],
    ],
)
async def test_list_all_employees_fails_closed_on_duplicate_or_total_drift(
    results: list[EmployeeListResult],
) -> None:
    service, _ = _service(results)
    with pytest.raises(DicValidationError):
        await service.list_all_employees(EmployeeListQuery())
