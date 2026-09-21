"""Bounded, process-local conversational references containing opaque identifiers only."""

from __future__ import annotations

import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from bh_dic.security.sanitization import validate_employee_id

_FUNCTION_ID = re.compile(r"^[A-Z][A-Z0-9-]{2,31}$")
_SAFE_PARAMETER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ORDINALS = {
    "primo": 1,
    "prima": 1,
    "secondo": 2,
    "seconda": 2,
    "terzo": 3,
    "terza": 3,
    "quarto": 4,
    "quarta": 4,
    "quinto": 5,
    "quinta": 5,
}
_ORDINAL = re.compile(
    r"(?i)^(?:(?:apri|usa|scegli|seleziona)\s+)?(?:il|la|l['\u2019])?\s*"
    r"(primo|prima|secondo|seconda|terzo|terza|quarto|quarta|quinto|quinta)$"
)
_SELECTION_CONTEXT_ID = re.compile(r"^[0-9a-f]{32}$")
_DIRECT_EMPLOYEE_ID = re.compile(
    r"(?i)^(?:(?:employee|dipendente)\s*id|id)?\s*[:#]?\s*([A-Za-z0-9_-]{1,64})$"
)
_BARE_EMPLOYEE_REFERENCE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9_-]{0,63}|"
    r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\u2019-]*"
    r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\u2019-]*){0,5})$"
)
_NEW_REQUEST_MARKERS = frozenset(
    {
        "apri",
        "capacita",
        "capacità",
        "cerca",
        "come",
        "dimmi",
        "elenca",
        "mostra",
        "quale",
        "quali",
        "quanti",
        "segna",
        "stampa",
        "status",
        "trova",
        "visualizza",
    }
)


@dataclass(frozen=True, slots=True)
class ConversationKey:
    user_id: int
    guild_id: int
    channel_id: int

    def __post_init__(self) -> None:
        if self.user_id <= 0 or self.guild_id <= 0 or self.channel_id <= 0:
            raise ValueError("conversation key identifiers must be positive")


