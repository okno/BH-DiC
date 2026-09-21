"""Fail-closed local OCR and deterministic parsing for Italian identity documents.

Raw OCR text and extracted PII must never be logged.  The models in this module suppress
those values from their representations, and public exceptions contain stable, sanitized
messages only.
"""

from __future__ import annotations

import asyncio
import os
import re

# The fixed OCR executable receives a validated local path through argv; no shell is used.
import subprocess  # nosec B404
import unicodedata
import warnings
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bh_dic.files.mime import canonical_mime

_SUPPORTED_MIME_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
}
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TAX_CODE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{6}[0-9]{2}[A-EHLMPRST][0-9]{2}[A-Z][0-9]{3}[A-Z])(?![A-Z0-9])",
    re.IGNORECASE,
)
_CIE_NUMBER = re.compile(r"(?<![A-Z0-9])([A-Z]{2}[0-9]{5}[A-Z]{2})(?![A-Z0-9])", re.I)
_EMAIL = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63})(?![\w.-])", re.I)
_PHONE = re.compile(
    r"(?:TELEFONO|PHONE|CELLULARE|MOBILE)\s*[:\-]?\s*(\+?[0-9][0-9 .()/\-]{5,24})", re.I
)
_DATE = re.compile(r"(?<![0-9])([0-3]?[0-9][./-][01]?[0-9][./-](?:19|20)[0-9]{2})(?![0-9])")

DEFAULT_REQUIRED_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "tax_code",
    "date_of_birth",
    "email",
    "phone",
)


class OcrError(RuntimeError):
    """Base error with a safe, non-PII public representation."""

    code: ClassVar[str] = "OCR_FAILED"
    default_message: ClassVar[str] = "L'estrazione locale del documento non è riuscita."

    def __init__(self, message: str | None = None) -> None:
        self.safe_message = message or self.default_message
        super().__init__(self.safe_message)


class OcrInputError(OcrError):
    code = "OCR_INPUT_REJECTED"
    default_message = "Il file OCR non è disponibile o non supera i controlli di sicurezza."


class OcrUnsupportedFormatError(OcrError):
    code = "OCR_FORMAT_UNSUPPORTED"
    default_message = "Formato documento non supportato dall'OCR locale; invia JPEG o PNG."


class OcrUnavailableError(OcrError):
    code = "OCR_UNAVAILABLE"
    default_message = "Il servizio OCR locale non è disponibile."


class OcrTimeoutError(OcrError):
    code = "OCR_TIMEOUT"
    default_message = "Il servizio OCR locale ha superato il tempo massimo consentito."


class OcrOutputError(OcrError):
    code = "OCR_OUTPUT_REJECTED"
    default_message = "Il risultato OCR non supera i controlli di sicurezza."


class OcrText(BaseModel):
    """OCR text tied to an opaque upload identifier; raw text is hidden from repr."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: str = Field(min_length=1, max_length=64)
    text: str = Field(repr=False, max_length=1_000_000)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if _SOURCE_ID.fullmatch(value) is None:
            raise ValueError("source must be an opaque identifier")
        return value


class FieldEvidence(BaseModel):
    """One deterministic field observation from one opaque source document."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    value: str = Field(min_length=1, max_length=512, repr=False)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = Field(min_length=1, max_length=64)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if _SOURCE_ID.fullmatch(value) is None:
            raise ValueError("source must be an opaque identifier")
        return value


class FieldConflict(BaseModel):
    """Distinct observations that require an HR choice; none is auto-selected."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    field_name: str = Field(min_length=1, max_length=64)
    candidates: tuple[FieldEvidence, ...] = Field(min_length=2)

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, value: str) -> str:
        if _FIELD_NAME.fullmatch(value) is None:
            raise ValueError("invalid onboarding field name")
        return value

    @model_validator(mode="after")
    def require_distinct_values(self) -> FieldConflict:
        values = {_comparison_value(item.value) for item in self.candidates}
        if len(values) < 2:
            raise ValueError("a conflict requires distinct candidate values")
        return self


class DocumentDraft(BaseModel):
    """Merged deterministic draft. Conflicting fields are deliberately absent from fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    fields: dict[str, FieldEvidence]
    conflicts: tuple[FieldConflict, ...] = ()
    missing_required: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_draft(self) -> DocumentDraft:
        field_names = set(self.fields)
        conflict_names = {item.field_name for item in self.conflicts}
        if field_names.intersection(conflict_names):
            raise ValueError("conflicting fields cannot be present in the merged draft")
        if len(conflict_names) != len(self.conflicts):
            raise ValueError("duplicate field conflict")
        for name in (*field_names, *conflict_names, *self.missing_required):
            if _FIELD_NAME.fullmatch(name) is None:
                raise ValueError("invalid onboarding field name")
        if set(self.missing_required).intersection(field_names):
            raise ValueError("present fields cannot be reported as missing")
        return self


