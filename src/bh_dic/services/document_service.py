"""Authorized document facade over metadata and quarantine workflows."""

from __future__ import annotations

from bh_dic.application import BHApplicationCoordinator
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import AttachmentPayload, InteractionResult


class DocumentService:
    """Keep document reads metadata-only and uploads inside the coordinator pipeline."""

    def __init__(self, coordinator: BHApplicationCoordinator) -> None:
        self._coordinator = coordinator

    async def list_metadata(
        self,
        actor: DiscordActor,
        employee_id: str,
        *,
        status: str | None = None,
    ) -> InteractionResult:
        """Return authorized document metadata, never document content."""

        return await self._coordinator.documents(actor, employee_id, status)

    async def upload(
        self,
        actor: DiscordActor,
        employee_id: str,
        category: str,
        attachment: AttachmentPayload,
    ) -> InteractionResult:
        """Send one attachment through quarantine, policy, confirmation, and approval checks."""

        return await self._coordinator.upload(actor, employee_id, category, attachment)
