from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bh_dic.dic.catalog import MUTATING_FUNCTIONS, READ_FUNCTIONS
from bh_dic.dic.errors import DicValidationError
from bh_dic.dic.mock import MockDicAdapter
from bh_dic.dic.models import (
    AccountState,
    DocumentQuery,
    EmployeeFilter,
    EmployeeListItem,
    EmployeeListQuery,
    EmployeeState,
    FunctionId,
    PreparedAction,
    ReconciliationState,
    SortDirection,
)
from bh_dic.dic.protocol import DipendentiInCloudAdapter


def prepared(
    function_id: FunctionId,
    *,
    target_employee_id: str | None = "EMP-SYNTH-001",
    **parameters: object,
) -> PreparedAction:
    now = datetime.now(UTC)
    return PreparedAction(
        action_id=str(uuid4()),
        function_id=function_id,
        employee_id=target_employee_id,
        parameters=parameters,
        idempotency_key=f"idem-{uuid4()}",
        correlation_id=f"corr-{uuid4()}",
        request_fingerprint="b" * 64,
        preview=(),
        required_approvals=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_mock_adapter_implements_complete_protocol_and_reads_all_routes() -> None:
    adapter = MockDicAdapter()
    assert isinstance(adapter, DipendentiInCloudAdapter)

    employees = await adapter.list_employees(EmployeeListQuery(employee_filter=EmployeeFilter.ALL))
    assert employees.total == 1
    employee_id = employees.items[0].employee_id
    assert (await adapter.get_employee_summary(employee_id)).tax_code_redacted.endswith("000X")
    assert await adapter.get_contracts(employee_id)
    assert (await adapter.get_contracts(employee_id))[0].actionable is True
    assert await adapter.get_roles(employee_id)
    assert (await adapter.get_time_access(employee_id)).timestamping_enabled is True
    assert await adapter.get_maturations(employee_id)
    assert (await adapter.get_balance(employee_id, 2026)).lines
    assert await adapter.get_payroll_metadata(employee_id, 2026)
    assert await adapter.get_document_metadata(employee_id, DocumentQuery())
    assert (await adapter.get_document_metadata(employee_id, DocumentQuery()))[0].actionable is True


@pytest.mark.asyncio
async def test_mock_write_is_idempotent_and_changes_postcondition() -> None:
    adapter = MockDicAdapter()
    action = prepared(FunctionId.EMP_STATUS_001)

    first = await adapter.execute_prepared(action)
    second = await adapter.execute_prepared(action)

    assert first == second
    assert first.postcondition_verified is True
    assert (await adapter.get_employee_summary("EMP-SYNTH-001")).state is EmployeeState.INACTIVE
    assert (await adapter.reconcile(action)).state.value == "confirmed_applied"


@pytest.mark.asyncio
async def test_mock_state_digest_is_keyed_stable_and_changes_with_raw_state() -> None:
    key = b"k" * 32
    adapter = MockDicAdapter(state_digest_key=key)
    before = await adapter.get_state_digest(
        FunctionId.EMP_STATUS_001,
        "EMP-SYNTH-001",
        {},
    )
    assert len(before) == 64
    assert before == await MockDicAdapter(state_digest_key=key).get_state_digest(
        FunctionId.EMP_STATUS_001,
        "EMP-SYNTH-001",
        {},
    )
    assert before != await MockDicAdapter(state_digest_key=b"z" * 32).get_state_digest(
        FunctionId.EMP_STATUS_001,
        "EMP-SYNTH-001",
        {},
    )

    await adapter.execute_prepared(prepared(FunctionId.EMP_STATUS_001))
    after = await adapter.get_state_digest(
        FunctionId.EMP_STATUS_001,
        "EMP-SYNTH-001",
        {},
    )
    assert after != before


@pytest.mark.asyncio
async def test_mock_search_and_filter_are_deterministic() -> None:
    adapter = MockDicAdapter()
    found = await adapter.list_employees(
        EmployeeListQuery(query="SYN-001", employee_filter=EmployeeFilter.ALL)
    )
    missing = await adapter.list_employees(
        EmployeeListQuery(query="not-present", employee_filter=EmployeeFilter.ALL)
    )
    assert [item.employee_id for item in found.items] == ["EMP-SYNTH-001"]
    assert missing.items == ()


@pytest.mark.asyncio
async def test_mock_applies_requested_sort_field_and_direction_before_pagination() -> None:
    adapter = MockDicAdapter()
    adapter._items["EMP-SYNTH-002"] = EmployeeListItem(
        employee_id="EMP-SYNTH-002",
        display_name_redacted="B. E.",
        payroll_number="SYN-999",
        contract_label="Synthetic fixed term",
        contract_state="expiring",
        workplace="Synthetic branch",
        account_state=AccountState.NOT_CONNECTED,
        employee_state=EmployeeState.ACTIVE,
    )

    result = await adapter.list_employees(
        EmployeeListQuery(
            employee_filter=EmployeeFilter.ALL,
            sort_by="payroll_number",
            sort_direction=SortDirection.DESC,
            page=1,
            page_size=1,
        )
    )

    assert result.total == 2
    assert result.has_next is True
    assert [item.employee_id for item in result.items] == ["EMP-SYNTH-002"]


@pytest.mark.asyncio
async def test_every_catalog_read_function_has_a_mock_execution_path() -> None:
    adapter = MockDicAdapter()
    employee_id = "EMP-SYNTH-001"
    exercised = {
        FunctionId.EMP_READ_001: lambda: adapter.list_employees(EmployeeListQuery()),
        FunctionId.EMP_READ_002: lambda: adapter.get_employee_summary(employee_id),
        FunctionId.EMP_SEARCH_001: lambda: adapter.list_employees(
            EmployeeListQuery(query="SYN-001", employee_filter=EmployeeFilter.ALL)
        ),
        FunctionId.EMP_FILTER_001: lambda: adapter.list_employees(
            EmployeeListQuery(employee_filter=EmployeeFilter.INACTIVE)
        ),
        FunctionId.EMP_SORT_001: lambda: adapter.list_employees(
            EmployeeListQuery(sort_by="payroll_number")
        ),
        FunctionId.EMP_PAGE_001: lambda: adapter.list_employees(
            EmployeeListQuery(page=2, page_size=1)
        ),
        FunctionId.EMP_CONTRACT_001: lambda: adapter.get_contracts(employee_id),
        FunctionId.EMP_RBAC_001: lambda: adapter.get_roles(employee_id),
        FunctionId.EMP_TIME_001: lambda: adapter.get_time_access(employee_id),
        FunctionId.EMP_MAT_001: lambda: adapter.get_maturations(employee_id),
        FunctionId.EMP_BAL_001: lambda: adapter.get_balance(employee_id, 2026),
        FunctionId.EMP_PAY_001: lambda: adapter.get_payroll_metadata(employee_id, 2026),
        FunctionId.EMP_PAY_002: lambda: adapter.list_employees(EmployeeListQuery()),
        FunctionId.EMP_DOC_001: lambda: adapter.get_document_metadata(employee_id, DocumentQuery()),
        FunctionId.EMP_NOTIF_001: adapter.list_notifications,
    }
    assert set(exercised) == set(READ_FUNCTIONS)
    for operation in exercised.values():
        await operation()


@pytest.mark.asyncio
async def test_every_catalog_write_function_has_a_mock_execution_path() -> None:
    parameters: dict[FunctionId, dict[str, object]] = {
        FunctionId.EMP_UPDATE_001: {"job_title": "Synthetic lead"},
        FunctionId.EMP_CREATE_001: {
            "creation_mode": "manual",
            "first_name": "New",
            "last_name": "Employee",
        },
        FunctionId.EMP_CONTRACT_002: {"schedule": "36h"},
        FunctionId.EMP_CONTRACT_003: {"contract_id": "CON-SYNTH-001"},
        FunctionId.EMP_MAT_002: {"category": "ROL"},
        FunctionId.EMP_BAL_002: {
            "year": 2026,
            "month": 8,
            "category": "Ferie",
            "previous_value": "0",
            "amount": "1",
        },
        FunctionId.EMP_CONNECT_001: {},
        FunctionId.EMP_CONNECT_002: {},
        FunctionId.EMP_INVITE_001: {},
        FunctionId.EMP_INVITE_002: {},
        FunctionId.EMP_RBAC_002: {"role_name": "Employee", "enabled": False},
        FunctionId.EMP_STATUS_001: {},
        FunctionId.EMP_STATUS_002: {},
        FunctionId.EMP_DOC_002: {"category": "CV"},
        FunctionId.EMP_DOC_003: {"document_id": "DOC-SYNTH-001"},
        FunctionId.EMP_DOC_004: {
            "document_id": "DOC-SYNTH-001",
            "category": "patente",
        },
        FunctionId.EMP_DOC_005: {"document_id": "DOC-SYNTH-001"},
        FunctionId.EMP_DELETE_001: {},
        FunctionId.EMP_EXPORT_001: {},
        FunctionId.EMP_NOTIF_002: {"notification_id": 1, "read": True},
    }
    assert set(parameters) == set(MUTATING_FUNCTIONS)
    for function_id, action_parameters in parameters.items():
        adapter = MockDicAdapter()
        target = (
            None
            if function_id
            in {
                FunctionId.EMP_CREATE_001,
                FunctionId.EMP_EXPORT_001,
                FunctionId.EMP_NOTIF_002,
            }
            else "EMP-SYNTH-001"
        )
        action = prepared(
            function_id,
            target_employee_id=target,
            **action_parameters,
        )
        state_digest = await adapter.get_state_digest(
            function_id,
            target,
            action.parameters,
        )
        assert len(state_digest) == 64
        result = await adapter.execute_prepared(action)
        assert result.status.value == "succeeded"
        assert (await adapter.reconcile(action)).state.value == "confirmed_applied"


@pytest.mark.asyncio
async def test_mock_applies_complete_catalog_fields_and_rechecks_postconditions() -> None:
    adapter = MockDicAdapter()
    employee_id = "EMP-SYNTH-001"
    employee_fields = {
        "first_name": "Bob",
        "last_name": "Tester",
        "payroll_number": "SYN-900",
        "tax_code": "SYNTHETIC900Z",
        "birth_date": "1990-09-09",
        "iban": "IT00SYNTHETIC9999999999999",
        "job_title": "Synthetic lead",
        "phone": "+399999999999",
        "business_email": "bob@example.invalid",
        "address": "Synthetic avenue 9",
        "workplace": "Synthetic branch",
        "notes": "Synthetic note changed",
    }
    update = prepared(FunctionId.EMP_UPDATE_001, **employee_fields)
    result = await adapter.execute_prepared(update)
    assert result.postcondition_verified is True
    assert adapter._raw_summaries[employee_id] == employee_fields

    contract_fields = {
        "contract_id": "CON-SYNTH-001",
        "schedule": "36h",
        "flexibility": "flexible",
        "permanent": False,
        "start_date": "2026-01-01",
        "end_date": "2027-01-01",
        "ccnl_level": "Synthetic L2",
        "work_regime": "part-time",
        "description": "Synthetic updated contract",
        "contract_type": "determinato",
    }
    contract_action = prepared(FunctionId.EMP_CONTRACT_002, **contract_fields)
    await adapter.execute_prepared(contract_action)
    contract = (await adapter.get_contracts(employee_id))[0]
    for key, value in contract_fields.items():
        if key != "contract_id":
            assert getattr(contract, key) == value

    upload = prepared(
        FunctionId.EMP_DOC_002,
        category="Patente",
        expiry_date="2028-01-01",
        safe_local_path="C:/synthetic/opaque",
        safe_local_sha256="a" * 64,
        safe_local_size=10,
        detected_mime="application/pdf",
    )
    upload_result = await adapter.execute_prepared(upload)
    assert upload_result.details == {}
    uploaded = (await adapter.get_document_metadata(employee_id, DocumentQuery()))[-1]
    assert uploaded.category == "Patente"
    assert uploaded.expiry_date == "2028-01-01"

    adapter._raw_summaries[employee_id]["job_title"] = "tampered"
    assert (await adapter.reconcile(update)).state is ReconciliationState.UNKNOWN


@pytest.mark.asyncio
async def test_mock_create_rbac_and_actionability_boundaries_match_live_schema() -> None:
    adapter = MockDicAdapter()
    with pytest.raises(DicValidationError, match="would not change"):
        await adapter.get_state_digest(
            FunctionId.EMP_UPDATE_001,
            "EMP-SYNTH-001",
            {"first_name": "Alice", "tax_code": "SYNTHETIC000X"},
        )
    with pytest.raises(DicValidationError, match="would not change"):
        await adapter.get_state_digest(
            FunctionId.EMP_CONTRACT_002,
            "EMP-SYNTH-001",
            {
                "contract_id": "CON-SYNTH-001",
                "description": "Synthetic contract",
            },
        )
    create = prepared(
        FunctionId.EMP_CREATE_001,
        target_employee_id=None,
        creation_mode="manual",
        first_name="New",
        last_name="Employee",
        tax_code="SYNTHETIC111X",
    )
    created = await adapter.execute_prepared(create)
    created_id = created.details["employee_id"]
    assert isinstance(created_id, str)
    assert adapter._raw_summaries[created_id]["first_name"] == "New"
    assert (await adapter.reconcile(create)).state is ReconciliationState.CONFIRMED_APPLIED

    with pytest.raises(DicValidationError, match="requires only"):
        await adapter.execute_prepared(prepared(FunctionId.EMP_RBAC_002, roles=["Employee"]))
    with pytest.raises(DicValidationError, match="already matches"):
        await adapter.execute_prepared(
            prepared(FunctionId.EMP_RBAC_002, role_name="Employee", enabled=True)
        )

    contract = adapter._contracts["EMP-SYNTH-001"][0]
    adapter._contracts["EMP-SYNTH-001"][0] = contract.model_copy(update={"actionable": False})
    with pytest.raises(DicValidationError, match="stable and actionable"):
        await adapter.get_state_digest(
            FunctionId.EMP_CONTRACT_003,
            "EMP-SYNTH-001",
            {"contract_id": "CON-SYNTH-001"},
        )
