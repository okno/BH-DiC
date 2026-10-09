"""Provider-neutral planner backend boundary."""

from __future__ import annotations

from typing import Protocol

from bh_dic.bridge.contracts import BackendResult, BridgeRequest


class PlannerBackend(Protocol):
    """A backend may classify/plan only; it receives no operational capability."""

    async def plan(self, request: BridgeRequest) -> BackendResult: ...

    async def close(self) -> None: ...


__all__ = ["PlannerBackend"]
