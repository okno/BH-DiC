from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bh_dic.dic.errors import DicWriteDisabledError
from bh_dic.dic.models import FunctionId, PreparedAction
from bh_dic.dic.playwright_adapter import PlaywrightDicAdapter


class UnusedPage:
    """The adapter write gate must fire before any browser method is reached."""


@pytest.mark.asyncio
async def test_playwright_adapter_is_fail_closed_for_live_writes() -> None:
    adapter = PlaywrightDicAdapter(  # type: ignore[arg-type]
        UnusedPage(), expected_tenant_id="123456789"
    )
    now = datetime.now(UTC)
    action = PreparedAction(
        action_id=str(uuid4()),
        function_id=FunctionId.EMP_UPDATE_001,
        employee_id="EMP-SYNTH-001",
        parameters={"job_title": "Synthetic"},
        idempotency_key="idem-00000001",
        correlation_id="corr-00000001",
        request_fingerprint="c" * 64,
        preview=(),
        required_approvals=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(DicWriteDisabledError):
        await adapter.execute_prepared(action)
    await adapter.close()
