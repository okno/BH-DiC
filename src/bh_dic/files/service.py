"""Fail-closed attachment ingestion pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import uuid
from collections.abc import AsyncIterable, Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, Unpack

from bh_dic.files.antivirus import AntivirusScanner, AntivirusVerdict
from bh_dic.files.mime import MimeDetector, canonical_mime, extension_matches_mime
from bh_dic.files.models import ResolvedUpload, UploadRecord, UploadStatus
from bh_dic.files.quarantine import QuarantineStore, SizeLimitExceeded
from bh_dic.files.repository import UploadRepository
from bh_dic.security.sanitization import InputValidationError, sanitize_filename_metadata

EventSink = Callable[[str, Mapping[str, Any]], None]
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UploadResolutionError(ValueError):
    """A quarantined upload cannot safely cross the DIC delivery boundary."""


class _UploadChanges(TypedDict, total=False):
    status: UploadStatus
    bucket: str | None
    detected_mime: str | None
    size_bytes: int
    sha256: str | None
    antivirus_status: str | None
    rejection_reason: str | None


class FileService:
    def __init__(
        self,
        *,
        store: QuarantineStore,
        repository: UploadRepository,
        mime_detector: MimeDetector,
        antivirus: AntivirusScanner,
        max_bytes: int,
        retention: timedelta,
        allowed_mime_types: frozenset[str],
        clamav_required: bool = True,
        event_sink: EventSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_bytes <= 0 or retention <= timedelta(0):
            raise ValueError("file size and retention must be positive")
        self._store = store
        self._repository = repository
        self._mime_detector = mime_detector
        self._antivirus = antivirus
        self._max_bytes = max_bytes
        self._retention = retention
        self._allowed = frozenset(
            mime for item in allowed_mime_types if (mime := canonical_mime(item)) is not None
        )
        self._clamav_required = clamav_required
        self._event_sink = event_sink or (lambda _event, _metadata: None)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._delivery_lock = asyncio.Lock()

    async def ingest(
        self,
        *,
        original_filename: str,
        claimed_mime: str | None,
        chunks: AsyncIterable[bytes] | Iterable[bytes],
    ) -> UploadRecord:
        upload_id = uuid.uuid4().hex
        now = self._now()
        try:
            filename = sanitize_filename_metadata(original_filename)
        except InputValidationError:
            return await self._record_early_rejection(
                upload_id, "[UNSAFE_FILENAME]", claimed_mime, now, "PATH_TRAVERSAL"
            )
        record = UploadRecord(
            upload_id=upload_id,
            original_filename=filename,
            opaque_name=upload_id,
            status=UploadStatus.QUARANTINED,
            bucket="quarantine",
            claimed_mime=canonical_mime(claimed_mime),
            detected_mime=None,
            size_bytes=0,
            sha256=None,
            antivirus_status=None,
            rejection_reason=None,
            created_at=now,
            expires_at=now + self._retention,
        )
        await self._repository.insert(record)
        self._event("FILE_QUARANTINED", record)
        try:
            stored = await self._store.write(upload_id, chunks, max_bytes=self._max_bytes)
        except SizeLimitExceeded as exc:
            record = await self._update(
                record,
                size_bytes=exc.size_bytes,
                sha256=exc.digest,
            )
            return await self._reject(record, "SIZE_LIMIT_EXCEEDED")
        except (OSError, TypeError, ValueError):
            return await self._reject(record, "QUARANTINE_WRITE_FAILED")
        if stored.size_bytes == 0:
            record = await self._update(record, sha256=stored.sha256)
            return await self._reject(record, "EMPTY_FILE")
        record = await self._update(
            record,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        path = self._store.path_for("quarantine", upload_id)
        try:
            detected = canonical_mime(await asyncio.to_thread(self._mime_detector.detect, path))
        except Exception:
            return await self._reject(record, "MIME_DETECTION_FAILED")
        record = await self._update(record, detected_mime=detected)
        if detected not in self._allowed:
            return await self._reject(record, "MIME_NOT_ALLOWED")
        if record.claimed_mime is not None and record.claimed_mime != detected:
            return await self._reject(record, "MIME_MISMATCH")
        if detected is None or not extension_matches_mime(filename, detected):
            return await self._reject(record, "EXTENSION_MISMATCH")
        try:
            scan = await asyncio.to_thread(self._antivirus.scan, path)
        except Exception:
            record = await self._update(record, antivirus_status=AntivirusVerdict.ERROR.value)
            return await self._reject(record, "ANTIVIRUS_UNAVAILABLE")
        record = await self._update(record, antivirus_status=scan.verdict.value)
        if scan.verdict == AntivirusVerdict.INFECTED:
            return await self._reject(record, "ANTIVIRUS_INFECTED")
        if (
            scan.verdict in {AntivirusVerdict.ERROR, AntivirusVerdict.UNAVAILABLE}
            and self._clamav_required
        ):
            return await self._reject(record, "ANTIVIRUS_UNAVAILABLE")
        if not await self._repository.claim_sha256(stored.sha256, upload_id):
            return await self._reject(record, "DUPLICATE_FILE")
        self._store.move(upload_id, "quarantine", "clean")
        clean = await self._update(record, status=UploadStatus.CLEAN, bucket="clean")
        self._event("FILE_CLEAN", clean)
        return clean

    async def mark_processed(self, upload_id: str) -> UploadRecord:
        await self.claim_clean_upload(upload_id)
        return await self.get(upload_id)

    async def resolve_clean_upload(self, upload_id: str) -> ResolvedUpload:
        """Return an internal capability for a verified CLEAN record without changing state."""

        record = await self._clean_record(upload_id)
        return await asyncio.to_thread(self._verify_stored_record, record, "clean")

    async def claim_clean_upload(self, upload_id: str) -> ResolvedUpload:
        """Consume one CLEAN upload before dispatch so it cannot be delivered twice."""

        async with self._delivery_lock:
            record = await self._clean_record(upload_id)
            await asyncio.to_thread(self._verify_stored_record, record, "clean")
            try:
                self._store.move(upload_id, "clean", "processed")
            except (OSError, ValueError):
                raise UploadResolutionError("clean upload could not be claimed") from None
            try:
                updated = await self._update(
                    record,
                    status=UploadStatus.PROCESSED,
                    bucket="processed",
                )
            except Exception:
                # The atomic filesystem move already prevents reuse. Metadata repair is an
                # operator action; never move the file back into the reusable CLEAN bucket.
                raise UploadResolutionError("upload claim metadata update failed closed") from None
            resolved = await asyncio.to_thread(
                self._verify_stored_record, updated, "processed", False
            )
            self._event("FILE_PROCESSED", updated)
            return resolved

    async def get(self, upload_id: str) -> UploadRecord:
        record = await self._repository.get(upload_id)
        if record is None:
            raise KeyError(upload_id)
        return record

    async def _clean_record(self, upload_id: str) -> UploadRecord:
        if not _UPLOAD_ID.fullmatch(upload_id):
            raise UploadResolutionError("invalid upload identifier")
        try:
            record = await self.get(upload_id)
        except KeyError:
            raise UploadResolutionError("upload is unavailable") from None
        if (
            record.upload_id != upload_id
            or record.opaque_name != upload_id
            or record.status is not UploadStatus.CLEAN
            or record.bucket != "clean"
            or record.deleted_at is not None
            or record.expires_at <= self._now()
            or record.antivirus_status != AntivirusVerdict.CLEAN.value
            or record.detected_mime not in self._allowed
            or record.size_bytes <= 0
            or record.sha256 is None
            or not _SHA256.fullmatch(record.sha256)
        ):
            raise UploadResolutionError("upload is not eligible for delivery")
        return record

    def _verify_stored_record(
        self,
        record: UploadRecord,
        bucket: str,
        require_clean_status: bool = True,
    ) -> ResolvedUpload:
        if require_clean_status and record.status is not UploadStatus.CLEAN:
            raise UploadResolutionError("upload state changed before delivery")
        expected_status = UploadStatus.CLEAN if bucket == "clean" else UploadStatus.PROCESSED
        if record.bucket != bucket or record.status is not expected_status:
            raise UploadResolutionError("upload metadata and storage bucket disagree")
        bucket_directory = self._store.root / bucket
        candidate = bucket_directory / record.upload_id
        try:
            if bucket_directory.is_symlink() or candidate.is_symlink():
                raise UploadResolutionError("upload storage contains a symbolic link")
            resolved_directory = bucket_directory.resolve(strict=True)
            resolved_path = candidate.resolve(strict=True)
        except OSError:
            raise UploadResolutionError("upload file is unavailable") from None
        if (
            resolved_directory.parent != self._store.root
            or resolved_path.parent != resolved_directory
        ):
            raise UploadResolutionError("upload storage escaped the configured root")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(resolved_path, flags)
        except OSError:
            raise UploadResolutionError("upload file cannot be opened safely") from None
        digest = hashlib.sha256()
        observed_size = 0
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise UploadResolutionError("upload storage entry is not a regular file")
            if os.name == "posix" and metadata.st_mode & 0o077:
                raise UploadResolutionError("upload file permissions are too broad")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                while chunk := handle.read(64 * 1024):
                    observed_size += len(chunk)
                    digest.update(chunk)
        except OSError:
            raise UploadResolutionError("upload file could not be verified") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if observed_size != record.size_bytes or digest.hexdigest() != record.sha256:
            raise UploadResolutionError("upload integrity verification failed")
        if record.detected_mime is None:
            raise UploadResolutionError("upload MIME metadata is unavailable")
        return ResolvedUpload(
            upload_id=record.upload_id,
            path=resolved_path,
            sha256=record.sha256,
            size_bytes=record.size_bytes,
            detected_mime=record.detected_mime,
        )

    async def _record_early_rejection(
        self,
        upload_id: str,
        filename: str,
        claimed_mime: str | None,
        now: datetime,
        reason: str,
    ) -> UploadRecord:
        record = UploadRecord(
            upload_id=upload_id,
            original_filename=filename,
            opaque_name=upload_id,
            status=UploadStatus.REJECTED,
            bucket=None,
            claimed_mime=canonical_mime(claimed_mime),
            detected_mime=None,
            size_bytes=0,
            sha256=None,
            antivirus_status=None,
            rejection_reason=reason,
            created_at=now,
            expires_at=now + self._retention,
        )
        await self._repository.insert(record)
        self._event("FILE_REJECTED", record)
        return record

    async def _reject(self, record: UploadRecord, reason: str) -> UploadRecord:
        bucket: str | None = None
        if record.bucket == "quarantine" and self._store.exists("quarantine", record.upload_id):
            self._store.move(record.upload_id, "quarantine", "rejected")
            bucket = "rejected"
        rejected = await self._update(
            record,
            status=UploadStatus.REJECTED,
            bucket=bucket,
            rejection_reason=reason,
        )
        self._event("FILE_REJECTED", rejected)
        return rejected

    async def _update(
        self,
        record: UploadRecord,
        **changes: Unpack[_UploadChanges],
    ) -> UploadRecord:
        updated = replace(record, **changes, version=record.version + 1)
        await self._repository.replace(updated, expected_version=record.version)
        return updated

    def _event(self, event: str, record: UploadRecord) -> None:
        self._event_sink(
            event,
            {
                "upload_id": record.upload_id,
                "status": record.status.value,
                "size_bytes": record.size_bytes,
                "detected_mime": record.detected_mime,
                "reason": record.rejection_reason,
            },
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("file clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
