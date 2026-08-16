from __future__ import annotations

import json
import logging
import sys

from bh_dic import __version__
from bh_dic.errors import ConfigurationError, ErrorCode
from bh_dic.logging import JsonFormatter, pseudonymize_identifier, redact


def test_package_exposes_semantic_version() -> None:
    assert __version__ == "0.2.3"


def test_public_error_does_not_include_internal_exception() -> None:
    internal = RuntimeError("token=do-not-leak")
    error = ConfigurationError(context={"cause_type": type(internal).__name__})

    assert error.code is ErrorCode.CONFIGURATION_INVALID
    assert "do-not-leak" not in str(error)
    assert error.context == {"cause_type": "RuntimeError"}


def test_recursive_log_redaction() -> None:
    payload = {
        "api_key": "sk-example-value-that-must-not-appear",
        "nested": {
            "message": "Authorization: Bearer abc.def.ghi",
            "iban": "IT60X0542811101000000123456",
        },
    }

    redacted = redact(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["iban"] == "[REDACTED]"
    assert "abc.def.ghi" not in redacted["nested"]["message"]


def test_json_formatter_emits_structured_redacted_event() -> None:
    record = logging.LogRecord(
        name="bh_dic.security",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Rejected Bearer abc.def.ghi",
        args=(),
        exc_info=None,
    )
    record.event_type = "security.authorization_denied"
    record.correlation_id = "corr-test-001"
    record.target_employee_id = "raw-employee-id-must-not-appear"
    record.password = "must-not-appear"

    event = json.loads(JsonFormatter(timezone="UTC").format(record))

    assert event["event_type"] == "security.authorization_denied"
    assert event["correlation_id"] == "corr-test-001"
    assert "abc.def.ghi" not in event["message"]
    assert event["target_employee_id"] == "[REDACTED_TARGET]"
    assert event["details"]["password"] == "[REDACTED]"


def test_json_formatter_redacts_email_and_phone_from_message_and_exception() -> None:
    email = "alice@example.invalid"
    phone = "+39 333 1234567"
    groq_key = "gsk_" + "syntheticvalue123456"
    try:
        raise RuntimeError(f"provider failure for {email} at {phone} with api_key={groq_key}")
    except RuntimeError:
        exception_info = sys.exc_info()
    record = logging.LogRecord(
        name="bh_dic.security",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=f"request rejected for {email} at {phone}",
        args=(),
        exc_info=exception_info,
    )

    rendered = JsonFormatter(timezone="UTC").format(record)

    assert email not in rendered
    assert phone not in rendered
    assert groq_key not in rendered
    assert "[REDACTED_EMAIL]" in rendered
    assert "[REDACTED_PHONE]" in rendered
    assert "[REDACTED_SECRET]" in rendered


def test_identifier_pseudonym_is_stable_and_keyed() -> None:
    first = pseudonymize_identifier("synthetic-employee-001", key=b"a" * 32)
    same = pseudonymize_identifier("synthetic-employee-001", key=b"a" * 32)
    changed = pseudonymize_identifier("synthetic-employee-001", key=b"b" * 32)

    assert first == same
    assert first != changed
    assert "synthetic-employee" not in first
