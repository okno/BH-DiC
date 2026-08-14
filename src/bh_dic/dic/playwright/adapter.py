"""Canonical import path for the async Playwright DIC adapter."""

from bh_dic.dic.playwright_adapter import (
    PlaywrightDicAdapter,
    PlaywrightDipendentiInCloudAdapter,
)

__all__ = ["PlaywrightDicAdapter", "PlaywrightDipendentiInCloudAdapter"]
