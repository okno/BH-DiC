"""Sanitized exceptions for the isolated planner bridge."""

from __future__ import annotations


class BridgeError(RuntimeError):
    """Base error that is safe to handle without provider response bodies."""


class BridgeProtocolError(BridgeError):
    """The peer violated the bounded JSONL protocol."""


class BridgeDeadlineExceeded(BridgeError):
    """The request could not complete before its declared deadline."""


class BridgeReplayError(BridgeError):
    """A request reused a nonce that was already accepted."""


class BridgeOverloadedError(BridgeError):
    """The host has no bounded execution slot available."""


class BackendError(BridgeError):
    """Base error for a planner backend failure."""


class BackendUnavailableError(BackendError):
    """The selected planner backend is unavailable."""


class BackendOutputError(BackendError):
    """The backend returned an invalid structured decision."""


__all__ = [
    "BackendError",
    "BackendOutputError",
    "BackendUnavailableError",
    "BridgeDeadlineExceeded",
    "BridgeError",
    "BridgeOverloadedError",
    "BridgeProtocolError",
    "BridgeReplayError",
]
