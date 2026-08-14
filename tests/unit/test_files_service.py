from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bh_dic.files import (
    AntivirusResult,
    AntivirusVerdict,
    ContentMimeDetector,
    FileRetentionService,
    FileService,
    InMemoryUploadRepository,
    QuarantineStore,
    UploadStatus,
)


class FakeScanner:
    def __init__(self, verdict: AntivirusVerdict = AntivirusVerdict.CLEAN) -> None:
        self.verdict = verdict
        self.paths: list[Path] = []

    def scan(self, path: Path) -> AntivirusResult:
        self.paths.append(path)
        return AntivirusResult(self.verdict, "synthetic result")


class FailingScanner:
    def scan(self, path: Path) -> AntivirusResult:
        raise OSError("synthetic scanner failure")


def _service(
    root: Path,
    *,
    scanner: FakeScanner | None = None,
    max_bytes: int = 1024,
    events: list[tuple[str, dict[str, object]]] | None = None,
) -> tuple[FileService, InMemoryUploadRepository, QuarantineStore]:
    repository = InMemoryUploadRepository()
    store = QuarantineStore(root)
    sink = events if events is not None else []
    service = FileService(
        store=store,
        repository=repository,
        mime_detector=ContentMimeDetector(use_python_magic=False),
        antivirus=scanner or FakeScanner(),
        max_bytes=max_bytes,
        retention=timedelta(hours=1),
        allowed_mime_types=frozenset({"application/pdf", "image/jpeg", "image/png"}),
        event_sink=lambda event, metadata: sink.append((event, dict(metadata))),
    )
    return service, repository, store


PDF = b"%PDF-1.7\nsynthetic fixture only\n%%EOF"


@pytest.mark.asyncio
async def test_files_clean_ingest_uses_uuid_path_hash_mime_antivirus_and_private_mode(
    tmp_path: Path,
) -> None:
    scanner = FakeScanner()
    events: list[tuple[str, dict[str, object]]] = []
    service, _, store = _service(tmp_path / "uploads", scanner=scanner, events=events)
    record = await service.ingest(
        original_filename="Curriculum Résumé.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF[:8], PDF[8:]],
    )
    assert record.status == UploadStatus.CLEAN
    assert record.detected_mime == "application/pdf"
    assert record.sha256 and len(record.sha256) == 64
    assert record.opaque_name == record.upload_id
    clean_path = store.path_for("clean", record.upload_id)
    assert clean_path.read_bytes() == PDF
    assert clean_path.name != record.original_filename
    assert scanner.paths and scanner.paths[0].parent.name == "quarantine"
    if os.name != "nt":
        assert stat.S_IMODE(clean_path.stat().st_mode) == 0o600
    assert all(
        "path" not in metadata and "original_filename" not in metadata for _, metadata in events
    )


@pytest.mark.asyncio
async def test_files_reject_path_traversal_without_writing_content(tmp_path: Path) -> None:
    service, _, store = _service(tmp_path / "uploads")
    record = await service.ingest(
        original_filename="../payroll.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    assert record.status == UploadStatus.REJECTED
    assert record.rejection_reason == "PATH_TRAVERSAL"
    assert not any(
        any((store.root / bucket).iterdir()) for bucket in ("quarantine", "clean", "rejected")
    )


@pytest.mark.asyncio
async def test_files_reject_size_mime_and_extension_mismatches(tmp_path: Path) -> None:
    too_small, _, _ = _service(tmp_path / "size", max_bytes=8)
    oversized = await too_small.ingest(
        original_filename="cv.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    assert oversized.rejection_reason == "SIZE_LIMIT_EXCEEDED"

    service, _, _ = _service(tmp_path / "mime")
    mismatch = await service.ingest(
        original_filename="cv.pdf",
        claimed_mime="image/png",
        chunks=[PDF],
    )
    assert mismatch.rejection_reason == "MIME_MISMATCH"
    extension = await service.ingest(
        original_filename="cv.png",
        claimed_mime="application/pdf",
        chunks=[PDF + b"different"],
    )
    assert extension.rejection_reason == "EXTENSION_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        (AntivirusVerdict.INFECTED, "ANTIVIRUS_INFECTED"),
        (AntivirusVerdict.UNAVAILABLE, "ANTIVIRUS_UNAVAILABLE"),
        (AntivirusVerdict.ERROR, "ANTIVIRUS_UNAVAILABLE"),
    ],
)
async def test_files_clamav_fail_closed(
    tmp_path: Path,
    verdict: AntivirusVerdict,
    reason: str,
) -> None:
    service, _, store = _service(tmp_path / verdict.value, scanner=FakeScanner(verdict))
    record = await service.ingest(
        original_filename="cv.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    assert record.status == UploadStatus.REJECTED
    assert record.rejection_reason == reason
    assert store.exists("rejected", record.upload_id)


@pytest.mark.asyncio
async def test_files_scanner_exception_fails_closed(tmp_path: Path) -> None:
    repository = InMemoryUploadRepository()
    store = QuarantineStore(tmp_path / "uploads")
    service = FileService(
        store=store,
        repository=repository,
        mime_detector=ContentMimeDetector(use_python_magic=False),
        antivirus=FailingScanner(),
        max_bytes=1024,
        retention=timedelta(hours=1),
        allowed_mime_types=frozenset({"application/pdf"}),
    )
    record = await service.ingest(
        original_filename="cv.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    assert record.status == UploadStatus.REJECTED
    assert record.rejection_reason == "ANTIVIRUS_UNAVAILABLE"
    assert record.antivirus_status == AntivirusVerdict.ERROR.value


@pytest.mark.asyncio
async def test_files_deduplicate_clean_payloads_and_process_by_opaque_id(tmp_path: Path) -> None:
    service, _, store = _service(tmp_path / "uploads")
    first = await service.ingest(
        original_filename="one.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    second = await service.ingest(
        original_filename="two.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    assert first.status == UploadStatus.CLEAN
    assert second.status == UploadStatus.REJECTED
    assert second.rejection_reason == "DUPLICATE_FILE"
    processed = await service.mark_processed(first.upload_id)
    assert processed.status == UploadStatus.PROCESSED
    assert store.exists("processed", first.upload_id)


@pytest.mark.asyncio
async def test_files_retention_deletes_bytes_and_records_deletion(tmp_path: Path) -> None:
    service, repository, store = _service(tmp_path / "uploads")
    record = await service.ingest(
        original_filename="cv.pdf",
        claimed_mime="application/pdf",
        chunks=[PDF],
    )
    retention = FileRetentionService(store=store, repository=repository)
    purged = await retention.purge_expired(now=datetime.now(UTC) + timedelta(hours=2))
    assert purged == (record.upload_id,)
    deleted = await service.get(record.upload_id)
    assert deleted.status == UploadStatus.DELETED
    assert deleted.bucket is None
    assert not store.exists("clean", record.upload_id)
