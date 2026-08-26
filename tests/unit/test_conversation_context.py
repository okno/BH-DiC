from __future__ import annotations

import pytest

from bh_dic.query.context import ConversationContextStore, ConversationKey


def test_context_is_isolated_by_user_channel_and_expires() -> None:
    now = 100.0
    store = ConversationContextStore(ttl_seconds=30, clock=lambda: now)
    key = ConversationKey(1, 10, 20)
    store.remember_candidates(
        key,
        ("EMP-SYNTH-001", "EMP-SYNTH-002"),
        function_id="EMP-PAY-001",
        parameters={"year": 2026, "month": 7},
    )
    selected = store.selection(key, "apri il secondo")
    assert selected is not None
    assert selected[0] == "EMP-SYNTH-002"
    assert dict(selected[1].parameters) == {"month": 7, "year": 2026}
    assert store.selection(ConversationKey(2, 10, 20), "il secondo") is None
    assert store.selection(ConversationKey(1, 10, 21), "il secondo") is None

    now = 131.0
    assert store.selection(key, "il secondo") is None


def test_context_is_bounded_validated_and_can_be_cleared() -> None:
    store = ConversationContextStore(max_conversations=1, max_candidates=2)
    first = ConversationKey(1, 10, 20)
    second = ConversationKey(2, 10, 20)
    store.remember_candidates(first, ("EMP-SYNTH-001",), function_id="EMP-READ-002")
    store.remember_candidates(second, ("EMP-SYNTH-002",), function_id="EMP-READ-002")
    assert store.selection(first, "il primo") is None
    assert store.selection(second, "il primo") is not None
    assert store.clear(second)
    assert not store.clear(second)

    with pytest.raises(ValueError, match="duplicate"):
        store.remember_candidates(
            first,
            ("EMP-SYNTH-001", "EMP-SYNTH-001"),
            function_id="EMP-READ-002",
        )


def test_pending_employee_target_accepts_one_bounded_name_or_id_and_is_consumed() -> None:
    store = ConversationContextStore()
    key = ConversationKey(1, 10, 20)
    store.remember_pending_target(
        key,
        function_id="EMP-PAY-001",
        parameters={"latest_paid": True, "include_net": True},
    )

    pending = store.pending_target(key, "Amine Mohamed Abbadi")

    assert pending is not None
    assert pending.function_id == "EMP-PAY-001"
    assert dict(pending.parameters) == {"include_net": True, "latest_paid": True}
    assert store.pending_target(key, "Amine Mohamed Abbadi") is not None
    assert store.clear_pending_target(key)
    assert store.pending_target(key, "Amine Mohamed Abbadi") is None


def test_pending_employee_target_does_not_consume_a_new_sentence() -> None:
    store = ConversationContextStore()
    key = ConversationKey(1, 10, 20)
    store.remember_pending_target(key, function_id="EMP-PAY-001")

    assert store.pending_target(key, "qual è lo stipendio di Amine?") is None
    assert store.pending_target(key, "mostra notifiche") is None
    assert store.pending_target(key, "EMP-SYNTH-001") is not None
