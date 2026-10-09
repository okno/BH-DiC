"""Standalone fail-closed configuration for the Mint planner bridge."""

from __future__ import annotations

import ipaddress
import os
import stat
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BridgeBackendName = Literal["codex", "ollama", "lmstudio"]


def _validate_regular_file(path: Path, *, label: str, private: bool) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if private and metadata.st_uid != os.geteuid():
        raise ValueError(f"{label} must be owned by the bridge account")
    if private and metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError(f"{label} must have mode 0600 or stricter")
    return resolved


class BridgeHostSettings(BaseSettings):
    """Settings that intentionally know nothing about Discord or DIC secrets."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="forbid",
        validate_default=True,
    )

    bridge_bind_host: str = "127.0.0.1"
    bridge_bind_port: int = Field(default=9443, ge=1, le=65_535)
    bridge_tls_cert_path: Path
    bridge_tls_key_path: Path
    bridge_client_ca_path: Path
    bridge_backend: BridgeBackendName = "codex"
    bridge_model: str = Field(default="workspace-default", min_length=1, max_length=128)
    bridge_max_concurrency: int = Field(default=2, ge=1, le=8)
    bridge_hr_memory_path: Path | None = None

    bridge_codex_state_root: Path | None = None
    bridge_codex_work_root: Path | None = None
    bridge_codex_executable: Path | None = None

    bridge_local_base_url: str = "http://127.0.0.1:11434/v1"

    @field_validator("bridge_bind_host")
    @classmethod
    def validate_bind_host(cls, value: str) -> str:
        candidate = value.strip().casefold()
        if candidate == "localhost":
            return candidate
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise ValueError("BRIDGE_BIND_HOST must be loopback") from exc
        if not address.is_loopback:
            raise ValueError("BRIDGE_BIND_HOST must remain loopback")
        return str(address)

    @field_validator("bridge_local_base_url")
    @classmethod
    def validate_local_base_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme != "http" or parsed.hostname is None:
            raise ValueError("BRIDGE_LOCAL_BASE_URL must be a loopback HTTP URL")
        hostname = parsed.hostname.casefold()
        try:
            loopback = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("BRIDGE_LOCAL_BASE_URL must be a credential-free loopback URL")
        if parsed.path not in {"", "/", "/v1", "/v1/"}:
            raise ValueError("BRIDGE_LOCAL_BASE_URL path must be /v1")
        port = parsed.port
        normalized_host = f"[{hostname}]" if ":" in hostname else hostname
        authority = normalized_host if port is None else f"{normalized_host}:{port}"
        return f"http://{authority}/v1"

    @model_validator(mode="after")
    def validate_boundary(self) -> BridgeHostSettings:
        self.bridge_tls_cert_path = _validate_regular_file(
            self.bridge_tls_cert_path,
            label="BRIDGE_TLS_CERT_PATH",
            private=False,
        )
        self.bridge_tls_key_path = _validate_regular_file(
            self.bridge_tls_key_path,
            label="BRIDGE_TLS_KEY_PATH",
            private=True,
        )
        self.bridge_client_ca_path = _validate_regular_file(
            self.bridge_client_ca_path,
            label="BRIDGE_CLIENT_CA_PATH",
            private=False,
        )
        if self.bridge_hr_memory_path is not None:
            self.bridge_hr_memory_path = _validate_regular_file(
                self.bridge_hr_memory_path,
                label="BRIDGE_HR_MEMORY_PATH",
                private=False,
            )
        if self.bridge_backend == "codex":
            if self.bridge_codex_state_root is None or self.bridge_codex_work_root is None:
                raise ValueError(
                    "Codex backend requires BRIDGE_CODEX_STATE_ROOT and BRIDGE_CODEX_WORK_ROOT"
                )
        elif self.bridge_model == "workspace-default":
            raise ValueError("local planner backend requires an explicit BRIDGE_MODEL")
        return self


__all__ = ["BridgeBackendName", "BridgeHostSettings"]
