"""Filesystem store that derives every path from a validated opaque UUID."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
_BUCKETS = frozenset({"quarantine", "clean", "rejected", "processed"})


@dataclass(frozen=True, slots=True)
class StoredDigest:
    size_bytes: int
    sha256: str


class SizeLimitExceeded(ValueError):
    def __init__(self, size_bytes: int, digest: str) -> None:
        super().__init__("attachment exceeds configured size limit")
        self.size_bytes = size_bytes
        self.digest = digest


class _Digest(Protocol):
    def update(self, content: bytes) -> None: ...

    def hexdigest(self) -> str: ...


class QuarantineStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for bucket in _BUCKETS:
            directory = self.root / bucket
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.resolve().parent != self.root:
                raise ValueError("upload bucket escapes the configured root")
            directory.chmod(0o700)

    async def write(
        self,
        upload_id: str,
        chunks: AsyncIterable[bytes] | Iterable[bytes],
        *,
        max_bytes: int,
    ) -> StoredDigest:
        path = self.path_for("quarantine", upload_id)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow, 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                async for chunk in self._iter_chunks(chunks):
                    size = self._write_chunk(handle, chunk, size, max_bytes, digest)
                handle.flush()
                os.fsync(handle.fileno())
        except SizeLimitExceeded:
            raise
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StoredDigest(size, digest.hexdigest())

    @staticmethod
    async def _iter_chunks(
        chunks: AsyncIterable[bytes] | Iterable[bytes],
    ) -> AsyncIterator[bytes]:
        if isinstance(chunks, AsyncIterable):
            async for chunk in chunks:
                yield chunk
        else:
            for chunk in chunks:
                yield chunk

    @staticmethod
    def _write_chunk(
        handle: BinaryIO,
        chunk: bytes,
        size: int,
        maximum: int,
        digest: _Digest,
    ) -> int:
        if not isinstance(chunk, bytes | bytearray | memoryview):
            raise TypeError("attachment chunks must be bytes")
        content = bytes(chunk)
        new_size = size + len(content)
        digest.update(content)
        if new_size > maximum:
            handle.write(content[: max(0, maximum + 1 - size)])
            raise SizeLimitExceeded(new_size, digest.hexdigest())
        handle.write(content)
        return new_size

    def move(self, upload_id: str, source: str, destination: str) -> Path:
        source_path = self.path_for(source, upload_id)
        destination_path = self.path_for(destination, upload_id)
        if destination_path.exists():
            raise FileExistsError("opaque upload destination already exists")
        os.replace(source_path, destination_path)
        destination_path.chmod(0o600)
        return destination_path

    def delete(self, bucket: str, upload_id: str) -> None:
        self.path_for(bucket, upload_id).unlink(missing_ok=True)

    def exists(self, bucket: str, upload_id: str) -> bool:
        return self.path_for(bucket, upload_id).is_file()

    def path_for(self, bucket: str, upload_id: str) -> Path:
        if bucket not in _BUCKETS:
            raise ValueError("invalid upload bucket")
        if not _UPLOAD_ID.fullmatch(upload_id):
            raise ValueError("invalid opaque upload ID")
        directory = (self.root / bucket).resolve()
        path = (directory / upload_id).resolve()
        if path.parent != directory:
            raise ValueError("upload path escaped its bucket")
        return path
