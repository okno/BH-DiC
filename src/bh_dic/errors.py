"""Stable, safe application errors.

Internal exceptions may contain confidential provider details. The exceptions in this module
carry a safe public message and a stable machine-readable code; callers must not surface the
original exception to Discord.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from bh_dic.policies.decisions import PolicyDecision


class ErrorCode(StrEnum):
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DATABASE_CONFLICT = "DATABASE_CONFLICT"
    AUDIT_APPEND_FAILED = "AUDIT_APPEND_FAILED"
    AUDIT_INTEGRITY_FAILED = "AUDIT_INTEGRITY_FAILED"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    EXTERNAL_SERVICE_FAILED = "EXTERNAL_SERVICE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class BHDicError(Exception):
    """Base exception whose string representation is safe for operator-facing output."""

    default_code = ErrorCode.INTERNAL_ERROR
    default_message = "Si è verificato un errore interno. Usa il correlation ID per la diagnosi."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.safe_message = message or self.default_message
        self.context = dict(context or {})
        super().__init__(self.safe_message)


class ConfigurationError(BHDicError):
    default_code = ErrorCode.CONFIGURATION_INVALID
    default_message = "Configurazione BH-DiC incompleta o non sicura."


class DatabaseUnavailableError(BHDicError):
    default_code = ErrorCode.DATABASE_UNAVAILABLE
    default_message = "Database BH-DiC non disponibile."


class DatabaseConflictError(BHDicError):
    default_code = ErrorCode.DATABASE_CONFLICT
    default_message = "Aggiornamento concorrente del database; operazione non eseguita."


class AuditAppendError(BHDicError):
    default_code = ErrorCode.AUDIT_APPEND_FAILED
    default_message = "Impossibile registrare l'evento di audit; operazione bloccata."


class AuditIntegrityError(BHDicError):
    default_code = ErrorCode.AUDIT_INTEGRITY_FAILED
    default_message = "Verifica di integrità della catena audit fallita."


class AuthorizationDeniedError(BHDicError):
    default_code = ErrorCode.AUTHORIZATION_DENIED
    default_message = "Utente, server o canale non autorizzato."


class FeatureDisabledError(BHDicError):
    default_code = ErrorCode.FEATURE_DISABLED
    default_message = "Funzione disabilitata dalla policy di sicurezza."


class ApplicationError(RuntimeError):
    """A deterministic application operation could not be completed."""


class ApplicationPolicyDenied(ApplicationError):
    """The authoritative application policy rejected an operation."""

    def __init__(self, decision: PolicyDecision, correlation_id: str | None = None) -> None:
        super().__init__(decision.reason)
        self.decision = decision
        self.correlation_id = correlation_id
