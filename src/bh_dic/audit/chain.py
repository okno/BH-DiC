"""Pure HMAC-SHA256 audit-chain functions."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Sequence

from pydantic import SecretStr

from bh_dic.audit.models import (
    AuditEventMaterial,
    AuditEventView,
    AuditVerificationResult,
)

GENESIS_HASH = "0" * 64
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def normalize_hmac_key(key: bytes | str | SecretStr) -> bytes:
    if isinstance(key, SecretStr):
        normalized = key.get_secret_value().encode("utf-8")
    elif isinstance(key, str):
        normalized = key.encode("utf-8")
    else:
        normalized = bytes(key)
    if len(normalized) < 32:
        raise ValueError("AUDIT_HMAC_KEY must contain at least 32 bytes")
    return normalized


def canonical_event_bytes(event: AuditEventMaterial) -> bytes:
    payload = event.model_dump(mode="json")
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return serialized.encode("utf-8")


def compute_event_hash(key: bytes | str | SecretStr, event: AuditEventMaterial) -> str:
    return hmac.new(
        normalize_hmac_key(key), canonical_event_bytes(event), hashlib.sha256
    ).hexdigest()


def verify_event_hash(
    key: bytes | str | SecretStr,
    event: AuditEventMaterial,
    claimed_hash: str,
) -> bool:
    if not _HASH_PATTERN.fullmatch(claimed_hash):
        return False
    return hmac.compare_digest(compute_event_hash(key, event), claimed_hash)


def verify_events(
    events: Sequence[AuditEventView],
    key: bytes | str | SecretStr,
    *,
    state_sequence: int | None = None,
    state_hash: str | None = None,
) -> AuditVerificationResult:
    normalized_key = normalize_hmac_key(key)
    expected_previous = GENESIS_HASH
    expected_sequence = 1

    for event in events:
        if event.sequence != expected_sequence:
            return AuditVerificationResult(
                valid=False,
                event_count=len(events),
                last_sequence=expected_sequence - 1,
                last_hash=expected_previous,
                failure_sequence=expected_sequence,
                reason="non-contiguous audit sequence",
            )
        if not hmac.compare_digest(event.previous_hash, expected_previous):
            return AuditVerificationResult(
                valid=False,
                event_count=len(events),
                last_sequence=expected_sequence - 1,
                last_hash=expected_previous,
                failure_sequence=event.sequence,
                reason="previous hash mismatch",
            )
        material = AuditEventMaterial.model_validate(event.model_dump(exclude={"event_hash"}))
        if not verify_event_hash(normalized_key, material, event.event_hash):
            return AuditVerificationResult(
                valid=False,
                event_count=len(events),
                last_sequence=expected_sequence - 1,
                last_hash=expected_previous,
                failure_sequence=event.sequence,
                reason="event HMAC mismatch",
            )
        expected_previous = event.event_hash
        expected_sequence += 1

    last_sequence = expected_sequence - 1
    if state_sequence is not None and state_sequence != last_sequence:
        return AuditVerificationResult(
            valid=False,
            event_count=len(events),
            last_sequence=last_sequence,
            last_hash=expected_previous,
            failure_sequence=None,
            reason="audit chain-state sequence mismatch",
        )
    if state_hash is not None and not hmac.compare_digest(state_hash, expected_previous):
        return AuditVerificationResult(
            valid=False,
            event_count=len(events),
            last_sequence=last_sequence,
            last_hash=expected_previous,
            failure_sequence=None,
            reason="audit chain-state hash mismatch",
        )
    return AuditVerificationResult(
        valid=True,
        event_count=len(events),
        last_sequence=last_sequence,
        last_hash=expected_previous,
    )
