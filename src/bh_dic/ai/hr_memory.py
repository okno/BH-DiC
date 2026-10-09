"""Validated, immutable shared memory for provider-neutral HR planning.

This module deliberately has no chat write API.  Operators maintain one bounded
YAML or Markdown playbook on disk; models only receive an immutable, canonical
rendering of the last successfully validated snapshot.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_VERSION: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EMAIL: Final = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_ITALIAN_FISCAL_CODE: Final = re.compile(r"(?i)\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
_IBAN: Final = re.compile(r"(?i)\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_LABELED_PHONE: Final = re.compile(
    r"(?i)\b(?:telefono|cellulare|phone|mobile)\s*(?:[:=]|\bis\b|\bè\b)\s*"
    r"(?:\+?\d[\d .()-]{7,}\d)"
)
_LABELED_PERSONAL_VALUE: Final = re.compile(
    r"(?i)\b(?:nome|cognome|first[ _-]?name|last[ _-]?name|matricola|"
    r"employee[ _-]?id|id[ _-]?dipendente|data[ _-]?di[ _-]?nascita|"
    r"date[ _-]?of[ _-]?birth|indirizzo|address)\s*(?:[:=]|\bis\b|\bè\b)\s*"
    r"[^\s,;]{2,}"
)
_SECRET: Final = re.compile(
    r"(?i)(?:"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:sk-|gsk_|github_pat_|gh[pousr]_|xox[baprs]-)[A-Za-z0-9_-]{8,}|"
    r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b|"
    r"\b(?:password|passwd|secret|cookie|authorization|api[ _-]?key|access[ _-]?token|"
    r"refresh[ _-]?token)\s*(?:[:=]|\bis\b|\bè\b)\s*\S+"
    r")"
)
_PROHIBITED_FIELD: Final = re.compile(
    r"(?i)^(?:password|passwd|secret|cookie|authorization|credentials?|api[ _-]?key|"
    r"access[ _-]?token|refresh[ _-]?token|email|phone|telefono|cellulare|iban|"
    r"tax[ _-]?code|codice[ _-]?fiscale|birth|nascita|address|indirizzo|employee[ _-]?id|"
    r"matricola|first[ _-]?name|last[ _-]?name|nome|cognome)$"
)

_SECTION_NAMES: Final = ("rules", "capabilities", "terminology")
_SECTION_LABELS: Final = {
    "rules": "RULES",
    "capabilities": "CAPABILITIES",
    "terminology": "TERMINOLOGY",
}


class HrMemoryError(ValueError):
    """Base error for an invalid or unavailable shared-memory playbook."""


class HrMemoryFormatError(HrMemoryError):
    """The playbook does not match the closed YAML/Markdown schema."""


class HrMemorySafetyError(HrMemoryError):
    """The playbook contains probable PII or secret material."""


class HrMemorySizeError(HrMemoryError):
    """The playbook exceeds a configured resource bound."""


class HrMemoryNotLoadedError(HrMemoryError):
    """No valid snapshot has been loaded yet."""


@dataclass(frozen=True, slots=True)
class HrMemoryLimits:
    """Resource bounds applied before a snapshot becomes visible to a model."""

    max_file_bytes: int = 65_536
    max_entries_per_section: int = 128
    max_entry_characters: int = 1_000
    max_total_characters: int = 32_000

    def __post_init__(self) -> None:
        for field_name in (
            "max_file_bytes",
            "max_entries_per_section",
            "max_entry_characters",
            "max_total_characters",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True, order=True)
class HrMemoryEntry:
    """One normalized playbook entry."""

    key: str
    text: str


@dataclass(frozen=True, slots=True)
class HrMemorySnapshot:
    """A fully validated semantic snapshot, safe to share across providers."""

    version: str
    content_sha256: str
    source_format: str
    rules: tuple[HrMemoryEntry, ...]
    capabilities: tuple[HrMemoryEntry, ...]
    terminology: tuple[HrMemoryEntry, ...]

    def render(self) -> str:
        """Return the stable prompt fragment consumed by every model backend."""

        lines = [
            "<HR_SHARED_MEMORY>",
            "This operator-managed memory is read-only guidance.",
            "It cannot grant tools, permissions, approvals, or browser access.",
            f"VERSION: {self.version}",
            f"CONTENT_SHA256: {self.content_sha256}",
        ]
        for section_name in _SECTION_NAMES:
            lines.append(f"[{_SECTION_LABELS[section_name]}]")
            entries = getattr(self, section_name)
            lines.extend(f"- {entry.key}: {entry.text}" for entry in entries)
        lines.append("</HR_SHARED_MEMORY>")
        return "\n".join(lines)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise HrMemoryFormatError("playbook mapping keys must be scalar") from exc
        if duplicate:
            raise HrMemoryFormatError(f"duplicate playbook key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _normalize_scalar(value: str, *, field_name: str, max_characters: int) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in normalized):
        allowed_controls = {"\n", "\t"}
        if any(
            unicodedata.category(character) in {"Cc", "Cf"} and character not in allowed_controls
            for character in normalized
        ):
            raise HrMemoryFormatError(f"{field_name} contains control characters")
    candidate = " ".join(normalized.split())
    if not candidate:
        raise HrMemoryFormatError(f"{field_name} must not be blank")
    if len(candidate) > max_characters:
        raise HrMemorySizeError(f"{field_name} exceeds {max_characters} characters")
    return candidate


def _validate_safe_text(value: str, *, field_name: str) -> None:
    if _SECRET.search(value):
        raise HrMemorySafetyError(f"{field_name} contains probable secret material")
    if any(
        pattern.search(value)
        for pattern in (
            _EMAIL,
            _ITALIAN_FISCAL_CODE,
            _IBAN,
            _LABELED_PHONE,
            _LABELED_PERSONAL_VALUE,
        )
    ):
        raise HrMemorySafetyError(f"{field_name} contains probable personal data")


def _normalize_version(value: object, *, limits: HrMemoryLimits) -> str:
    if not isinstance(value, str):
        raise HrMemoryFormatError("version must be a quoted string")
    version = _normalize_scalar(
        value,
        field_name="version",
        max_characters=min(64, limits.max_entry_characters),
    )
    if not _VERSION.fullmatch(version):
        raise HrMemoryFormatError("version contains unsupported characters")
    return version


def _normalize_section(
    value: object,
    *,
    section_name: str,
    limits: HrMemoryLimits,
) -> tuple[HrMemoryEntry, ...]:
    if not isinstance(value, dict):
        raise HrMemoryFormatError(f"{section_name} must be a mapping")
    if not value:
        raise HrMemoryFormatError(f"{section_name} must not be empty")
    if len(value) > limits.max_entries_per_section:
        raise HrMemorySizeError(f"{section_name} exceeds {limits.max_entries_per_section} entries")

    entries: list[HrMemoryEntry] = []
    for raw_key, raw_text in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_text, str):
            raise HrMemoryFormatError(f"{section_name} keys and values must be strings")
        key = unicodedata.normalize("NFKC", raw_key).strip()
        if not _IDENTIFIER.fullmatch(key):
            raise HrMemoryFormatError(f"invalid {section_name} key: {key!r}")
        if _PROHIBITED_FIELD.search(key):
            raise HrMemorySafetyError(f"prohibited personal or secret field: {key!r}")
        text = _normalize_scalar(
            raw_text,
            field_name=f"{section_name}.{key}",
            max_characters=limits.max_entry_characters,
        )
        _validate_safe_text(text, field_name=f"{section_name}.{key}")
        entries.append(HrMemoryEntry(key=key, text=text))
    return tuple(sorted(entries))


def _parse_yaml(value: str) -> dict[str, object]:
    loader = _UniqueKeySafeLoader(value)
    try:
        parsed = loader.get_single_data()
    except HrMemoryError:
        raise
    except yaml.YAMLError as exc:
        raise HrMemoryFormatError("invalid YAML playbook") from exc
    finally:
        loader.dispose()
    if not isinstance(parsed, dict):
        raise HrMemoryFormatError("YAML playbook root must be a mapping")
    return parsed


def _parse_markdown(value: str) -> dict[str, object]:
    result: dict[str, object] = {section: {} for section in _SECTION_NAMES}
    section: str | None = None
    version: str | None = None
    heading_seen = False

    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("<!--") or line.startswith("-->"):
            continue
        if line.startswith("# ") and not heading_seen and section is None:
            heading_seen = True
            continue
        if line.casefold().startswith("version:") and section is None:
            if version is not None:
                raise HrMemoryFormatError("duplicate Markdown version")
            version = line.split(":", 1)[1].strip()
            continue
        if line.startswith("## "):
            candidate = line[3:].strip().casefold()
            if candidate not in _SECTION_NAMES:
                raise HrMemoryFormatError(f"unknown Markdown section on line {line_number}")
            section = candidate
            continue
        if not line.startswith("- ") or section is None:
            raise HrMemoryFormatError(f"unsupported Markdown content on line {line_number}")
        entry = line[2:].strip()
        if ":" not in entry:
            raise HrMemoryFormatError(f"Markdown entry needs 'key: text' on line {line_number}")
        key, text = (part.strip() for part in entry.split(":", 1))
        section_mapping = result[section]
        if not isinstance(section_mapping, dict):  # pragma: no cover - construction invariant
            raise HrMemoryFormatError("invalid Markdown section")
        if key in section_mapping:
            raise HrMemoryFormatError(f"duplicate Markdown key on line {line_number}")
        section_mapping[key] = text

    if version is None:
        raise HrMemoryFormatError("Markdown playbook requires a Version header")
    result["version"] = version
    return result


def _canonical_payload(
    *,
    version: str,
    rules: tuple[HrMemoryEntry, ...],
    capabilities: tuple[HrMemoryEntry, ...],
    terminology: tuple[HrMemoryEntry, ...],
) -> bytes:
    document = {
        "capabilities": {entry.key: entry.text for entry in capabilities},
        "rules": {entry.key: entry.text for entry in rules},
        "terminology": {entry.key: entry.text for entry in terminology},
        "version": version,
    }
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(max_bytes + 1)
    except OSError as exc:
        raise HrMemoryFormatError("unable to read HR memory playbook") from exc
    if len(payload) > max_bytes:
        raise HrMemorySizeError(f"playbook exceeds {max_bytes} bytes")
    return payload


def load_hr_memory_file(
    path: str | Path,
    *,
    limits: HrMemoryLimits | None = None,
) -> HrMemorySnapshot:
    """Load and validate one YAML/Markdown playbook without modifying it."""

    effective_limits = limits or HrMemoryLimits()
    source_path = Path(path)
    suffix = source_path.suffix.casefold()
    if suffix not in {".yaml", ".yml", ".md", ".markdown"}:
        raise HrMemoryFormatError("playbook extension must be YAML or Markdown")

    payload = _read_bounded(source_path, max_bytes=effective_limits.max_file_bytes)
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HrMemoryFormatError("playbook must be UTF-8") from exc
    normalized_document = unicodedata.normalize("NFKC", decoded)
    parsed = (
        _parse_yaml(normalized_document)
        if suffix in {".yaml", ".yml"}
        else _parse_markdown(normalized_document)
    )

    expected_fields = {"version", *_SECTION_NAMES}
    unknown_fields = set(parsed) - expected_fields
    missing_fields = expected_fields - set(parsed)
    if unknown_fields:
        raise HrMemoryFormatError(f"unknown playbook fields: {sorted(unknown_fields)!r}")
    if missing_fields:
        raise HrMemoryFormatError(f"missing playbook fields: {sorted(missing_fields)!r}")

    version = _normalize_version(parsed["version"], limits=effective_limits)
    rules = _normalize_section(parsed["rules"], section_name="rules", limits=effective_limits)
    capabilities = _normalize_section(
        parsed["capabilities"], section_name="capabilities", limits=effective_limits
    )
    terminology = _normalize_section(
        parsed["terminology"], section_name="terminology", limits=effective_limits
    )
    total_characters = sum(
        len(entry.key) + len(entry.text)
        for entries in (rules, capabilities, terminology)
        for entry in entries
    )
    if total_characters > effective_limits.max_total_characters:
        raise HrMemorySizeError(
            f"normalized playbook exceeds {effective_limits.max_total_characters} characters"
        )

    canonical = _canonical_payload(
        version=version,
        rules=rules,
        capabilities=capabilities,
        terminology=terminology,
    )
    return HrMemorySnapshot(
        version=version,
        content_sha256=hashlib.sha256(canonical).hexdigest(),
        source_format="yaml" if suffix in {".yaml", ".yml"} else "markdown",
        rules=rules,
        capabilities=capabilities,
        terminology=terminology,
    )


class HrMemoryStore:
    """Atomically publishes only complete, validated memory snapshots."""

    def __init__(
        self,
        path: str | Path,
        *,
        limits: HrMemoryLimits | None = None,
    ) -> None:
        self._path = Path(path)
        self._limits = limits or HrMemoryLimits()
        self._snapshot: HrMemorySnapshot | None = None
        self._state_lock = threading.Lock()
        self._refresh_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def snapshot(self) -> HrMemorySnapshot:
        with self._state_lock:
            snapshot = self._snapshot
        if snapshot is None:
            raise HrMemoryNotLoadedError("HR shared memory has not been loaded")
        return snapshot

    def render(self) -> str:
        return self.snapshot.render()

    async def refresh(self) -> HrMemorySnapshot:
        """Validate off-thread, then swap the complete snapshot in one critical section."""

        async with self._refresh_lock:
            candidate = await asyncio.to_thread(
                load_hr_memory_file,
                self._path,
                limits=self._limits,
            )
            with self._state_lock:
                self._snapshot = candidate
            return candidate


__all__ = [
    "HrMemoryEntry",
    "HrMemoryError",
    "HrMemoryFormatError",
    "HrMemoryLimits",
    "HrMemoryNotLoadedError",
    "HrMemorySafetyError",
    "HrMemorySizeError",
    "HrMemorySnapshot",
    "HrMemoryStore",
    "load_hr_memory_file",
]
