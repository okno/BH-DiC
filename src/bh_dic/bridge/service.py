"""Composition root for the standalone Mint planner bridge."""

from __future__ import annotations

import asyncio
import ssl

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from bh_dic.ai.hr_memory import load_hr_memory_file
from bh_dic.bridge.backend import PlannerBackend
from bh_dic.bridge.backends.codex import CodexPlannerBackend
from bh_dic.bridge.backends.openai_compatible import (
    LMStudioPlannerBackend,
    OllamaPlannerBackend,
    OpenAISdkChatTransport,
)
from bh_dic.bridge.host import JsonlBridgeHost
from bh_dic.bridge.model_output import BridgeModelOutput
from bh_dic.bridge.prompts import build_bridge_system_instruction
from bh_dic.bridge.settings import BridgeHostSettings


def build_server_ssl_context(settings: BridgeHostSettings) -> ssl.SSLContext:
    """Build the TLS 1.3 server context and require the Ubiquiti client cert."""

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(
            certfile=str(settings.bridge_tls_cert_path),
            keyfile=str(settings.bridge_tls_key_path),
        )
        context.load_verify_locations(cafile=str(settings.bridge_client_ca_path))
    except (OSError, ssl.SSLError):
        raise ValueError("planner bridge server mTLS material is invalid") from None
    return context


def build_planner_backend(settings: BridgeHostSettings) -> PlannerBackend:
    memory = (
        None
        if settings.bridge_hr_memory_path is None
        else load_hr_memory_file(settings.bridge_hr_memory_path)
    )
    instruction = build_bridge_system_instruction(memory)
    if settings.bridge_backend == "codex":
        if settings.bridge_codex_state_root is None or settings.bridge_codex_work_root is None:
            raise ValueError("Codex bridge isolation roots are required")
        return CodexPlannerBackend(
            state_root=settings.bridge_codex_state_root,
            isolated_work_root=settings.bridge_codex_work_root,
            executable=settings.bridge_codex_executable,
            output_model=BridgeModelOutput,
            system_instruction=instruction,
            model=settings.bridge_model,
        )

    provider = AsyncOpenAI(
        api_key="local-loopback-planner",
        base_url=settings.bridge_local_base_url,
        max_retries=0,
        timeout=60.0,
        http_client=DefaultAsyncHttpxClient(follow_redirects=False, trust_env=False),
    )
    transport = OpenAISdkChatTransport(provider)
    backend_type = (
        OllamaPlannerBackend if settings.bridge_backend == "ollama" else LMStudioPlannerBackend
    )
    return backend_type(
        model=settings.bridge_model,
        output_model=BridgeModelOutput,
        system_instruction=instruction,
        transport=transport,
    )


async def serve_bridge(settings: BridgeHostSettings) -> None:
    """Serve until cancelled; all owned resources are closed deterministically."""

    backend = build_planner_backend(settings)
    host = JsonlBridgeHost(
        backend,
        max_concurrency=settings.bridge_max_concurrency,
    )
    server: asyncio.Server | None = None
    try:
        server = await host.start_server(
            host=settings.bridge_bind_host,
            port=settings.bridge_bind_port,
            ssl_context=build_server_ssl_context(settings),
        )
        async with server:
            await server.serve_forever()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        await host.close()


__all__ = ["build_planner_backend", "build_server_ssl_context", "serve_bridge"]
