"""Guild-scoped slash command group."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date

import discord
from discord import app_commands

from bh_dic.discord.checks import DiscordAccessDenied, DiscordActor, DiscordGate
from bh_dic.discord.embeds import result_embed
from bh_dic.discord.interactions import AttachmentPayload, InteractionCoordinator, InteractionResult
from bh_dic.discord.views import ApprovalView
from bh_dic.security.rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


class BHCommandGroup(app_commands.Group):
    def __init__(
        self,
        *,
        gate: DiscordGate,
        coordinator: InteractionCoordinator,
        upload_max_bytes: int = 20 * 1024 * 1024,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        super().__init__(name="bh", description="Assistente HR autorizzato BH-DiC")
        if upload_max_bytes <= 0:
            raise ValueError("upload_max_bytes must be positive")
        self._gate = gate
        self._coordinator = coordinator
        self._upload_max_bytes = upload_max_bytes
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(limit=30, window_seconds=60)

    def _actor(self, interaction: discord.Interaction) -> DiscordActor:
        role_ids = [role.id for role in getattr(interaction.user, "roles", [])]
        return self._gate.authorize(
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            role_ids=role_ids,
            is_thread=isinstance(interaction.channel, discord.Thread),
            is_bot=interaction.user.bot,
            is_webhook=False,
        )

    async def _send(
        self,
        interaction: discord.Interaction,
        operation: Callable[[DiscordActor], Awaitable[InteractionResult]],
    ) -> None:
        try:
            actor = self._actor(interaction)
            rate_limit = await self._rate_limiter.check(str(actor.user_id))
            if not rate_limit.allowed:
                message = (
                    "Troppe richieste. Riprova tra "
                    f"{max(1, int(rate_limit.retry_after_seconds) + 1)} secondi."
                )
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
                return
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            result = await operation(actor)
            view: discord.ui.View | None = None
            if result.action_id:
                view = ApprovalView(
                    result.action_id, self._approve_from_view, self._reject_from_view
                )
            if view is None:
                await interaction.followup.send(
                    embed=result_embed(result),
                    ephemeral=result.ephemeral,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.followup.send(
                    embed=result_embed(result),
                    ephemeral=result.ephemeral,
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except DiscordAccessDenied as exc:
            logger.warning("discord_access_denied", extra={"reason": exc.reason.value})
            message = "Richiesta non autorizzata per questo server, canale o ruolo."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            logger.exception("discord_command_failed")
            message = "Operazione non completata. Usa il correlation ID nei log amministrativi."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)

    async def _approve_from_view(
        self,
        interaction: discord.Interaction,
        action_id: str,
        confirmation_code: str,
        target_confirmation: str | None,
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.approve(
                actor,
                action_id,
                confirmation_code,
                target_confirmation,
            ),
        )

    async def _reject_from_view(
        self, interaction: discord.Interaction, action_id: str, reason: str
    ) -> None:
        await self._send(
            interaction, lambda actor: self._coordinator.reject(actor, action_id, reason)
        )

    @app_commands.command(name="ask", description="Interpreta una richiesta HR autorizzata")
    @app_commands.describe(richiesta="Richiesta in italiano (massimo 2.000 caratteri)")
    async def ask(
        self, interaction: discord.Interaction, richiesta: app_commands.Range[str, 1, 2000]
    ) -> None:
        await self._send(interaction, lambda actor: self._coordinator.ask(actor, str(richiesta)))

    @app_commands.command(name="help", description="Mostra funzioni autorizzate e limiti")
    async def help_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.help)

    @app_commands.command(name="status", description="Mostra lo stato operativo redatto")
    async def status_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.status)

    @app_commands.command(name="health", description="Esegue un health check non sensibile")
    async def health_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.health)

    @app_commands.command(name="pending", description="Elenca le azioni pending autorizzate")
    async def pending_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.pending)

    @app_commands.command(name="approve", description="Approva una azione pending")
    async def approve_command(
        self,
        interaction: discord.Interaction,
        action_id: app_commands.Range[str, 36, 36],
        confirmation_code: app_commands.Range[str, 4, 64],
        target_confirmation: app_commands.Range[str, 1, 100] | None = None,
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.approve(
                actor,
                str(action_id),
                str(confirmation_code),
                str(target_confirmation) if target_confirmation else None,
            ),
        )

    @app_commands.command(name="reject", description="Rifiuta una azione pending")
    async def reject_command(
        self,
        interaction: discord.Interaction,
        action_id: app_commands.Range[str, 36, 36],
        reason: app_commands.Range[str, 3, 500],
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.reject(actor, str(action_id), str(reason)),
        )

    @app_commands.command(name="upload", description="Mette un allegato autorizzato in quarantena")
    async def upload_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        category: app_commands.Range[str, 1, 64],
        attachment: discord.Attachment,
    ) -> None:
        async def operation(actor: DiscordActor) -> InteractionResult:
            if attachment.size > self._upload_max_bytes:
                return InteractionResult(
                    title="Allegato troppo grande",
                    description="Il file supera il limite di upload configurato.",
                    success=False,
                )
            content = await attachment.read(use_cached=True)
            payload = AttachmentPayload(
                original_filename=attachment.filename,
                content_type=attachment.content_type,
                declared_size=attachment.size,
                content=content,
            )
            return await self._coordinator.upload(
                actor,
                str(employee_id),
                str(category),
                payload,
            )

        await self._send(interaction, operation)

    @app_commands.command(
        name="employee", description="Mostra il riepilogo redatto di un dipendente"
    )
    async def employee_command(
        self, interaction: discord.Interaction, employee_id: app_commands.Range[str, 1, 64]
    ) -> None:
        await self._send(
            interaction, lambda actor: self._coordinator.employee(actor, str(employee_id))
        )

    @app_commands.command(name="contracts", description="Consulta contratti e scadenze")
    async def contracts_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64] | None = None,
        expiring_from: str | None = None,
        expiring_to: str | None = None,
    ) -> None:
        try:
            start = date.fromisoformat(expiring_from) if expiring_from else None
            end = date.fromisoformat(expiring_to) if expiring_to else None
        except ValueError:
            await interaction.response.send_message("Usa date ISO YYYY-MM-DD.", ephemeral=True)
            return
        await self._send(
            interaction,
            lambda actor: self._coordinator.contracts(
                actor, str(employee_id) if employee_id else None, start, end
            ),
        )

    @app_commands.command(name="documents", description="Consulta soltanto metadati documentali")
    async def documents_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        status: str | None = None,
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.documents(actor, str(employee_id), status),
        )

    @app_commands.command(name="balances", description="Consulta il bilancio autorizzato")
    async def balances_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        year: app_commands.Range[int, 2000, 2100],
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.balances(actor, str(employee_id), int(year)),
        )

    @app_commands.command(
        name="operator-balance-correction",
        description="Prepara una correzione bilancio ad alta criticita",
    )
    @app_commands.describe(
        previous_value="Valore corrente verificato dall'operatore",
        new_value="Nuovo valore da applicare",
        motivation="Motivazione obbligatoria per audit e approvazione",
    )
    async def operator_balance_correction_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        year: app_commands.Range[int, 2000, 2100],
        month: app_commands.Range[int, 1, 12],
        category: app_commands.Range[str, 1, 64],
        previous_value: app_commands.Range[str, 1, 64],
        new_value: app_commands.Range[str, 1, 64],
        motivation: app_commands.Range[str, 3, 500],
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.prepare_operator_action(
                actor,
                "EMP-BAL-002",
                str(employee_id),
                {
                    "year": int(year),
                    "month": int(month),
                    "category": str(category),
                    "previous_value": str(previous_value),
                    "amount": str(new_value),
                    "motivation": str(motivation),
                },
            ),
        )

    @app_commands.command(
        name="operator-rbac-update",
        description="Prepara una modifica autorizzazioni ad alta criticita",
    )
    @app_commands.describe(
        motivation="Motivazione obbligatoria per audit e approvazione",
        role_name="Nome esatto del ruolo gia presente sul dipendente",
        enabled="Nuovo stato del ruolo",
    )
    async def operator_rbac_update_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        motivation: app_commands.Range[str, 3, 500],
        role_name: app_commands.Range[str, 1, 128],
        enabled: bool,
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.prepare_operator_action(
                actor,
                "EMP-RBAC-002",
                str(employee_id),
                {
                    "motivation": str(motivation),
                    "role_name": str(role_name),
                    "enabled": enabled,
                },
            ),
        )

    @app_commands.command(
        name="operator-document-download",
        description="Prepara un artifact documentale locale protetto",
    )
    @app_commands.describe(
        document_id="Identificativo stabile del documento",
        motivation="Motivazione obbligatoria per audit e approvazione",
    )
    async def operator_document_download_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        document_id: app_commands.Range[str, 1, 128],
        motivation: app_commands.Range[str, 3, 500],
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.prepare_operator_action(
                actor,
                "EMP-DOC-003",
                str(employee_id),
                {"document_id": str(document_id), "motivation": str(motivation)},
            ),
        )

    @app_commands.command(
        name="operator-employee-delete",
        description="Prepara l'eliminazione definitiva di un dipendente",
    )
    @app_commands.describe(motivation="Motivazione obbligatoria per audit e approvazione")
    async def operator_employee_delete_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        motivation: app_commands.Range[str, 3, 500],
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.prepare_operator_action(
                actor,
                "EMP-DELETE-001",
                str(employee_id),
                {"motivation": str(motivation)},
            ),
        )

    @app_commands.command(
        name="operator-contract-delete",
        description="Prepara l'eliminazione di un contratto",
    )
    @app_commands.describe(
        contract_id="Identificativo stabile del contratto",
        motivation="Motivazione obbligatoria per audit e approvazione",
    )
    async def operator_contract_delete_command(
        self,
        interaction: discord.Interaction,
        employee_id: app_commands.Range[str, 1, 64],
        contract_id: app_commands.Range[str, 1, 128],
        motivation: app_commands.Range[str, 3, 500],
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.prepare_operator_action(
                actor,
                "EMP-CONTRACT-003",
                str(employee_id),
                {"contract_id": str(contract_id), "motivation": str(motivation)},
            ),
        )
