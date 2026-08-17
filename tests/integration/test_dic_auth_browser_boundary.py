from __future__ import annotations

import asyncio
import os
import time

import pytest
from playwright.async_api import async_playwright

from bh_dic.dic.auth import (
    _TEAMSYSTEM_CREDENTIAL_FILL_SCRIPT,
    _TEAMSYSTEM_CREDENTIAL_SUBMIT_SCRIPT,
    DicAuthStage,
    DicAuthUiChangedError,
    PlaywrightAuthenticator,
)


def test_atomic_credential_scripts_reject_noncanonical_authority_before_payload() -> None:
    canonical_href_guard = (
        'window.location.href !==\n        "https://identity.teamsystem.com/'
        'Account/LoginPassword" +\n          window.location.search'
    )
    for script in (
        _TEAMSYSTEM_CREDENTIAL_FILL_SCRIPT,
        _TEAMSYSTEM_CREDENTIAL_SUBMIT_SCRIPT,
    ):
        assert canonical_href_guard in script
        assert script.index("window.location.href") < script.index("typeof payload")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_forged_global_url_cannot_bypass_atomic_credential_origin_boundary() -> None:
    """A hostile document cannot forge the native Location checks with global URL."""

    async with async_playwright() as playwright:
        executable = playwright.chromium.executable_path
        if not await asyncio.to_thread(os.path.isfile, executable):
            pytest.skip("Playwright Chromium is not installed")
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(
                """
                <input id="Login_Email" value="synthetic@example.invalid">
                <input id="selectPassword" type="password">
                <button id="submitBtn" onclick="globalThis.submitCount += 1">Submit</button>
                <script>
                  globalThis.submitCount = 0;
                  globalThis.URL = class ForgedURL {
                    constructor() {
                      return {
                        protocol: "https:",
                        hostname: "identity.teamsystem.com",
                        port: "",
                        username: "",
                        password: "",
                        pathname: "/Account/LoginPassword",
                        hash: ""
                      };
                    }
                  };
                </script>
                """
            )
            auth = PlaywrightAuthenticator(  # type: ignore[arg-type]
                page,
                "https://secure.dipendentincloud.it",
                expected_tenant_id="123456789",
            )
            auth._flow_deadline = time.monotonic() + 5
            secret_control = page.locator("#selectPassword")

            with pytest.raises(DicAuthUiChangedError) as fill_error:
                await auth._fill_teamsystem_secret_atomically(
                    secret_control,
                    configured_username="synthetic@example.invalid",
                    secret="synthetic-private-boundary-marker",
                )

            assert fill_error.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL
            assert await secret_control.input_value() == ""

            await secret_control.fill("synthetic-existing-local-value")
            with pytest.raises(DicAuthUiChangedError) as submit_error:
                await auth._submit_teamsystem_secret_atomically(
                    page.locator("#submitBtn"),
                    configured_username="synthetic@example.invalid",
                )

            assert submit_error.value.stage is DicAuthStage.TEAMSYSTEM_CREDENTIAL_SUBMIT
            assert await page.evaluate("globalThis.submitCount") == 0
        finally:
            await browser.close()
