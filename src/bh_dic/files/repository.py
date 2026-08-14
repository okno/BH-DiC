"""Upload metadata repository contract and in-memory implementation."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bh_dic.database.models import UploadedFile
from bh_dic.files.models import UploadRecord, UploadStatus


class UploadRepository(Protocol):
    async def insert(self, record: UploadRecord) -> None: ...

    async def get(self, upload_id: str) -> UploadRecord | None: ...

    async def replace(self, record: UploadRecord, *, expected_version: int) -> None: ...

    async def find_by_sha256(self, digest: str) -> UploadRecord | None: ...

    async def claim_sha256(self, digest: str, upload_id: str) -> bool: ...

    async def list_records(self) -> tuple[UploadRecord, ...]: ...


class InMemoryUploadRepository:
    def __init__(self) -> None:
        self._records: dict[str, UploadRecord] = {}
        self._digests: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def insert(self, record: UploadRecord) -> None:
        async with self._lock:
            if record.upload_id in self._records:
                raise ValueError("duplicate upload ID")
            self._records[record.upload_id] = record

    async def get(self, upload_id: str) -> UploadRecord | None:
        async with self._lock:
            return self._records.get(upload_id)

    async def replace(self, record: UploadRecord, *, expected_version: int) -> None:
        async with self._lock:
            current = self._records.get(record.upload_id)
            if current is None:
                raise KeyError(record.upload_id)
            if current.version != expected_version:
                raise RuntimeError("concurrent upload metadata update")
            if record.version != expected_version + 1:
                raise ValueError("replacement must increment version exactly once")
            self._records[record.upload_id] = record

    async def find_by_sha256(self, digest: str) -> UploadRecord | None:
        async with self._lock:
            for record in self._records.values():
                if record.sha256 == digest and record.status in {
                    UploadStatus.CLEAN,
                    UploadStatus.PROCESSED,
                }:
                    return record
            return None

    async def claim_sha256(self, digest: str, upload_id: str) -> bool:
        async with self._lock:
            owner = self._digests.get(digest)
            if owner is not None:
                return owner == upload_id
            self._digests[digest] = upload_id
            return True

    async def list_records(self) -> tuple[UploadRecord, ...]:
        async with self._lock:
            return tuple(self._records.values())


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAlchemyUploadRepository:
    """Durable, metadata-only upload repository with optimistic CAS."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory
        self._claim_lock = asyncio.Lock()
        self._process_claims: dict[str, str] = {}

    async def insert(self, record: UploadRecord) -> None:
        row = UploadedFile(**self._values(record))
        async with self._sessions() as session, session.begin():
            session.add(row)

    async def get(self, upload_id: str) -> UploadRecord | None:
        async with self._sessions() as session:
            row = await session.get(UploadedFile, upload_id)
            return None if row is None else self._to_domain(row)

    async def replace(self, record: UploadRecord, *, expected_version: int) -> None:
        if record.version != expected_version + 1:
            raise ValueError("replacement must increment version exactly once")
        statement = (
            update(UploadedFile)
            .where(
                UploadedFile.upload_id == record.upload_id,
                UploadedFile.version == expected_version,
            )
            .values(**self._values(record))
        )
        async with self._sessions() as session, session.begin():
            result = await session.execute(statement)
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                raise RuntimeError("concurrent upload metadata update")

    async def find_by_sha256(self, digest: str) -> UploadRecord | None:
        async with self._sessions() as session:
            result = await session.execute(
                select(UploadedFile)
                .where(
                    UploadedFile.sha256 == digest,
                    UploadedFile.status.in_(
                        (UploadStatus.CLEAN.value, UploadStatus.PROCESSED.value)
                    ),
                )
                .order_by(UploadedFile.created_at)
            )
            row = result.scalars().first()
            return None if row is None else self._to_domain(row)

    async def claim_sha256(self, digest: str, upload_id: str) -> bool:
        async with self._claim_lock:
            owner = self._process_claims.get(digest)
            if owner is not None:
                return owner == upload_id
            existing = await self.find_by_sha256(digest)
            if existing is not None and existing.upload_id != upload_id:
                return False
            self._process_claims[digest] = upload_id
            return True

    async def list_records(self) -> tuple[UploadRecord, ...]:
        async with self._sessions() as session:
            result = await session.execute(
                select(UploadedFile).order_by(UploadedFile.created_at, UploadedFile.upload_id)
            )
            return tuple(self._to_domain(row) for row in result.scalars())

    @staticmethod
    def _values(record: UploadRecord) -> dict[str, object]:
        extension = Path(record.original_filename).suffix.lower()[:16]
        safe_name = f"[REDACTED]{extension}" if extension else "[REDACTED]"
        return {
            "upload_id": record.upload_id,
            "correlation_id": f"file:{record.upload_id}",
            "uploader_discord_id": "AUDIT_ONLY",
            "original_name_hash": hashlib.sha256(
                record.original_filename.encode("utf-8")
            ).hexdigest(),
            "safe_name": safe_name,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "declared_mime": record.claimed_mime,
            "detected_mime": record.detected_mime,
            "storage_path": (
                f"{record.bucket}/{record.opaque_name}" if record.bucket is not None else None
            ),
            "bucket": record.bucket,
            "status": record.status.value,
            "antivirus_result": record.antivirus_status,
            "rejection_reason": record.rejection_reason,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "deleted_at": record.deleted_at,
            "version": record.version,
        }

    @staticmethod
    def _to_domain(row: UploadedFile) -> UploadRecord:
        created_at = _aware(row.created_at)
        expires_at = _aware(row.expires_at)
        if created_at is None or expires_at is None:
            raise ValueError("persisted upload timestamps are invalid")
        return UploadRecord(
            upload_id=row.upload_id,
            original_filename=row.safe_name,
            opaque_name=row.upload_id,
            status=UploadStatus(row.status),
            bucket=row.bucket,
            claimed_mime=row.declared_mime,
            detected_mime=row.detected_mime,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            antivirus_status=row.antivirus_result,
            rejection_reason=row.rejection_reason,
            created_at=created_at,
            expires_at=expires_at,
            deleted_at=_aware(row.deleted_at),
            version=row.version,
        )
