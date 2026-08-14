"""Discord UI elements, including restart-safe approval controls."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence

import discord

ApprovalCallback = Callable[[discord.Interaction, str, str, str | None], Awaitable[None]]
RejectCallback = Callable[[discord.Interaction, str, str], Awaitable[None]]
SelectionCallback = Callable[[discord.Interaction, str], Awaitable[None]]

_ACTION_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


class ApprovalCodeModal(discord.ui.Modal, title="Conferma azione BH-DiC"):
    confirmation_code: discord.ui.TextInput[ApprovalCodeModal] = discord.ui.TextInput(
        label="Codice di conferma",
        placeholder="Inserisci il codice monouso",
        min_length=4,
        max_length=64,
    )
    target_confirmation: discord.ui.TextInput[ApprovalCodeModal] = discord.ui.TextInput(
        label="Conferma target (se richiesta)",
        placeholder="Esempio: DELETE 529874",
        required=False,
        max_length=100,
    )

    def __init__(self, action_id: str, callback: ApprovalCallback) -> None:
        super().__init__(timeout=180)
        self._action_id = action_id
        self._callback = callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        target = str(self.target_confirmation).strip() or None
        await self._callback(
            interaction,
            self._action_id,
            str(self.confirmation_code),
            target,
        )


class RejectReasonModal(discord.ui.Modal, title="Rifiuta azione BH-DiC"):
    reason: discord.ui.TextInput[RejectReasonModal] = discord.ui.TextInput(
        label="Motivazione",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=500,
    )

    def __init__(self, action_id: str, callback: RejectCallback) -> None:
        super().__init__(timeout=180)
        self._action_id = action_id
        self._callback = callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction, self._action_id, str(self.reason))


class ApprovalView(discord.ui.View):
    """Persistent view; reconstruct one instance for every pending action on boot."""

    def __init__(
        self,
        action_id: str,
        approve_callback: ApprovalCallback,
        reject_callback: RejectCallback,
    ) -> None:
        if not _ACTION_ID.fullmatch(action_id):
            raise ValueError("action_id must be a UUID")
        super().__init__(timeout=None)
        self.action_id = action_id
        self._approve_callback = approve_callback
        self._reject_callback = reject_callback

        approve: discord.ui.Button[ApprovalView] = discord.ui.Button(
            label="Approva",
            style=discord.ButtonStyle.success,
            custom_id=f"bh-dic:approve:{action_id}",
        )
        reject: discord.ui.Button[ApprovalView] = discord.ui.Button(
            label="Rifiuta",
            style=discord.ButtonStyle.danger,
            custom_id=f"bh-dic:reject:{action_id}",
        )
        approve.callback = self._approve  # type: ignore[method-assign]
        reject.callback = self._reject  # type: ignore[method-assign]
        self.add_item(approve)
        self.add_item(reject)

    async def _approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ApprovalCodeModal(self.action_id, self._approve_callback)
        )

    async def _reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            RejectReasonModal(self.action_id, self._reject_callback)
        )


class EmployeeSelect(discord.ui.Select[discord.ui.View]):
    def __init__(self, choices: Sequence[tuple[str, str]], callback: SelectionCallback) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=employee_id[:100])
            for employee_id, label in choices[:25]
        ]
        super().__init__(placeholder="Seleziona il dipendente tramite ID", options=options)
        self._selection_callback = callback

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._selection_callback(interaction, self.values[0])


class EmployeeSelectView(discord.ui.View):
    def __init__(self, choices: Sequence[tuple[str, str]], callback: SelectionCallback) -> None:
        super().__init__(timeout=180)
        self.add_item(EmployeeSelect(choices, callback))


class PaginationView(discord.ui.View):
    def __init__(
        self, previous: Callable[[], Awaitable[None]], following: Callable[[], Awaitable[None]]
    ) -> None:
        super().__init__(timeout=180)
        self._previous = previous
        self._following = following

    @discord.ui.button(label="Precedente", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self, interaction: discord.Interaction, _: discord.ui.Button[PaginationView]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._previous()

    @discord.ui.button(label="Successiva", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, _: discord.ui.Button[PaginationView]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._following()
