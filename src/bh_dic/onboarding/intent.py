"""Conservative local intent detection for Discord onboarding uploads."""

from __future__ import annotations

import re
import unicodedata

_ACTION = (
    r"(?:aggiungi(?:lo|la)?|aggiungere|inserisci(?:lo|la)?|inserire|"
    r"registra(?:lo|la)?|registrare|crea(?:re)?|avvia(?:re)?|"
    r"inizia(?:re)?|prepara(?:re)?|procedi)"
)
_SUBJECT = (
    r"(?:nuov[oa]\s+)?dipendent[ei]|scheda\s+(?:del\s+)?dipendente|"
    r"persona\s+come\s+dipendente|onboarding(?:\s+(?:del|di un)\s+dipendente)?|"
    r"nuova\s+assunzione"
)

_DIRECT_REQUEST = re.compile(
    rf"(?:\b{_ACTION}\b.{{0,120}}\b{_SUBJECT}\b|"
    rf"\b{_SUBJECT}\b.{{0,120}}\b{_ACTION}\b)",
    flags=re.IGNORECASE,
)
_NEGATED_REQUEST = re.compile(
    rf"\b(?:non|senza|evita(?:re)?)\b.{{0,50}}\b{_ACTION}\b",
    flags=re.IGNORECASE,
)
_INFORMATIONAL_OR_META_REQUEST = re.compile(
    rf"(?:\b(?:come|dove)\b.{{0,80}}\b{_ACTION}\b|"
    r"\b(?:funzione|comando|documentazione|istruzioni|esempio|test|simula(?:re)?)\b)",
    flags=re.IGNORECASE,
)


def is_explicit_employee_onboarding_request(value: object) -> bool:
    """Return true only for a direct, non-negated employee onboarding request.

    This classifier deliberately stays local and conservative.  An attachment is a
    capability to process HR documents, so ambiguous, informational, negated, or
    meta-level phrases must not activate it.
    """

    if not isinstance(value, str):
        return False
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized or len(normalized) > 2_000:
        return False
    if _NEGATED_REQUEST.search(normalized) is not None:
        return False
    if _INFORMATIONAL_OR_META_REQUEST.search(normalized) is not None:
        return False
    return _DIRECT_REQUEST.search(normalized) is not None
