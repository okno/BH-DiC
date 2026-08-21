"""Discord bot lifecycle and optional mention/channel modes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Literal, cast
from uuid import uuid4

import discord
from discord.ext import commands

from bh_dic.dic.errors import DicError, DicUiChangedError
from bh_dic.discord.approvals import PendingViewSource, restore_approval_views
from bh_dic.discord.checks import DiscordAccessDenied, DiscordGate
from bh_dic.discord.commands import BHCommandGroup
from bh_dic.discord.embeds import result_embed
from bh_dic.discord.interactions import InteractionCoordinator
from bh_dic.errors import ApplicationPolicyDenied
from bh_dic.hr_assistant import (
    HrRequestInputError,
    is_general_hr_request,
    is_operational_hr_request,
)
from bh_dic.language import BotLanguageProfile
from bh_dic.logging import log_context
from bh_dic.model_usage import ModelUsageKey, ModelUsageService, ModelUsageStart
from bh_dic.openai.client import IntentProviderError, PublicHrProviderError, PublicHrResponder
from bh_dic.openai.redaction import UnsafePromptError, prepare_public_hr_input
from bh_dic.security.rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)

_STARTUP_ONLINE_MESSAGE = "BOT HR Bitcoin Hotel Online!"
_STARTUP_DIC_READY_MESSAGE = "Stato Dipendenti in Cloud: ATTIVO."
_STARTUP_DIC_UNAVAILABLE_MESSAGE = (
    "Stato Dipendenti in Cloud: NON DISPONIBILE. Un amministratore autorizzato può eseguire "
    "`/bh dic reconnect`; il bot non reinvia automaticamente le credenziali."
)


@dataclass(frozen=True, slots=True)
class StartupStatusSnapshot:
    adapter_ready: bool
    browser_available: bool
    dic_authenticated: bool
    write_enabled: bool
    provider: str
    model: str


class BHDiCBot(commands.Bot):
    def __init__(
        self,
        *,
        application_id: int,
        guild_id: int,
        gate: DiscordGate,
        coordinator: InteractionCoordinator,
        interaction_mode: Literal["slash", "mention", "channel"] = "slash",
        publish_sensitive_channel_responses: bool = False,
        upload_max_bytes: int = 20 * 1024 * 1024,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        message_rate_limiter: SlidingWindowRateLimiter | None = None,
        global_message_rate_limiter: SlidingWindowRateLimiter | None = None,
        pending_view_source: PendingViewSource | None = None,
        language_profile: BotLanguageProfile | None = None,
        public_hr_responder: PublicHrResponder | None = None,
        model_usage: ModelUsageService | None = None,
        public_hr_provider: str = "unconfigured",
        public_hr_model: str = "unconfigured",
        startup_status_probe: Callable[[], Awaitable[StartupStatusSnapshot | bool]] | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        if interaction_mode in {"mention", "channel"}:
            intents.guild_messages = True
            intents.dm_messages = False
            intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned, intents=intents, application_id=application_id
        )
        self.allowed_guild = discord.Object(id=guild_id)
        self.gate = gate
        self.coordinator = coordinator
        self.interaction_mode = interaction_mode
        self.publish_sensitive_channel_responses = publish_sensitive_channel_responses
        self.pending_view_source = pending_view_source
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter(limit=30, window_seconds=60)
        self.message_rate_limiter = message_rate_limiter or SlidingWindowRateLimiter(
            limit=10, window_seconds=60
        )
        self.global_message_rate_limiter = global_message_rate_limiter or SlidingWindowRateLimiter(
            limit=60, window_seconds=60
        )
        self._public_hr_slots = asyncio.Semaphore(4)
        self.bh_commands = BHCommandGroup(
            gate=gate,
            coordinator=coordinator,
            upload_max_bytes=upload_max_bytes,
            publish_sensitive_channel_responses=publish_sensitive_channel_responses,
            rate_limiter=self.rate_limiter,
            language_profile=language_profile,
        )
        self.language_profile = language_profile
        if interaction_mode == "channel" and public_hr_responder is None:
            raise ValueError("channel interaction mode requires a public HR responder")
        self.public_hr_responder = public_hr_responder
        self.model_usage = model_usage
        self.public_hr_provider = public_hr_provider
        self.public_hr_model = public_hr_model
        self.startup_status_probe = startup_status_probe
        self._startup_notice_sent = False
        self._startup_notice_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.tree.add_command(self.bh_commands, guild=self.allowed_guild)
        if self.pending_view_source is not None:
            restored = await restore_approval_views(
                self, self.bh_commands, self.pending_view_source
            )
            logger.info("discord_approval_views_restored", extra={"count": restored})

    async def on_ready(self) -> None:
        logger.info("discord_ready", extra={"guild_id": self.allowed_guild.id})
        await self._send_startup_notice()

    async def _send_startup_notice(self) -> None:
        """Publish one non-sensitive availability notice for each bot process start."""

        async with self._startup_notice_lock:
            if self._startup_notice_sent:
                return
            channel = self.get_channel(self.gate.channel_id)
            if channel is None or not hasattr(channel, "send"):
                logger.warning("discord_startup_notice_channel_unavailable")
                return
            message_channel = cast(discord.abc.Messageable, channel)
            snapshot = StartupStatusSnapshot(
                adapter_ready=False,
                browser_available=False,
                dic_authenticated=False,
                write_enabled=False,
                provider=self.public_hr_provider,
                model=self.public_hr_model,
            )
            if self.startup_status_probe is not None:
                try:
                    probed = await self.startup_status_probe()
                    if isinstance(probed, StartupStatusSnapshot):
                        snapshot = probed
                    else:
                        snapshot = StartupStatusSnapshot(
                            adapter_ready=probed,
                            browser_available=probed,
                            dic_authenticated=probed,
                            write_enabled=False,
                            provider=self.public_hr_provider,
                            model=self.public_hr_model,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "discord_startup_status_probe_failed",
                        extra={"exception_type": type(exc).__name__},
                    )
            status = (
                _STARTUP_DIC_READY_MESSAGE
                if snapshot.adapter_ready and snapshot.dic_authenticated
                else _STARTUP_DIC_UNAVAILABLE_MESSAGE
            )
            status_lines = (
                f"Adapter: {'READY' if snapshot.adapter_ready else 'DEGRADED'}",
                f"Browser: {'available' if snapshot.browser_available else 'unavailable'}",
                f"DIC tenant: {'AUTHENTICATED' if snapshot.dic_authenticated else 'UNAVAILABLE'}",
                f"Write kill switch: {'ENABLED' if snapshot.write_enabled else 'DISABLED'}",
                f"Provider/modello AI: {snapshot.provider} · {snapshot.model}",
            )
            try:
                await message_channel.send(
                    "\n".join((_STARTUP_ONLINE_MESSAGE, status, *status_lines)),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "discord_startup_notice_failed",
                    extra={"exception_type": type(exc).__name__},
                )
                return
            self._startup_notice_sent = True
            logger.info(
                "discord_startup_notice_sent",
                extra={
                    "dic_available": snapshot.adapter_ready and snapshot.dic_authenticated,
                },
            )

    async def on_message(self, message: discord.Message) -> None:
        if self.interaction_mode == "slash":
            return
        if message.author.bot or message.webhook_id is not None or message.guild is None:
            return
        if isinstance(message.channel, discord.Thread):
            return
        is_mentioned = self.user is not None and self.user in message.mentions
        referenced = getattr(getattr(message, "reference", None), "resolved", None)
        is_reply_to_bot = (
            self.user is not None
            and isinstance(referenced, discord.Message)
            and referenced.author.id == self.user.id
        )
        if self.interaction_mode == "mention" and not is_mentioned:
            return
        if self.interaction_mode == "channel" and message.channel.id != self.gate.channel_id:
            return
        if (
            self.interaction_mode == "channel"
            and not is_mentioned
            and not is_reply_to_bot
            and not is_general_hr_request(message.content)
        ):
            return
        request = message.content
        if is_mentioned and self.user is not None:
            request = request.replace(self.user.mention, "", 1).strip()
        correlation_id = f"msg-{uuid4().hex}"
        with log_context(
            correlation_id=correlation_id,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
        ):
            await self._dispatch_message(message, request, correlation_id)

    async def _dispatch_message(
        self,
        message: discord.Message,
        request: str,
        correlation_id: str,
    ) -> None:
        guild = message.guild
        if guild is None:
            return
        try:
            actor = self.gate.authorize(
                user_id=message.author.id,
                guild_id=guild.id,
                channel_id=message.channel.id,
                role_ids=[role.id for role in getattr(message.author, "roles", [])],
                is_thread=False,
                is_bot=message.author.bot,
                is_webhook=message.webhook_id is not None,
            )
            limiter = (
                self.message_rate_limiter
                if self.interaction_mode == "channel"
                else self.rate_limiter
            )
            rate_limit = await limiter.check(str(actor.user_id))
            if rate_limit.allowed and self.interaction_mode == "channel":
                rate_limit = await self.global_message_rate_limiter.check(
                    f"{actor.guild_id}:{actor.channel_id}"
                )
            if not rate_limit.allowed:
                await message.reply(
                    "Troppe richieste. Riprova tra pochi secondi.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            if self.interaction_mode == "channel":
                if is_operational_hr_request(request):
                    result = await self.coordinator.ask(actor, request)
                    await self._reply_with_result(message, result)
                    return
                if self._public_hr_slots.locked():
                    await message.reply(
                        "L'assistente HR sta gestendo altre richieste. Riprova tra pochi secondi.",
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                async with self._public_hr_slots:
                    await self._reply_as_public_hr(message, request, correlation_id)
                return
            result = await self.coordinator.ask(actor, request)
            if result.ephemeral:
                await message.reply(
                    "Per questa risposta sensibile usa `/bh ask`; "
                    "la modalità messaggio non è ephemeral.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            await message.reply(
                embed=result_embed(result, self.language_profile),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except DiscordAccessDenied as exc:
            logger.warning(
                "discord_message_access_denied",
                extra={"reason": exc.reason.value},
            )
            try:
                await message.reply(
                    "Richiesta non autorizzata per questo server, canale o ruolo.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return
        except ApplicationPolicyDenied as exc:
            logger.info(
                "discord_message_policy_denied",
                extra={"reason": exc.decision.code.value},
            )
            if exc.decision.code.value == "FEATURE_DISABLED":
                reply = "Funzione disabilitata dalla policy operativa corrente."
            elif exc.decision.code.value == "ROLE_DENIED":
                reply = "Il tuo ruolo Discord non autorizza questa funzione."
            else:
                reply = "La policy applicativa non autorizza questa richiesta."
            if exc.correlation_id:
                reply += f" Correlation ID: `{exc.correlation_id}`."
            try:
                await message.reply(
                    reply,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return
        except (HrRequestInputError, UnsafePromptError):
            logger.info("discord_message_hr_request_rejected_locally")
            try:
                await message.reply(
                    "Richiesta rifiutata localmente: usa un solo ID o nome dipendente e non "
                    "includere istruzioni tecniche o di bypass.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return
        except IntentProviderError as exc:
            logger.warning(
                "discord_message_intent_provider_unavailable",
                extra={"exception_type": type(exc).__name__},
            )
            try:
                await message.reply(
                    "Il servizio AI non ha completato il routing. Nessuna operazione DIC è "
                    "stata eseguita; riprova più tardi.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return
        except DicUiChangedError as exc:
            logger.warning(
                "discord_message_dic_ui_contract_changed",
                extra={"exception_type": type(exc).__name__},
            )
            try:
                await message.reply(
                    "La sessione DIC è attiva, ma la pagina richiesta è cambiata. Nessun dato "
                    "è stato modificato; segnala la richiesta all'amministratore del bot.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return
        except (DicError, TimeoutError) as exc:
            logger.warning(
                "discord_message_dic_unavailable",
                extra={"exception_type": type(exc).__name__},
            )
            try:
                await message.reply(
                    "Dipendenti in Cloud non è disponibile o la sessione verificata è scaduta. "
                    "Nessuna operazione è stata completata.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return
        except Exception as exc:
            logger.error(
                "discord_message_failed",
                extra={"error_code": "UNEXPECTED_ERROR", "exception_type": type(exc).__name__},
            )
            try:
                await message.reply(
                    "Operazione non completata. Riprova; per un caso individuale usa `/bh ask` "
                    "solo se sei autorizzato, altrimenti contatta HR.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return

    async def _reply_with_result(
        self,
        message: discord.Message,
        result: object,
    ) -> None:
        from bh_dic.discord.interactions import InteractionResult

        if not isinstance(result, InteractionResult):
            raise TypeError("coordinator returned an invalid interaction result")
        if result.ephemeral and not self.publish_sensitive_channel_responses:
            await message.reply(
                "La risposta contiene dati HR sensibili. Usa `/bh ask` oppure abilita "
                "esplicitamente la pubblicazione nel canale HR protetto.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        view = self.bh_commands.approval_view(result.action_id) if result.action_id else None
        files = [
            discord.File(BytesIO(attachment.content), filename=attachment.filename)
            for attachment in result.attachments
        ]
        await message.reply(
            embed=result_embed(result, self.language_profile),
            view=cast(discord.ui.View, view),
            files=files,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        for index, chunk in enumerate(result.messages, start=1):
            await message.channel.send(
                f"Parte {index}/{len(result.messages)}\n{chunk}",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _reply_as_public_hr(
        self,
        message: discord.Message,
        request: str,
        correlation_id: str,
    ) -> None:
        responder = self.public_hr_responder
        if responder is None:
            raise RuntimeError("public HR responder is unavailable")
        try:
            prepare_public_hr_input(request)
        except (UnsafePromptError, ValueError):
            logger.info("public_hr_request_rejected_locally")
            await message.reply(
                "Formula una domanda HR generale senza dati personali, segreti o istruzioni "
                "tecniche. Per casi individuali usa `/bh ask` solo se sei autorizzato; "
                "altrimenti contatta HR.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        usage_key = ModelUsageKey(
            correlation_id=correlation_id,
            purpose="public_hr_response",
            ordinal=1,
        )
        usage_started = False
        if self.model_usage is not None:
            await self.model_usage.start(
                ModelUsageStart(
                    key=usage_key,
                    provider=self.public_hr_provider,
                    model=self.public_hr_model,
                )
            )
            usage_started = True
        try:
            response = await responder.respond(request)
        except asyncio.CancelledError:
            if usage_started and self.model_usage is not None:
                try:
                    await asyncio.shield(
                        self.model_usage.complete(
                            usage_key,
                            response_received=False,
                            usage=None,
                        )
                    )
                except (asyncio.CancelledError, Exception):
                    # Usage telemetry must never replace the cancellation that initiated
                    # shutdown. The STARTED row remains an explicit, auditable gap.
                    logger.warning("public_hr_usage_completion_failed_during_cancellation")
            raise
        except PublicHrProviderError as exc:
            if usage_started and self.model_usage is not None:
                await self.model_usage.complete(
                    usage_key,
                    response_received=exc.response_received,
                    usage=exc.usage,
                )
            logger.warning(
                "public_hr_provider_unavailable",
                extra={
                    "provider": exc.provider,
                    "model": exc.model,
                    "response_received": exc.response_received,
                },
            )
            await message.reply(
                "L'assistente HR non è disponibile in questo momento. Riprova tra poco.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        except Exception:
            if usage_started and self.model_usage is not None:
                await self.model_usage.complete(
                    usage_key,
                    response_received=False,
                    usage=None,
                )
            raise
        if usage_started and self.model_usage is not None:
            await self.model_usage.complete(
                usage_key,
                response_received=True,
                usage=response.usage,
            )
        logger.info(
            "public_hr_response_completed",
            extra={
                "provider": response.provider,
                "model": response.model,
                "usage_reported": response.usage is not None,
            },
        )
        await message.reply(
            response.text,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def close(self) -> None:
        await super().close()
