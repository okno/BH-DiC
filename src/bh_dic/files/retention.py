"""Auditable deletion of expired upload artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from bh_dic.files.models import UploadStatus
from bh_dic.files.quarantine import QuarantineStore
from bh_dic.files.repository import UploadRepository


class FileRetentionService:
    def __init__(
        self,
        *,
        store: QuarantineStore,
        repository: UploadRepository,
        event_sink: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._repository = repository
        self._event_sink = event_sink or (lambda _event, _metadata: None)

    async def purge_expired(self, *, now: datetime | None = None) -> tuple[str, ...]:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("retention timestamp must be timezone-aware")
        current = current.astimezone(UTC)
        purged: list[str] = []
        for record in await self._repository.list_records():
            if record.status == UploadStatus.DELETED or current < record.expires_at:
                continue
            if record.bucket is not None:
                self._store.delete(record.bucket, record.upload_id)
            deleted = replace(
                record,
                status=UploadStatus.DELETED,
                bucket=None,
                deleted_at=current,
                version=record.version + 1,
            )
            await self._repository.replace(deleted, expected_version=record.version)
            self._event_sink(
                "FILE_RETENTION_DELETED",
                {"upload_id": record.upload_id, "status": deleted.status.value},
            )
            purged.append(record.upload_id)
        return tuple(purged)
