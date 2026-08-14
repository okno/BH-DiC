from __future__ import annotations

import pytest

from bh_dic.security import (
    InputValidationError,
    PayloadCipher,
    PayloadDecryptionError,
    SecretRedactor,
    SlidingWindowRateLimiter,
    contains_path_traversal,
    normalize_text,
    pseudonymize_identifier,
    redact_pii,
    sanitize_discord_text,
    sanitize_filename_metadata,
    validate_employee_id,
)


def test_security_normalizes_unicode_controls_and_validates_identifiers() -> None:
    assert normalize_text("  \uff2dario\x00  ", max_length=20) == "Mario"
    assert validate_employee_id("emp_123-AB") == "emp_123-AB"
    with pytest.raises(InputValidationError):
        validate_employee_id("../../123")
    with pytest.raises(InputValidationError):
        normalize_text("too long", max_length=3)


@pytest.mark.parametrize(
    "filename",
    [
        "../cv.pdf",
        "..\\cv.pdf",
        "C:\\cv.pdf",
        "/absolute/cv.pdf",
        "folder/cv.pdf",
        "\x00cv.pdf",
    ],
)
def test_security_rejects_path_traversal_metadata(filename: str) -> None:
    assert contains_path_traversal(filename)
    with pytest.raises(InputValidationError):
        sanitize_filename_metadata(filename)


def test_security_discord_output_cannot_create_mentions() -> None:
    output = sanitize_discord_text("@everyone <@123> <#456>")
    assert "@everyone" not in output
    assert "<@" not in output
    assert "<#" not in output


def test_security_redacts_nested_secrets_and_hr_pii() -> None:
    secret = "discord-super-secret"
    redactor = SecretRedactor([secret])
    value = {
        "password": secret,
        "profile": {
            "email": "mario.rossi@example.test",
            "message": f"Bearer {secret} RSSMRA80A01H501U",
        },
    }
    redacted = redactor.redact(value)
    rendered = repr(redacted)
    assert secret not in rendered
    assert "example.test" not in rendered
    assert "RSSMRA" not in rendered
    assert redacted["password"] == "[SECRET_REDACTED]"
    assert redact_pii({"iban": "IT60X0542811101000000123456"})["iban"] == "[PII_REDACTED]"


def test_security_pseudonymization_is_stable_and_keyed() -> None:
    key = b"pseudonymization-key-at-least-32-bytes"
    first = pseudonymize_identifier("employee-123", key)
    assert first == pseudonymize_identifier("employee-123", key)
    assert first != pseudonymize_identifier("employee-124", key)
    assert "employee-123" not in first
    with pytest.raises(ValueError):
        pseudonymize_identifier("employee-123", b"short")


@pytest.mark.asyncio
async def test_security_sliding_window_rate_limit_and_recovery() -> None:
    now = [100.0]
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10, clock=lambda: now[0])
    assert (await limiter.check("user")).allowed
    second = await limiter.check("user")
    assert second.allowed and second.remaining == 0
    denied = await limiter.check("user")
    assert not denied.allowed
    assert denied.retry_after_seconds == pytest.approx(10.0)
    now[0] += 10.1
    assert (await limiter.check("user")).allowed


def test_security_payload_cipher_round_trip_is_canonical_and_tamper_evident() -> None:
    cipher = PayloadCipher(b"x" * 32)
    payload = {"employee_id": "123", "changes": {"job": "Manager"}, "count": 1}
    encrypted = cipher.encrypt_json(payload)
    assert b"employee_id" not in encrypted
    assert cipher.decrypt_json(encrypted) == payload
    with pytest.raises(PayloadDecryptionError):
        cipher.decrypt_json(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(PayloadDecryptionError):
        cipher.decrypt_json(encrypted, purpose="browser_session")


def test_security_payload_cipher_accepts_a_long_high_entropy_secret() -> None:
    cipher = PayloadCipher(b"long-deployment-secret-material-" * 2)
    encrypted = cipher.encrypt_json({"synthetic": True})
    assert cipher.decrypt_json(encrypted) == {"synthetic": True}


def test_security_payload_cipher_rejects_non_json_and_non_finite_values() -> None:
    cipher = PayloadCipher(PayloadCipher.generate_key())
    with pytest.raises(ValueError):
        cipher.encrypt_json({"bad": object()})
    with pytest.raises(ValueError):
        cipher.encrypt_json({"bad": float("nan")})
