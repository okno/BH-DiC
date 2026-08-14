from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bh_dic.dic.auth import DicSessionManager
from bh_dic.dic.errors import DicSessionExpiredError, DicSessionVaultError
from bh_dic.dic.models import StoredBrowserSession
from bh_dic.dic.session_vault import FernetSessionVault, resolve_session_vault_path


def session(now: datetime, expires_at: datetime) -> StoredBrowserSession:
    return StoredBrowserSession(
        storage_state={
            "cookies": [
                {
                    "name": "synthetic-session",
                    "value": "DO-NOT-STORE-PLAINTEXT",
                    "domain": "example.invalid",
                    "path": "/",
                }
            ],
            "origins": [],
        },
        authenticated_at=now,
        expires_at=expires_at,
        account_hint_redacted="s***@example.invalid",
    )


def test_session_vault_round_trip_is_encrypted_and_mode_is_restricted(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "session.enc").resolve()
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    expected = session(now, now + timedelta(hours=1))

    vault.save(expected)

    assert b"DO-NOT-STORE-PLAINTEXT" not in path.read_bytes()
    assert vault.load() == expected
    vault.invalidate()
    assert vault.exists() is False


def test_session_vault_rejects_wrong_key(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "session.enc").resolve()
    first = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    first.save(session(now, now + timedelta(hours=1)))
    second = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)

    with pytest.raises(DicSessionVaultError):
        second.load()


def test_session_vault_rejects_expired_state(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "session.enc").resolve()
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    vault.save(session(now - timedelta(hours=2), now - timedelta(hours=1)))

    with pytest.raises(DicSessionExpiredError):
        vault.load()


def test_session_vault_derives_fernet_key_from_strong_application_secret(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "session.enc").resolve()
    vault = FernetSessionVault(path, "synthetic-secret-" * 4, clock=lambda: now)
    expected = session(now, now + timedelta(hours=1))
    vault.save(expected)
    assert vault.load() == expected


def test_session_vault_rejects_relative_path_and_weak_secret(tmp_path) -> None:
    with pytest.raises(DicSessionVaultError, match="absolute"):
        FernetSessionVault(Path("relative.enc"), FernetSessionVault.generate_key())
    with pytest.raises(DicSessionVaultError, match="32 bytes"):
        FernetSessionVault((tmp_path / "weak.enc").resolve(), "too-short")


def test_session_vault_path_must_stay_inside_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    inside = data_dir / "session" / "state.enc"

    assert resolve_session_vault_path(inside, data_dir) == inside.resolve()
    with pytest.raises(DicSessionVaultError, match="inside DATA_DIR"):
        resolve_session_vault_path(tmp_path / "outside.enc", data_dir)


def test_session_vault_handles_missing_and_malformed_files(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "session.enc").resolve()
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    assert vault.load() is None
    path.write_bytes(b"not-a-fernet-token")
    with pytest.raises(DicSessionVaultError, match="unreadable or invalid"):
        vault.load()


class StorageProvider:
    async def storage_state(self):
        return {"cookies": [], "origins": []}


@pytest.mark.asyncio
async def test_session_manager_persists_loads_expires_and_invalidates(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = (tmp_path / "manager.enc").resolve()
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    manager = DicSessionManager(vault, lifetime=timedelta(hours=1), clock=lambda: now)
    assert manager.load_storage_state() is None
    stored = await manager.persist(StorageProvider(), account_hint_redacted="s***@example.invalid")
    assert stored.expires_at == now + timedelta(hours=1)
    assert manager.load_storage_state() == {"cookies": [], "origins": []}
    manager.invalidate()
    assert vault.exists() is False

    expired_vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    expired_vault.save(session(now - timedelta(hours=2), now - timedelta(hours=1)))
    expired_manager = DicSessionManager(expired_vault, clock=lambda: now)
    assert expired_manager.load_storage_state() is None


def test_session_manager_rejects_non_positive_lifetime(tmp_path) -> None:
    vault = FernetSessionVault(
        (tmp_path / "manager.enc").resolve(), FernetSessionVault.generate_key()
    )
    with pytest.raises(ValueError, match="positive"):
        DicSessionManager(vault, lifetime=timedelta(0))
