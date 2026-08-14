"""Authorized balance facade over the application coordinator."""

from __future__ import annotations

from bh_dic.application import BHApplicationCoordinator
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import InteractionResult


class BalanceService:
    """Expose the entitlement-checked balance read path."""

    def __init__(self, coordinator: BHApplicationCoordinator) -> None:
        self._coordinator = coordinator

    async def get_balance(
        self,
        actor: DiscordActor,
        employee_id: str,
        year: int,
    ) -> InteractionResult:
        """Return an authorized balance rendering for one employee and year."""

        return await self._coordinator.balances(actor, employee_id, year)
