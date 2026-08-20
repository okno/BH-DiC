"""Intent router facade plus a deterministic offline implementation."""

from __future__ import annotations

import re
from datetime import date
from typing import Protocol

from bh_dic.language import BotLanguageProfile
from bh_dic.openai.client import IntentClient, RoutedIntent
from bh_dic.openai.redaction import prepare_provider_input
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, RouteMetadata, Sensitivity


class IntentRouter(Protocol):
    async def route(self, request: str, allowed_function_ids: frozenset[str]) -> RoutedIntent: ...

    async def close(self) -> None: ...


class OpenAIIntentRouter:
    """Provider-neutral router facade; name retained for import compatibility."""

    def __init__(self, client: IntentClient) -> None:
        self._client = client

    async def route(self, request: str, allowed_function_ids: frozenset[str]) -> RoutedIntent:
        minimized = prepare_provider_input(request)
        return await self._client.route(minimized, allowed_function_ids)

    async def close(self) -> None:
        await self._client.close()


_EMPLOYEE_ID = re.compile(
    r"(?i)(?:employee\s*id|dipendente\s*(?:id)?|id)\s*[:#]?\s*([A-Za-z0-9_-]{1,64})"
)


def _september_range(text: str, today: date) -> tuple[date | None, date | None]:
    if "settembre" not in text.casefold():
        return None, None
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else today.year
    if not year_match and date(year, 9, 30) < today:
        year += 1
    return date(year, 9, 1), date(year, 9, 30)


class MockIntentRouter:
    """Deterministic router for tests and setup checks without provider access."""

    def __init__(self, profile: BotLanguageProfile | None = None) -> None:
        self._profile = profile

    def _unsupported_question(self) -> str:
        if self._profile is not None and self._profile.language == "en":
            return "The request does not match an authorized function."
        return "La richiesta non corrisponde a una funzione autorizzata."

    def _employee_id_question(self) -> str:
        if self._profile is None:
            return "Indica l'Employee ID esatto."
        if self._profile.language == "en":
            return "Provide the exact Employee ID."
        if self._profile.address_style == "tu":
            return "Indica l'Employee ID esatto."
        if self._profile.address_style == "lei":
            return "Indichi l'Employee ID esatto."
        return "È necessario indicare l'Employee ID esatto."

    async def route(self, request: str, allowed_function_ids: frozenset[str]) -> RoutedIntent:
        text = prepare_provider_input(request)
        lowered = text.casefold()
        employee_match = _EMPLOYEE_ID.search(text)
        employee_id = employee_match.group(1) if employee_match else None
        date_from, date_to = _september_range(text, date.today())

        candidates: list[tuple[str, str, ActionClass, Sensitivity]] = [
            (
                "employee_headcount",
                "EMP-READ-001",
                ActionClass.READ,
                Sensitivity.LOW,
            ),
            (
                "employment_contract",
                "EMP-CONTRACT-001",
                ActionClass.READ,
                Sensitivity.MEDIUM,
            ),
            (
                "employee_search",
                "EMP-SEARCH-001",
                ActionClass.SEARCH,
                Sensitivity.MEDIUM,
            ),
            (
                "employee_records",
                "EMP-READ-002" if employee_id else "EMP-READ-001",
                ActionClass.READ,
                Sensitivity.MEDIUM,
            ),
            ("elimin", "EMP-DELETE-001", ActionClass.PREPARE_WRITE, Sensitivity.CRITICAL),
            ("disattiv", "EMP-STATUS-001", ActionClass.PREPARE_WRITE, Sensitivity.CRITICAL),
            ("riattiv", "EMP-STATUS-002", ActionClass.PREPARE_WRITE, Sensitivity.HIGH),
            ("modifica", "EMP-UPDATE-001", ActionClass.PREPARE_WRITE, Sensitivity.HIGH),
            ("carica", "EMP-DOC-002", ActionClass.FILE_UPLOAD, Sensitivity.HIGH),
            ("document", "EMP-DOC-001", ActionClass.READ, Sensitivity.HIGH),
            ("bilanc", "EMP-BAL-001", ActionClass.READ, Sensitivity.HIGH),
            ("matur", "EMP-MAT-001", ActionClass.READ, Sensitivity.MEDIUM),
            ("ruol", "EMP-RBAC-001", ActionClass.READ, Sensitivity.HIGH),
            ("bust", "EMP-PAY-001", ActionClass.READ, Sensitivity.HIGH),
            ("contratt", "EMP-CONTRACT-001", ActionClass.READ, Sensitivity.MEDIUM),
            ("cerc", "EMP-SEARCH-001", ActionClass.SEARCH, Sensitivity.MEDIUM),
            ("quanti", "EMP-READ-001", ActionClass.READ, Sensitivity.LOW),
            ("elenco", "EMP-READ-001", ActionClass.READ, Sensitivity.MEDIUM),
            (
                "dipendent",
                "EMP-READ-002" if employee_id else "EMP-READ-001",
                ActionClass.READ,
                Sensitivity.MEDIUM,
            ),
        ]
        selected = next((item for item in candidates if item[0] in lowered), None)
        if selected is None or selected[1] not in allowed_function_ids:
            envelope = IntentEnvelope(
                intent="unsupported",
                function_id="UNSUPPORTED",
                action_class=ActionClass.UNSUPPORTED,
                employee_id=None,
                query=text,
                parameters={},
                date_from=None,
                date_to=None,
                requires_clarification=True,
                clarification_question=self._unsupported_question(),
                sensitivity=Sensitivity.LOW,
                confidence=0.0,
            )
            tool_name = "unsupported_request"
        else:
            _, function_id, action_class, sensitivity = selected
            requires_id = action_class in {ActionClass.PREPARE_WRITE, ActionClass.FILE_UPLOAD}
            needs_clarification = (
                requires_id and employee_id is None and function_id != "EMP-CREATE-001"
            )
            envelope = IntentEnvelope(
                intent=function_id.lower().replace("-", "_"),
                function_id=function_id,
                action_class=action_class,
                employee_id=employee_id,
                query=text if function_id == "EMP-SEARCH-001" else None,
                parameters={},
                date_from=date_from,
                date_to=date_to,
                requires_clarification=needs_clarification,
                clarification_question=self._employee_id_question()
                if needs_clarification
                else None,
                sensitivity=sensitivity,
                confidence=1.0,
            )
            tool_name = "mock_rule"
        return RoutedIntent(
            envelope=envelope,
            metadata=RouteMetadata(provider="mock", model="deterministic", tool_name=tool_name),
        )

    async def close(self) -> None:
        return None
