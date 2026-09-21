"""Discord UI elements, including restart-safe approval controls."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence

import discord

ApprovalCallback = Callable[[discord.Interaction, str, str, str | None], Awaitable[None]]
RejectCallback = Callable[[discord.Interaction, str, str], Awaitable[None]]
SelectionCallback = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnboardingCallback = Callable[[discord.Interaction, str, Mapping[str, str]], Awaitable[None]]

_ACTION_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")
_ONBOARDING_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ONBOARDING_LABELS = {
    "first_name": "Nome",
    "last_name": "Cognome",
    "tax_code": "Codice fiscale",
    "date_of_birth": "Data di nascita (YYYY-MM-DD)",
    "email": "Email",
    "phone": "Telefono",
}


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
    def __init__(
        self,
        choices: Sequence[tuple[str, str]],
        callback: SelectionCallback,
        *,
        context_id: str,
    ) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=employee_id[:100])
            for employee_id, label in choices[:25]
        ]
        super().__init__(placeholder="Seleziona il dipendente tramite ID", options=options)
        self._selection_callback = callback
        self._context_id = context_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._selection_callback(interaction, self._context_id, self.values[0])


class EmployeeSelectView(discord.ui.View):
    def __init__(
        self,
        choices: Sequence[tuple[str, str]],
        callback: SelectionCallback,
        *,
        context_id: str,
        requester_user_id: int,
    ) -> None:
        if requester_user_id <= 0:
            raise ValueError("requester_user_id must be positive")
        if _DRAFT_ID.fullmatch(context_id) is None:
            raise ValueError("invalid employee selection context ID")
        super().__init__(timeout=180)
        self._requester_user_id = requester_user_id
        self.add_item(EmployeeSelect(choices, callback, context_id=context_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._requester_user_id:
            return True
        message = "Solo chi ha avviato la richiesta può scegliere questo dipendente."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False


class OnboardingFormModal(discord.ui.Modal):
    """Collect at most five currently missing fields from an actor-bound draft."""

    def __init__(
        self,
        draft_id: str,
        field_names: Sequence[str],
        callback: OnboardingCallback,
    ) -> None:
        if _DRAFT_ID.fullmatch(draft_id) is None:
            raise ValueError("invalid onboarding draft ID")
        if not 1 <= len(field_names) <= 5 or any(
            _ONBOARDING_FIELD.fullmatch(name) is None for name in field_names
        ):
            raise ValueError("invalid onboarding form fields")
        super().__init__(title="Completa bozza dipendente", timeout=300)
        self._draft_id = draft_id
        self._field_names = tuple(field_names)
        self._callback = callback
        self._inputs: dict[str, discord.ui.TextInput[OnboardingFormModal]] = {}
        for name in self._field_names:
            item: discord.ui.TextInput[OnboardingFormModal] = discord.ui.TextInput(
                label=_ONBOARDING_LABELS.get(name, name.replace("_", " ").title())[:45],
                min_length=1,
                max_length=320,
                required=True,
            )
            self._inputs[name] = item
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {name: str(item).strip() for name, item in self._inputs.items()}
        await self._callback(interaction, self._draft_id, values)


class OnboardingDraftView(discord.ui.View):
    def __init__(
        self,
        draft_id: str,
        field_names: Sequence[str],
        callback: OnboardingCallback,
        *,
        requester_user_id: int,
    ) -> None:
        if requester_user_id <= 0:
            raise ValueError("requester_user_id must be positive")
        if _DRAFT_ID.fullmatch(draft_id) is None:
            raise ValueError("invalid onboarding draft ID")
        if not 1 <= len(field_names) <= 5:
            raise ValueError("invalid onboarding field count")
        super().__init__(timeout=300)
        self._draft_id = draft_id
        self._field_names = tuple(field_names)
        self._callback = callback
        self._requester_user_id = requester_user_id
        button: discord.ui.Button[OnboardingDraftView] = discord.ui.Button(
            label="Completa dati mancanti",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self._open_modal  # type: ignore[method-assign]
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._requester_user_id:
            return True
        message = "Solo chi ha avviato l'onboarding può compilare questa bozza."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False

    async def _open_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            OnboardingFormModal(self._draft_id, self._field_names, self._callback)
        )


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
