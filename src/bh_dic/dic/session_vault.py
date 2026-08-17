"""Fernet-encrypted Playwright storage-state vault with atomic persistence."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from bh_dic.dic.errors import DicSessionExpiredError, DicSessionVaultError
from bh_dic.dic.models import StoredBrowserSession


def resolve_session_vault_path(configured_path: Path, data_dir: Path) -> Path:
    """Resolve a vault path only when it is a regular path contained by DATA_DIR."""

    expanded = configured_path.expanduser()
    if expanded.is_symlink():
        raise DicSessionVaultError("session vault path must not be a symbolic link")
    resolved_root = data_dir.expanduser().resolve()
    resolved = expanded.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise DicSessionVaultError("session vault path must stay inside DATA_DIR")
    return resolved


class FernetSessionVault:
    """Never logs, prints, or returns the encryption key."""

    def __init__(
        self,
        path: Path,
        key: bytes | str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not path.is_absolute():
            raise DicSessionVaultError("session vault path must be absolute")
        raw_key = key.encode("utf-8") if isinstance(key, str) else key
        try:
            self._fernet = Fernet(raw_key)
        except (TypeError, ValueError) as exc:
            if len(raw_key) < 32:
                raise DicSessionVaultError(
                    "session encryption secret must contain 32 bytes"
                ) from exc
            derived_key = base64.urlsafe_b64encode(hashlib.sha256(raw_key).digest())
            self._fernet = Fernet(derived_key)
        self.path = path
        self._clock = clock

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def save(self, session: StoredBrowserSession) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        encrypted = self._fernet.encrypt(session.model_dump_json().encode("utf-8"))
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=".session-", delete=False
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(temporary_path, 0o600)
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            persist_failed = True
        else:
            persist_failed = False
        if persist_failed:
            raise DicSessionVaultError("failed to persist encrypted browser session")

    def load(self) -> StoredBrowserSession | None:
        if not self.path.exists():
            return None
        session: StoredBrowserSession | None = None
        try:
            encrypted = self.path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            session = StoredBrowserSession.model_validate_json(decrypted)
        except (OSError, InvalidToken, ValueError):
            load_failed = True
        else:
            load_failed = False
        if load_failed or session is None:
            raise DicSessionVaultError("browser session vault is unreadable or invalid")
        if session.expires_at <= self._clock():
            raise DicSessionExpiredError("encrypted DIC browser session is expired")
        return session

    def invalidate(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            invalidate_failed = True
        else:
            invalidate_failed = False
        if invalidate_failed:
            raise DicSessionVaultError("failed to invalidate browser session")

    def exists(self) -> bool:
        return self.path.is_file()
