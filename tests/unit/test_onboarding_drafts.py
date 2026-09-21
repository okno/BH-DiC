from __future__ import annotations

import asyncio

import pytest

from bh_dic.onboarding import (
    DocumentDraft,
    FieldEvidence,
    OnboardingDraftError,
    OnboardingDraftKey,
    OnboardingDraftStore,
)


def _draft() -> DocumentDraft:
    return DocumentDraft(
        fields={
            "first_name": FieldEvidence(value="Nome Sintetico", confidence=0.9, source="upload_01"),
            "last_name": FieldEvidence(
                value="Cognome Sintetico", confidence=0.9, source="upload_01"
            ),
        },
        missing_required=("email", "phone"),
    )


def test_draft_is_actor_bound_fillable_and_discarded_without_repr_pii() -> None:
    store = OnboardingDraftStore()
    key = OnboardingDraftKey(1, 2, 3)
    stored = store.create(key, _draft())

    assert "Nome Sintetico" not in repr(stored)
    updated = store.fill_missing(
        key,
        stored.draft_id,
        {"email": "persona@example.test", "phone": "+39 333 1234567"},
    )

    assert updated.draft.missing_required == ()
    assert updated.draft.fields["email"].value == "persona@example.test"
    assert store.discard(key, stored.draft_id)
    with pytest.raises(OnboardingDraftError):
        store.get(key, stored.draft_id)


def test_draft_rejects_cross_actor_unknown_fields_and_expiry() -> None:
    now = 100.0
    store = OnboardingDraftStore(ttl_seconds=60, clock=lambda: now)
    key = OnboardingDraftKey(1, 2, 3)
    stored = store.create(key, _draft())

    with pytest.raises(OnboardingDraftError):
        store.get(OnboardingDraftKey(9, 2, 3), stored.draft_id)
    with pytest.raises(OnboardingDraftError):
        store.get(OnboardingDraftKey(1, 9, 3), stored.draft_id)
    with pytest.raises(OnboardingDraftError):
        store.get(OnboardingDraftKey(1, 2, 9), stored.draft_id)
    with pytest.raises(OnboardingDraftError):
        store.fill_missing(key, stored.draft_id, {"email": "persona@example.test"})

    now = 161.0
    with pytest.raises(OnboardingDraftError):
        store.get(key, stored.draft_id)


@pytest.mark.asyncio
async def test_draft_is_removed_by_scheduled_expiry_without_later_store_access() -> None:
    store = OnboardingDraftStore(ttl_seconds=0.01)
    key = OnboardingDraftKey(1, 2, 3)
    stored = store.create(key, _draft())

    await asyncio.sleep(0.03)

    assert store.purge_expired() == 0
    with pytest.raises(OnboardingDraftError):
        store.get(key, stored.draft_id)
