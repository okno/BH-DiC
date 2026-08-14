"""Content-based MIME detection with a safe signature fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

MIME_EXTENSIONS: dict[str, frozenset[str]] = {
    "application/pdf": frozenset({".pdf"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
}

_ALIASES = {
    "image/jpg": "image/jpeg",
    "application/x-pdf": "application/pdf",
}


def canonical_mime(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.split(";", 1)[0].strip().lower()
    return _ALIASES.get(normalized, normalized)


def extension_matches_mime(filename: str, mime_type: str) -> bool:
    allowed = MIME_EXTENSIONS.get(canonical_mime(mime_type) or "", frozenset())
    return Path(filename).suffix.lower() in allowed


class MimeDetector(Protocol):
    def detect(self, path: Path) -> str: ...


class ContentMimeDetector:
    """Prefer python-magic, falling back only to explicit supported signatures."""

    def __init__(self, *, use_python_magic: bool = True) -> None:
        self._use_python_magic = use_python_magic

    def detect(self, path: Path) -> str:
        if self._use_python_magic:
            try:
                import magic

                detected = canonical_mime(str(magic.from_file(str(path), mime=True)))
                if detected and detected != "application/octet-stream":
                    return detected
            except (ImportError, OSError, ValueError):
                pass
        with path.open("rb") as handle:
            header = handle.read(16)
        if header.startswith(b"%PDF-"):
            return "application/pdf"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        return "application/octet-stream"
