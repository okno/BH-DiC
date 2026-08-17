from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from bh_dic.dic.auth import DicSessionManager
from bh_dic.dic.errors import DicSessionExpiredError, DicSessionVaultError
from bh_dic.dic.models import (
    StoredBrowserSession,
    StoredDicSessionStorage,
    StoredSessionStorageEntry,
)
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
        session_storage=StoredDicSessionStorage(
            origin="https://secure.dipendentincloud.it",
            entries=(
                StoredSessionStorageEntry(
                    key="TSID",
                    value="PRIVATE-SESSION-STORAGE-MARKER",
                ),
            ),
        ),
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
    assert b"PRIVATE-SESSION-STORAGE-MARKER" not in path.read_bytes()
    assert "PRIVATE-SESSION-STORAGE-MARKER" not in repr(expected)
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


def test_session_vault_rejects_tampered_ciphertext_without_disclosure(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "tampered.enc").resolve()
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    vault.save(session(now, now + timedelta(hours=1)))
    tampered = bytearray(path.read_bytes())
    tampered[len(tampered) // 2] ^= 1
    path.write_bytes(tampered)

    with pytest.raises(DicSessionVaultError, match="unreadable or invalid") as caught:
        vault.load()

    assert "PRIVATE-SESSION-STORAGE-MARKER" not in str(caught.value)


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


def test_session_vault_loads_legacy_payload_without_session_storage(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "legacy.enc").resolve()
    key = FernetSessionVault.generate_key()
    legacy_payload = {
        "storage_state": {"cookies": [], "origins": []},
        "authenticated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "account_hint_redacted": None,
    }
    path.write_bytes(Fernet(key).encrypt(json.dumps(legacy_payload).encode("utf-8")))

    loaded = FernetSessionVault(path, key, clock=lambda: now).load()

    assert loaded is not None
    assert loaded.session_storage is None


@pytest.mark.parametrize(
    "session_storage",
    [
        {
            "origin": "https://secure.dipendentincloud.it.evil.invalid",
            "entries": [{"key": "TSID", "value": "PRIVATE-MALFORMED-MARKER"}],
        },
        {
            "origin": " https://secure.dipendentincloud.it ",
            "entries": [{"key": "TSID", "value": "PRIVATE-MALFORMED-MARKER"}],
        },
        {
            "origin": "https://secure.dipendentincloud.it",
            "entries": [{"key": "TSID", "value": "PRIVATE-MALFORMED-MARKER" + ("x" * 32_768)}],
        },
        {
            "origin": "https://secure.dipendentincloud.it",
            "entries": [
                {"key": "TSID", "value": "PRIVATE-MALFORMED-MARKER"},
                {"key": "TSID", "value": "duplicate"},
            ],
        },
        {
            "origin": "https://secure.dipendentincloud.it",
            "entries": [{"key": "TSID", "value": "value", "unexpected": "field"}],
        },
    ],
)
def test_session_vault_rejects_invalid_session_storage_without_disclosure(
    tmp_path: Path, session_storage: dict[str, object]
) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "invalid-session-storage.enc").resolve()
    key = FernetSessionVault.generate_key()
    payload = {
        "storage_state": {"cookies": [], "origins": []},
        "session_storage": session_storage,
        "authenticated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "account_hint_redacted": None,
    }
    path.write_bytes(Fernet(key).encrypt(json.dumps(payload).encode("utf-8")))
    vault = FernetSessionVault(path, key, clock=lambda: now)

    with pytest.raises(DicSessionVaultError, match="unreadable or invalid") as caught:
        vault.load()

    assert "PRIVATE-MALFORMED-MARKER" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class StorageProvider:
    async def storage_state(self):
        return {"cookies": [], "origins": []}

    async def dic_session_storage(self) -> StoredDicSessionStorage:
        return StoredDicSessionStorage(
            origin="https://secure.dipendentincloud.it",
            entries=(StoredSessionStorageEntry(key="TSID", value="synthetic"),),
        )


def test_session_storage_opaque_whitespace_round_trips_without_normalization(tmp_path) -> None:
    now = datetime.now(UTC)
    path = (tmp_path / "whitespace.enc").resolve()
    key = "  TSID\t"
    value = " \tPRIVATE-OPAQUE-VALUE\r\n "
    expected = StoredBrowserSession(
        storage_state={"cookies": [], "origins": []},
        session_storage=StoredDicSessionStorage(
            origin="https://secure.dipendentincloud.it",
            entries=(StoredSessionStorageEntry(key=key, value=value),),
        ),
        authenticated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)

    vault.save(expected)
    loaded = vault.load()

    assert loaded is not None
    assert loaded.session_storage is not None
    assert loaded.session_storage.entries[0].key == key
    assert loaded.session_storage.entries[0].value == value


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["storage_state", "session_storage"])
async def test_session_manager_sanitizes_provider_failures_without_exception_chaining(
    tmp_path: Path, failure_stage: str
) -> None:
    marker = "PRIVATE-PROVIDER-EXCEPTION-MARKER"

    class FailingProvider:
        async def storage_state(self) -> dict[str, object]:
            if failure_stage == "storage_state":
                raise RuntimeError(marker)
            return {"cookies": [], "origins": []}

        async def dic_session_storage(self) -> StoredDicSessionStorage:
            if failure_stage == "session_storage":
                raise RuntimeError(marker)
            return StoredDicSessionStorage(
                origin="https://secure.dipendentincloud.it",
                entries=(),
            )

    vault = FernetSessionVault(
        (tmp_path / "provider-error.enc").resolve(), FernetSessionVault.generate_key()
    )
    manager = DicSessionManager(vault)

    with pytest.raises(DicSessionVaultError, match="captured safely") as caught:
        await manager.persist(FailingProvider())  # type: ignore[arg-type]

    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert vault.exists() is False


@pytest.mark.asyncio
async def test_session_manager_preserves_provider_cancellation(tmp_path: Path) -> None:
    cancellation = asyncio.CancelledError()

    class CancelledProvider:
        async def storage_state(self) -> dict[str, object]:
            raise cancellation

        async def dic_session_storage(self) -> StoredDicSessionStorage:
            raise AssertionError("sessionStorage capture must not run after cancellation")

    vault = FernetSessionVault(
        (tmp_path / "provider-cancel.enc").resolve(), FernetSessionVault.generate_key()
    )
    manager = DicSessionManager(vault)

    with pytest.raises(asyncio.CancelledError) as caught:
        await manager.persist(CancelledProvider())  # type: ignore[arg-type]

    assert caught.value is cancellation
    assert vault.exists() is False


@pytest.mark.asyncio
async def test_session_manager_persists_loads_expires_and_invalidates(tmp_path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    path = (tmp_path / "manager.enc").resolve()
    vault = FernetSessionVault(path, FernetSessionVault.generate_key(), clock=lambda: now)
    manager = DicSessionManager(vault, lifetime=timedelta(hours=1), clock=lambda: now)
    assert manager.load_session() is None
    assert manager.load_storage_state() is None
    stored = await manager.persist(StorageProvider(), account_hint_redacted="s***@example.invalid")
    assert stored.expires_at == now + timedelta(hours=1)
    assert manager.load_session() == stored
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
