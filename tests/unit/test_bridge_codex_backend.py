from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from openai_codex import ApprovalMode, CodexConfig, Sandbox

from bh_dic.bridge.backends.codex import CodexPlannerBackend, CodexSession
from bh_dic.bridge.contracts import BoundedJsonObject, BridgeRequest
from bh_dic.bridge.errors import BackendOutputError
from bh_dic.bridge.model_output import BridgeModelOutput

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000003")


def _decision() -> dict[str, object]:
    return {
        "kind": "unsupported",
        "code": "outside_catalog",
        "reason": "La funzione non appartiene al catalogo autorizzato.",
    }


class _Thread:
    def __init__(self, *, item_type: str = "agentMessage") -> None:
        self.item_type = item_type
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run(self, input: str, **kwargs: object) -> object:
        self.calls.append((input, kwargs))
        usage = SimpleNamespace(
            last=SimpleNamespace(input_tokens=25, output_tokens=7, total_tokens=32)
        )
        return SimpleNamespace(
            final_response=json.dumps(_decision()),
            items=[SimpleNamespace(type=self.item_type)],
            usage=usage,
        )


class _Session(AbstractAsyncContextManager[CodexSession]):
    def __init__(self, thread: _Thread) -> None:
        self.thread = thread
        self.thread_start_kwargs: dict[str, object] = {}

    async def __aenter__(self) -> CodexSession:
        return self  # type: ignore[return-value]

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def thread_start(self, **kwargs: object) -> _Thread:
        self.thread_start_kwargs = kwargs
        return self.thread


class _Factory:
    def __init__(self, thread: _Thread) -> None:
        self.session = _Session(thread)
        self.configs: list[CodexConfig] = []

    def __call__(self, config: CodexConfig) -> _Session:
        self.configs.append(config)
        return self.session


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _backend(tmp_path: Path, factory: _Factory) -> CodexPlannerBackend:
    state = _private_directory(tmp_path / "state")
    work = _private_directory(state / "work")
    return CodexPlannerBackend(
        state_root=state,
        isolated_work_root=work,
        executable=Path("/usr/bin/true"),
        output_model=BridgeModelOutput,
        system_instruction="Synthetic closed planner instruction.",
        session_factory=factory,
    )


def _request() -> BridgeRequest:
    return BridgeRequest(
        request_id=REQUEST_ID,
        nonce="A" * 32,
        issued_at_ms=1_000,
        deadline_ms=11_000,
        payload=BoundedJsonObject.model_validate(
            {"task": "hr_planning", "request": "employee_headcount"}
        ),
    )


@pytest.mark.asyncio
async def test_codex_backend_uses_app_server_ephemeral_deny_all_and_clean_environment(
    tmp_path: Path,
) -> None:
    thread = _Thread()
    factory = _Factory(thread)
    backend = _backend(tmp_path, factory)

    result = await backend.plan(_request())

    assert result.payload.root == _decision()
    assert result.usage is not None
    assert result.usage.total_tokens == 32
    config = factory.configs[0]
    assert config.launch_args_override is not None
    argv = config.launch_args_override
    assert argv[:2] == ("/usr/bin/env", "-i")
    assert "shell_tool" in argv
    assert argv[-3:] == ("app-server", "--listen", "stdio://")
    assert factory.session.thread_start_kwargs["approval_mode"] is ApprovalMode.deny_all
    assert factory.session.thread_start_kwargs["sandbox"] is Sandbox.read_only
    assert factory.session.thread_start_kwargs["ephemeral"] is True
    _, run_kwargs = thread.calls[0]
    assert run_kwargs["approval_mode"] is ApprovalMode.deny_all
    assert run_kwargs["sandbox"] is Sandbox.read_only
    assert isinstance(run_kwargs["output_schema"], dict)
    assert "public_hr_response" not in json.dumps(run_kwargs["output_schema"])


@pytest.mark.asyncio
async def test_codex_backend_fails_closed_if_any_tool_item_completed(tmp_path: Path) -> None:
    backend = _backend(tmp_path, _Factory(_Thread(item_type="commandExecution")))

    with pytest.raises(BackendOutputError, match="unsupported operation"):
        await backend.plan(_request())


def test_codex_backend_rejects_state_inside_home_or_repository(tmp_path: Path) -> None:
    factory = _Factory(_Thread())
    with pytest.raises(ValueError):
        CodexPlannerBackend(
            state_root=Path.home(),
            isolated_work_root=tmp_path,
            executable=Path("/usr/bin/true"),
            output_model=BridgeModelOutput,
            system_instruction="Synthetic instruction.",
            session_factory=factory,
        )
