"""Test-suite isolation from operator runtime configuration."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from bh_dic.config import AppSettings, clear_settings_cache


@pytest.fixture(autouse=True)
def isolate_app_settings_sources() -> Iterator[None]:
    """Keep a production ``.env`` or exported setting from influencing tests."""

    clear_settings_cache()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(AppSettings.model_config, "env_file", None)
            setting_names = {name.casefold() for name in AppSettings.model_fields}
            for environment_name in tuple(os.environ):
                if environment_name.casefold() in setting_names:
                    patch.delenv(environment_name, raising=False)
            yield
    finally:
        clear_settings_cache()
