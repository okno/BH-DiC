from __future__ import annotations

import asyncio

import pytest

from bh_dic.dic.errors import (
    DicCircuitOpenError,
    DicReconciliationRequiredError,
    DicTransientError,
    DicUiChangedError,
)
from bh_dic.dic.models import ReconciliationResult, ReconciliationState
from bh_dic.services.browser_runtime import (
    BrowserCoordinator,
    BrowserOperationQueue,
    CircuitBreaker,
    CircuitState,
    ReadRetryPolicy,
)


@pytest.mark.asyncio
async def test_reads_retry_but_writes_are_never_retried() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(
            attempts=3,
            initial_delay_seconds=0,
            maximum_delay_seconds=0,
            operation_timeout_seconds=1,
        ),
        circuit_breaker=CircuitBreaker(failure_threshold=10),
    )
    read_calls = 0
    write_calls = 0

    async def flaky_read() -> str:
        nonlocal read_calls
        read_calls += 1
        if read_calls < 3:
            raise DicTransientError("synthetic transient read")
        return "ok"

    async def failing_write() -> None:
        nonlocal write_calls
        write_calls += 1
        raise DicTransientError("synthetic write failure")

    assert await coordinator.run_read("read", "employee:1", flaky_read) == "ok"
    with pytest.raises(DicTransientError):
        await coordinator.run_write("write", "employee:1", failing_write)
    assert read_calls == 3
    assert write_calls == 1
    await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionError("synthetic")])
async def test_run_once_never_retries_transport_failures(failure: Exception) -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(
            attempts=3,
            initial_delay_seconds=0,
            maximum_delay_seconds=0,
            operation_timeout_seconds=1,
        ),
        circuit_breaker=CircuitBreaker(failure_threshold=10),
    )
    calls = 0

    async def fail_once() -> None:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(type(failure)):
        await coordinator.run_once("auth", "dic-browser", fail_once)

    assert calls == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_run_once_uses_positive_timeout_override_instead_of_read_default() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(operation_timeout_seconds=0.001),
    )
    calls = 0

    async def slower_than_default() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "ok"

    assert (
        await coordinator.run_once(
            "auth",
            "dic-browser",
            slower_than_default,
            timeout_seconds=5.0,
        )
        == "ok"
    )
    assert calls == 1
    with pytest.raises(ValueError, match="timeout"):
        await coordinator.run_once(
            "auth",
            "dic-browser",
            slower_than_default,
            timeout_seconds=0,
        )
    assert calls == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_run_once_timeout_cancels_one_invocation_without_retry() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(attempts=3, operation_timeout_seconds=1),
        circuit_breaker=CircuitBreaker(failure_threshold=10),
    )
    calls = 0

    async def too_slow() -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        await coordinator.run_once(
            "auth",
            "dic-browser",
            too_slow,
            timeout_seconds=0.001,
        )

    assert calls == 1
    await coordinator.close()


@pytest.mark.asyncio
async def test_keyed_lock_serializes_same_target_even_with_multiple_workers() -> None:
    queue = BrowserOperationQueue(workers=2)
    active = 0
    maximum_active = 0

    async def operation() -> int:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return 1

    assert await asyncio.gather(
        queue.submit("employee:one", operation),
        queue.submit("employee:one", operation),
    ) == [1, 1]
    assert maximum_active == 1
    await queue.close()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_fails_fast() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60)
    coordinator = BrowserCoordinator(
        circuit_breaker=breaker,
        read_retry=ReadRetryPolicy(
            attempts=1,
            initial_delay_seconds=0,
            maximum_delay_seconds=0,
            operation_timeout_seconds=1,
        ),
    )

    async def fail() -> None:
        raise DicTransientError("synthetic")

    with pytest.raises(DicTransientError):
        await coordinator.run_read("read", "one", fail)
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(DicCircuitOpenError):
        await coordinator.run_read("read", "two", fail)

    reconciliation_calls = 0

    async def reconcile() -> str:
        nonlocal reconciliation_calls
        reconciliation_calls += 1
        return "known"

    assert await coordinator.run_reconciliation("two", reconcile) == "known"
    assert reconciliation_calls == 1
    assert breaker.state is CircuitState.CLOSED
    await coordinator.close()