class LocalTesseractOcr:
    """Run Tesseract locally against already quarantined JPEG/PNG files only."""

    def __init__(
        self,
        *,
        quarantine_root: Path,
        executable: str = "tesseract",
        timeout_seconds: float = 20.0,
        max_input_bytes: int = 12 * 1024 * 1024,
        max_output_bytes: int = 512 * 1024,
        max_pixels: int = 25_000_000,
        language: str = "ita+eng",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("OCR timeout must be positive")
        if max_input_bytes <= 0 or max_output_bytes <= 0 or max_pixels <= 0:
            raise ValueError("OCR size limits must be positive")
        if language != "ita+eng":
            raise ValueError("OCR language must remain pinned to ita+eng")
        if not executable or "\x00" in executable or len(executable) > 512:
            raise ValueError("invalid OCR executable configuration")
        try:
            root = quarantine_root.resolve(strict=True)
        except OSError:
            raise ValueError("OCR quarantine root is unavailable") from None
        if not root.is_dir():
            raise ValueError("OCR quarantine root must be a directory")
        self._quarantine_root = root
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._max_pixels = max_pixels
        self._language = language

    async def extract(self, *, path: Path, mime_type: str, source: str) -> OcrText:
        """Validate an image and extract text without sending it to an external provider."""

        # Validate the opaque source before touching the document so validation errors cannot
        # accidentally include a filename or another caller-controlled PII value.
        try:
            OcrText(source=source, text="")
        except ValueError:
            raise OcrInputError() from None
        resolved = await asyncio.to_thread(self._validate_input, path, mime_type)
        try:
            completed = await asyncio.to_thread(self._run_tesseract, resolved)
        except subprocess.TimeoutExpired:
            raise OcrTimeoutError() from None
        except (OSError, ValueError):
            raise OcrUnavailableError() from None
        if completed.returncode != 0:
            raise OcrUnavailableError()
        output = completed.stdout
        if not isinstance(output, bytes) or len(output) > self._max_output_bytes:
            raise OcrOutputError()
        try:
            text = output.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise OcrOutputError() from None
        if "\x00" in text:
            raise OcrOutputError()
        return OcrText(source=source, text=text.strip())

    def _validate_input(self, path: Path, mime_type: str) -> Path:
        detected_mime = canonical_mime(mime_type)
        if detected_mime == "application/pdf":
            raise OcrUnsupportedFormatError(
                "Il PDF non è supportato dall'OCR locale; invia una pagina JPEG o PNG."
            )
        expected_format = _SUPPORTED_MIME_FORMATS.get(detected_mime or "")
        if expected_format is None:
            raise OcrUnsupportedFormatError()
        candidate = Path(path)
        if not candidate.is_absolute():
            raise OcrInputError()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise OcrInputError() from None
        # Requiring the supplied path to already be canonical rejects traversal components and
        # every symlink (including a symlink that happens to point back inside quarantine).
        if candidate != resolved or not resolved.is_relative_to(self._quarantine_root):
            raise OcrInputError()
        try:
            stat_result = resolved.stat()
        except OSError:
            raise OcrInputError() from None
        if not resolved.is_file() or stat_result.st_size <= 0:
            raise OcrInputError()
        if stat_result.st_size > self._max_input_bytes:
            raise OcrInputError("Il documento supera il limite dimensionale consentito.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(resolved) as image:
                    if image.format != expected_format:
                        raise OcrInputError()
                    if (
                        image.width <= 0
                        or image.height <= 0
                        or image.width * image.height > self._max_pixels
                    ):
                        raise OcrInputError("Il documento supera il limite di pixel consentito.")
                    image.verify()
        except OcrInputError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
        ):
            raise OcrInputError() from None
        except (OSError, SyntaxError, ValueError):
            raise OcrInputError() from None
        return resolved

    def _run_tesseract(self, path: Path) -> subprocess.CompletedProcess[bytes]:
        # Executable/language are operator configuration, and path has just been resolved,
        # symlink-rejected and bounded inside the quarantine root. No shell is ever involved.
        return subprocess.run(  # noqa: S603  # nosec B603
            [self._executable, os.fspath(path), "stdout", "-l", self._language],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=self._timeout_seconds,
        )


def parse_italian_document(document: OcrText) -> tuple[tuple[str, FieldEvidence], ...]:
    """Extract deterministic candidate fields without invoking an AI model."""

    text = unicodedata.normalize("NFKC", document.text)
    lines = tuple(_clean_line(line) for line in text.splitlines() if _clean_line(line))
    candidates: list[tuple[str, FieldEvidence]] = []

    def add(field_name: str, value: str | None, confidence: float) -> None:
        if value:
            candidates.append(
                (
                    field_name,
                    FieldEvidence(value=value, confidence=confidence, source=document.source),
                )
            )

    add(
        "first_name",
        _labeled_value(lines, (r"NOME(?:\s*/\s*NAME)?", r"NAME"), _valid_person_name),
        0.96,
    )
    add(
        "last_name",
        _labeled_value(
            lines,
            (r"COGNOME(?!\s+E\s+NOME)(?:\s*/\s*SURNAME)?", r"SURNAME"),
            _valid_person_name,
        ),
        0.96,
    )

    tax_match = _TAX_CODE.search(text)
    add("tax_code", tax_match.group(1).upper() if tax_match else None, 0.97)

    birth_context = _labeled_value(
        lines,
        (
            r"(?:LUOGO\s+E\s+)?DATA\s+DI\s+NASCITA(?:\s*/\s*DATE\s+OF\s+BIRTH)?",
            r"DATE\s+OF\s+BIRTH",
        ),
        lambda value: value if _DATE.search(value) else None,
    )
    if birth_context:
        birth_match = _DATE.search(birth_context)
        if birth_match:
            add("date_of_birth", _normalize_date(birth_match.group(1)), 0.96)
            place = _valid_place_name(
                (birth_context[: birth_match.start()] + birth_context[birth_match.end() :]).strip(
                    " ,-()"
                )
            )
            if place is None:
                place = _labeled_value(
                    lines,
                    (
                        r"LUOGO\s+DI\s+NASCITA(?:\s*/\s*PLACE\s+OF\s+BIRTH)?",
                        r"PLACE\s+OF\s+BIRTH",
                    ),
                    _valid_place_name,
                )
            add("place_of_birth", place, 0.88)
    else:
        birth_date = _labeled_date(
            lines,
            (
                r"DATA\s+DI\s+NASCITA(?:\s*/\s*DATE\s+OF\s+BIRTH)?",
                r"DATE\s+OF\s+BIRTH",
            ),
        )
        add("date_of_birth", birth_date, 0.95)
        add(
            "place_of_birth",
            _labeled_value(
                lines,
                (r"LUOGO\s+DI\s+NASCITA(?:\s*/\s*PLACE\s+OF\s+BIRTH)?", r"PLACE\s+OF\s+BIRTH"),
                _valid_place_name,
            ),
            0.92,
        )

    add(
        "gender",
        _labeled_value(lines, (r"SESSO(?:\s*/\s*SEX)?", r"SEX"), _valid_gender),
        0.95,
    )
    add(
        "citizenship",
        _labeled_value(
            lines,
            (r"CITTADINANZA(?:\s*/\s*NATIONALITY)?", r"NATIONALITY"),
            _valid_short_text,
        ),
        0.92,
    )
    add(
        "address",
        _labeled_value(
            lines,
            (
                r"INDIRIZZO\s+DI\s+RESIDENZA(?:\s*/\s*RESIDENCE)?",
                r"RESIDENZA(?:\s*/\s*RESIDENCE)?",
                r"RESIDENCE",
            ),
            _valid_address,
        ),
        0.90,
    )
    add(
        "document_number",
        _labeled_value(
            lines,
            (r"NUMERO\s+(?:DEL\s+)?DOCUMENTO", r"DOCUMENT(?:\s+NO|\s+NUMBER)?"),
            _valid_document_number,
        ),
        0.95,
    )
    if not any(name == "document_number" for name, _item in candidates):
        document_match = _CIE_NUMBER.search(text)
        add(
            "document_number",
            document_match.group(1).upper() if document_match else None,
            0.78,
        )
    add(
        "expiry_date",
        _labeled_date(
            lines,
            (
                r"(?:DATA\s+DI\s+)?SCADENZA(?:\s*/\s*EXPIRY)?",
                r"EXPIRY(?:\s+DATE)?",
                r"VALIDA\s+FINO\s+AL",
            ),
        ),
        0.95,
    )

    email_match = _EMAIL.search(text)
    add("email", email_match.group(1).lower() if email_match else None, 0.94)
    phone_match = _PHONE.search(text)
    add("phone", _normalize_phone(phone_match.group(1)) if phone_match else None, 0.90)
    return tuple(candidates)


def merge_ocr_texts(
    documents: Iterable[OcrText],
    *,
    required_fields: Sequence[str] = DEFAULT_REQUIRED_FIELDS,
) -> DocumentDraft:
    """Merge document evidence; distinct values become unresolved conflicts."""

    required = tuple(dict.fromkeys(required_fields))
    if any(_FIELD_NAME.fullmatch(item) is None for item in required):
        raise ValueError("invalid required onboarding field")
    by_field: defaultdict[str, list[FieldEvidence]] = defaultdict(list)
    for document in documents:
        for name, evidence in parse_italian_document(document):
            by_field[name].append(evidence)

    fields: dict[str, FieldEvidence] = {}
    conflicts: list[FieldConflict] = []
    for name in sorted(by_field):
        unique: dict[str, FieldEvidence] = {}
        for evidence in by_field[name]:
            key = _comparison_value(evidence.value)
            current = unique.get(key)
            if current is None or evidence.confidence > current.confidence:
                unique[key] = evidence
        if len(unique) == 1:
            fields[name] = next(iter(unique.values()))
            continue
        candidates = tuple(sorted(unique.values(), key=lambda item: (item.source, item.value)))
        conflicts.append(FieldConflict(field_name=name, candidates=candidates))

    missing = tuple(name for name in required if name not in fields)
    return DocumentDraft(fields=fields, conflicts=tuple(conflicts), missing_required=missing)


def _clean_line(value: str) -> str:
    return " ".join(value.replace("\x00", " ").strip().split())


def _labeled_value(
    lines: Sequence[str],
    labels: Sequence[str],
    validator: object,
) -> str | None:
    validate = validator
    assert callable(validate)
    for index, line in enumerate(lines):
        for label in labels:
            match = re.fullmatch(rf"{label}\s*[:\-]?\s*(.*)", line, re.I)
            if match is None:
                continue
            inline = match.group(1).strip()
            if inline:
                candidate = validate(inline)
                if isinstance(candidate, str) and candidate:
                    return candidate
            if index + 1 < len(lines):
                candidate = validate(lines[index + 1])
                if isinstance(candidate, str) and candidate:
                    return candidate
    return None


def _labeled_date(lines: Sequence[str], labels: Sequence[str]) -> str | None:
    value = _labeled_value(
        lines,
        labels,
        lambda candidate: candidate if _DATE.search(candidate) else None,
    )
    if not value:
        return None
    match = _DATE.search(value)
    return _normalize_date(match.group(1)) if match else None


def _normalize_date(value: str) -> str | None:
    normalized = value.replace(".", "/").replace("-", "/")
    try:
        return datetime.strptime(normalized, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _valid_person_name(value: str) -> str | None:
    candidate = value.strip(" :-")
    if not 1 <= len(candidate) <= 128:
        return None
    if any(not (character.isalpha() or character in " '-") for character in candidate):
        return None
    if candidate.upper() in {"NAME", "NOME", "SURNAME", "COGNOME"}:
        return None
    return candidate


def _valid_place_name(value: str) -> str | None:
    candidate = value.strip(" :-")
    if not 1 <= len(candidate) <= 128 or not any(character.isalpha() for character in candidate):
        return None
    if any(not (character.isalpha() or character in " '()-.,") for character in candidate):
        return None
    return candidate


def _valid_gender(value: str) -> str | None:
    match = re.fullmatch(r"\s*([MF])(?:\s+.*)?", value, re.I)
    return match.group(1).upper() if match else None


def _valid_short_text(value: str) -> str | None:
    candidate = value.strip(" :-")
    if not 1 <= len(candidate) <= 64 or not any(character.isalpha() for character in candidate):
        return None
    if any(not (character.isalpha() or character in " '-") for character in candidate):
        return None
    return candidate


def _valid_address(value: str) -> str | None:
    candidate = value.strip(" :-")
    if not 3 <= len(candidate) <= 256 or not any(character.isalpha() for character in candidate):
        return None
    if any(ord(character) < 32 for character in candidate):
        return None
    return candidate


def _valid_document_number(value: str) -> str | None:
    candidate = re.sub(r"\s+", "", value).upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{4,31}", candidate) is None:
        return None
    return candidate


def _normalize_phone(value: str) -> str | None:
    candidate = re.sub(r"[^+0-9]", "", value)
    if candidate.count("+") > 1 or ("+" in candidate and not candidate.startswith("+")):
        return None
    digits = candidate.removeprefix("+")
    return candidate if 6 <= len(digits) <= 20 else None


def _comparison_value(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
