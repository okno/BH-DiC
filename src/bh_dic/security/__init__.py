"""Central security primitives used across application boundaries."""

from bh_dic.security.cipher import (
    PayloadCipher,
    PayloadDecryptionError,
    canonical_json_bytes,
)
from bh_dic.security.pii import pseudonymize_identifier, redact_pii, redact_pii_text
from bh_dic.security.rate_limit import RateLimitDecision, SlidingWindowRateLimiter
from bh_dic.security.sanitization import (
    InputValidationError,
    contains_path_traversal,
    normalize_text,
    sanitize_discord_text,
    sanitize_filename_metadata,
    validate_employee_id,
)
from bh_dic.security.secrets import SecretRedactor, has_private_file_permissions

__all__ = [
    "InputValidationError",
    "PayloadCipher",
    "PayloadDecryptionError",
    "RateLimitDecision",
    "SecretRedactor",
    "SlidingWindowRateLimiter",
    "canonical_json_bytes",
    "contains_path_traversal",
    "has_private_file_permissions",
    "normalize_text",
    "pseudonymize_identifier",
    "redact_pii",
    "redact_pii_text",
    "sanitize_discord_text",
    "sanitize_filename_metadata",
    "validate_employee_id",
]
