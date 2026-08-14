"""Authorized contract facade over the application coordinator."""

from __future__ import annotations

from datetime import date

from bh_dic.application import BHApplicationCoordinator
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import InteractionResult


class ContractService:
    """Expose deterministic, policy-checked contract queries."""

    def __init__(self, coordinator: BHApplicationCoordinator) -> None:
        self._coordinator = coordinator

    async def list_contracts(
        self,
        actor: DiscordActor,
        *,
        employee_id: str | None = None,
        expiring_from: date | None = None,
        expiring_to: date | None = None,
    ) -> InteractionResult:
        """List contracts or expirations through the coordinator's authorized read path."""

        return await self._coordinator.contracts(
            actor,
            employee_id,
            expiring_from,
            expiring_to,
        )
