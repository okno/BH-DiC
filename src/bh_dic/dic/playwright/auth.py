"""Compatibility exports for deterministic DIC authentication."""

from bh_dic.dic.auth import DicSessionManager, PlaywrightAuthenticator, StorageStateProvider

__all__ = ["DicSessionManager", "PlaywrightAuthenticator", "StorageStateProvider"]
