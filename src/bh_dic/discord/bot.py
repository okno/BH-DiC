"""Discord bot lifecycle and optional mention/channel modes."""

from __future__ import annotations

import logging
from typing import Literal

import discord
from discord.ext import commands

from bh_dic.discord.approvals import PendingViewSource, restore_approval_views
from bh_dic.discord.checks import DiscordAccessDenied, DiscordGate
from bh_dic.discord.commands import BHCommandGroup
from bh_dic.discord.embeds import result_embed
from bh_dic.discord.interactions import InteractionCoordinator
from bh_dic.language import BotLanguageProfile
from bh_dic.security.rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


class BHDiCBot(commands.Bot):
    def __init__(
        self,
        *,
        application_id: int,
        guild_id: int,
        gate: DiscordGate,
        coordinator: InteractionCoordinator,
        interaction_mode: Literal["slash", "mention", "channel"] = "slash",
        upload_max_bytes: int = 20 * 1024 * 1024,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        pending_view_source: PendingViewSource | None = None,
        language_profile: BotLanguageProfile | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        if interaction_mode in {"mention", "channel"}:
            intents.messages = True
            intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned, intents=intents, application_id=application_id
        )
        self.allowed_guild = discord.Object(id=guild_id)
        self.gate = gate
        self.coordinator = coordinator
        self.interaction_mode = interaction_mode
        self.pending_view_source = pending_view_source
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter(limit=30, window_seconds=60)
        self.bh_commands = BHCommandGroup(
            gate=gate,
            coordinator=coordinator,
            upload_max_bytes=upload_max_bytes,
            rate_limiter=self.rate_limiter,
            language_profile=language_profile,
        )
        self.language_profile = language_profile

    async def setup_hook(self) -> None:
        self.tree.add_command(self.bh_commands, guild=self.allowed_guild)
        if self.pending_view_source is not None:
            restored = await restore_approval_views(
                self, self.bh_commands, self.pending_view_source
            )
            logger.info("discord_approval_views_restored", extra={"count": restored})

    async def on_ready(self) -> None:
        logger.info("discord_ready", extra={"guild_id": self.allowed_guild.id})

    async def on_message(self, message: discord.Message) -> None:
        if self.interaction_mode == "slash":
            return
        if message.author.bot or message.webhook_id is not None or message.guild is None:
            return
        if isinstance(message.channel, discord.Thread):
            return
        is_mentioned = self.user is not None and self.user in message.mentions
        if self.interaction_mode == "mention" and not is_mentioned:
            return
        if self.interaction_mode == "channel" and message.channel.id != self.gate.channel_id:
            return
        request = message.content
        if is_mentioned and self.user is not None:
            request = request.replace(self.user.mention, "", 1).strip()
        try:
            actor = self.gate.authorize(
                user_id=message.author.id,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                role_ids=[role.id for role in getattr(message.author, "roles", [])],
                is_thread=False,
                is_bot=message.author.bot,
                is_webhook=message.webhook_id is not None,
            )
            rate_limit = await self.rate_limiter.check(str(actor.user_id))
            if not rate_limit.allowed:
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
        except DiscordAccessDenied:
            logger.warning("discord_message_access_denied")
        except Exception as exc:
            logger.error(
                "discord_message_failed",
                extra={"error_code": "UNEXPECTED_ERROR", "exception_type": type(exc).__name__},
            )
            try:
                await message.reply(
                    "Operazione non completata. Usa `/bh ask` per riprovare in modo privato.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                return

    async def close(self) -> None:
        await super().close()
