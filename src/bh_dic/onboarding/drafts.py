"""Short-lived, actor-bound onboarding drafts containing no raw document bytes or OCR text."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date

from bh_dic.onboarding.ocr import DocumentDraft, FieldEvidence

_DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EMAIL = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.IGNORECASE)
_PHONE = re.compile(r"^\+?[0-9][0-9 ()/.-]{5,24}$")
_TAX_CODE = re.compile(r"^[A-Z0-9]{11,32}$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class OnboardingDraftKey:
    user_id: int
    guild_id: int
    channel_id: int

    def __post_init__(self) -> None:
        if self.user_id <= 0 or self.guild_id <= 0 or self.channel_id <= 0:
            raise ValueError("onboarding draft key identifiers must be positive")


@dataclass(frozen=True, slots=True)
class StoredOnboardingDraft:
    draft_id: str
    key: OnboardingDraftKey
    draft: DocumentDraft = field(repr=False)
    expires_at: float


class OnboardingDraftError(ValueError):
    """A safe, non-PII draft validation or ownership failure."""


class OnboardingDraftStore:
    """Bounded process-local LRU store; extracted PII expires without persistence."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 900,
        max_drafts: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < ttl_seconds <= 3_600:
            raise ValueError("onboarding draft TTL is out of bounds")
        if not 1 <= max_drafts <= 1_000:
            raise ValueError("onboarding draft bound is invalid")
        self._ttl_seconds = float(ttl_seconds)
        self._max_drafts = max_drafts
        self._clock = clock
        self._items: OrderedDict[str, StoredOnboardingDraft] = OrderedDict()

    def create(self, key: OnboardingDraftKey, draft: DocumentDraft) -> StoredOnboardingDraft:
        now = self._clock()
        self._purge(now)
        draft_id = uuid.uuid4().hex
        stored = StoredOnboardingDraft(draft_id, key, draft, now + self._ttl_seconds)
        self._items[draft_id] = stored
        while len(self._items) > self._max_drafts:
            self._items.popitem(last=False)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Synchronous tooling still receives lazy TTL enforcement on every store access.
            pass
        else:
            loop.call_later(
                self._ttl_seconds,
                _expire_scheduled_draft,
                weakref.ref(self),
                draft_id,
                stored.expires_at,
            )
        return stored

    def get(self, key: OnboardingDraftKey, draft_id: str) -> StoredOnboardingDraft:
        if _DRAFT_ID.fullmatch(draft_id) is None:
            raise OnboardingDraftError("bozza onboarding non valida")
        self._purge(self._clock())
        stored = self._items.get(draft_id)
        if stored is None or stored.key != key:
            raise OnboardingDraftError("bozza onboarding non disponibile")
        self._items.move_to_end(draft_id)
        return stored

    def fill_missing(
        self,
        key: OnboardingDraftKey,
        draft_id: str,
        values: Mapping[str, str],
    ) -> StoredOnboardingDraft:
        stored = self.get(key, draft_id)
        if stored.draft.conflicts:
            raise OnboardingDraftError("risolvi i conflitti documentali con un nuovo caricamento")
        if not values or len(values) > 5:
            raise OnboardingDraftError("compilazione onboarding non valida")
        allowed = set(stored.draft.missing_required[:5])
        if set(values) != allowed:
            raise OnboardingDraftError("i campi della bozza non corrispondono")
        fields = dict(stored.draft.fields)
        for name, raw_value in values.items():
            fields[name] = FieldEvidence(
                value=self._validate_value(name, raw_value),
                confidence=1.0,
                source="discord_form",
            )
        missing = tuple(name for name in stored.draft.missing_required if name not in fields)
        updated = StoredOnboardingDraft(
            draft_id=stored.draft_id,
            key=stored.key,
            draft=DocumentDraft(
                fields=fields,
                conflicts=stored.draft.conflicts,
                missing_required=missing,
            ),
            expires_at=stored.expires_at,
        )
        self._items[draft_id] = updated
        self._items.move_to_end(draft_id)
        return updated

    def discard(self, key: OnboardingDraftKey, draft_id: str) -> bool:
        try:
            stored = self.get(key, draft_id)
        except OnboardingDraftError:
            return False
        return self._items.pop(stored.draft_id, None) is not None

    def purge_expired(self) -> int:
        """Eagerly discard expired PII and return the number of removed drafts."""

        return self._purge(self._clock())

    @staticmethod
    def _validate_value(name: str, raw_value: str) -> str:
        if _FIELD_NAME.fullmatch(name) is None or not isinstance(raw_value, str):
            raise OnboardingDraftError("campo onboarding non valido")
        value = " ".join(raw_value.strip().split())
        if not value or len(value) > 320 or any(ord(character) < 32 for character in value):
            raise OnboardingDraftError("valore onboarding non valido")
        if name in {"first_name", "last_name"}:
            if len(value) > 128 or any(
                not (character.isalpha() or character in " '-") for character in value
            ):
                raise OnboardingDraftError("nome onboarding non valido")
        elif name == "email" and _EMAIL.fullmatch(value) is None:
            raise OnboardingDraftError("email onboarding non valida")
        elif name == "phone" and _PHONE.fullmatch(value) is None:
            raise OnboardingDraftError("telefono onboarding non valido")
        elif name == "tax_code" and _TAX_CODE.fullmatch(value) is None:
            raise OnboardingDraftError("codice fiscale onboarding non valido")
        elif name == "date_of_birth":
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise OnboardingDraftError("data onboarding non valida") from None
            if not date(1900, 1, 1) <= parsed <= date.today():
                raise OnboardingDraftError("data onboarding fuori intervallo")
        return value

    def _expire_if_current(self, draft_id: str, expires_at: float) -> None:
        stored = self._items.get(draft_id)
        if stored is not None and stored.expires_at == expires_at and expires_at <= self._clock():
            self._items.pop(draft_id, None)

    def _purge(self, now: float) -> int:
        expired = [draft_id for draft_id, item in self._items.items() if item.expires_at <= now]
        for draft_id in expired:
            self._items.pop(draft_id, None)
        return len(expired)


def _expire_scheduled_draft(
    store_reference: weakref.ReferenceType[OnboardingDraftStore],
    draft_id: str,
    expires_at: float,
) -> None:
    store = store_reference()
    if store is not None:
        store._expire_if_current(draft_id, expires_at)


__all__ = [
    "OnboardingDraftError",
    "OnboardingDraftKey",
    "OnboardingDraftStore",
    "StoredOnboardingDraft",
]
