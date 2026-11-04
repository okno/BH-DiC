from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bh_dic.bridge.settings import BridgeHostSettings


def _private_file(path: Path, content: str = "synthetic") -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _host_values(tmp_path: Path) -> dict[str, object]:
    state = tmp_path / "state"
    work = state / "work"
    work.mkdir(parents=True, mode=0o700, exist_ok=True)
    state.chmod(0o700)
    work.chmod(0o700)
    return {
        "bridge_tls_cert_path": _private_file(tmp_path / "server.crt"),
        "bridge_tls_key_path": _private_file(tmp_path / "server.key"),
        "bridge_client_ca_path": _private_file(tmp_path / "client-ca.crt"),
        "bridge_codex_state_root": state,
        "bridge_codex_work_root": work,
    }


def test_bridge_host_is_loopback_only_and_normalizes_ipv6_local_model_url(
    tmp_path: Path,
) -> None:
    settings = BridgeHostSettings(
        **_host_values(tmp_path),
        bridge_bind_host="::1",
        bridge_local_base_url="http://[::1]:11434/",
        _env_file=None,
    )

    assert settings.bridge_bind_host == "::1"
    assert settings.bridge_local_base_url == "http://[::1]:11434/v1"

    with pytest.raises(ValidationError, match="loopback"):
        BridgeHostSettings(
            **_host_values(tmp_path),
            bridge_bind_host="192.0.2.10",
            _env_file=None,
        )


def test_local_bridge_backend_requires_an_explicit_model(tmp_path: Path) -> None:
    values = _host_values(tmp_path)
    values.pop("bridge_codex_state_root")
    values.pop("bridge_codex_work_root")

    with pytest.raises(ValidationError, match="explicit BRIDGE_MODEL"):
        BridgeHostSettings(
            **values,
            bridge_backend="ollama",
            _env_file=None,
        )

    settings = BridgeHostSettings(
        **values,
        bridge_backend="lmstudio",
        bridge_model="synthetic-local-model",
        _env_file=None,
    )
    assert settings.bridge_backend == "lmstudio"


def test_bridge_rejects_a_shared_private_key(tmp_path: Path) -> None:
    values = _host_values(tmp_path)
    key = Path(values["bridge_tls_key_path"])
    key.chmod(0o640)

    with pytest.raises(ValidationError, match="0600"):
        BridgeHostSettings(**values, _env_file=None)


def test_bridge_environment_rejects_bot_or_dic_secrets(tmp_path: Path) -> None:
    environment = _private_file(
        tmp_path / "bridge.env",
        "DIC_PASSWORD=must-not-enter-the-bridge\n",
    )

    with pytest.raises(ValidationError, match="dic_password"):
        BridgeHostSettings(**_host_values(tmp_path), _env_file=environment)
