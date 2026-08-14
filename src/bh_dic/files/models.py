"""Non-content upload metadata stored by the application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class UploadStatus(StrEnum):
    QUARANTINED = "QUARANTINED"
    CLEAN = "CLEAN"
    REJECTED = "REJECTED"
    PROCESSED = "PROCESSED"
    DELETED = "DELETED"


@dataclass(frozen=True, slots=True)
class UploadRecord:
    upload_id: str
    original_filename: str
    opaque_name: str
    status: UploadStatus
    bucket: str | None
    claimed_mime: str | None
    detected_mime: str | None
    size_bytes: int
    sha256: str | None
    antivirus_status: str | None
    rejection_reason: str | None
    created_at: datetime
    expires_at: datetime
    deleted_at: datetime | None = None
    version: int = 1
