from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bh_dic.database.engine import Database
from bh_dic.files.antivirus import AntivirusResult, AntivirusVerdict
from bh_dic.files.mime import ContentMimeDetector
from bh_dic.files.models import UploadStatus
from bh_dic.files.quarantine import QuarantineStore
from bh_dic.files.repository import SqlAlchemyUploadRepository
from bh_dic.files.retention import FileRetentionService
from bh_dic.files.service import FileService


class CleanScanner:
    def scan(self, path: Path) -> AntivirusResult:
        assert path.is_file()
        return AntivirusResult(AntivirusVerdict.CLEAN, "synthetic clean fixture")


@pytest.mark.asyncio
async def test_upload_metadata_is_durable_redacted_and_retained(tmp_path: Path) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = SqlAlchemyUploadRepository(database.sessions)
    store = QuarantineStore(tmp_path / "uploads")
    now = datetime.now(UTC)
    service = FileService(
        store=store,
        repository=repository,
        mime_detector=ContentMimeDetector(use_python_magic=False),
        antivirus=CleanScanner(),
        max_bytes=1024,
        retention=timedelta(hours=1),
        allowed_mime_types=frozenset({"application/pdf"}),
        clock=lambda: now,
    )

    first = await service.ingest(
        original_filename="Synthetic Employee.pdf",
        claimed_mime="application/pdf",
        chunks=[b"%PDF-1.7\nsynthetic fixture\n"],
    )
    assert first.status is UploadStatus.CLEAN

    restarted_repository = SqlAlchemyUploadRepository(database.sessions)
    persisted = await restarted_repository.get(first.upload_id)
    assert persisted is not None
    assert persisted.original_filename == "[REDACTED].pdf"
    assert persisted.sha256 == first.sha256
    assert persisted.bucket == "clean"

    duplicate = await service.ingest(
        original_filename="Another.pdf",
        claimed_mime="application/pdf",
        chunks=[b"%PDF-1.7\nsynthetic fixture\n"],
    )
    assert duplicate.status is UploadStatus.REJECTED
    assert duplicate.rejection_reason == "DUPLICATE_FILE"

    purged = await FileRetentionService(
        store=store,
        repository=restarted_repository,
    ).purge_expired(now=now + timedelta(hours=2))
    assert first.upload_id in purged
    deleted = await restarted_repository.get(first.upload_id)
    assert deleted is not None and deleted.status is UploadStatus.DELETED
    assert not store.exists("clean", first.upload_id)
    await database.dispose()
