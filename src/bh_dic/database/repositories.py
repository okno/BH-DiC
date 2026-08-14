"""Small persistence repositories used by deterministic services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bh_dic.database.models import Approval, DiscordRequest, FeatureFlag, PendingAction


class DiscordRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, request: DiscordRequest) -> DiscordRequest:
        self.session.add(request)
        await self.session.flush()
        return request

    async def get_by_correlation_id(self, correlation_id: str) -> DiscordRequest | None:
        result = await self.session.execute(
            select(DiscordRequest).where(DiscordRequest.correlation_id == correlation_id)
        )
        return result.scalar_one_or_none()


class PendingActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, action: PendingAction) -> PendingAction:
        self.session.add(action)
        await self.session.flush()
        return action

    async def get(self, action_id: str, *, for_update: bool = False) -> PendingAction | None:
        statement = select(PendingAction).where(PendingAction.action_id == action_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, idempotency_key: str) -> PendingAction | None:
        result = await self.session.execute(
            select(PendingAction).where(PendingAction.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, approval: Approval) -> Approval:
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def list_for_action(self, action_id: str) -> tuple[Approval, ...]:
        result = await self.session.execute(
            select(Approval)
            .where(Approval.action_id == action_id)
            .order_by(Approval.created_at, Approval.approval_id)
        )
        return tuple(result.scalars())


class FeatureFlagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_enabled(self, name: str, *, default: bool = False) -> bool:
        flag = await self.session.get(FeatureFlag, name)
        return default if flag is None else flag.enabled

    async def set(self, name: str, enabled: bool, *, actor_discord_id: str | None) -> FeatureFlag:
        flag = await self.session.get(FeatureFlag, name)
        if flag is None:
            flag = FeatureFlag(name=name, enabled=enabled, updated_by_discord_id=actor_discord_id)
            self.session.add(flag)
        else:
            flag.enabled = enabled
            flag.updated_by_discord_id = actor_discord_id
        await self.session.flush()
        return flag
