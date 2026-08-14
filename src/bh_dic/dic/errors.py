"""Typed failures raised by the Dipendenti in Cloud integration."""

from __future__ import annotations

from bh_dic.errors import BHDicError, ErrorCode


class DicError(BHDicError):
    """Base class for errors that are safe to classify at service boundaries."""

    default_code = ErrorCode.EXTERNAL_SERVICE_FAILED
    default_message = "Dipendenti in Cloud non è disponibile."


class DicConfigurationError(DicError):
    """The adapter was configured with an unsafe or incomplete value."""

    default_code = ErrorCode.CONFIGURATION_INVALID


class DicAuthenticationError(DicError):
    """Authentication failed or the current browser session is no longer valid."""


class DicMfaRequiredError(DicAuthenticationError):
    """Interactive MFA is required and cannot be completed unattended."""


class DicCaptchaRequiredError(DicAuthenticationError):
    """A CAPTCHA or other human verification gate was encountered."""


class DicSessionExpiredError(DicAuthenticationError):
    """The encrypted browser session is expired."""


class DicSessionVaultError(DicError):
    """The encrypted session vault could not be read or validated."""


class DicAuthorizationError(DicError):
    """The authenticated DIC account cannot perform the requested operation."""

    default_code = ErrorCode.AUTHORIZATION_DENIED


class DicNotFoundError(DicError):
    """The requested employee or related record was not found."""


class DicAmbiguousTargetError(DicError):
    """A query resolved to zero or multiple mutation targets."""


class DicValidationError(DicError):
    """A deterministic adapter input failed validation."""

    default_code = ErrorCode.VALIDATION_FAILED


class DicUiChangedError(DicError):
    """Expected route or stable UI control is no longer present."""


class DicTransientError(DicError):
    """A read operation may be retried after a bounded delay."""


class DicCircuitOpenError(DicTransientError):
    """The browser circuit breaker is open."""


class DicWriteDisabledError(DicAuthorizationError):
    """A write feature flag prevents preparation or execution."""

    default_code = ErrorCode.FEATURE_DISABLED


class DicApprovalError(DicAuthorizationError):
    """Approval evidence is absent, expired, duplicated, or mismatched."""


class DicInvalidPreparedActionError(DicValidationError):
    """A prepared action failed integrity or lifecycle validation."""


class DicAmbiguousWriteOutcomeError(DicError):
    """A write may have reached DIC, so automatic retry is forbidden."""


class DicReconciliationRequiredError(DicError):
    """Post-write state could not be proven and requires operator review."""
