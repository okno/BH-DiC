"""Async SQLAlchemy engine and unit-of-work helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from bh_dic.database.models import AuditChainState, Base

_AUDIT_GENESIS_HASH = "0" * 64


class Database:
    """Own the async engine and enforce safe SQLite defaults."""

    def __init__(
        self,
        database_url: str,
        *,
        echo: bool = False,
        sqlite_busy_timeout_ms: int = 5000,
    ) -> None:
        self.url: URL = make_url(database_url)
        self.sqlite_busy_timeout_ms = sqlite_busy_timeout_ms
        engine_options: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
        if self.is_sqlite:
            engine_options["connect_args"] = {"timeout": sqlite_busy_timeout_ms / 1000}
            if self.url.database in {None, "", ":memory:"}:
                engine_options["poolclass"] = StaticPool
        self.engine: AsyncEngine = create_async_engine(database_url, **engine_options)
        self.sessions = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )
        if self.is_sqlite:
            self._configure_sqlite()

    @property
    def is_sqlite(self) -> bool:
        return self.url.get_backend_name() == "sqlite"

    def _configure_sqlite(self) -> None:
        busy_timeout = self.sqlite_busy_timeout_ms
        database_name = self.url.database

        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={busy_timeout:d}")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()
            if isinstance(database_name, str) and database_name not in {"", ":memory:"}:
                Path(database_name).expanduser().chmod(0o600)

    def ensure_sqlite_parent(self) -> None:
        """Create only the configured SQLite parent directory, never a broad path."""

        database_name = self.url.database
        if not self.is_sqlite:
            return
        if database_name is None or database_name in {"", ":memory:"}:
            return
        database_path = Path(database_name).expanduser()
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            yield session

    async def create_schema(self) -> None:
        """Create the current schema for isolated mock/tests; production uses Alembic."""

        self.ensure_sqlite_parent()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.transaction() as session:
            if await session.get(AuditChainState, 1) is None:
                session.add(
                    AuditChainState(
                        id=1,
                        last_sequence=0,
                        last_hash=_AUDIT_GENESIS_HASH,
                    )
                )

    async def drop_schema(self) -> None:
        """Drop the schema in an explicitly selected test database."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def healthcheck(self) -> dict[str, str]:
        self.ensure_sqlite_parent()
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            if self.is_sqlite:
                result = await connection.execute(text("PRAGMA journal_mode"))
                return {"status": "ok", "journal_mode": str(result.scalar_one()).lower()}
            return {"status": "ok", "journal_mode": "not-applicable"}

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def __aenter__(self) -> Database:
        self.ensure_sqlite_parent()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.dispose()
