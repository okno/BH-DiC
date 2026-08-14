"""Structured JSON logging with centralized redaction."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging as stdlib_logging
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bh_dic import __version__

_CONTEXT_FIELDS = (
    "correlation_id",
    "discord_user_id",
    "guild_id",
    "channel_id",
    "function_id",
    "target_employee_id",
)
_CONTEXT: dict[str, contextvars.ContextVar[str | None]] = {
    name: contextvars.ContextVar(f"bh_dic_{name}", default=None) for name in _CONTEXT_FIELDS
}

_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|cookie|password|passwd|token|secret|api[_-]?key|totp|"
    r"storage[_-]?state|confirmation[_-]?code|iban|codice[_-]?fiscale|file[_-]?content)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PROVIDER_KEY = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b"
)
_IBAN = re.compile(r"(?i)\bIT\d{2}[A-Z]\d{10}[A-Z0-9]{12}\b")
_ITALIAN_TAX_ID = re.compile(r"(?i)\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b")
_TARGET_PSEUDONYM = re.compile(r"^emp_[a-f0-9]{8,64}$")
_STANDARD_RECORD_KEYS = set(stdlib_logging.makeLogRecord({}).__dict__)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secrets and common high-risk Italian identifiers."""

    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return "[REDACTED_BINARY]"
    if isinstance(value, str):
        result = _BEARER.sub("Bearer [REDACTED]", value)
        result = _PROVIDER_KEY.sub("[REDACTED]", result)
        result = _IBAN.sub("[REDACTED_IBAN]", result)
        result = _ITALIAN_TAX_ID.sub("[REDACTED_TAX_ID]", result)
        return result
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return redact(str(value))


def pseudonymize_identifier(identifier: str, *, key: bytes, length: int = 16) -> str:
    """Return a stable, non-reversible identifier suitable for logs."""

    if not identifier or len(key) < 16:
        raise ValueError("identifier and a key of at least 16 bytes are required")
    digest = hashlib.blake2b(identifier.encode("utf-8"), key=key, digest_size=32).hexdigest()
    return f"emp_{digest[:length]}"


class ContextFilter(stdlib_logging.Filter):
    def filter(self, record: stdlib_logging.LogRecord) -> bool:
        for name, variable in _CONTEXT.items():
            if not hasattr(record, name):
                setattr(record, name, variable.get())
        return True


class PrefixFilter(stdlib_logging.Filter):
    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix

    def filter(self, record: stdlib_logging.LogRecord) -> bool:
        return record.name == self.prefix or record.name.startswith(f"{self.prefix}.")


class JsonFormatter(stdlib_logging.Formatter):
    """Wazuh-friendly one-event-per-line formatter."""

    def __init__(self, *, timezone: str = "Europe/Rome") -> None:
        super().__init__()
        self.local_timezone = ZoneInfo(timezone)

    def format(self, record: stdlib_logging.LogRecord) -> str:
        utc_timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        local_timestamp = utc_timestamp.astimezone(self.local_timezone)
        event: dict[str, Any] = {
            "timestamp_utc": utc_timestamp.isoformat().replace("+00:00", "Z"),
            "timestamp_local": local_timestamp.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event_type": getattr(record, "event_type", "application.log"),
            "message": redact(record.getMessage()),
            "application_version": __version__,
        }
        for name in _CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                if name == "target_employee_id" and not _TARGET_PSEUDONYM.fullmatch(str(value)):
                    event[name] = "[REDACTED_TARGET]"
                else:
                    event[name] = redact(value, key=name)
        for name in ("outcome", "duration_ms", "error_code"):
            value = getattr(record, name, None)
            if value is not None:
                event[name] = redact(value, key=name)

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_KEYS
            and key not in event
            and key not in _CONTEXT_FIELDS
            and key not in {"event_type", "outcome", "duration_ms", "error_code"}
        }
        if extras:
            event["details"] = redact(extras)
        if record.exc_info:
            event["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(redact(event), ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(
    *,
    log_dir: Path,
    level: str = "INFO",
    timezone: str = "Europe/Rome",
    stream: bool = True,
) -> stdlib_logging.Logger:
    """Configure idempotent application and component JSONL handlers."""

    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    logger = stdlib_logging.getLogger("bh_dic")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(getattr(stdlib_logging, level.upper(), stdlib_logging.INFO))

    formatter = JsonFormatter(timezone=timezone)
    context_filter = ContextFilter()

    app_handler = _secure_file_handler(log_dir / "app.jsonl")
    app_handler.setFormatter(formatter)
    app_handler.addFilter(context_filter)
    logger.addHandler(app_handler)

    for component in ("discord", "openai", "browser", "audit", "security"):
        handler = _secure_file_handler(log_dir / f"{component}.jsonl")
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        handler.addFilter(PrefixFilter(f"bh_dic.{component}"))
        logger.addHandler(handler)

    if stream:
        stream_handler = stdlib_logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(context_filter)
        logger.addHandler(stream_handler)
    return logger


def _secure_file_handler(path: Path) -> stdlib_logging.FileHandler:
    handler = stdlib_logging.FileHandler(path, encoding="utf-8")
    path.chmod(0o600)
    return handler


def get_logger(component: str | None = None) -> stdlib_logging.Logger:
    name = "bh_dic" if not component else f"bh_dic.{component.strip('.')}"
    return stdlib_logging.getLogger(name)


@contextlib.contextmanager
def log_context(**values: str | int | None) -> Iterator[None]:
    """Bind correlation metadata to logs within the current async context."""

    tokens: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]] = []
    try:
        for name, value in values.items():
            if name not in _CONTEXT:
                raise KeyError(f"Unsupported log context field: {name}")
            variable = _CONTEXT[name]
            token = variable.set(None if value is None else str(value))
            tokens.append((variable, token))
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