@pytest.mark.asyncio
async def test_default_circuits_are_isolated_by_semantic_resource() -> None:
    coordinator = BrowserCoordinator(
        read_retry=ReadRetryPolicy(
            attempts=1,
            initial_delay_seconds=0,
            maximum_delay_seconds=0,
            operation_timeout_seconds=1,
        )
    )

    async def contract_drift() -> None:
        raise DicUiChangedError("synthetic contract drift")

    for _ in range(3):
        with pytest.raises(DicUiChangedError):
            await coordinator.run_read("employees.contracts", "dic-browser", contract_drift)

    with pytest.raises(DicCircuitOpenError):
        await coordinator.run_read("employees.contracts", "dic-browser", contract_drift)

    async def payroll_ok() -> str:
        return "payroll remains available"

    assert (
        await coordinator.run_read("employees.payrolls", "dic-browser", payroll_ok)
        == "payroll remains available"
    )
    assert coordinator.circuit_states() == {
        "employees.contracts": CircuitState.OPEN,
        "employees.payrolls": CircuitState.CLOSED,
    }
    await coordinator.close()


def test_runtime_configuration_rejects_unsafe_bounds_and_caps_backoff() -> None:
    with pytest.raises(ValueError, match="circuit breaker"):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError, match="circuit breaker"):
        CircuitBreaker(recovery_timeout_seconds=0)
    with pytest.raises(ValueError, match="attempts"):
        ReadRetryPolicy(attempts=0)
    with pytest.raises(ValueError, match="delays"):
        ReadRetryPolicy(initial_delay_seconds=-1)
    with pytest.raises(ValueError, match="timeout"):
        ReadRetryPolicy(operation_timeout_seconds=0)
    with pytest.raises(ValueError, match="queue size"):
        BrowserOperationQueue(max_queue_size=0)
    with pytest.raises(ValueError, match="queue size"):
        BrowserOperationQueue(workers=0)
    assert ReadRetryPolicy(initial_delay_seconds=1, maximum_delay_seconds=2).delay(10) == 2


@pytest.mark.asyncio
async def test_queue_rejects_empty_keys_and_submissions_after_close() -> None:
    queue = BrowserOperationQueue()

    async def operation() -> str:
        return "synthetic"

    assert queue.queued == 0
    with pytest.raises(ValueError, match="lock key"):
        await queue.submit("", operation)
    await queue.close()
    await queue.close()
    with pytest.raises(RuntimeError, match="closing"):
        await queue.submit("employee:one", operation)


@pytest.mark.asyncio
async def test_circuit_half_open_allows_one_probe_then_recovers() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=5,
        clock=lambda: now[0],
    )
    await breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(DicCircuitOpenError, match="open"):
        await breaker.before_call()
    now[0] = 6
    await breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN
    with pytest.raises(DicCircuitOpenError, match="half-open"):
        await breaker.before_call()
    await breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    now[0] = 12
    await breaker.before_call()
    await breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_non_transient_errors_are_not_retried_and_ui_drift_opens_circuit() -> None:
    breaker = CircuitBreaker(failure_threshold=1)
    coordinator = BrowserCoordinator(
        circuit_breaker=breaker,
        read_retry=ReadRetryPolicy(attempts=2, operation_timeout_seconds=1),
    )
    calls = 0

    async def invalid() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("synthetic invalid request")

    with pytest.raises(ValueError, match="invalid request"):
        await coordinator.run_read("read", "one", invalid)
    assert calls == 1
    assert breaker.state is CircuitState.CLOSED

    async def drift() -> None:
        raise DicUiChangedError("synthetic drift")

    with pytest.raises(DicUiChangedError, match="drift"):
        await coordinator.run_write("write", "one", drift)
    assert breaker.state is CircuitState.OPEN
    await coordinator.close()


@pytest.mark.asyncio
async def test_reconciliation_helper_distinguishes_known_unknown_and_failed_reads() -> None:
    coordinator = BrowserCoordinator(
        circuit_breaker=CircuitBreaker(failure_threshold=1),
        read_retry=ReadRetryPolicy(operation_timeout_seconds=1),
    )
    known = ReconciliationResult(
        action_id="00000000-0000-4000-8000-000000000001",
        state=ReconciliationState.CONFIRMED_APPLIED,
        detail="synthetic known",
    )

    async def known_result() -> ReconciliationResult:
        return known

    assert await coordinator.reconcile_or_raise(known_result) == known

    async def unknown_result() -> ReconciliationResult:
        return ReconciliationResult(
            action_id="00000000-0000-4000-8000-000000000002",
            state=ReconciliationState.UNKNOWN,
            detail="synthetic unknown",
        )

    with pytest.raises(DicReconciliationRequiredError, match="synthetic unknown"):
        await coordinator.reconcile_or_raise(unknown_result)

    async def failed_result() -> None:
        raise RuntimeError("synthetic reconciliation failure")

    with pytest.raises(RuntimeError, match="reconciliation failure"):
        await coordinator.run_reconciliation("one", failed_result)
    assert coordinator.circuit_breaker.state is CircuitState.OPEN
    await coordinator.close()
