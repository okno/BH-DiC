"""Guild-scoped slash command group."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from io import BytesIO

import discord
from discord import app_commands

from bh_dic.dic.auth import DicAuthOutcomeUnknownError
from bh_dic.dic.errors import (
    DicAuthenticationError,
    DicCaptchaRequiredError,
    DicError,
    DicMfaRequiredError,
    DicPasswordExpiredError,
    DicUiChangedError,
)
from bh_dic.discord.checks import DiscordAccessDenied, DiscordActor, DiscordGate
from bh_dic.discord.embeds import result_embed
from bh_dic.discord.interactions import AttachmentPayload, InteractionCoordinator, InteractionResult
from bh_dic.discord.views import ApprovalView
from bh_dic.errors import ApplicationPolicyDenied
from bh_dic.hr_assistant import HrRequestInputError
from bh_dic.language import BotLanguageProfile
from bh_dic.openai.client import IntentProviderError
from bh_dic.openai.redaction import UnsafePromptError
from bh_dic.security.rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


class DicCommandGroup(app_commands.Group):
    """Administrative DIC session commands nested below ``/bh dic``."""

    def __init__(self, reconnect: Callable[[discord.Interaction], Awaitable[None]]) -> None:
        super().__init__(name="dic", description="Gestione sicura della sessione DiC")
        self._reconnect = reconnect

    @app_commands.command(
        name="reconnect",
        description="Ripristina una sessione DiC scaduta con le credenziali configurate",
    )
    async def reconnect_command(self, interaction: discord.Interaction) -> None:
        await self._reconnect(interaction)


class BHCommandGroup(app_commands.Group):
    def __init__(
        self,
        *,
        gate: DiscordGate,
        coordinator: InteractionCoordinator,
        upload_max_bytes: int = 20 * 1024 * 1024,
        publish_sensitive_channel_responses: bool = False,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        language_profile: BotLanguageProfile | None = None,
    ) -> None:
        super().__init__(name="bh", description="Assistente HR autorizzato BH-DiC")
        if upload_max_bytes <= 0:
            raise ValueError("upload_max_bytes must be positive")
        self._gate = gate
        self._coordinator = coordinator
        self._upload_max_bytes = upload_max_bytes
        self._publish_sensitive_channel_responses = publish_sensitive_channel_responses
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(limit=30, window_seconds=60)
        self._language_profile = language_profile
        self.dic_commands = DicCommandGroup(self._reconnect_dic_from_command)
        self.add_command(self.dic_commands)

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

    def approval_view(self, action_id: str) -> ApprovalView:
        return ApprovalView(action_id, self._approve_from_view, self._reject_from_view)

    @staticmethod
    def _files(result: InteractionResult) -> list[discord.File]:
        return [
            discord.File(BytesIO(attachment.content), filename=attachment.filename)
            for attachment in result.attachments
        ]

    async def _send(
        self,
        interaction: discord.Interaction,
        operation: Callable[[DiscordActor], Awaitable[InteractionResult]],
        *,
        publish_sensitive: bool = False,
    ) -> None:
        deferred_ephemeral = False
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
                await interaction.response.defer(ephemeral=not publish_sensitive, thinking=True)
                deferred_ephemeral = not publish_sensitive
            result = await operation(actor)
            delivery_ephemeral = result.ephemeral and not publish_sensitive
            view: discord.ui.View | None = None
            if result.action_id:
                view = self.approval_view(result.action_id)
            embed = result_embed(result, self._language_profile)
            if not result.ephemeral and deferred_ephemeral:
                # Discord fixes the privacy of the deferred original response.
                # Complete that private acknowledgement first, then publish a
                # separate follow-up only for an explicitly public aggregate.
                await interaction.edit_original_response(
                    content="Risultato aggregato pubblicato nel canale autorizzato.",
                    embed=None,
                    view=None,
                )
            if view is None:
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=delivery_ephemeral,
                    files=self._files(result),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=delivery_ephemeral,
                    view=view,
                    files=self._files(result),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            for index, message in enumerate(result.messages, start=1):
                await interaction.followup.send(
                    content=f"Parte {index}/{len(result.messages)}\n{message}",
                    ephemeral=delivery_ephemeral,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except DiscordAccessDenied as exc:
            logger.warning(
                "discord_access_denied",
                extra={"reason": exc.reason.value},
            )
            message = "Richiesta non autorizzata per questo server, canale o ruolo."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (HrRequestInputError, UnsafePromptError):
            logger.info("hr_request_rejected_locally")
            message = (
                "Per proteggere i dati HR, usa un solo Employee ID esplicito per le richieste "
                "individuali e non includere istruzioni tecniche o di bypass."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except IntentProviderError as exc:
            logger.warning(
                "model_provider_unavailable",
                extra={"exception_type": type(exc).__name__},
            )
            message = (
                "Il servizio AI non ha completato l'interpretazione della richiesta. "
                "Nessuna operazione DIC è stata eseguita; riprova più tardi."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except DicAuthOutcomeUnknownError as exc:
            logger.warning(
                "dic_authentication_outcome_unknown",
                extra={"exception_type": type(exc).__name__},
            )
            message = (
                "L'esito del login DIC è incerto. Per sicurezza le credenziali non saranno "
                "reinviate automaticamente: verifica `/bh status` e la sessione web prima di "
                "un nuovo tentativo."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (DicCaptchaRequiredError, DicMfaRequiredError, DicPasswordExpiredError) as exc:
            logger.warning(
                "dic_authentication_interactive_step_required",
                extra={"exception_type": type(exc).__name__},
            )
            message = (
                "Il provider richiede un passaggio interattivo (MFA, CAPTCHA o rinnovo password). "
                "Completa la verifica amministrativa via web e poi riprova una sola volta."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except DicAuthenticationError as exc:
            logger.warning(
                "dic_authentication_failed",
                extra={"exception_type": type(exc).__name__},
            )
            message = (
                "Il login DIC non è stato completato. Verifica le credenziali configurate e gli "
                "eventuali controlli interattivi, senza incollarli su Discord."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except DicUiChangedError as exc:
            logger.warning(
                "dic_ui_contract_changed",
                extra={"exception_type": type(exc).__name__},
            )
            message = (
                "La sessione DIC è attiva, ma la pagina richiesta non corrisponde più al "
                "contratto UI verificato. Nessun dato è stato modificato; segnala la richiesta "
                "all'amministratore del bot."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (DicError, TimeoutError) as exc:
            logger.warning(
                "dic_operation_unavailable",
                extra={"exception_type": type(exc).__name__},
            )
            message = (
                "Dipendenti in Cloud non è disponibile o la sessione verificata è scaduta. "
                "Il bot resta online, ma questa lettura HR non è stata completata."
            )
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except ApplicationPolicyDenied as exc:
            logger.info(
                "application_policy_denied",
                extra={"reason": exc.decision.code.value},
            )
            if exc.decision.code.value == "FEATURE_DISABLED":
                message = "Funzione disabilitata dalla policy operativa corrente."
            elif exc.decision.code.value == "ROLE_DENIED":
                message = "Il tuo ruolo Discord non autorizza questa funzione."
            else:
                message = "La policy applicativa non autorizza questa richiesta."
            if exc.correlation_id:
                message += f" Correlation ID: `{exc.correlation_id}`."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception as exc:
            logger.error(
                "discord_command_failed",
                extra={"error_code": "UNEXPECTED_ERROR", "exception_type": type(exc).__name__},
            )
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
            publish_sensitive=self._component_may_publish(interaction),
        )

    async def _reject_from_view(
        self, interaction: discord.Interaction, action_id: str, reason: str
    ) -> None:
        await self._send(
            interaction,
            lambda actor: self._coordinator.reject(actor, action_id, reason),
            publish_sensitive=self._component_may_publish(interaction),
        )

    def _component_may_publish(self, interaction: discord.Interaction) -> bool:
        """Allow public completion only for a component attached to a public HR message."""

        if not self._publish_sensitive_channel_responses:
            return False
        source_message = getattr(interaction, "message", None)
        if source_message is None:
            return False
        flags = getattr(source_message, "flags", None)
        return not bool(getattr(flags, "ephemeral", False))

    async def _reconnect_dic_from_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.reconnect_dic)

    @app_commands.command(name="ask", description="Interpreta una richiesta HR autorizzata")
    @app_commands.describe(richiesta="Richiesta in italiano (massimo 2.000 caratteri)")
    async def ask(
        self, interaction: discord.Interaction, richiesta: app_commands.Range[str, 1, 2000]
    ) -> None:
        await self._send(interaction, lambda actor: self._coordinator.ask(actor, str(richiesta)))

    @app_commands.command(name="help", description="Mostra funzioni autorizzate e limiti")
    async def help_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.help)

    @app_commands.command(name="capabilities", description="Mostra la matrice funzioni DiC")
    async def capabilities_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.capabilities)

    @app_commands.command(name="funzioni", description="Mostra la matrice funzioni DiC")
    async def functions_command(self, interaction: discord.Interaction) -> None:
        await self._send(interaction, self._coordinator.capabilities)

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
