"""Bounded, process-local conversational references containing opaque identifiers only."""

from __future__ import annotations

import re
import time
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
    r"(?i)\b(?:il|la|l['\u2019])?\s*(primo|prima|secondo|seconda|terzo|terza|"
    r"quarto|quarta|quinto|quinta)\b"
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

    def remember_candidates(
        self,
        key: ConversationKey,
        candidate_employee_ids: tuple[str, ...],
        *,
        function_id: str,
        parameters: Mapping[str, object] | None = None,
    ) -> None:
        if not candidate_employee_ids or len(candidate_employee_ids) > self._max_candidates:
            raise ValueError("candidate result set is empty or exceeds the bound")
        validated_ids = tuple(validate_employee_id(item) for item in candidate_employee_ids)
        if len(set(validated_ids)) != len(validated_ids):
            raise ValueError("candidate result set contains duplicate identifiers")
        if _FUNCTION_ID.fullmatch(function_id) is None:
            raise ValueError("invalid context function identifier")
        safe_parameters: list[tuple[str, int | bool | str]] = []
        for name, value in (parameters or {}).items():
            if _SAFE_PARAMETER.fullmatch(name) is None:
                raise ValueError("invalid context parameter name")
            if type(value) not in {str, int, bool}:
                raise ValueError("context parameters must be scalar")
            if isinstance(value, str) and (not value or len(value) > 64):
                raise ValueError("context parameter string is invalid")
            safe_parameters.append((name, cast(int | bool | str, value)))
        now = self._clock()
        self._purge_expired(now)
        self._items[key] = ConversationContext(
            candidate_employee_ids=validated_ids,
            function_id=function_id,
            parameters=tuple(sorted(safe_parameters)),
            expires_at=now + self._ttl_seconds,
        )
        self._items.move_to_end(key)
        while len(self._items) > self._max_conversations:
            self._items.popitem(last=False)

    def selection(
        self, key: ConversationKey, request: str
    ) -> tuple[str, ConversationContext] | None:
        match = _ORDINAL.search(request)
        if match is None:
            return None
        now = self._clock()
        self._purge_expired(now)
        context = self._items.get(key)
        if context is None:
            return None
        ordinal = _ORDINALS[match.group(1).casefold()]
        if ordinal > len(context.candidate_employee_ids):
            return None
        self._items.move_to_end(key)
        return context.candidate_employee_ids[ordinal - 1], context

    def clear(self, key: ConversationKey) -> bool:
        return self._items.pop(key, None) is not None

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)


__all__ = ["ConversationContext", "ConversationContextStore", "ConversationKey"]
