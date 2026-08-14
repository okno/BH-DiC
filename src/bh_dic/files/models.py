"""Non-content upload metadata stored by the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


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


@dataclass(frozen=True, slots=True)
class ResolvedUpload:
    """Internal delivery capability; its filesystem path must never be rendered or logged."""

    upload_id: str
    path: Path = field(repr=False)
    sha256: str = field(repr=False)
    size_bytes: int
    detected_mime: str
