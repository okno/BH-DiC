"""Programmatic Alembic migration entry points."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config


def migration_config(database_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[3]
    config_path = project_root / "migrations" / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["database_url_override"] = database_url
    return config


def run_migrations(database_url: str, revision: str = "head") -> None:
    """Apply migrations synchronously; invoke before the async application starts."""

    command.upgrade(migration_config(database_url), revision)


async def run_migrations_async(database_url: str, revision: str = "head") -> None:
    """Apply migrations from an async CLI without nesting event loops."""

    await asyncio.to_thread(run_migrations, database_url, revision)
