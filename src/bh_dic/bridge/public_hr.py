"""Data-minimized public-HR response adapter for the planner bridge."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from bh_dic.bridge.client import JsonlBridgeClient
from bh_dic.bridge.contracts import BoundedJsonObject
from bh_dic.bridge.errors import BridgeError
from bh_dic.bridge.model_output import PublicHrBridgeOutput
from bh_dic.openai.client import PublicHrProviderError, PublicHrResponse
from bh_dic.openai.redaction import prepare_public_hr_input, redact_public_hr_text
from bh_dic.security.sanitization import sanitize_discord_text


class PublicHrBridgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task: Literal["public_hr_response"] = "public_hr_response"
    request: StrictStr = Field(min_length=1, max_length=2_000)


class BridgePublicHrResponder:
    """Generate only a bounded public answer; no DIC fact crosses the bridge."""

    def __init__(
        self,
        client: JsonlBridgeClient,
        *,
        timeout_seconds: float,
        configured_model: str = "workspace-default",
    ) -> None:
        if not 0.05 <= timeout_seconds <= 60.0:
            raise ValueError("bridge public-HR timeout is invalid")
        if not configured_model.strip() or len(configured_model) > 128:
            raise ValueError("bridge public-HR model label is invalid")
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._configured_model = configured_model

    async def respond(self, request: str) -> PublicHrResponse:
        minimized = prepare_public_hr_input(request)
        payload = PublicHrBridgePayload(request=minimized)
        try:
            response = await self._client.plan(
                BoundedJsonObject.model_validate(payload.model_dump(mode="json")),
                timeout_seconds=self._timeout_seconds,
            )
        except BridgeError:
            raise PublicHrProviderError(
                "planner bridge public-HR request failed",
                provider="bridge",
                model=self._configured_model,
                response_received=False,
            ) from None
        if response.result is None:
            raise PublicHrProviderError(
                "planner bridge returned no public-HR result",
                provider="bridge",
                model=self._configured_model,
                response_received=True,
            )
        try:
            output = PublicHrBridgeOutput.model_validate(response.result.payload.root)
        except ValidationError:
            raise PublicHrProviderError(
                "planner bridge returned an invalid public-HR result",
                provider=response.result.backend,
                model=response.result.model,
                usage=response.result.usage,
                response_received=True,
            ) from None
        safe_text = sanitize_discord_text(
            redact_public_hr_text(output.text),
            max_length=1_500,
        )
        if not safe_text:
            raise PublicHrProviderError(
                "planner bridge returned an empty public-HR result",
                provider=response.result.backend,
                model=response.result.model,
                usage=response.result.usage,
                response_received=True,
            )
        return PublicHrResponse(
            text=safe_text,
            provider=response.result.backend,
            model=response.result.model,
            usage=response.result.usage,
        )

    async def close(self) -> None:
        await self._client.close()


__all__ = ["BridgePublicHrResponder", "PublicHrBridgePayload"]
