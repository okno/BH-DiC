"""Data minimization before provider calls and deterministic result rendering."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_IBAN = re.compile(r"(?i)\bIT\d{2}[A-Z]\d{10}[A-Z0-9]{12}\b")
_ITALIAN_FISCAL_CODE = re.compile(r"(?i)\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?39[ .-]?)?(?:0\d{1,3}|3\d{2})[ .-]?\d(?:[ .-]?\d){5,9}(?!\w)")
_SECRET = re.compile(
    r"(?i)(?:\bBearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:sk-|gsk_|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|"
    r"\b(?:token|password|secret|cookie|api[\s_-]?key)\s*(?:[:=]|\bis\b|\bè\b)\s*\S+)"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DISCORD_REFERENCE = re.compile(r"<(?:(?:@!?|@&|#)\d{1,20})>")
_PUBLIC_EMPLOYEE_IDENTIFIER = re.compile(
    r"(?i)\b(?:employee\s*id|id\s+dipendente|dipendente\s+id|matricola)"
    r"\s*[:#=]?\s*[A-Za-z0-9_-]{1,64}\b"
)
_PUBLIC_BIRTH_DATE = re.compile(
    r"(?i)\b(?:data\s+di\s+nascita|nato|nata)\s*(?:il)?\s*[:=]?\s*"
    r"(?:\d{1,2}[./-]){2}\d{2,4}\b"
)
_PUBLIC_DATE = re.compile(r"\b(?:(?:\d{1,2}[./-]){2}\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b")
_PUBLIC_ADDRESS = re.compile(
    r"(?i)\b(?:via|viale|piazza|corso|largo|vicolo)\s+"
    r"[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ' -]{1,60}\s+\d{1,5}[A-Za-z]?"
    r"(?:\s*,\s*[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ' -]{1,40})?"
)
_PUBLIC_URL = re.compile(
    r"(?i)(?:\[[^\]\n]{1,100}\]\((?:https?|ftp|mailto):[^)\s]+\)|"
    r"\b(?:https?|ftp)://[^\s<>]+|\bmailto:[^\s<>]+|\bwww\.[^\s<>]+|"
    r"\b(?:[a-z0-9-]+\.)+[a-z]{2,63}"
    r"(?:/[^\s<>]*)?)"
)
_PUBLIC_LABELED_NAME = re.compile(
    r"(?i)\b(nome(?:\s+dipendente)?|dipendente|collega)\s*[:=]\s*"
    r"[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ'-]{1,40}"
    r"(?:\s+[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ'-]{1,40}){1,3}"
)
_PUBLIC_DIRECTED_NAME = re.compile(
    r"\b((?i:contatta|contact|scrivi\s+a|rivolgiti\s+a|parla\s+con|"
    r"(?:ho\s+)?incontrat[oa]|invita))\s+"
    r"(?i:[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}"
    r"(?:\s+(?!(?:durante|alla|al|nella|nel|per|su|domani|oggi|ieri)\b)"
    r"[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}){1,3})\b"
)
_PUBLIC_ROLE_NAME = re.compile(
    r"(?i)\b(?P<label>responsabile|collega|dipendente|manager|supervisor|employee|coworker)\s+"
    r"(?P<name>[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}"
    r"(?:\s+(?!(?:ha|è|e|chiede|segnala|presenta|partecip\w*|guid\w*|"
    r"lavora\w*|vuole|deve|può|puo)\b)[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}){1,3})"
    r"(?=\s+(?:ha|è|e|chiede|segnala|presenta|partecip\w*|guid\w*|"
    r"lavora\w*|vuole|deve|può|puo)\b|[?.!,;:]|$)"
)
_PUBLIC_PREDICATE_NAME = re.compile(
    r"(?i)\b(?P<name>[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}"
    r"(?:\s+(?!(?:ha|è|e|chiede|segnala|presenta|partecip\w*|guid\w*|"
    r"lavora\w*|vuole|deve|può|puo)\b)[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}){1,3})"
    r"\s+(?P<verb>ha|è|e|chiede|segnala|presenta|partecip\w*|guid\w*|"
    r"lavora\w*|vuole|deve|può|puo)\b"
)
_PUBLIC_MONEY = re.compile(r"(?i)(?:€\s*\d[\d.,]{0,15}|\b\d[\d.,]{0,15}\s*(?:euro|eur)\b)")
_PUBLIC_COMPENSATION = re.compile(
    r"(?i)\b(?:ral|stipendio|retribuzione|bonus)\s*[:=]?\s*"
    r"(?:€\s*)?\d[\d.,]{0,15}(?:\s*k)?\b"
)
_PUBLIC_LIKELY_FULL_NAME = re.compile(
    r"\b[A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ'-]{1,40}"
    r"(?:\s+[A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ'-]{1,40}){1,3}\b"
)
_PUBLIC_ALL_CAPS_FULL_NAME = re.compile(r"\b[A-ZÀ-ÖØ-Ý]{2,40}(?:\s+[A-ZÀ-ÖØ-Ý]{2,40}){1,3}\b")
_PUBLIC_SENSITIVE_TOPIC = re.compile(
    r"(?i)\b(?:hiv|aids|cancro|tumor\w*|patologi\w*|diagnos\w*|salute|medic\w*|"
    r"certificat\w*|malatti\w*|disabil\w*|gravidanz\w*|incint\w*|"
    r"stipendi\w*|ral|retribuzion\w*|bust\w+\s+pag\w*|ferie\s+residue|"
    r"assenze|disciplin\w*)\b"
)
_PUBLIC_FIRST_PERSON = re.compile(
    r"(?i)(?:^\s*sono\b|\b(?:io|ho|mio|mia|miei|mie|il\s+mio|la\s+mia|"
    r"i\s+miei|le\s+mie)\b)"
)
_PUBLIC_PERSONAL_REFERENCE = re.compile(
    r"(?i)\b(?:il|la|un|una|mio|mia|miei|mie|nostro|nostra)\s+"
    r"(?:collega|dipendente|moglie|marito|partner|figli[oa]|persona)\b"
)
_PUBLIC_LOWERCASE_CASE_NAME = re.compile(
    r"(?i)\b(?P<first>[a-zà-öø-ÿ'-]{2,40})\s+"
    r"(?P<second>[a-zà-öø-ÿ'-]{2,40})\s+"
    r"(?:ha|è|e|chiede|segnala|presenta)\b"
)
_PUBLIC_BARE_LOWERCASE_NAME = re.compile(
    r"^\s*(?P<name>[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40}\s+"
    r"[a-zà-öø-ÿ][a-zà-öø-ÿ'-]{1,40})\s*[?!.]?\s*$"
)
_PUBLIC_NAME_ALLOWLIST = frozenset(
    {
        "smart working",
        "risorse umane",
        "team hr",
        "buongiorno team",
        "dipendenti in cloud",
        "developer portal",
        "contratto collettivo nazionale",
        "codice civile",
        "next steps",
        "annual leave policy",
        "annual leave",
        "buone ferie",
        "ciao colleghi",
        "collective bargaining agreement",
        "ferie residue",
        "ferie e permessi",
        "ciao team",
        "grazie mille",
    }
)
_PUBLIC_NON_NAME_FIRST_WORDS = frozenset(
    {
        "che",
        "chi",
        "come",
        "cosa",
        "dove",
        "gli",
        "i",
        "il",
        "la",
        "le",
        "lo",
        "perche",
        "perché",
        "quale",
        "se",
        "un",
        "una",
    }
)

_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignora le istruzioni precedenti",
    "system prompt",
    "esegui javascript",
    "run javascript",
    "shell command",
    "bypass policy",
)


class UnsafePromptError(ValueError):
    """The request contains a high-confidence policy-bypass marker."""


def normalize_user_text(value: str, *, max_length: int = 2_000) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _CONTROL.sub("", normalized).strip()
    if not normalized:
        raise ValueError("empty request")
    if len(normalized) > max_length:
        raise ValueError("request is too long")
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _INJECTION_MARKERS):
        raise UnsafePromptError("suspicious prompt-injection marker")
    return normalized


def redact_text(value: str) -> str:
    value = _SECRET.sub("[SECRET_REDACTED]", value)
    value = _IBAN.sub("[IBAN_REDACTED]", value)
    value = _ITALIAN_FISCAL_CODE.sub("[FISCAL_CODE_REDACTED]", value)
    value = _EMAIL.sub("[EMAIL_REDACTED]", value)
    return _PHONE.sub("[PHONE_REDACTED]", value)


def prepare_provider_input(value: str) -> str:
    return redact_text(normalize_user_text(value))


def redact_public_hr_text(value: str) -> str:
    """Remove identifiers that have no purpose in a public general-HR response."""

    prepared = redact_text(value)
    prepared = _DISCORD_REFERENCE.sub("[DISCORD_REFERENCE_REDACTED]", prepared)
    prepared = _PUBLIC_EMPLOYEE_IDENTIFIER.sub("[EMPLOYEE_ID_REDACTED]", prepared)
    prepared = _PUBLIC_BIRTH_DATE.sub("[BIRTH_DATE_REDACTED]", prepared)
    prepared = _PUBLIC_DATE.sub("[DATE_REDACTED]", prepared)
    prepared = _PUBLIC_ADDRESS.sub("[ADDRESS_REDACTED]", prepared)
    prepared = _PUBLIC_URL.sub("[URL_REDACTED]", prepared)
    prepared = _PUBLIC_LABELED_NAME.sub(
        lambda match: f"{match.group(1)}: [PERSON_REDACTED]",
        prepared,
    )
    prepared = _PUBLIC_DIRECTED_NAME.sub(
        lambda match: f"{match.group(1)} [PERSON_REDACTED]",
        prepared,
    )
    prepared = _PUBLIC_ROLE_NAME.sub(
        lambda match: f"{match.group('label')} [PERSON_REDACTED]",
        prepared,
    )
    prepared = _PUBLIC_PREDICATE_NAME.sub(_redact_public_predicate_name, prepared)
    prepared = _redact_public_name_shapes(prepared)
    bare_lowercase = _PUBLIC_BARE_LOWERCASE_NAME.fullmatch(prepared)
    if (
        bare_lowercase is not None
        and bare_lowercase.group("name").casefold() not in _PUBLIC_NAME_ALLOWLIST
    ):
        prepared = "[PERSON_REDACTED]"
    prepared = _PUBLIC_COMPENSATION.sub("[COMPENSATION_REDACTED]", prepared)
    return _PUBLIC_MONEY.sub("[AMOUNT_REDACTED]", prepared)


def _is_allowlisted_public_name_shape(match: re.Match[str]) -> bool:
    return match.group(0).casefold() in _PUBLIC_NAME_ALLOWLIST


def _redact_public_predicate_name(match: re.Match[str]) -> str:
    candidate = match.group("name")
    first_word = candidate.split(maxsplit=1)[0].casefold()
    if candidate.casefold() in _PUBLIC_NAME_ALLOWLIST or first_word in _PUBLIC_NON_NAME_FIRST_WORDS:
        return match.group(0)
    return f"[PERSON_REDACTED] {match.group('verb')}"


def _redact_public_name_shapes(value: str) -> str:
    """Fail closed on name-shaped Title Case/all-caps sequences in public output.

    A small closed allowlist preserves common HR concepts and headings. False positives only
    remove text from a public answer; accepting a likely person's name would disclose it.
    """

    redacted = _PUBLIC_LIKELY_FULL_NAME.sub(
        lambda match: (
            match.group(0) if _is_allowlisted_public_name_shape(match) else "[PERSON_REDACTED]"
        ),
        value,
    )
    return _PUBLIC_ALL_CAPS_FULL_NAME.sub(
        lambda match: (
            match.group(0) if _is_allowlisted_public_name_shape(match) else "[PERSON_REDACTED]"
        ),
        redacted,
    )


def _looks_like_lowercase_case_name(value: str) -> bool:
    for match in _PUBLIC_LOWERCASE_CASE_NAME.finditer(value):
        candidate = f"{match.group('first')} {match.group('second')}".casefold()
        if (
            match.group("first").casefold() not in _PUBLIC_NON_NAME_FIRST_WORDS
            and candidate not in _PUBLIC_NAME_ALLOWLIST
        ):
            return True
    return False


def prepare_public_hr_input(value: str) -> str:
    """Minimize one public-channel message before stateless HR generation.

    This boundary is deliberately stricter than intent routing: identifiers useful only for an
    individual case are removed because the public responder has no authorized operational use
    for them. The transformed text remains sufficient for general HR guidance.
    """

    normalized = normalize_user_text(value)
    bare_lowercase = _PUBLIC_BARE_LOWERCASE_NAME.fullmatch(normalized)
    if (
        bare_lowercase is not None
        and bare_lowercase.group("name").casefold() not in _PUBLIC_NAME_ALLOWLIST
    ):
        raise UnsafePromptError("public HR request contains a likely individual case")
    has_high_confidence_name = any(
        not _is_allowlisted_public_name_shape(match)
        for pattern in (_PUBLIC_LIKELY_FULL_NAME, _PUBLIC_ALL_CAPS_FULL_NAME)
        for match in pattern.finditer(normalized)
    )
    sensitive_topic = _PUBLIC_SENSITIVE_TOPIC.search(normalized)
    personal_sensitive_case = bool(
        sensitive_topic
        and (
            _PUBLIC_FIRST_PERSON.search(normalized)
            or _PUBLIC_PERSONAL_REFERENCE.search(normalized)
            or _looks_like_lowercase_case_name(normalized)
            or _PUBLIC_ALL_CAPS_FULL_NAME.search(normalized)
        )
    )
    if has_high_confidence_name or personal_sensitive_case:
        raise UnsafePromptError("public HR request contains a likely individual case")
    prepared = redact_public_hr_text(normalized)
    if len(prepared) > 2_000:
        raise ValueError("redacted public HR request is too long")
    return prepared


def redact_structure(value: Any) -> Any:
    """Recursively redact JSON-like output without mutating the input."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(key): redact_structure(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_structure(child) for child in value]
    return value
