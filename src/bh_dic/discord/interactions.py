"""Transport-neutral command contracts used by slash and message modes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from bh_dic.discord.checks import DiscordActor


class ResponseSensitivity(StrEnum):
    PUBLIC_AGGREGATE = "PUBLIC_AGGREGATE"
    SENSITIVE = "SENSITIVE"


@dataclass(frozen=True, slots=True)
class ResultField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True, slots=True)
class InteractionResult:
    title: str
    description: str
    fields: tuple[ResultField, ...] = ()
    correlation_id: str | None = None
    sensitivity: ResponseSensitivity = ResponseSensitivity.SENSITIVE
    action_id: str | None = None
    success: bool = True

    @property
    def ephemeral(self) -> bool:
        return self.sensitivity != ResponseSensitivity.PUBLIC_AGGREGATE


@dataclass(frozen=True, slots=True)
class AttachmentPayload:
    original_filename: str
    content_type: str | None
    declared_size: int
    content: bytes = field(repr=False)


class InteractionCoordinator(Protocol):
    async def ask(self, actor: DiscordActor, request: str) -> InteractionResult: ...

    async def help(self, actor: DiscordActor) -> InteractionResult: ...

    async def status(self, actor: DiscordActor) -> InteractionResult: ...

    async def health(self, actor: DiscordActor) -> InteractionResult: ...

    async def pending(self, actor: DiscordActor) -> InteractionResult: ...

    async def approve(
        self,
        actor: DiscordActor,
        action_id: str,
        confirmation_code: str,
        target_confirmation: str | None = None,
    ) -> InteractionResult: ...

    async def reject(
        self, actor: DiscordActor, action_id: str, reason: str
    ) -> InteractionResult: ...

    async def upload(
        self,
        actor: DiscordActor,
        employee_id: str,
        category: str,
        attachment: AttachmentPayload,
    ) -> InteractionResult: ...

    async def employee(self, actor: DiscordActor, employee_id: str) -> InteractionResult: ...

    async def contracts(
        self,
        actor: DiscordActor,
        employee_id: str | None,
        expiring_from: date | None,
        expiring_to: date | None,
    ) -> InteractionResult: ...

    async def documents(
        self, actor: DiscordActor, employee_id: str, status: str | None
    ) -> InteractionResult: ...

    async def balances(
        self, actor: DiscordActor, employee_id: str, year: int
    ) -> InteractionResult: ...

    async def prepare_operator_action(
        self,
        actor: DiscordActor,
        function_id: str,
        employee_id: str,
        parameters: Mapping[str, object],
    ) -> InteractionResult: ...
