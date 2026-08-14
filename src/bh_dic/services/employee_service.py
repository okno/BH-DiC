"""Authorized employee-summary facade over the application coordinator."""

from __future__ import annotations

from bh_dic.application import BHApplicationCoordinator
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import InteractionResult


class EmployeeService:
    """Expose the coordinator's policy-checked employee summary operation."""

    def __init__(self, coordinator: BHApplicationCoordinator) -> None:
        self._coordinator = coordinator

    async def get_summary(self, actor: DiscordActor, employee_id: str) -> InteractionResult:
        """Return a redacted summary after the coordinator rechecks policy and scope."""

        return await self._coordinator.employee(actor, employee_id)
