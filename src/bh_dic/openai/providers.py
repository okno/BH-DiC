"""Provider constants and fail-closed endpoint validation."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

ModelProvider = Literal["openai", "groq", "llama"]

OPENAI_RESPONSES_BASE_URL = "https://api.openai.com/v1"
GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_LLAMA_BASE_URL = "http://127.0.0.1:11434/v1"


def validate_llama_base_url(value: str) -> str:
    """Validate an OpenAI-compatible llama endpoint without resolving DNS.

    Plain HTTP is accepted only for an explicit loopback literal or ``localhost``.
    Remote endpoints must use HTTPS. Credentials and request-specific URL parts are
    never valid in a configured API origin.
    """

    candidate = value.strip()
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("LLAMA_BASE_URL contains an invalid port") from exc
    del port

    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("LLAMA_BASE_URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("LLAMA_BASE_URL must not contain user information")
    if parsed.query or parsed.fragment:
        raise ValueError("LLAMA_BASE_URL must not contain a query or fragment")
    if parsed.path not in {"", "/", "/v1", "/v1/"}:
        raise ValueError("LLAMA_BASE_URL path must be /v1")

    hostname = parsed.hostname.casefold()
    is_loopback = _is_loopback_hostname(hostname)
    if parsed.scheme == "http" and not is_loopback:
        raise ValueError("LLAMA_BASE_URL permits HTTP only for a loopback endpoint")

    return urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))


def llama_endpoint_is_loopback(value: str) -> bool:
    """Return endpoint scope after applying the complete URL validator."""

    parsed = urlsplit(validate_llama_base_url(value))
    if parsed.hostname is None:
        raise ValueError("LLAMA_BASE_URL must include a hostname")
    return _is_loopback_hostname(parsed.hostname.casefold())


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
