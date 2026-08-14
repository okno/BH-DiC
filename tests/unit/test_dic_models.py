from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bh_dic.dic.catalog import FUNCTION_CATALOG
from bh_dic.dic.errors import DicConfigurationError, DicValidationError
from bh_dic.dic.models import EmployeeListQuery, FunctionId, PreparedAction
from bh_dic.dic.pages import EmployeeSummaryPage


def test_function_enum_is_exact_policy_catalog_projection() -> None:
    assert {item.value for item in FunctionId} == set(FUNCTION_CATALOG)


def test_prepared_action_accepts_standard_uuid_and_forbids_extra_fields() -> None:
    now = datetime.now(UTC)
    action = PreparedAction(
        action_id=str(uuid4()),
        function_id=FunctionId.EMP_UPDATE_001,
        employee_id="EMP-SYNTH-001",
        parameters={"job_title": "Synthetic title"},
        idempotency_key="idem-00000001",
        correlation_id="corr-00000001",
        request_fingerprint="a" * 64,
        preview=(),
        required_approvals=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    assert len(action.action_id) == 36

    with pytest.raises(ValidationError):
        PreparedAction.model_validate({**action.model_dump(), "unexpected": True})


def test_prepared_action_rejects_old_opaque_action_id() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        PreparedAction(
            action_id="act_" + "a" * 24,
            function_id=FunctionId.EMP_UPDATE_001,
            employee_id="EMP-SYNTH-001",
            parameters={},
            idempotency_key="idem-00000001",
            correlation_id="corr-00000001",
            request_fingerprint="a" * 64,
            preview=(),
            required_approvals=0,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
        )


def test_models_reject_coercion_and_non_finite_or_invalid_parameters() -> None:
    with pytest.raises(ValidationError):
        EmployeeListQuery(page="1")  # type: ignore[arg-type]

    now = datetime.now(UTC)
    common = {
        "action_id": str(uuid4()),
        "function_id": FunctionId.EMP_UPDATE_001,
        "employee_id": "EMP-SYNTH-001",
        "idempotency_key": "idem-00000001",
        "correlation_id": "corr-00000001",
        "request_fingerprint": "a" * 64,
        "preview": (),
        "required_approvals": 0,
        "created_at": now,
        "expires_at": now + timedelta(minutes=5),
    }
    with pytest.raises(ValidationError):
        PreparedAction(**common, parameters={"Invalid-Key": "value"})
    with pytest.raises(ValidationError):
        PreparedAction(**common, parameters={"value": float("nan")})


def test_page_routes_reject_path_injection() -> None:
    page = object()
    summary = EmployeeSummaryPage(page, "https://secure.dipendentincloud.it")  # type: ignore[arg-type]
    assert summary.route("EMP-SYNTH-001") == "/it/app/employees/info/EMP-SYNTH-001/summary"
    with pytest.raises(DicValidationError, match="invalid employee identifier"):
        summary.route("../../settings")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://secure.dipendentincloud.it",
        "https://user:pass@secure.dipendentincloud.it",
        "https://secure.dipendentincloud.it/it/app",
        "javascript:alert(1)",
    ],
)
def test_page_base_url_must_be_credential_free_https_origin(base_url: str) -> None:
    with pytest.raises(DicConfigurationError):
        EmployeeSummaryPage(object(), base_url)  # type: ignore[arg-type]