@dataclass(frozen=True, slots=True)
class ConversationContext:
    candidate_employee_ids: tuple[str, ...]
    function_id: str
    parameters: tuple[tuple[str, int | bool | str], ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class PendingEmployeeTarget:
    function_id: str
    parameters: tuple[tuple[str, int | bool | str], ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class EmployeeSelectionContext:
    key: ConversationKey
    context: ConversationContext


class ConversationContextStore:
    """LRU/TTL store isolated by user, guild and transport conversation."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 900,
        max_conversations: int = 1_000,
        max_candidates: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= ttl_seconds <= 86_400:
            raise ValueError("conversation TTL is out of bounds")
        if not 1 <= max_conversations <= 10_000:
            raise ValueError("conversation store size is out of bounds")
        if not 1 <= max_candidates <= 500:
            raise ValueError("conversation candidate limit is out of bounds")
        self._ttl_seconds = float(ttl_seconds)
        self._max_conversations = max_conversations
        self._max_candidates = max_candidates
        self._clock = clock
        self._items: OrderedDict[ConversationKey, ConversationContext] = OrderedDict()
        self._pending_targets: OrderedDict[ConversationKey, PendingEmployeeTarget] = OrderedDict()
        self._selection_contexts: OrderedDict[str, EmployeeSelectionContext] = OrderedDict()

    @staticmethod
    def _validated_context_parameters(
        parameters: Mapping[str, object] | None,
    ) -> tuple[tuple[str, int | bool | str], ...]:
        safe_parameters: list[tuple[str, int | bool | str]] = []
        for name, value in (parameters or {}).items():
            if _SAFE_PARAMETER.fullmatch(name) is None:
                raise ValueError("invalid context parameter name")
            if type(value) not in {str, int, bool}:
                raise ValueError("context parameters must be scalar")
            if isinstance(value, str) and (not value or len(value) > 64):
                raise ValueError("context parameter string is invalid")
            safe_parameters.append((name, cast(int | bool | str, value)))
        return tuple(sorted(safe_parameters))

    def remember_candidates(
        self,
        key: ConversationKey,
        candidate_employee_ids: tuple[str, ...],
        *,
        function_id: str,
        parameters: Mapping[str, object] | None = None,
    ) -> str:
        if not candidate_employee_ids or len(candidate_employee_ids) > self._max_candidates:
            raise ValueError("candidate result set is empty or exceeds the bound")
        validated_ids = tuple(validate_employee_id(item) for item in candidate_employee_ids)
        if len(set(validated_ids)) != len(validated_ids):
            raise ValueError("candidate result set contains duplicate identifiers")
        if _FUNCTION_ID.fullmatch(function_id) is None:
            raise ValueError("invalid context function identifier")
        safe_parameters = self._validated_context_parameters(parameters)
        now = self._clock()
        self._purge_expired(now)
        context = ConversationContext(
            candidate_employee_ids=validated_ids,
            function_id=function_id,
            parameters=safe_parameters,
            expires_at=now + self._ttl_seconds,
        )
        self._items[key] = context
        self._pending_targets.pop(key, None)
        self._items.move_to_end(key)
        while len(self._items) > self._max_conversations:
            self._items.popitem(last=False)
        context_id = uuid.uuid4().hex
        self._selection_contexts[context_id] = EmployeeSelectionContext(key, context)
        self._selection_contexts.move_to_end(context_id)
        while len(self._selection_contexts) > self._max_conversations:
            self._selection_contexts.popitem(last=False)
        return context_id

    def remember_pending_target(
        self,
        key: ConversationKey,
        *,
        function_id: str,
        parameters: Mapping[str, object] | None = None,
    ) -> None:
        """Remember an operation while waiting for one employee name or opaque ID."""

        if _FUNCTION_ID.fullmatch(function_id) is None:
            raise ValueError("invalid context function identifier")
        now = self._clock()
        self._purge_expired(now)
        self._pending_targets[key] = PendingEmployeeTarget(
            function_id=function_id,
            parameters=self._validated_context_parameters(parameters),
            expires_at=now + self._ttl_seconds,
        )
        self._pending_targets.move_to_end(key)
        while len(self._pending_targets) > self._max_conversations:
            self._pending_targets.popitem(last=False)

    def pending_target(
        self,
        key: ConversationKey,
        request: str,
    ) -> PendingEmployeeTarget | None:
        """Consume a bounded bare name/ID only when an operation is awaiting that target."""

        normalized = " ".join(request.strip().split())
        first_word = normalized.split(maxsplit=1)[0].casefold() if normalized else ""
        if (
            not normalized
            or len(normalized) > 128
            or _BARE_EMPLOYEE_REFERENCE.fullmatch(normalized) is None
            or first_word in _NEW_REQUEST_MARKERS
        ):
            return None
        now = self._clock()
        self._purge_expired(now)
        context = self._pending_targets.get(key)
        if context is not None:
            self._pending_targets.move_to_end(key)
        return context

    def clear_pending_target(self, key: ConversationKey) -> bool:
        return self._pending_targets.pop(key, None) is not None

    def selection(
        self, key: ConversationKey, request: str
    ) -> tuple[str, ConversationContext] | None:
        now = self._clock()
        self._purge_expired(now)
        context = self._items.get(key)
        if context is None:
            return None
        match = _ORDINAL.fullmatch(" ".join(request.strip().split()))
        selected: str | None = None
        if match is not None:
            ordinal = _ORDINALS[match.group(1).casefold()]
            if ordinal <= len(context.candidate_employee_ids):
                selected = context.candidate_employee_ids[ordinal - 1]
        else:
            direct = _DIRECT_EMPLOYEE_ID.fullmatch(" ".join(request.strip().split()))
            if direct is not None:
                try:
                    candidate = validate_employee_id(direct.group(1))
                except ValueError:
                    candidate = ""
                if candidate in context.candidate_employee_ids:
                    selected = candidate
        if selected is None:
            return None
        self._items.move_to_end(key)
        return selected, context

    def activate_selection_context(
        self,
        key: ConversationKey,
        context_id: str,
        employee_id: str,
    ) -> bool:
        """Restore the immutable context which produced one Discord selection menu."""

        if _SELECTION_CONTEXT_ID.fullmatch(context_id) is None:
            return False
        try:
            selected = validate_employee_id(employee_id)
        except ValueError:
            return False
        now = self._clock()
        self._purge_expired(now)
        snapshot = self._selection_contexts.get(context_id)
        if (
            snapshot is None
            or snapshot.key != key
            or selected not in snapshot.context.candidate_employee_ids
        ):
            return False
        self._selection_contexts.move_to_end(context_id)
        self._items[key] = snapshot.context
        self._items.move_to_end(key)
        return True

    def candidate_context(self, key: ConversationKey, request: str) -> ConversationContext | None:
        """Return an opaque candidate set for a bounded bare surname/name follow-up."""

        normalized = " ".join(request.strip().split())
        first_word = normalized.split(maxsplit=1)[0].casefold() if normalized else ""
        if (
            not normalized
            or len(normalized) > 128
            or _BARE_EMPLOYEE_REFERENCE.fullmatch(normalized) is None
            or first_word in _NEW_REQUEST_MARKERS
        ):
            return None
        now = self._clock()
        self._purge_expired(now)
        context = self._items.get(key)
        if context is not None:
            self._items.move_to_end(key)
        return context

    def clear(self, key: ConversationKey) -> bool:
        candidate_removed = self._items.pop(key, None) is not None
        pending_removed = self._pending_targets.pop(key, None) is not None
        selection_ids = [
            context_id
            for context_id, snapshot in self._selection_contexts.items()
            if snapshot.key == key
        ]
        for context_id in selection_ids:
            self._selection_contexts.pop(context_id, None)
        return candidate_removed or pending_removed or bool(selection_ids)

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
        pending_expired = [
            key for key, value in self._pending_targets.items() if value.expires_at <= now
        ]
        for key in pending_expired:
            self._pending_targets.pop(key, None)
        expired_selections = [
            context_id
            for context_id, snapshot in self._selection_contexts.items()
            if snapshot.context.expires_at <= now
        ]
        for context_id in expired_selections:
            self._selection_contexts.pop(context_id, None)


__all__ = [
    "ConversationContext",
    "ConversationContextStore",
    "ConversationKey",
    "EmployeeSelectionContext",
    "PendingEmployeeTarget",
]
