from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bh_dic.audit.chain import GENESIS_HASH, compute_event_hash, verify_events
from bh_dic.audit.models import AuditEventInput, AuditEventMaterial, AuditEventView, AuditOutcome

KEY = b"synthetic-audit-key-material-32bytes!!"


def event_input(**overrides: object) -> AuditEventInput:
    values: dict[str, object] = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "timestamp_utc": datetime(2026, 1, 1, tzinfo=UTC),
        "event_type": "employee.read.completed",
        "correlation_id": "corr-test-0001",
        "actor_discord_id": "100000000000000001",
        "guild_id": "100000000000000002",
        "channel_id": "100000000000000003",
        "function_id": "EMP-READ-001",
        "target_pseudonym": "emp_0123456789abcdef",
        "outcome": AuditOutcome.SUCCESS,
        "payload": {"result_count": 2, "source": "synthetic"},
    }
    values.update(overrides)
    return AuditEventInput.model_validate(values)


def test_event_hash_is_deterministic_and_covers_previous_hash() -> None:
    event = event_input()
    material = AuditEventMaterial(
        sequence=1,
        previous_hash=GENESIS_HASH,
        **event.model_dump(),
    )
    changed = material.model_copy(update={"previous_hash": "1" * 64})

    assert compute_event_hash(KEY, material) == compute_event_hash(KEY, material)
    assert compute_event_hash(KEY, material) != compute_event_hash(KEY, changed)


def test_verifier_accepts_valid_chain_and_rejects_tampering() -> None:
    first_material = AuditEventMaterial(
        sequence=1,
        previous_hash=GENESIS_HASH,
        **event_input().model_dump(),
    )
    first_hash = compute_event_hash(KEY, first_material)
    first = AuditEventView(**first_material.model_dump(), event_hash=first_hash)

    second_material = AuditEventMaterial(
        sequence=2,
        previous_hash=first_hash,
        **event_input(
            event_id="00000000-0000-4000-8000-000000000002",
            correlation_id="corr-test-0002",
        ).model_dump(),
    )
    second_hash = compute_event_hash(KEY, second_material)
    second = AuditEventView(**second_material.model_dump(), event_hash=second_hash)

    valid = verify_events((first, second), KEY, state_sequence=2, state_hash=second_hash)
    tampered = second.model_copy(update={"outcome": AuditOutcome.FAILED})
    invalid = verify_events((first, tampered), KEY)

    assert valid.valid is True
    assert valid.event_count == 2
    assert invalid.valid is False
    assert invalid.failure_sequence == 2
    assert invalid.reason == "event HMAC mismatch"


def test_sensitive_audit_payload_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="sensitive audit payload key"):
        event_input(payload={"nested": {"api_token": "synthetic-secret"}})
    with pytest.raises(ValidationError, match="sensitive audit payload key"):
        event_input(payload={"employee_email": "synthetic@example.invalid"})


def test_short_hmac_key_is_rejected() -> None:
    material = AuditEventMaterial(
        sequence=1,
        previous_hash=GENESIS_HASH,
        **event_input().model_dump(),
    )
    with pytest.raises(ValueError, match="at least 32 bytes"):
        compute_event_hash(b"short", material)
