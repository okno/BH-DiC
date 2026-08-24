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
