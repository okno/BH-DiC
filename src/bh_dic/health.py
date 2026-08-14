"""Local, side-effect-free health reporting primitives."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from bh_dic import __version__
from bh_dic.config import AppSettings
from bh_dic.database.engine import Database


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    state: HealthState
    detail: str = Field(max_length=256)


class HealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: HealthState
    checked_at: datetime
    application_version: str
    components: tuple[ComponentHealth, ...]


class HealthChecker:
    """Check local dependencies without contacting external providers."""

    def __init__(self, settings: AppSettings, database: Database) -> None:
        self.settings = settings
        self.database = database

    async def check(self) -> HealthReport:
        components = [
            ComponentHealth(
                name="configuration",
                state=HealthState.HEALTHY,
                detail=(
                    "mock configuration"
                    if self.settings.mock_mode
                    else "runtime configuration valid"
                ),
            )
        ]
        try:
            database_health = await self.database.healthcheck()
        except Exception:
            components.append(
                ComponentHealth(
                    name="database",
                    state=HealthState.UNHEALTHY,
                    detail="database connection failed; inspect redacted application logs",
                )
            )
        else:
            mode = database_health.get("journal_mode", "not-applicable")
            components.append(
                ComponentHealth(
                    name="database",
                    state=HealthState.HEALTHY,
                    detail=f"database reachable; journal_mode={mode}",
                )
            )
        overall = (
            HealthState.UNHEALTHY
            if any(item.state == HealthState.UNHEALTHY for item in components)
            else HealthState.DEGRADED
            if any(item.state == HealthState.DEGRADED for item in components)
            else HealthState.HEALTHY
        )
        return HealthReport(
            state=overall,
            checked_at=datetime.now(UTC),
            application_version=__version__,
            components=tuple(components),
        )
