"""Application-level vertical slice shared by every Discord interaction mode.

The model produces only an intent candidate.  This module repeats policy checks,
dispatches reads to deterministic services, prepares writes, and appends audit
events.  No provider object can reach the browser adapter.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from pydantic import JsonValue

from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.approvals.service import ApprovalService
from bh_dic.approvals.storage import ApprovalRepository
from bh_dic.audit.models import AuditEventInput, AuditOutcome
from bh_dic.audit.service import AuditService
from bh_dic.dic.auth import DicAuthOutcomeUnknownError, DicAuthStage
from bh_dic.dic.errors import (
    DicAmbiguousWriteOutcomeError,
    DicNotFoundError,
    DicReconciliationRequiredError,
    DicValidationError,
)
from bh_dic.dic.models import (
    DocumentQuery,
    EmployeeFilter,
    EmployeeListItem,
    EmployeeListQuery,
    FunctionId,
    PayrollMetadata,
    ReconciliationState,
    SessionState,
    SessionStatus,
    SortDirection,
)
from bh_dic.dic.route_registry import DIC_ROUTES, RouteVerificationState
from bh_dic.dic.values import canonical_decimal_text
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import (
    AttachmentPayload,
    InteractionCoordinator,
    InteractionResult,
    ResponseAttachment,
    ResponseSensitivity,
    ResultField,
)
from bh_dic.errors import ApplicationError as ApplicationError
from bh_dic.errors import ApplicationPolicyDenied as ApplicationPolicyDenied
from bh_dic.exports import EmployeeExportRow, ExportFormat, GeneratedExport, HrExportService
from bh_dic.exports.service import ascii_table, split_ascii_for_discord
from bh_dic.files.models import UploadStatus
from bh_dic.files.service import FileService, UploadResolutionError
from bh_dic.hr_assistant import (
    SeniorHrPresenter,
    is_capabilities_request,
    is_employee_aggregate_request,
    local_contract_expiry_fallback_interval,
    local_employee_search_query,
    minimize_hr_router_request,
    normalize_hr_intent,
    parse_local_operational_intent,
)
from bh_dic.language import BotLanguageProfile
from bh_dic.logging import get_logger
from bh_dic.model_usage import (
    ModelUsageKey,
    ModelUsageService,
    ModelUsageStart,
    ModelUsageStatus,
    ModelUsageTotals,
)
from bh_dic.openai.client import (
    IntentProviderError,
    ProviderFailureKind,
    RoutedIntent,
)
from bh_dic.openai.intent_router import IntentRouter
from bh_dic.openai.redaction import prepare_provider_input, redact_structure
from bh_dic.openai.schemas import ActionClass, IntentEnvelope, RouteMetadata
from bh_dic.policies.catalog import (
    FUNCTION_CATALOG,
    FunctionSpec,
    ResourceSnapshotKind,
    WriteParameterValidationError,
    get_function_spec,
    validate_write_parameters,
)
from bh_dic.policies.decisions import DecisionCode, PolicyDecision
from bh_dic.policies.engine import PolicyContext, PolicyEngine, PolicyPhase
from bh_dic.policies.feature_flags import FeatureFlags
from bh_dic.policies.roles import LogicalRole, normalize_roles
from bh_dic.query.context import ConversationContext, ConversationContextStore, ConversationKey
from bh_dic.query.plan import FilterOperator, HRQueryPlan
from bh_dic.query.planner import build_local_hr_query_plan
from bh_dic.security.cipher import PayloadCipher
from bh_dic.security.pii import pseudonymize_identifier
from bh_dic.security.sanitization import InputValidationError, normalize_text
from bh_dic.services.dic_service import DicService

logger = get_logger("application")


@dataclass(frozen=True, slots=True)
class ApplicationScope:
    allowed_guild_ids: frozenset[str]
    allowed_channel_ids: frozenset[str]
    current_tenant_id: str
    allowed_tenant_ids: frozenset[str]
    capabilities: frozenset[str] = frozenset()
    mock_mode: bool = False


RequesterActorResolver = Callable[[PendingAction], Awaitable[DiscordActor]]
TodayProvider = Callable[[], date]
DicReconnectHandler = Callable[[], Awaitable[SessionStatus]]
_MAX_INLINE_ASCII_CHUNKS = 10
_DIC_RECONNECT_FUNCTION_ID = "DIC-RECONNECT"


class BHApplicationCoordinator(InteractionCoordinator):
    """Policy-first coordinator implementing the full read/preview path."""

    def __init__(
        self,
        *,
        router: IntentRouter,
        policy: PolicyEngine,
        flags: FeatureFlags,
        dic: DicService,
        scope: ApplicationScope,
        pseudonym_key: bytes,
        audit: AuditService | None = None,
        approvals: ApprovalService | None = None,
        approval_repository: ApprovalRepository | None = None,
        payload_cipher: PayloadCipher | None = None,
        files: FileService | None = None,
        exports: HrExportService | None = None,
        response_attachment_max_bytes: int = 8 * 1024 * 1024,
        requester_actor_resolver: RequesterActorResolver | None = None,
        language_profile: BotLanguageProfile | None = None,
        today_provider: TodayProvider | None = None,
        model_usage: ModelUsageService | None = None,
        model_provider: str = "unconfigured",
        model_name: str = "unconfigured",
        dic_reconnect_handler: DicReconnectHandler | None = None,
        conversation_context: ConversationContextStore | None = None,
    ) -> None:
        if len(pseudonym_key) < 32:
            raise ValueError("pseudonym key must contain at least 32 bytes")
        if response_attachment_max_bytes < 1:
            raise ValueError("response attachment limit must be positive")
        self.router = router
        self.policy = policy
        self.flags = flags
        self.dic = dic
        self.scope = scope
        self.audit = audit
        self.approvals = approvals
        self.approval_repository = approval_repository
        self.payload_cipher = payload_cipher
        self.files = files
        self.exports = exports
        self._response_attachment_max_bytes = response_attachment_max_bytes
        self.requester_actor_resolver = requester_actor_resolver
        self._presenter = SeniorHrPresenter(language_profile)
        self._today = today_provider or date.today
        self._model_usage = model_usage
        self._model_provider = model_provider
        self._model_name = model_name
        self._dic_reconnect_handler = dic_reconnect_handler
        self._conversation_context = conversation_context or ConversationContextStore()
        self._pseudonym_key = bytes(pseudonym_key)
        self._dic_reconnect_lock = asyncio.Lock()
        self._dic_reconnect_outcome_unknown = False
        self._global_write_lock = asyncio.Lock()
        self._target_write_locks: dict[str, asyncio.Lock] = {}

    async def ask(self, actor: DiscordActor, request: str) -> InteractionResult:
        correlation_id = str(uuid.uuid4())
        request_today = self._today()
        normalized_request = normalize_text(request, max_length=2_000, allow_newlines=True)
        conversation_key = ConversationKey(actor.user_id, actor.guild_id, actor.channel_id)
        context_selection = self._conversation_context.selection(
            conversation_key, normalized_request
        )
        context_intent: IntentEnvelope | None = None
        if context_selection is not None:
            selected_employee_id, remembered = context_selection
            context_intent = self._context_followup_intent(
                normalized_request,
                selected_employee_id,
                remembered,
            )
        if is_capabilities_request(normalized_request):
            return await self.capabilities(actor)
        exposure_context = self._context(
            actor,
            "EMP-READ-001",
            phase=PolicyPhase.EXPOSURE,
            operation_scope="aggregate",
        )
        visible = self.policy.visible_function_ids(exposure_context)
        planned = (
            None
            if context_intent is not None
            else build_local_hr_query_plan(normalized_request, today=request_today)
        )
        if planned is not None and len(planned.plan.steps) > 1:
            result = await self._execute_query_plan(
                actor,
                correlation_id,
                planned.plan,
            )
            return await self._with_request_usage(result, correlation_id)
        # The query-plan registry currently contains read-only capabilities. Preserve the
        # existing deterministic write/export parser until those workflows have their own
        # separately validated plan model; never send a locally recognized write target to
        # the provider merely because it is absent from the read planner.
        local = (
            planned.legacy_intent
            if planned is not None
            else parse_local_operational_intent(normalized_request, today=request_today)
        )
        target_query: str | None = None
        if context_intent is not None:
            intent = context_intent
            routed = RoutedIntent(
                envelope=context_intent,
                metadata=RouteMetadata(
                    provider="local",
                    model="deterministic",
                    tool_name="conversation_context",
                ),
            )
        elif local is not None:
            routed = RoutedIntent(
                envelope=local.envelope,
                metadata=RouteMetadata(
                    provider="local",
                    model="deterministic",
                    tool_name="closed_hr_parser",
                ),
            )
            target_query = local.target_query
            intent = local.envelope
        else:
            prepared_request = prepare_provider_input(normalized_request)
            provider_request, explicit_employee_id = minimize_hr_router_request(prepared_request)
            local_search_query = local_employee_search_query(prepared_request)
            contract_expiry_fallback_interval = None
            provider_visible = visible
            if "EMP-CONTRACT-001" in visible:
                contract_expiry_fallback_interval = local_contract_expiry_fallback_interval(
                    prepared_request,
                    provider_request,
                    today=request_today,
                )
                if contract_expiry_fallback_interval is not None:
                    # Narrow only this exact locally recognized read subset.
                    provider_visible = frozenset({"EMP-CONTRACT-001"})
            usage_key = ModelUsageKey(
                correlation_id=correlation_id,
                purpose="intent_route",
                ordinal=1,
            )
            if self._model_usage is not None:
                await self._model_usage.start(
                    ModelUsageStart(
                        key=usage_key,
                        provider=self._model_provider,
                        model=self._model_name,
                    )
                )
            usage_completed = False
            try:
                routed = await self.router.route(provider_request, provider_visible)
            except IntentProviderError as exc:
                if self._model_usage is not None:
                    await self._model_usage.complete(
                        usage_key,
                        response_received=exc.response_received,
                        usage=exc.usage,
                    )
                    usage_completed = True
                if (
                    self._model_provider != "groq"
                    or exc.provider != "groq"
                    or exc.failure_kind is not ProviderFailureKind.TOOL_USE_FAILED
                    or not exc.response_received
                    or contract_expiry_fallback_interval is None
                ):
                    raise
                routed = RoutedIntent(
                    envelope=self._direct_intent(
                        "EMP-CONTRACT-001",
                        date_from=contract_expiry_fallback_interval[0],
                        date_to=contract_expiry_fallback_interval[1],
                    ),
                    metadata=RouteMetadata(
                        provider="local_fallback",
                        model="deterministic",
                        tool_name="get_contracts",
                    ),
                )
            except asyncio.CancelledError:
                if self._model_usage is not None:
                    try:
                        await asyncio.shield(
                            self._model_usage.complete(
                                usage_key,
                                response_received=False,
                                usage=None,
                            )
                        )
                    except (asyncio.CancelledError, Exception):
                        logger.warning("intent_usage_completion_failed_during_cancellation")
                raise
            except Exception:
                if self._model_usage is not None:
                    await self._model_usage.complete(
                        usage_key,
                        response_received=False,
                        usage=None,
                    )
                raise
            if self._model_usage is not None and not usage_completed:
                await self._model_usage.complete(
                    usage_key,
                    response_received=True,
                    usage=routed.metadata.usage,
                )
            # Provider identity/search fields are never trusted. Restore only local values.
            trusted_updates: dict[str, object] = {
                "employee_id": explicit_employee_id,
                "query": None,
            }
            if routed.envelope.function_id == "EMP-SEARCH-001":
                trusted_updates["query"] = local_search_query
            routed_envelope = routed.envelope.model_copy(update=trusted_updates)
            intent = normalize_hr_intent(
                routed_envelope,
                normalized_request,
                today=request_today,
            )
        if intent.requires_clarification:
            await self._audit(
                actor,
                correlation_id,
                "intent.clarification",
                intent.function_id if intent.function_id != "UNSUPPORTED" else None,
                AuditOutcome.DENIED,
                intent.employee_id,
                {"provider": routed.metadata.provider},
            )
            result = InteractionResult(
                title="Chiarimento necessario",
                description=intent.clarification_question or "Specifica meglio la richiesta.",
                correlation_id=correlation_id,
                success=False,
            )
            return await self._with_request_usage(result, correlation_id)
        if intent.function_id == "UNSUPPORTED":
            result = InteractionResult(
                title="Funzione non disponibile",
                description="La richiesta non corrisponde a una funzione autorizzata.",
                correlation_id=correlation_id,
                success=False,
                public_hr_fallback=True,
            )
            return await self._with_request_usage(result, correlation_id)

        resolved_item: EmployeeListItem | None = None
        if target_query is not None and intent.function_id in visible:
            resolved = await self._resolve_employee_target(
                target_query,
                correlation_id,
                actor=actor,
                function_id=intent.function_id,
                parameters=intent.parameters,
            )
            if isinstance(resolved, InteractionResult):
                return await self._with_request_usage(resolved, correlation_id)
            resolved_item = resolved
            intent = intent.model_copy(update={"employee_id": resolved.employee_id})

        scope = self._operation_scope(intent, normalized_request)
        decision = self.policy.evaluate(
            self._context(
                actor,
                intent.function_id,
                target_employee_id=intent.employee_id,
                operation_scope=scope,
            )
        )
        if not decision.allowed:
            await self._audit_denial(actor, correlation_id, intent, decision)
            raise ApplicationPolicyDenied(decision, correlation_id)

        if resolved_item is not None and intent.function_id in {
            "EMP-STATUS-001",
            "EMP-STATUS-002",
        }:
            desired = "inactive" if intent.function_id == "EMP-STATUS-001" else "active"
            if resolved_item.employee_state.value == desired:
                result = InteractionResult(
                    title="Nessuna modifica necessaria",
                    description=(
                        f"{self._employee_display_name(resolved_item)}, ID "
                        f"{resolved_item.employee_id}, risulta già {desired}."
                    ),
                    correlation_id=correlation_id,
                )
                return await self._with_request_usage(result, correlation_id)

        if intent.action_class in {
            ActionClass.PREPARE_WRITE,
            ActionClass.FILE_UPLOAD,
            ActionClass.EXPORT,
        }:
            result = await self._prepare_write(actor, correlation_id, intent)
        else:
            result = await self._dispatch_read(actor, correlation_id, intent, scope)
        await self._audit(
            actor,
            correlation_id,
            "request.completed",
            intent.function_id,
            AuditOutcome.PENDING if result.action_id else AuditOutcome.SUCCESS,
            intent.employee_id,
            {"provider": routed.metadata.provider},
        )
        return await self._with_request_usage(result, correlation_id)

    async def _execute_query_plan(
        self,
        actor: DiscordActor,
        correlation_id: str,
        plan: HRQueryPlan,
    ) -> InteractionResult:
        """Execute only locally implemented, read-only multi-step plan shapes."""

        if plan.intent != "contract_expiry_payroll_comparison" or len(plan.steps) != 3:
            raise ApplicationError("validated HR query plan is not locally executable")
        required_functions = tuple(dict.fromkeys(step.function_id for step in plan.steps))
        for function_id in required_functions:
            decision = self.policy.evaluate(self._context(actor, function_id))
            if not decision.allowed:
                denied = self._direct_intent(function_id)
                await self._audit_denial(actor, correlation_id, denied, decision)
                raise ApplicationPolicyDenied(decision, correlation_id)
        if plan.date_range is None:
            raise ApplicationError("compound contract plan requires a date range")

        filters = {item.field: item for item in plan.filters}
        month_filter = filters.get("payroll_month")
        year_filter = filters.get("payroll_year")
        payroll_filter = filters.get("payroll")
        if (
            month_filter is None
            or year_filter is None
            or payroll_filter is None
            or type(month_filter.value) is not int
            or type(year_filter.value) is not int
            or payroll_filter.operator not in {FilterOperator.EXISTS, FilterOperator.NOT_EXISTS}
        ):
            raise ApplicationError("compound payroll filters are incomplete")
        month = month_filter.value
        year = year_filter.value
        if not 1 <= month <= 12 or not 2000 <= year <= 2200:
            raise ApplicationError("compound payroll period is invalid")

        employees = await self.dic.list_all_employees(
            EmployeeListQuery(employee_filter=EmployeeFilter.ALL),
            max_records=500,
        )
        group_filter = filters.get("group")
        group_value = (
            str(group_filter.value).strip().casefold() if group_filter is not None else None
        )
        candidates = tuple(
            employee
            for employee in employees.items
            if (
                employee.current_contract_valid_to is not None
                and plan.date_range.date_from
                <= employee.current_contract_valid_to
                <= plan.date_range.date_to
                and (
                    group_value is None
                    or group_value in (employee.group_name or "").strip().casefold()
                )
            )
        )

        matches: list[tuple[EmployeeListItem, bool]] = []
        for employee in candidates:
            payrolls = await self.dic.get_payroll_metadata(employee.employee_id, year)
            if any(
                record.employee_id != employee.employee_id or record.year != year
                for record in payrolls
            ):
                raise ApplicationError("payroll resource identity changed during query plan")
            available = any(record.month == month for record in payrolls)
            wanted = (
                not available if payroll_filter.operator is FilterOperator.NOT_EXISTS else available
            )
            if wanted:
                matches.append((employee, available))

        acquired_at = datetime.now(UTC)
        lines = ["Nome\tEmployee ID\tScadenza contratto\tBusta paga disponibile"]
        lines.extend(
            "\t".join(
                (
                    self._employee_display_name(employee),
                    employee.employee_id,
                    employee.current_contract_valid_to.isoformat()
                    if employee.current_contract_valid_to
                    else "",
                    "sì" if available else "no",
                )
            )
            for employee, available in matches
        )
        attachment_content = ("\n".join(lines) + "\n").encode("utf-8")
        if len(attachment_content) > self._response_attachment_max_bytes:
            raise ApplicationError("compound result exceeds the configured attachment limit")
        shown = matches[:25]
        group_description = f" · gruppo contiene: {group_value}" if group_value else ""
        result = InteractionResult(
            title="Contratti e buste paga — confronto completo",
            description=(
                f"Dipendenti completi acquisiti: {employees.total}; candidati con contratto in "
                f"scadenza: {len(candidates)}; risultati: {len(matches)}. "
                f"Periodo contratti: {plan.date_range.date_from.isoformat()} → "
                f"{plan.date_range.date_to.isoformat()}; periodo paga: {month:02d}/{year}"
                f"{group_description}. Mostrati nel messaggio: {len(shown)}; il dataset completo "
                f"è allegato. Acquisizione UTC: {acquired_at.isoformat()}. Completezza: COMPLETE."
            ),
            fields=tuple(
                ResultField(
                    f"{self._employee_display_name(employee)} · ID {employee.employee_id}",
                    (
                        f"Scadenza: {employee.current_contract_valid_to.isoformat()} · "
                        f"busta paga {month:02d}/{year}: {'presente' if available else 'assente'}"
                    ),
                )
                for employee, available in shown
                if employee.current_contract_valid_to is not None
            ),
            attachments=(
                ResponseAttachment(
                    filename=f"contratti_payroll_{year}_{month:02d}.tsv",
                    content_type="text/tab-separated-values; charset=utf-8",
                    content=attachment_content,
                ),
            ),
            correlation_id=correlation_id,
        )
        await self._audit(
            actor,
            correlation_id,
            "query_plan.completed",
            "EMP-PAY-002",
            AuditOutcome.SUCCESS,
            None,
            {
                "plan_intent": plan.intent,
                "step_count": len(plan.steps),
                "source_records": employees.total,
                "candidate_records": len(candidates),
                "result_records": len(matches),
                "complete": True,
            },
        )
        return result

    async def help(self, actor: DiscordActor) -> InteractionResult:
        context = self._context(
            actor,
            "EMP-READ-001",
            phase=PolicyPhase.EXPOSURE,
            operation_scope="aggregate",
        )
        visible = self.policy.visible_function_ids(context)
        lines = [
            f"`{function_id}` — {FUNCTION_CATALOG[function_id].title}"
            for function_id in sorted(visible)
        ]
        return InteractionResult(
            title="BH-DiC — funzioni autorizzate",
            description="\n".join(lines) or "Nessuna funzione disponibile per i ruoli correnti.",
            sensitivity=ResponseSensitivity.SENSITIVE,
        )

    async def capabilities(self, actor: DiscordActor) -> InteractionResult:
        """Render the authoritative catalog against current RBAC and feature flags."""

        rows: list[tuple[str, str, str]] = []
        for function_id, spec in sorted(FUNCTION_CATALOG.items()):
            # This is an application capability check, not a model-tool exposure check.
            # Evaluate every catalogued scope with a non-real placeholder target so hidden
            # local/operator routes remain visible without reading or changing DIC.
            decisions = tuple(
                self.policy.evaluate(
                    self._context(
                        actor,
                        function_id,
                        phase=PolicyPhase.PREPARE,
                        target_employee_id=("CAPABILITY-CHECK" if spec.requires_target else None),
                        operation_scope=scoped.scope,
                    )
                )
                for scoped in spec.role_rules
            )
            if not spec.operator_live_available:
                state = "NOT_AVAILABLE_LIVE"
            elif any(decision.allowed for decision in decisions):
                state = "AVAILABLE"
            elif any(decision.code is DecisionCode.FEATURE_DISABLED for decision in decisions):
                state = "DISABLED_BY_POLICY"
            elif decisions and all(
                decision.code is DecisionCode.ROLE_DENIED for decision in decisions
            ):
                state = "NOT_AUTHORIZED"
            else:
                state = "UNAVAILABLE"
            rows.append((function_id, spec.title, state))
        header = "Function ID | Funzione | Stato"
        body = "\n".join(f"{function_id} | {title} | {state}" for function_id, title, state in rows)
        matrix = f"{header}\n{'-' * len(header)}\n{body}\n"
        counts: dict[str, int] = {}
        for _function_id, _title, state in rows:
            counts[state] = counts.get(state, 0) + 1
        summary = " · ".join(f"{state}: {count}" for state, count in sorted(counts.items()))
        return InteractionResult(
            title="Matrice funzionalità Dipendenti in Cloud",
            description=(
                f"{summary}. AVAILABLE indica un percorso applicativo autorizzato, non una "
                "certificazione live dell'intera UI DiC; consulta l'allegato per tutte le righe."
            ),
            attachments=(
                ResponseAttachment(
                    filename="bh_dic_capabilities.txt",
                    content_type="text/plain; charset=utf-8",
                    content=matrix.encode("utf-8"),
                ),
            ),
        )

    async def status(self, actor: DiscordActor) -> InteractionResult:
        del actor
        write_enabled = self.flags.enabled("ENABLE_WRITE_ACTIONS")
        health = await self.dic.adapter.health()
        operational = health.ready and health.authenticated
        title, description = self._presenter.operational_status(operational)
        usage_fields = await self._model_status_fields()
        return InteractionResult(
            title=title,
            description=description,
            fields=(
                ResultField("Adapter", "READY" if operational else "DEGRADED", True),
                ResultField(
                    "Browser", "available" if health.browser_available else "mock/unavailable", True
                ),
                ResultField(
                    "DIC tenant",
                    "AUTHENTICATED" if health.authenticated else "UNAVAILABLE",
                    True,
                ),
                ResultField("Write kill switch", "ENABLED" if write_enabled else "DISABLED", True),
                *usage_fields,
            ),
            success=operational,
        )

    async def _with_request_usage(
        self,
        result: InteractionResult,
        correlation_id: str,
    ) -> InteractionResult:
        if self._model_usage is None:
            return result
        current = await self._model_usage.totals(correlation_id=correlation_id)
        cumulative = await self._model_usage.totals()
        usage_copy = (
            f"**Token AI — questa richiesta:** {self._format_request_usage(current)}\n"
            f"**Token AI — cumulativo locale:** {self._format_cumulative_usage(cumulative)}"
        )
        return replace(
            result,
            # Discord supports at most 25 fields. Keeping usage in the bounded description makes
            # the counters visible even when an HR result already fills every data-field slot.
            description=f"{result.description}\n\n{usage_copy}",
        )

    async def _model_status_fields(self) -> tuple[ResultField, ...]:
        provider_value = f"{self._model_provider} · {self._model_name}"
        if self._model_usage is None:
            return ()
        totals = await self._model_usage.totals()
        latest = await self._model_usage.latest()
        if latest is None:
            api_state = "NESSUNA CHIAMATA OSSERVATA IN QUESTO DATABASE"
        else:
            observed_at = latest.completed_at or latest.created_at
            last = observed_at.isoformat(timespec="seconds")
            api_state = {
                ModelUsageStatus.STARTED: "ULTIMA CHIAMATA SENZA ESITO TERMINALE",
                ModelUsageStatus.REPORTED: "ULTIMA RISPOSTA CON CONTATORI",
                ModelUsageStatus.UNAVAILABLE: "ULTIMA RISPOSTA SENZA CONTATORI",
                ModelUsageStatus.UNKNOWN: "ULTIMO ESITO REMOTO NON DETERMINABILE",
            }[latest.status]
            api_state = f"{api_state} · {last}"
        return (
            ResultField("Bot Discord", "ONLINE", True),
            ResultField("Provider e modello AI", provider_value, False),
            ResultField("API AI", api_state, False),
            ResultField("Token AI", self._format_cumulative_usage(totals), False),
        )

    @staticmethod
    def _format_request_usage(totals: ModelUsageTotals) -> str:
        if totals.reported_calls:
            usage = totals.usage
            return (
                f"input {usage.input_tokens} · output {usage.output_tokens} · "
                f"totale {usage.total_tokens}"
            )
        if totals.unavailable_calls:
            return "Il provider ha completato la risposta senza contatori disponibili."
        if totals.unknown_calls:
            return "Esito remoto non determinabile; nessuna stima locale applicata."
        return "Contatori non disponibili."

    @staticmethod
    def _format_cumulative_usage(totals: ModelUsageTotals) -> str:
        if totals.total_calls == 0:
            return "Nessuna chiamata registrata in questo database."
        since = (
            totals.first_recorded_at.date().isoformat()
            if totals.first_recorded_at is not None
            else "data non disponibile"
        )
        gaps = totals.started_calls + totals.unavailable_calls + totals.unknown_calls
        usage = totals.usage
        return (
            f"Dal {since}: {totals.total_calls} chiamate · input {usage.input_tokens} · "
            f"output {usage.output_tokens} · totale {usage.total_tokens} · "
            f"contatori mancanti/incerti {gaps}. Non equivale alla fatturazione provider."
        )

    async def health(self, actor: DiscordActor) -> InteractionResult:
        del actor
        health = await self.dic.adapter.health()
        operational = health.ready and health.authenticated
        return InteractionResult(
            title="Health check",
            description=health.detail,
            fields=(
                ResultField("Ready", str(health.ready), True),
                ResultField("Authenticated", str(health.authenticated), True),
            ),
            success=operational,
        )

    @staticmethod
    def _require_diagnostics_admin(actor: DiscordActor) -> None:
        roles = normalize_roles(actor.logical_roles)
        if not roles.intersection({LogicalRole.SECURITY_ADMIN, LogicalRole.SYSTEM_ADMIN}):
            raise ApplicationPolicyDenied(
                PolicyDecision.deny(
                    "DIC-DIAGNOSTICS",
                    DecisionCode.ROLE_DENIED,
                    "diagnostics require an administrative role",
                )
            )

    async def diagnostics(self, actor: DiscordActor) -> InteractionResult:
        self._require_diagnostics_admin(actor)
        health = await self.dic.health()
        routes = DIC_ROUTES.snapshot()
        degraded = sum(
            route.verification is RouteVerificationState.DEGRADED_SCHEMA for route in routes
        )
        return InteractionResult(
            title="Diagnostica DIC redatta",
            description=(
                "Nessun payload, URL identificativo o dato dipendente è incluso. "
                f"Route registrate: {len(routes)}; schemi degradati: {degraded}."
            ),
            fields=(
                ResultField("Adapter", "READY" if health.ready else "DEGRADED", True),
                ResultField(
                    "Tenant", "AUTHENTICATED" if health.authenticated else "UNAVAILABLE", True
                ),
                ResultField(
                    "Browser", "available" if health.browser_available else "unavailable", True
                ),
            ),
        )

    async def coverage(self, actor: DiscordActor) -> InteractionResult:
        self._require_diagnostics_admin(actor)
        routes = DIC_ROUTES.snapshot()
        states: dict[RouteVerificationState, int] = {}
        for route in routes:
            states[route.verification] = states.get(route.verification, 0) + 1
        return InteractionResult(
            title="Copertura read DIC",
            description=f"Route inventariate: {len(routes)}. Stato derivato dal registro runtime.",
            fields=tuple(
                ResultField(state.value, str(count), True)
                for state, count in sorted(states.items(), key=lambda item: item[0].value)
            ),
        )

    async def route_status(self, actor: DiscordActor) -> InteractionResult:
        self._require_diagnostics_admin(actor)
        routes = DIC_ROUTES.snapshot()
        return InteractionResult(
            title="Stato route DIC",
            description=f"Mostrate {len(routes)} route autorizzate su {len(routes)}.",
            fields=tuple(ResultField(route.name, route.verification.value) for route in routes),
        )

    async def schema_status(self, actor: DiscordActor) -> InteractionResult:
        self._require_diagnostics_admin(actor)
        routes = tuple(
            route
            for route in DIC_ROUTES.snapshot()
            if route.verification is RouteVerificationState.DEGRADED_SCHEMA
        )
        return InteractionResult(
            title="Stato schemi DIC",
            description=(
                "Nessuno schema degradato."
                if not routes
                else f"Schemi da rivalidare: {len(routes)}; le altre risorse restano isolate."
            ),
            fields=tuple(ResultField(route.name, route.verification.value) for route in routes),
            success=not routes,
        )

    async def reconnect_dic(self, actor: DiscordActor) -> InteractionResult:
        """Re-establish one attested DIC session without exposing credentials to Discord."""

        correlation_id = str(uuid.uuid4())
        roles = normalize_roles(actor.logical_roles)
        if not roles & {LogicalRole.SECURITY_ADMIN, LogicalRole.SYSTEM_ADMIN}:
            decision = PolicyDecision.deny(
                _DIC_RECONNECT_FUNCTION_ID,
                DecisionCode.ROLE_DENIED,
                "DIC reconnect requires SECURITY_ADMIN or SYSTEM_ADMIN",
            )
            await self._audit(
                actor,
                correlation_id,
                "dic.reconnect.denied",
                _DIC_RECONNECT_FUNCTION_ID,
                AuditOutcome.DENIED,
                None,
                {"decision": decision.code.value},
            )
            raise ApplicationPolicyDenied(decision, correlation_id)
        if not self.flags.enabled("ENABLE_DIC_RECONNECT"):
            decision = PolicyDecision.deny(
                _DIC_RECONNECT_FUNCTION_ID,
                DecisionCode.FEATURE_DISABLED,
                "DIC reconnect is disabled",
            )
            await self._audit(
                actor,
                correlation_id,
                "dic.reconnect.denied",
                _DIC_RECONNECT_FUNCTION_ID,
                AuditOutcome.DENIED,
                None,
                {"decision": decision.code.value},
            )
            raise ApplicationPolicyDenied(decision, correlation_id)
        if self._dic_reconnect_handler is None:
            return InteractionResult(
                title="Riconnessione DIC non disponibile",
                description="Il runtime non dispone di un percorso di login configurato.",
                correlation_id=correlation_id,
                success=False,
            )
        if self._dic_reconnect_lock.locked():
            return InteractionResult(
                title="Riconnessione DIC già in corso",
                description="Attendi il completamento del tentativo già avviato; non verrà "
                "eseguito un secondo invio delle credenziali.",
                correlation_id=correlation_id,
                success=False,
            )

        async with self._dic_reconnect_lock:
            health = await self.dic.adapter.health()
            if health.ready and health.authenticated:
                self._dic_reconnect_outcome_unknown = False
                await self._audit(
                    actor,
                    correlation_id,
                    "dic.reconnect.completed",
                    _DIC_RECONNECT_FUNCTION_ID,
                    AuditOutcome.SUCCESS,
                    None,
                    {"result": "ALREADY_AUTHENTICATED"},
                )
                return InteractionResult(
                    title="Sessione DIC già attiva",
                    description=(
                        "La sessione tenant è già autenticata e verificata; nessuna credenziale "
                        "è stata inviata."
                    ),
                    correlation_id=correlation_id,
                    fields=(ResultField("DIC tenant", "AUTHENTICATED", True),),
                )
            if self._dic_reconnect_outcome_unknown:
                return InteractionResult(
                    title="Esito login DIC da verificare",
                    description=(
                        "Il tentativo precedente ha avuto esito incerto. Per sicurezza il bot "
                        "non invia nuovamente le credenziali: verifica `/bh status` e la sessione "
                        "web, oppure riavvia il solo bot dopo la verifica amministrativa."
                    ),
                    correlation_id=correlation_id,
                    success=False,
                )

            await self._audit(
                actor,
                correlation_id,
                "dic.reconnect.started",
                _DIC_RECONNECT_FUNCTION_ID,
                AuditOutcome.PENDING,
                None,
                {"result": "LOGIN_REQUIRED"},
            )
            try:
                status = await self._dic_reconnect_handler()
                if status.state is not SessionState.AUTHENTICATED:
                    raise DicAuthOutcomeUnknownError(DicAuthStage.CREDENTIAL_SUBMIT)
            except asyncio.CancelledError:
                self._dic_reconnect_outcome_unknown = True
                await self._audit_reconnect_failure_without_masking(
                    actor,
                    correlation_id,
                    AuditOutcome.UNCERTAIN,
                )
                raise
            except DicAuthOutcomeUnknownError:
                self._dic_reconnect_outcome_unknown = True
                await self._audit_reconnect_failure_without_masking(
                    actor,
                    correlation_id,
                    AuditOutcome.UNCERTAIN,
                )
                raise
            except Exception:
                await self._audit_reconnect_failure_without_masking(
                    actor,
                    correlation_id,
                    AuditOutcome.FAILED,
                )
                raise

            self._dic_reconnect_outcome_unknown = False
            await self._audit(
                actor,
                correlation_id,
                "dic.reconnect.completed",
                _DIC_RECONNECT_FUNCTION_ID,
                AuditOutcome.SUCCESS,
                None,
                {"result": "AUTHENTICATED_AND_PERSISTED"},
            )
            return InteractionResult(
                title="Sessione DIC ripristinata",
                description=(
                    "Il login è stato completato, il tenant verificato e la nuova sessione "
                    "salvata nel vault cifrato."
                ),
                correlation_id=correlation_id,
                fields=(ResultField("DIC tenant", "AUTHENTICATED", True),),
            )

    async def _audit_reconnect_failure_without_masking(
        self,
        actor: DiscordActor,
        correlation_id: str,
        outcome: AuditOutcome,
    ) -> None:
        try:
            await asyncio.shield(
                self._audit(
                    actor,
                    correlation_id,
                    "dic.reconnect.failed",
                    _DIC_RECONNECT_FUNCTION_ID,
                    outcome,
                    None,
                    {"result": "NOT_VERIFIED"},
                )
            )
        except (asyncio.CancelledError, Exception):
            logger.warning("dic_reconnect_terminal_audit_failed")

    async def pending(self, actor: DiscordActor) -> InteractionResult:
        if self.approval_repository is None:
            return self._unavailable("Il workflow approvazioni non è configurato.")
        actions = await self.approval_repository.list_actions()
        roles = normalize_roles(actor.logical_roles)
        is_approver = bool(
            roles
            & {
                LogicalRole.APPROVER,
                LogicalRole.SYSTEM_ADMIN,
                LogicalRole.HR_WRITE,
                LogicalRole.IAM_OPERATOR,
            }
        )
        active = [
            action
            for action in actions
            if action.status
            in {ActionStatus.PENDING, ActionStatus.PARTIALLY_APPROVED, ActionStatus.APPROVED}
            and (action.requester_id == str(actor.user_id) or is_approver)
        ]
        fields = tuple(
            ResultField(
                action.action_id,
                f"{action.function_id} · {action.status.value} · "
                f"scade {action.expires_at.isoformat()}",
            )
            for action in active[:25]
        )
        return InteractionResult(
            title="Azioni pending",
            description=f"Totale visibile: {len(active)}",
            fields=fields,
        )

    async def approve(
        self,
        actor: DiscordActor,
        action_id: str,
        confirmation_code: str,
        target_confirmation: str | None = None,
    ) -> InteractionResult:
        if self.approvals is None or self.payload_cipher is None:
            return self._unavailable("Il workflow approvazioni non è configurato.")
        action = await self.approvals.get(action_id)
        if str(actor.user_id) == action.requester_id:
            action = await self.approvals.confirm(
                action_id,
                requester_id=str(actor.user_id),
                confirmation_code=confirmation_code,
                text_confirmation=target_confirmation,
            )
        else:
            if confirmation_code.strip().upper() != "APPROVE":
                raise ApplicationError("l'approvatore deve inserire APPROVE")
            action = await self.approvals.approve(
                action_id,
                approver_id=str(actor.user_id),
                approver_roles=frozenset(actor.logical_roles),
            )

        if action.status is ActionStatus.APPROVED:
            return await self._execute_approved(actor, action)
        return InteractionResult(
            title="Approvazione registrata",
            description=(
                f"Azione `{action.action_id}`: {len(action.approvals)}/"
                f"{action.approvals_required} approvazioni."
            ),
            action_id=action.action_id,
        )

    async def reject(self, actor: DiscordActor, action_id: str, reason: str) -> InteractionResult:
        if self.approvals is None:
            return self._unavailable("Il workflow approvazioni non è configurato.")
        action = await self.approvals.reject(
            action_id,
            actor_id=str(actor.user_id),
            actor_roles=frozenset(actor.logical_roles),
            reason=reason,
        )
        return InteractionResult(
            title="Azione rifiutata",
            description=f"`{action.action_id}` non sarà eseguita.",
        )

    async def upload(
        self,
        actor: DiscordActor,
        employee_id: str,
        category: str,
        attachment: AttachmentPayload,
    ) -> InteractionResult:
        correlation_id = str(uuid.uuid4())
        decision = self.policy.evaluate(
            self._context(actor, "EMP-DOC-002", target_employee_id=employee_id)
        )
        if not decision.allowed:
            raise ApplicationPolicyDenied(decision)
        if self.files is None:
            return self._unavailable("La quarantena file non è configurata.")
        if attachment.declared_size != len(attachment.content):
            raise ApplicationError("la dimensione dell'allegato non coincide")

        async def chunks() -> Any:
            yield attachment.content

        record = await self.files.ingest(
            original_filename=attachment.original_filename,
            claimed_mime=attachment.content_type,
            chunks=chunks(),
        )
        await self._audit(
            actor,
            correlation_id,
            "file.ingested",
            "EMP-DOC-002",
            AuditOutcome.SUCCESS if record.status is UploadStatus.CLEAN else AuditOutcome.DENIED,
            employee_id,
            {"upload_id": record.upload_id, "status": record.status.value},
        )
        if record.status is not UploadStatus.CLEAN:
            return InteractionResult(
                title="Allegato rifiutato",
                description=f"Stato: {record.status.value}; motivo: {record.rejection_reason}",
                correlation_id=correlation_id,
                success=False,
            )
        intent = IntentEnvelope(
            intent="prepare_document_upload",
            function_id="EMP-DOC-002",
            action_class=ActionClass.FILE_UPLOAD,
            employee_id=employee_id,
            query=None,
            parameters={
                "upload_id": record.upload_id,
                "category": category,
            },
            date_from=None,
            date_to=None,
            requires_clarification=False,
            clarification_question=None,
            sensitivity="HIGH",
            confidence=1.0,
        )
        return await self._prepare_write(actor, correlation_id, intent)

    async def employee(self, actor: DiscordActor, employee_id: str) -> InteractionResult:
        return await self._direct_read(actor, "EMP-READ-002", employee_id=employee_id)

    async def contracts(
        self,
        actor: DiscordActor,
        employee_id: str | None,
        expiring_from: date | None,
        expiring_to: date | None,
    ) -> InteractionResult:
        intent = self._direct_intent(
            "EMP-CONTRACT-001",
            employee_id=employee_id,
            date_from=expiring_from,
            date_to=expiring_to,
        )
        return await self._direct_read(actor, "EMP-CONTRACT-001", intent=intent)

    async def documents(
        self, actor: DiscordActor, employee_id: str, status: str | None
    ) -> InteractionResult:
        intent = self._direct_intent(
            "EMP-DOC-001",
            employee_id=employee_id,
            parameters={"status": status or "all"},
        )
        return await self._direct_read(actor, "EMP-DOC-001", intent=intent)

    async def balances(self, actor: DiscordActor, employee_id: str, year: int) -> InteractionResult:
        intent = self._direct_intent(
            "EMP-BAL-001",
            employee_id=employee_id,
            parameters={"year": year},
        )
        return await self._direct_read(actor, "EMP-BAL-001", intent=intent)

    async def prepare_operator_action(
        self,
        actor: DiscordActor,
        function_id: str,
        employee_id: str,
        parameters: Mapping[str, Any],
    ) -> InteractionResult:
        """Prepare one catalog-declared, model-hidden write without invoking OpenAI."""

        correlation_id = str(uuid.uuid4())
        spec = self._operator_spec(function_id)
        validated_parameters = self._validate_write_parameters(spec, parameters)
        try:
            intent = self._direct_intent(
                function_id,
                employee_id=employee_id,
                parameters=validated_parameters,
            )
        except ValueError as exc:
            raise ApplicationError("invalid deterministic operator request") from exc

        decision = self.policy.evaluate(
            self._context(actor, function_id, target_employee_id=intent.employee_id)
        )
        if not decision.allowed:
            await self._audit_denial(actor, correlation_id, intent, decision)
            raise ApplicationPolicyDenied(decision)

        if not self.scope.mock_mode and not spec.operator_live_available:
            await self._audit(
                actor,
                correlation_id,
                "request.unavailable",
                function_id,
                AuditOutcome.DENIED,
                intent.employee_id,
                {"source": "slash_operator", "status": "NOT_AVAILABLE"},
            )
            return InteractionResult(
                title="Funzione non disponibile",
                description=(
                    f"NOT_AVAILABLE: {spec.title} non dispone ancora di un percorso live "
                    "verificato e fail-closed."
                ),
                correlation_id=correlation_id,
                success=False,
            )

        result = await self._prepare_write(actor, correlation_id, intent)
        await self._audit(
            actor,
            correlation_id,
            "request.completed",
            function_id,
            AuditOutcome.PENDING if result.action_id else AuditOutcome.FAILED,
            intent.employee_id,
            {"source": "slash_operator"},
        )
        return result

    async def _resolve_employee_target(
        self,
        query: str,
        correlation_id: str,
        *,
        actor: DiscordActor,
        function_id: str,
        parameters: Mapping[str, object],
    ) -> EmployeeListItem | InteractionResult:
        """Resolve a local name query without exposing it to the model provider."""

        normalized_query = normalize_text(query, max_length=128, allow_newlines=False)
        result = await self.dic.list_all_employees(
            EmployeeListQuery(
                query=normalized_query,
                employee_filter=EmployeeFilter.ALL,
                sort_by="name",
                sort_direction=SortDirection.ASC,
                page=1,
                page_size=100,
            )
        )
        exact = [
            item
            for item in result.items
            if self._employee_display_name(item).casefold() == normalized_query.casefold()
        ]
        candidates = exact if exact else list(result.items)
        if not candidates:
            return InteractionResult(
                title="Dipendente non trovato",
                description="Nessun dipendente corrisponde alla ricerca indicata.",
                correlation_id=correlation_id,
                success=False,
            )
        if len(candidates) > 1:
            self._conversation_context.remember_candidates(
                ConversationKey(actor.user_id, actor.guild_id, actor.channel_id),
                tuple(item.employee_id for item in candidates[:100]),
                function_id=function_id,
                parameters=parameters,
            )
            return InteractionResult(
                title="Risultato non univoco",
                description=(
                    f"Ho trovato {len(candidates)} dipendenti. Scegli quello corretto e ripeti "
                    "la richiesta indicando `employee id <ID>`."
                ),
                fields=tuple(
                    ResultField(
                        self._employee_display_name(item),
                        f"ID: {item.employee_id} · stato: {item.employee_state.value}",
                    )
                    for item in candidates[:25]
                ),
                correlation_id=correlation_id,
                success=False,
            )
        return candidates[0]

    def _context_followup_intent(
        self,
        request: str,
        employee_id: str,
        remembered: ConversationContext,
    ) -> IntentEnvelope:
        """Resolve an ordinal locally without exposing a prior result set to the provider."""

        folded = request.casefold()
        function_id = remembered.function_id
        if any(marker in folded for marker in ("mostrami tutto", "dettaglio", "profilo")):
            function_id = "EMP-READ-002"
        elif "document" in folded:
            function_id = "EMP-DOC-001"
        elif any(marker in folded for marker in ("ruolo", "permess")):
            function_id = "EMP-RBAC-001"
        elif any(marker in folded for marker in ("timbr", "presenz")):
            function_id = "EMP-TIME-001"
        elif "maturaz" in folded:
            function_id = "EMP-MAT-001"
        elif any(marker in folded for marker in ("bilancio", "saldo", "contator")):
            function_id = "EMP-BAL-001"
        elif any(marker in folded for marker in ("busta paga", "cedolino", "netto")):
            function_id = "EMP-PAY-001"
        return self._direct_intent(
            function_id,
            employee_id=employee_id,
            parameters=dict(remembered.parameters),
        )

    async def _direct_read(
        self,
        actor: DiscordActor,
        function_id: str,
        *,
        employee_id: str | None = None,
        intent: IntentEnvelope | None = None,
    ) -> InteractionResult:
        correlation_id = str(uuid.uuid4())
        candidate = intent or self._direct_intent(function_id, employee_id=employee_id)
        decision = self.policy.evaluate(
            self._context(actor, function_id, target_employee_id=candidate.employee_id)
        )
        if not decision.allowed:
            raise ApplicationPolicyDenied(decision)
        result = await self._dispatch_read(actor, correlation_id, candidate, "default")
        await self._audit(
            actor,
            correlation_id,
            "request.completed",
            function_id,
            AuditOutcome.SUCCESS,
            candidate.employee_id,
            {"source": "slash"},
        )
        return result

    async def _dispatch_read(
        self,
        actor: DiscordActor,
        correlation_id: str,
        intent: IntentEnvelope,
        operation_scope: str,
    ) -> InteractionResult:
        function_id = intent.function_id
        if function_id in {
            "EMP-READ-001",
            "EMP-SEARCH-001",
            "EMP-FILTER-001",
            "EMP-SORT-001",
            "EMP-PAGE-001",
        }:
            filter_status = str(intent.parameters.get("status", "active")).lower()
            employee_filter = {
                "active": EmployeeFilter.ACTIVE,
                "inactive": EmployeeFilter.INACTIVE,
                "all": EmployeeFilter.ALL,
            }.get(filter_status, EmployeeFilter.ACTIVE)
            sort_by_raw = intent.parameters.get("sort_by", "name")
            if not isinstance(sort_by_raw, str):
                raise ApplicationError("sort_by must be a supported string")
            normalized_sort_by = sort_by_raw.strip().casefold()
            sort_by: Literal["name", "payroll_number", "status", "contract"]
            if normalized_sort_by == "name":
                sort_by = "name"
            elif normalized_sort_by == "payroll_number":
                sort_by = "payroll_number"
            elif normalized_sort_by == "status":
                sort_by = "status"
            elif normalized_sort_by == "contract":
                sort_by = "contract"
            else:
                raise ApplicationError("unsupported employee sort field")
            sort_direction_raw = intent.parameters.get("sort_direction", SortDirection.ASC.value)
            if not isinstance(sort_direction_raw, str):
                raise ApplicationError("sort_direction must be 'asc' or 'desc'")
            try:
                sort_direction = SortDirection(sort_direction_raw.strip().casefold())
            except ValueError as exc:
                raise ApplicationError("sort_direction must be 'asc' or 'desc'") from exc
            employee_query = EmployeeListQuery(
                query=intent.query,
                employee_filter=employee_filter,
                sort_by=sort_by,
                sort_direction=sort_direction,
                page=int(intent.parameters.get("page", 1)),
                page_size=min(int(intent.parameters.get("page_size", 25)), 100),
            )
            include_all = intent.parameters.get("include_all") is True
            result = (
                await self.dic.list_all_employees(employee_query)
                if include_all
                else await self.dic.list_employees(employee_query)
            )
            if operation_scope == "aggregate":
                title, description = self._presenter.employee_count(
                    result.total, employee_filter.value
                )
                aggregate_fields: tuple[ResultField, ...] = ()
                if employee_filter is EmployeeFilter.ALL:
                    active = await self.dic.list_employees(
                        employee_query.model_copy(
                            update={"employee_filter": EmployeeFilter.ACTIVE, "page": 1}
                        )
                    )
                    inactive = await self.dic.list_employees(
                        employee_query.model_copy(
                            update={"employee_filter": EmployeeFilter.INACTIVE, "page": 1}
                        )
                    )
                    aggregate_fields = (
                        ResultField("Attivi", str(active.total), True),
                        ResultField("Disattivati", str(inactive.total), True),
                        ResultField("Totale", str(result.total), True),
                    )
                return InteractionResult(
                    title=title,
                    description=description,
                    fields=aggregate_fields,
                    correlation_id=correlation_id,
                    sensitivity=ResponseSensitivity.PUBLIC_AGGREGATE,
                )
            if intent.parameters.get("view") == "ascii":
                export_rows = self._employee_export_rows(result.items)
                table = ascii_table(export_rows)
                attachment_content = (table + "\n").encode("utf-8")
                if len(attachment_content) > self._response_attachment_max_bytes:
                    raise ApplicationError("ASCII export exceeds the configured attachment limit")
                all_chunks = split_ascii_for_discord(table)
                inline_chunks = all_chunks[:_MAX_INLINE_ASCII_CHUNKS]
                timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
                return InteractionResult(
                    title="Elenco completo dipendenti",
                    description=(
                        f"Record inclusi: {len(export_rows)} su {result.total}. "
                        "Il netto mensile non è esposto dall'adapter DiC corrente ed è indicato "
                        "come N/D; nessun valore è stato inventato. "
                        + (
                            "La tabella completa è nell'allegato; nel canale è mostrata solo "
                            "un'anteprima bounded."
                            if len(all_chunks) > len(inline_chunks)
                            else "La tabella completa è riportata anche nell'allegato."
                        )
                    ),
                    messages=inline_chunks,
                    attachments=(
                        ResponseAttachment(
                            filename=f"dipendenti_{timestamp}.txt",
                            content_type="text/plain; charset=utf-8",
                            content=attachment_content,
                        ),
                    ),
                    correlation_id=correlation_id,
                )
            fields = tuple(
                ResultField(
                    self._employee_display_name(item),
                    "\n".join(
                        (
                            f"Employee ID: {item.employee_id}",
                            f"Stato dipendente: {item.employee_state.value}",
                            f"Stato account: {item.account_state.value}",
                            f"Stato contratto: {item.contract_state or item.contract_label or '—'}",
                            f"Mansione: {item.job_title or '—'}",
                            f"Gruppo: {item.group_name or '—'}",
                            f"Luogo di lavoro: {item.workplace or '—'}",
                            f"Matricola: {self._redact_list_identifier(item.payroll_number)}",
                        )
                    ),
                )
                for item in result.items
            )
            title, description = self._presenter.employee_list(
                page=result.page,
                shown=len(result.items),
                total=result.total,
                has_next=result.has_next,
            )
            return InteractionResult(
                title=title,
                description=description,
                fields=fields,
                correlation_id=correlation_id,
            )
        if function_id == "EMP-READ-002":
            employee_id = self._require_employee(intent)
            summary = await self.dic.get_employee_summary(employee_id)
            return InteractionResult(
                title=f"Dipendente {summary.employee_id}",
                description="Riepilogo redatto",
                fields=(
                    ResultField("Nome", summary.first_name_redacted or "—", True),
                    ResultField("Cognome", summary.last_name_redacted or "—", True),
                    ResultField(
                        "Matricola",
                        (
                            summary.payroll_number or "—"
                            if "pii:read" in actor.entitlements
                            else self._redact_list_identifier(summary.payroll_number)
                        ),
                        True,
                    ),
                    ResultField("Mansione", summary.job_title or "—", True),
                    ResultField("Luogo", summary.workplace or "—", True),
                    ResultField("Stato", summary.state.value, True),
                ),
                correlation_id=correlation_id,
            )
        if function_id == "EMP-CONTRACT-001":
            return await self._render_contracts(intent, correlation_id)
        if function_id == "EMP-RBAC-001":
            employee_id = self._require_employee(intent)
            roles = await self.dic.get_roles(employee_id)
            return InteractionResult(
                title=f"Ruoli {employee_id}",
                description="; ".join(role.name for role in roles.roles)
                or "Nessun ruolo disponibile",
                correlation_id=correlation_id,
            )
        if function_id == "EMP-TIME-001":
            employee_id = self._require_employee(intent)
            time_access = await self.dic.get_time_access(employee_id)
            return InteractionResult(
                title=f"Timbratura {employee_id}",
                description="Stato accessi redatto",
                fields=(
                    ResultField("Timbratura", str(time_access.timestamping_enabled), True),
                    ResultField("Foglio presenze", str(time_access.attendance_sheet_access), True),
                    ResultField("Turni", str(time_access.shift_management), True),
                ),
                correlation_id=correlation_id,
            )
        if function_id == "EMP-MAT-001":
            employee_id = self._require_employee(intent)
            maturation_records = await self.dic.get_maturations(employee_id)
            shown_maturations = maturation_records[:25]
            maturation_attachments: tuple[ResponseAttachment, ...] = ()
            if len(maturation_records) > len(shown_maturations):
                rows = ["category\tvalid_from\tvalid_to\tstatus"]
                rows.extend(
                    "\t".join(
                        (
                            record.category.replace("\t", " ").replace("\n", " "),
                            record.valid_from or "",
                            record.valid_to or "",
                            record.status or "",
                        )
                    )
                    for record in maturation_records
                )
                content = ("\n".join(rows) + "\n").encode("utf-8")
                if len(content) > self._response_attachment_max_bytes:
                    raise ApplicationError(
                        "maturation result exceeds the configured attachment limit"
                    )
                maturation_attachments = (
                    ResponseAttachment(
                        filename="maturazioni.tsv",
                        content_type="text/tab-separated-values; charset=utf-8",
                        content=content,
                    ),
                )
            return InteractionResult(
                title=f"Maturazioni {employee_id}",
                description=(
                    f"Record totali: {len(maturation_records)} · "
                    f"mostrati: {len(shown_maturations)} · acquisizione completa"
                ),
                fields=tuple(
                    ResultField(
                        record.category, f"{record.valid_from or '—'} → {record.valid_to or '—'}"
                    )
                    for record in shown_maturations
                ),
                attachments=maturation_attachments,
                correlation_id=correlation_id,
            )
        if function_id == "EMP-BAL-001":
            employee_id = self._require_employee(intent)
            year = int(intent.parameters.get("year", datetime.now(UTC).year))
            balance = await self.dic.get_balance(employee_id, year)
            shown_balance_lines = balance.lines[:25]
            attachment: tuple[ResponseAttachment, ...] = ()
            if len(balance.lines) > len(shown_balance_lines):
                rows = [
                    "month\tcategory\tcounter_id\tbalance\tmaturation\tutilization\t"
                    "correction\tresidue\tprojection"
                ]
                rows.extend(
                    "\t".join(
                        (
                            str(line.month or ""),
                            line.category.replace("\t", " ").replace("\n", " "),
                            line.counter_id or "",
                            line.balance or "",
                            line.maturation or line.accrued or "",
                            line.utilization or line.used or "",
                            line.corrections or "",
                            line.current_residual or "",
                            line.projection or "",
                        )
                    )
                    for line in balance.lines
                )
                content = ("\n".join(rows) + "\n").encode("utf-8")
                if len(content) > self._response_attachment_max_bytes:
                    raise ApplicationError("balance result exceeds the configured attachment limit")
                attachment = (
                    ResponseAttachment(
                        filename=f"bilancio_{year}.tsv",
                        content_type="text/tab-separated-values; charset=utf-8",
                        content=content,
                    ),
                )
            return InteractionResult(
                title=f"Bilancio {employee_id} — {year}",
                description=(
                    f"Record totali: {len(balance.lines)} · "
                    f"mostrati: {len(shown_balance_lines)} · "
                    "acquisizione completa"
                ),
                fields=tuple(
                    ResultField(
                        f"{line.month:02d} · {line.category}" if line.month else line.category,
                        f"Residuo: {line.current_residual or '—'} · "
                        f"maturato: {line.maturation or line.accrued or '—'} · "
                        f"utilizzato: {line.utilization or line.used or '—'}",
                    )
                    for line in shown_balance_lines
                ),
                attachments=attachment,
                correlation_id=correlation_id,
            )
        if function_id == "EMP-PAY-001":
            employee_id = self._require_employee(intent)
            payroll_year_raw = intent.parameters.get("year")
            payroll_month_raw = intent.parameters.get("month")
            payroll_records = await self.dic.get_payroll_metadata(
                employee_id,
                int(payroll_year_raw) if payroll_year_raw is not None else None,
            )
            if isinstance(payroll_month_raw, int) and not isinstance(payroll_month_raw, bool):
                payroll_records = tuple(
                    record for record in payroll_records if record.month == payroll_month_raw
                )
            if not payroll_records:
                period = (
                    f"{payroll_month_raw:02d}/{payroll_year_raw}"
                    if isinstance(payroll_month_raw, int) and isinstance(payroll_year_raw, int)
                    else str(payroll_year_raw or "richiesto")
                )
                return InteractionResult(
                    title=f"Busta paga non trovata — {employee_id}",
                    description=f"Non risulta una busta paga disponibile per il periodo {period}.",
                    correlation_id=correlation_id,
                    success=False,
                )

            shown_payroll_records = payroll_records[:25]
            payroll_attachments: tuple[ResponseAttachment, ...] = ()
            if len(payroll_records) > len(shown_payroll_records):
                rows = ["year\tmonth\tstatus\tpublished_at\tnet_cents\tpdf_available"]
                rows.extend(
                    "\t".join(
                        (
                            str(record.year),
                            str(record.month or ""),
                            (record.status or "").replace("\t", " ").replace("\n", " "),
                            record.published_at or "",
                            str(record.net_cents) if record.net_cents is not None else "",
                            "yes" if record.attachment_url is not None else "no",
                        )
                    )
                    for record in payroll_records
                )
                content = ("\n".join(rows) + "\n").encode("utf-8")
                if len(content) > self._response_attachment_max_bytes:
                    raise ApplicationError("payroll result exceeds the configured attachment limit")
                payroll_attachments = (
                    ResponseAttachment(
                        filename=f"buste_paga_{payroll_records[0].year}.tsv",
                        content_type="text/tab-separated-values; charset=utf-8",
                        content=content,
                    ),
                )

            def net_text(record: PayrollMetadata) -> str:
                if record.net_cents is None:
                    return "—"
                euros, cents = divmod(record.net_cents, 100)
                whole = f"{euros:,}".replace(",", ".")
                return f"€ {whole},{cents:02d}"

            def attachment_text(record: PayrollMetadata) -> str:
                if record.attachment_url is None:
                    return "PDF non disponibile"
                if "protected_documents:download" not in actor.entitlements:
                    return "PDF disponibile; download non autorizzato per il ruolo corrente"
                target = record.attachment_url.get_secret_value()
                if len(target) > 850:
                    return "PDF disponibile su DIC; link temporaneo troppo lungo per Discord"
                return f"[Apri il PDF della busta paga](<{target}>)"

            return InteractionResult(
                title=f"Busta paga {employee_id}",
                description=(
                    f"Ho consultato la sezione Buste paga di Dipendenti in Cloud. "
                    f"Record totali: {len(payroll_records)} · "
                    f"mostrati: {len(shown_payroll_records)} · acquisizione completa."
                ),
                fields=tuple(
                    ResultField(
                        f"{record.month:02d}/{record.year}"
                        if record.month is not None
                        else str(record.year),
                        (
                            f"Netto a pagare: **{net_text(record)}** · "
                            f"emessa: {record.published_at or '—'} · "
                            f"stato: {record.status or '—'}\n{attachment_text(record)}"
                        ),
                    )
                    for record in shown_payroll_records
                ),
                attachments=payroll_attachments,
                correlation_id=correlation_id,
            )
        if function_id == "EMP-PAY-002":
            month_raw = intent.parameters.get("month")
            year_raw = intent.parameters.get("year")
            if (
                isinstance(month_raw, bool)
                or not isinstance(month_raw, int)
                or not 1 <= month_raw <= 12
                or isinstance(year_raw, bool)
                or not isinstance(year_raw, int)
                or not 2000 <= year_raw <= 2200
            ):
                raise ApplicationError("payroll search requires a valid month and year")
            payrolls = await self.dic.find_employees_with_payroll(
                year=year_raw,
                month=month_raw,
            )
            lines = ["Dipendente | Employee ID"]
            lines.extend(
                f"{self._employee_display_name(item)} | {item.employee_id}"
                for item in payrolls.employees
            )
            attachment_content = ("\n".join(lines) + "\n").encode("utf-8")
            if len(attachment_content) > self._response_attachment_max_bytes:
                raise ApplicationError("payroll result exceeds the configured attachment limit")
            shown = payrolls.employees[:25]
            month_label = f"{payrolls.month:02d}/{payrolls.year}"
            return InteractionResult(
                title=f"Buste paga disponibili — {month_label}",
                description=(
                    f"Ho consultato l'elenco Dipendenti e, per ciascuno, la sezione Buste paga "
                    f"del mese {month_label}: trovati {len(payrolls.employees)} dipendenti su "
                    f"{payrolls.scanned} analizzati."
                ),
                fields=tuple(
                    ResultField(
                        f"{self._employee_display_name(item)} · ID {item.employee_id}",
                        f"Busta paga {month_label}: disponibile",
                    )
                    for item in shown
                ),
                attachments=(
                    ResponseAttachment(
                        filename=f"buste_paga_disponibili_{payrolls.year}_{payrolls.month:02d}.txt",
                        content_type="text/plain; charset=utf-8",
                        content=attachment_content,
                    ),
                ),
                correlation_id=correlation_id,
            )
        if function_id == "EMP-DOC-001":
            employee_id = self._require_employee(intent)
            document_status = str(intent.parameters.get("status", "all"))
            normalized_document_status: Literal["uploaded", "pending", "all"] = "all"
            if document_status == "uploaded":
                normalized_document_status = "uploaded"
            elif document_status == "pending":
                normalized_document_status = "pending"
            document_query = DocumentQuery(state=normalized_document_status)
            document_records = await self.dic.get_document_metadata(employee_id, document_query)
            shown_documents = document_records[:25]
            document_attachments: tuple[ResponseAttachment, ...] = ()
            if len(document_records) > len(shown_documents):
                rows = ["document_id\tcategory\tstate\texpiry_date\tuploaded_at"]
                rows.extend(
                    "\t".join(
                        (
                            record.document_id,
                            (record.category or "").replace("\t", " ").replace("\n", " "),
                            record.state,
                            record.expiry_date or "",
                            record.uploaded_at or "",
                        )
                    )
                    for record in document_records
                )
                content = ("\n".join(rows) + "\n").encode("utf-8")
                if len(content) > self._response_attachment_max_bytes:
                    raise ApplicationError(
                        "document result exceeds the configured attachment limit"
                    )
                document_attachments = (
                    ResponseAttachment(
                        filename="documenti.tsv",
                        content_type="text/tab-separated-values; charset=utf-8",
                        content=content,
                    ),
                )
            return InteractionResult(
                title=f"Metadati documenti {employee_id}",
                description=(
                    f"Record totali: {len(document_records)} · "
                    f"mostrati: {len(shown_documents)} · acquisizione completa"
                ),
                fields=tuple(
                    ResultField(
                        record.document_id,
                        f"{record.category or '—'} · {record.state} · "
                        f"scadenza {record.expiry_date or '—'}",
                    )
                    for record in shown_documents
                ),
                attachments=document_attachments,
                correlation_id=correlation_id,
            )
        raise ApplicationError(f"read dispatcher missing for {function_id}")

    async def _render_contracts(
        self, intent: IntentEnvelope, correlation_id: str
    ) -> InteractionResult:
        records: list[tuple[date | None, ResultField]] = []
        unparseable = 0
        if intent.employee_id:
            for contract in await self.dic.get_contracts(intent.employee_id):
                end: date | None = None
                if contract.end_date:
                    end = self._parse_dic_date(contract.end_date)
                    if end is None:
                        unparseable += 1
                        continue
                if intent.date_from and (end is None or end < intent.date_from):
                    continue
                if intent.date_to and (end is None or end > intent.date_to):
                    continue
                records.append(
                    (
                        end,
                        ResultField(
                            intent.employee_id,
                            f"{contract.contract_type or 'contratto'} · "
                            f"fine {contract.end_date or 'indeterminato'}",
                        ),
                    )
                )
        else:
            page = 1
            expected_total: int | None = None
            seen_employee_ids: set[str] = set()
            while True:
                result = await self.dic.list_employees(
                    EmployeeListQuery(employee_filter=EmployeeFilter.ALL, page=page, page_size=100)
                )
                if expected_total is None:
                    expected_total = result.total
                elif result.total != expected_total:
                    raise ApplicationError("employee total changed during contract analysis")
                page_start_count = len(seen_employee_ids)
                for item in result.items:
                    if item.employee_id in seen_employee_ids:
                        raise ApplicationError("employee pagination returned a duplicate")
                    seen_employee_ids.add(item.employee_id)
                    end = item.current_contract_valid_to
                    if intent.date_from and (end is None or end < intent.date_from):
                        continue
                    if intent.date_to and (end is None or end > intent.date_to):
                        continue
                    records.append(
                        (
                            end,
                            ResultField(
                                f"{self._employee_display_name(item)} · ID {item.employee_id}",
                                f"{item.contract_label or 'contratto corrente'} · "
                                f"fine {end.isoformat() if end else 'indeterminato'}",
                            ),
                        )
                    )
                if len(seen_employee_ids) > (expected_total or 0):
                    raise ApplicationError("employee pagination exceeded the reported total")
                if not result.has_next:
                    break
                if len(seen_employee_ids) == page_start_count:
                    raise ApplicationError("employee pagination made no progress")
                page += 1
                if page > 100:
                    raise ApplicationError("employee pagination safety limit exceeded")
            if len(seen_employee_ids) != (expected_total or 0):
                raise ApplicationError("employee pagination did not match the reported total")
        records.sort(key=lambda item: (item[0] is None, item[0] or date.max, item[1].name))
        fields = tuple(item[1] for item in records[:25])
        title, description = self._presenter.contract_summary(
            date_from=intent.date_from,
            date_to=intent.date_to,
            found=len(records),
            shown=len(fields),
            unparseable=unparseable,
        )
        return InteractionResult(
            title=title,
            description=description,
            fields=fields,
            correlation_id=correlation_id,
        )

    async def _prepare_write(
        self,
        actor: DiscordActor,
        correlation_id: str,
        intent: IntentEnvelope,
    ) -> InteractionResult:
        if self.approvals is None or self.payload_cipher is None:
            return self._unavailable("Il workflow write non è configurato.")
        spec = self._spec(intent.function_id)
        validated_parameters = self._validate_write_parameters(spec, intent.parameters)
        if spec.requires_target and not intent.employee_id:
            return InteractionResult(
                title="Employee ID necessario",
                description="Le modifiche non possono usare soltanto il nome.",
                correlation_id=correlation_id,
                success=False,
            )
        if not self.scope.mock_mode and not spec.operator_live_available:
            return InteractionResult(
                title="Funzione non disponibile",
                description=(
                    f"NOT_AVAILABLE: {spec.title} non dispone ancora di un percorso live "
                    "verificato e fail-closed."
                ),
                correlation_id=correlation_id,
                success=False,
            )
        motivation_value = validated_parameters.get("motivation")
        motivation = (
            motivation_value.strip()
            if isinstance(motivation_value, str) and motivation_value.strip()
            else None
        )
        if spec.destructive and motivation is None:
            return InteractionResult(
                title="Motivazione necessaria",
                description="Le azioni critiche richiedono una motivazione esplicita.",
                correlation_id=correlation_id,
                success=False,
            )
        action_parameters = {
            key: value for key, value in validated_parameters.items() if key != "motivation"
        }
        state_fingerprint, target_label, resource_before = await self._target_snapshot(
            intent.employee_id,
            function_id=intent.function_id,
            parameters=action_parameters,
            validate_precondition=True,
        )
        raw_diff = self._build_write_preview(spec, action_parameters, resource_before)
        if not self._preview_changes_resource(spec, raw_diff):
            raise ApplicationError("write is a no-op against the current resource")
        redacted = self._redact_write_preview_values(
            spec,
            action_parameters,
            invalid_shape_message="redacted parameters must remain an object",
        )
        redacted_before = self._redact_write_preview_values(
            spec,
            resource_before,
            invalid_shape_message="redacted resource snapshot must remain an object",
        )
        redacted_diff = self._build_write_preview(spec, redacted, redacted_before)
        pending_payload = {
            "version": 1,
            "parameters": action_parameters,
            "requester_context": {
                "user_id": str(actor.user_id),
                "guild_id": str(actor.guild_id),
                "channel_id": str(actor.channel_id),
                "logical_roles": sorted(actor.logical_roles),
                "entitlements": sorted(actor.entitlements),
            },
        }
        prepared = await self.approvals.prepare(
            function_id=intent.function_id,
            correlation_id=correlation_id,
            requester_id=str(actor.user_id),
            guild_id=str(actor.guild_id),
            channel_id=str(actor.channel_id),
            target_employee_id=intent.employee_id,
            encrypted_parameters=self.payload_cipher.encrypt_json(pending_payload),
            redacted_diff=redacted_diff,
            state_fingerprint=state_fingerprint,
            motivation=motivation,
        )
        fields = (
            ResultField("Target verificato", target_label),
            *(
                ResultField(str(key), f"{change['before']} → {change['after']}")
                for key, change in redacted_diff.items()
            ),
        )
        confirmation = f"Codice monouso: `{prepared.confirmation_code}`"
        if prepared.required_text_confirmation:
            confirmation += f"\nConferma target richiesta: `{prepared.required_text_confirmation}`"
        return InteractionResult(
            title=f"Anteprima {intent.function_id}",
            description=(
                f"Azione `{prepared.action.action_id}` preparata; "
                "nessuna modifica è stata eseguita.\n"
                f"{confirmation}"
            ),
            fields=fields,
            correlation_id=correlation_id,
            action_id=prepared.action.action_id,
        )

    async def _execute_approved(
        self, actor: DiscordActor, action: PendingAction
    ) -> InteractionResult:
        if self.approvals is None or self.payload_cipher is None:
            raise ApplicationError("approval dependencies are missing")
        parameters, stored_requester = self._decode_pending_payload(action)
        spec = self._spec(action.function_id)
        validation_parameters = dict(parameters)
        if action.motivation is not None:
            validation_parameters["motivation"] = action.motivation
        validated = self._validate_write_parameters(spec, validation_parameters)
        parameters = {key: value for key, value in validated.items() if key != "motivation"}
        requester = (
            await self.requester_actor_resolver(action)
            if self.requester_actor_resolver is not None
            else stored_requester
        )
        self._validate_requester_binding(requester, action)
        # Re-check the requester's current RBAC (when a live resolver is configured),
        # tenant, feature flags, target and system state.  Approver roles never
        # substitute for the requester's authorization.
        execution_scope = (
            str(parameters.get("scope", "default"))
            if action.function_id == "EMP-EXPORT-001"
            else "default"
        )
        decision = self.policy.evaluate(
            self._context(
                requester,
                action.function_id,
                phase=PolicyPhase.EXECUTE,
                target_employee_id=action.target_employee_id,
                operation_scope=execution_scope,
            )
        )
        if not decision.allowed:
            raise ApplicationPolicyDenied(decision)
        target_lock_key = action.target_employee_id or "__targetless_write__"
        # Lock order is invariant: global first, then the stable target key. The
        # critical section begins with the fresh CAS read and ends only after a
        # durable terminal/UNKNOWN outcome (including reconciliation) is stored.
        # Preview and approval registration intentionally stay outside both locks.
        async with self._global_write_lock:
            target_lock = self._target_write_locks.setdefault(
                target_lock_key,
                asyncio.Lock(),
            )
            async with target_lock:
                completed, generated_export = await self._execute_write_critical_section(
                    action, parameters
                )
        uncertain = completed.status is ActionStatus.UNKNOWN_REQUIRES_RECONCILIATION
        applied = completed.status is ActionStatus.SUCCEEDED
        if uncertain:
            event_type = "write.uncertain"
            audit_outcome = AuditOutcome.UNCERTAIN
        elif applied:
            event_type = "write.completed"
            audit_outcome = AuditOutcome.SUCCESS
        else:
            event_type = "write.reconciled_not_applied"
            audit_outcome = AuditOutcome.FAILED
        await self._audit(
            actor,
            action.correlation_id,
            event_type,
            action.function_id,
            audit_outcome,
            action.target_employee_id,
            {"action_id": action.action_id, "status": completed.status.value},
        )
        return InteractionResult(
            title="Esito azione",
            description=(
                f"`{action.action_id}`: {completed.status.value}"
                + (
                    f" · export completo con {generated_export.record_count} record."
                    if generated_export is not None
                    else ""
                )
            ),
            correlation_id=action.correlation_id,
            attachments=(
                (
                    ResponseAttachment(
                        filename=generated_export.filename,
                        content_type=generated_export.media_type,
                        content=generated_export.content,
                    ),
                )
                if generated_export is not None and applied
                else ()
            ),
            success=applied,
        )

    async def _execute_write_critical_section(
        self,
        action: PendingAction,
        parameters: Mapping[str, Any],
    ) -> tuple[PendingAction, GeneratedExport | None]:
        """Run the entire post-approval write transaction under application locks."""

        if self.approvals is None:
            raise ApplicationError("approval dependencies are missing")
        current_fingerprint = await self._state_fingerprint(
            action.function_id, action.target_employee_id, parameters
        )
        executing = await self.approvals.begin_execution(
            action.action_id,
            current_state_fingerprint=current_fingerprint,
        )
        execution_parameters: Mapping[str, Any] = parameters
        try:
            execution_parameters = await self._execution_parameters(action.function_id, parameters)
        except Exception:
            await self.approvals.complete_failure(
                action.action_id,
                result="deterministic preparation failed before dispatch",
                outcome_uncertain=False,
            )
            raise
        generated_export: GeneratedExport | None = None
        try:
            if action.function_id == "EMP-EXPORT-001":
                generated_export = await self._generate_employee_export(action, parameters)
                result_payload = json.dumps(
                    {
                        "filename": generated_export.filename,
                        "record_count": generated_export.record_count,
                        "media_type": generated_export.media_type,
                    },
                    sort_keys=True,
                )
                completed = await self.approvals.complete_success(
                    action.action_id,
                    execution_result=result_payload,
                    postcondition_result="export generated and validated in memory",
                    postcondition_verified=True,
                )
                return completed, generated_export
            result = await self.dic.execute(executing, execution_parameters)
        except (DicAmbiguousWriteOutcomeError, DicReconciliationRequiredError):
            completed = await self._persist_uncertain_and_reconcile(
                action,
                execution_parameters,
                result="write outcome uncertain; reconciliation required",
            )
        except Exception:
            await self.approvals.complete_failure(
                action.action_id,
                result="deterministic execution failed before a verified result",
                outcome_uncertain=False,
            )
            raise
        else:
            try:
                completed = await self.approvals.complete_success(
                    action.action_id,
                    execution_result=json.dumps(result.model_dump(mode="json"), sort_keys=True),
                    postcondition_result=result.message,
                    postcondition_verified=result.postcondition_verified,
                )
            except Exception as persistence_error:
                # The adapter has already returned a verified write result. From this
                # point forward the action can only be durably UNKNOWN/reconciled;
                # recording a deterministic failure would be false and could invite
                # an unsafe retry of a mutation that already happened.
                try:
                    completed = await self._persist_uncertain_and_reconcile(
                        action,
                        execution_parameters,
                        result="verified write result could not be persisted",
                    )
                except Exception:
                    raise ApplicationError(
                        "verified write outcome remains claimed for operator reconciliation"
                    ) from persistence_error
                if completed.status is ActionStatus.EXECUTING:
                    raise ApplicationError(
                        "verified write outcome remains claimed for operator reconciliation"
                    ) from persistence_error
        return completed, generated_export

    async def _generate_employee_export(
        self,
        action: PendingAction,
        parameters: Mapping[str, Any],
    ) -> GeneratedExport:
        if self.exports is None:
            raise ApplicationError("export generation boundary is unavailable")
        try:
            export_format = ExportFormat(str(parameters["format"]))
            employee_filter = EmployeeFilter(str(parameters["status"]))
        except (KeyError, ValueError) as exc:
            raise ApplicationError("export parameters are invalid") from exc
        dataset = parameters.get("dataset")
        if dataset not in {"employees", "contracts_expiring"}:
            raise ApplicationError("export dataset is invalid")
        result = await self.dic.list_all_employees(
            EmployeeListQuery(
                employee_filter=employee_filter,
                sort_by="name",
                sort_direction=SortDirection.ASC,
                page=1,
                page_size=100,
            )
        )
        rows = list(self._employee_export_rows(result.items))
        filter_parts = [f"stato={employee_filter.value}"]
        if dataset == "contracts_expiring":
            start_raw = parameters.get("date_from")
            end_raw = parameters.get("date_to")
            try:
                start = date.fromisoformat(str(start_raw)) if start_raw is not None else None
                end = date.fromisoformat(str(end_raw)) if end_raw is not None else None
            except ValueError as exc:
                raise ApplicationError("export contract date interval is invalid") from exc
            rows = [
                row
                for row in rows
                if row.contract_expiry is not None
                and (start is None or row.contract_expiry >= start)
                and (end is None or row.contract_expiry <= end)
            ]
            filter_parts.extend(
                (
                    f"scadenza_da={start.isoformat() if start else 'qualsiasi'}",
                    f"scadenza_a={end.isoformat() if end else 'qualsiasi'}",
                )
            )
        title = "Contratti in scadenza" if dataset == "contracts_expiring" else "Elenco dipendenti"
        return await asyncio.to_thread(
            self.exports.generate_employees,
            tuple(rows),
            export_format=export_format,
            created_at=datetime.now(UTC),
            requester="Utente Discord autorizzato",
            filters="; ".join(filter_parts),
            title=title,
        )

    async def _persist_uncertain_and_reconcile(
        self,
        action: PendingAction,
        execution_parameters: Mapping[str, Any],
        *,
        result: str,
    ) -> PendingAction:
        if self.approvals is None:
            raise ApplicationError("approval dependencies are missing")
        uncertain = await self.approvals.complete_failure(
            action.action_id,
            result=result,
            outcome_uncertain=True,
        )
        try:
            reconciliation = await self.dic.reconcile(uncertain, execution_parameters)
        except Exception:
            logger.warning(
                "Write reconciliation read failed",
                extra={
                    "event_type": "write.reconciliation_failed",
                    "correlation_id": action.correlation_id,
                    "function_id": action.function_id,
                    "outcome": "UNCERTAIN",
                },
            )
            return uncertain
        # This helper is entered only after dispatch may have happened. An
        # immediate NOT_APPLIED observation is not final under eventual
        # consistency and must never make the write automatically retryable.
        # Only a positive postcondition can safely promote durable UNKNOWN.
        if reconciliation.state is not ReconciliationState.CONFIRMED_APPLIED:
            return uncertain
        try:
            return await self.approvals.reconcile(
                action.action_id,
                postcondition_met=True,
                result=reconciliation.detail,
            )
        except Exception:
            # Durable UNKNOWN is already recorded. Persistence repair must never
            # trigger another call to the mutating adapter.
            logger.warning(
                "Write reconciliation persistence failed",
                extra={
                    "event_type": "write.reconciliation_persistence_failed",
                    "correlation_id": action.correlation_id,
                    "function_id": action.function_id,
                    "outcome": "UNCERTAIN",
                },
            )
            return uncertain

    async def _execution_parameters(
        self, function_id: str, parameters: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Resolve one-use upload capabilities only after the action is claimed.

        The returned filesystem capability exists only in this stack frame. It is
        never written to the pending action, audit chain, logs or interaction output.
        """

        if function_id != "EMP-DOC-002":
            return dict(parameters)
        if self.files is None:
            raise ApplicationError("file delivery boundary is unavailable")
        upload_id = parameters.get("upload_id")
        if not isinstance(upload_id, str):
            raise ApplicationError("approved upload identifier is invalid")
        try:
            resolved = await self.files.claim_clean_upload(upload_id)
        except UploadResolutionError as exc:
            raise ApplicationError("approved upload is no longer eligible for delivery") from exc
        return {
            **{key: value for key, value in parameters.items() if key != "upload_id"},
            "safe_local_path": str(resolved.path),
            "safe_local_sha256": resolved.sha256,
            "safe_local_size": resolved.size_bytes,
            "detected_mime": resolved.detected_mime,
        }

    def _decode_pending_payload(
        self, action: PendingAction
    ) -> tuple[Mapping[str, Any], DiscordActor]:
        if self.payload_cipher is None:
            raise ApplicationError("payload cipher is unavailable")
        payload = self.payload_cipher.decrypt_json(action.encrypted_parameters)
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            raise ApplicationError("decrypted pending payload has an invalid version")
        parameters = payload.get("parameters")
        requester_context = payload.get("requester_context")
        if not isinstance(parameters, Mapping) or not isinstance(requester_context, Mapping):
            raise ApplicationError("decrypted pending payload is malformed")
        roles = requester_context.get("logical_roles")
        entitlements = requester_context.get("entitlements")
        if (
            not isinstance(roles, list)
            or not all(isinstance(role, str) for role in roles)
            or not isinstance(entitlements, list)
            or not all(isinstance(entitlement, str) for entitlement in entitlements)
        ):
            raise ApplicationError("decrypted requester authorization is malformed")
        try:
            requester = DiscordActor(
                user_id=int(str(requester_context.get("user_id", ""))),
                guild_id=int(str(requester_context.get("guild_id", ""))),
                channel_id=int(str(requester_context.get("channel_id", ""))),
                logical_roles=frozenset(roles),
                discord_role_ids=frozenset(),
                entitlements=frozenset(entitlements),
            )
        except ValueError as exc:
            raise ApplicationError("decrypted requester identity is malformed") from exc
        self._validate_requester_binding(requester, action)
        return parameters, requester

    @staticmethod
    def _validate_requester_binding(requester: DiscordActor, action: PendingAction) -> None:
        if (
            str(requester.user_id) != action.requester_id
            or str(requester.guild_id) != action.guild_id
            or str(requester.channel_id) != action.channel_id
        ):
            raise ApplicationError("requester authorization does not match pending action")

    async def _state_fingerprint(
        self,
        function_id: str,
        employee_id: str | None,
        parameters: Mapping[str, Any],
    ) -> str:
        fingerprint, _target, _before = await self._target_snapshot(
            employee_id,
            function_id=function_id,
            parameters=parameters,
            validate_precondition=False,
        )
        return fingerprint

    async def _target_snapshot(
        self,
        employee_id: str | None,
        *,
        function_id: str,
        parameters: Mapping[str, Any],
        validate_precondition: bool = False,
    ) -> tuple[str, str, Mapping[str, Any]]:
        import hashlib

        spec = self._spec(function_id)
        function = FunctionId(function_id)
        json_parameters = cast(Mapping[str, JsonValue], dict(parameters))
        first_digest = await self._opaque_state_digest(
            function,
            employee_id,
            json_parameters,
            validate_precondition=validate_precondition,
        )
        try:
            resource_before, local_cas, target = await self._write_resource_snapshot(
                spec,
                employee_id,
                parameters,
                validate_precondition=validate_precondition,
            )
        except (DicNotFoundError, DicValidationError, UploadResolutionError) as exc:
            if validate_precondition:
                raise ApplicationError("write resource is unavailable or ambiguous") from exc
            resource_before = {"resource_state": "UNAVAILABLE"}
            local_cas = {"resource_state": "UNAVAILABLE"}
            target = employee_id or "Operazione senza singolo target"
        second_digest = await self._opaque_state_digest(
            function,
            employee_id,
            json_parameters,
            validate_precondition=validate_precondition,
        )
        if first_digest != second_digest:
            if validate_precondition:
                raise ApplicationError("write resource changed while preparing its preview")
            second_digest = "UNSTABLE"
        material = {
            "version": 2,
            "function_id": function_id,
            "employee_id": employee_id,
            "opaque_state_digest": second_digest,
            "local_cas": local_cas,
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest(), target, resource_before

    async def _opaque_state_digest(
        self,
        function_id: FunctionId,
        employee_id: str | None,
        parameters: Mapping[str, JsonValue],
        *,
        validate_precondition: bool,
    ) -> str:
        try:
            digest = await self.dic.adapter.get_state_digest(function_id, employee_id, parameters)
        except (DicNotFoundError, DicValidationError) as exc:
            if validate_precondition:
                raise ApplicationError("opaque resource snapshot is unavailable") from exc
            return "UNAVAILABLE"
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ApplicationError("adapter returned an invalid opaque resource digest")
        return digest

    async def _write_resource_snapshot(
        self,
        spec: FunctionSpec,
        employee_id: str | None,
        parameters: Mapping[str, Any],
        *,
        validate_precondition: bool,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
        kind = spec.resource_snapshot
        if kind is None:
            raise ApplicationError("write has no resource snapshot plan")

        if kind is ResourceSnapshotKind.EMPLOYEE_CREATE:
            result = await self.dic.list_employees(
                EmployeeListQuery(employee_filter=EmployeeFilter.ALL, page=1, page_size=1)
            )
            creation_before: dict[str, Any] = {
                key: "[NOT_SET]" for key in parameters if key != "creation_mode"
            }
            creation_before["existing_employee_count"] = result.total
            return creation_before, {}, "Nuovo dipendente - elenco sorgente verificato"

        if kind is ResourceSnapshotKind.EXPORT_SOURCE:
            scope = parameters.get("scope")
            if not isinstance(scope, str):
                raise ApplicationError("export scope is invalid")
            export_before: dict[str, Any] = {
                "scope": scope,
                "source_state": "[VERIFIED_OPAQUE]",
            }
            if scope == "employees":
                result = await self.dic.list_employees(
                    EmployeeListQuery(employee_filter=EmployeeFilter.ALL, page=1, page_size=1)
                )
                export_before["record_count"] = result.total
            return export_before, {}, f"Export protetto - ambito {scope}"

        if employee_id is None:
            raise ApplicationError("write resource requires employee_id")
        summary = await self.dic.get_employee_summary(employee_id)
        redacted_name = " ".join(
            value for value in (summary.first_name_redacted, summary.last_name_redacted) if value
        )
        target = f"{summary.employee_id} - {redacted_name or '[NOME REDATTO]'}"

        if kind is ResourceSnapshotKind.EMPLOYEE:
            if spec.deletes_resource:
                return (
                    {
                        "employee_id": summary.employee_id,
                        "employee_state": summary.state.value,
                        "name": redacted_name or "[NOME REDATTO]",
                    },
                    {},
                    target,
                )
            employee_current: dict[str, Any] = {
                "first_name": summary.first_name_redacted,
                "last_name": summary.last_name_redacted,
                "payroll_number": summary.payroll_number,
                "tax_code": summary.tax_code_redacted,
                "birth_date": summary.birth_date_redacted,
                "iban": summary.iban_redacted,
                "job_title": summary.job_title,
                "phone": summary.phone_redacted,
                "business_email": summary.business_email_redacted,
                "address": summary.address_redacted,
                "workplace": summary.workplace,
                "notes": summary.notes_redacted,
            }
            return {key: employee_current[key] for key in parameters}, {}, target

        if kind is ResourceSnapshotKind.ACCOUNT:
            item = await self._employee_list_item(employee_id)
            field = next(iter(dict(spec.fixed_preview_after)), None)
            if field == "account_state":
                account_before = {field: item.account_state.value}
            elif field == "employee_state":
                account_before = {field: item.employee_state.value}
            else:
                raise ApplicationError("account action has no fixed state transition")
            return account_before, {}, target

        if kind is ResourceSnapshotKind.CONTRACT:
            contracts = await self.dic.get_contracts(employee_id)
            contract_id = parameters.get("contract_id")
            if contract_id is None:
                contract_before: dict[str, Any] = {key: "[NOT_SET]" for key in parameters}
                contract_before["existing_contract_count"] = len(contracts)
                return contract_before, {}, target
            if not isinstance(contract_id, str):
                raise ApplicationError("contract target is invalid")
            contract_matches = [record for record in contracts if record.contract_id == contract_id]
            if len(contract_matches) != 1:
                raise ApplicationError("contract target must identify exactly one current record")
            contract = contract_matches[0]
            contract_current: dict[str, Any] = contract.model_dump(mode="json", exclude_none=False)
            if contract_current.get("description") is not None:
                contract_current["description"] = "[REDACTED]"
            contract_before = {key: contract_current.get(key) for key in parameters}
            contract_before["contract_status"] = contract.status
            return contract_before, {}, target

        if kind is ResourceSnapshotKind.MATURATION_COLLECTION:
            records = await self.dic.get_maturations(employee_id)
            category = parameters.get("category")
            valid_from = parameters.get("valid_from")
            valid_to = parameters.get("valid_to")
            duplicate = any(
                record.category.casefold() == str(category).casefold()
                and record.valid_from == valid_from
                and record.valid_to == valid_to
                for record in records
            )
            if duplicate and validate_precondition:
                raise ApplicationError("maturation already exists with the approved values")
            maturation_before: dict[str, Any] = {key: "[NOT_SET]" for key in parameters}
            maturation_before["existing_maturation_count"] = len(records)
            return maturation_before, {}, target

        if kind is ResourceSnapshotKind.BALANCE_CORRECTION:
            year = parameters.get("year")
            month = parameters.get("month")
            category = parameters.get("category")
            if (
                not isinstance(year, int)
                or isinstance(year, bool)
                or not isinstance(month, int)
                or isinstance(month, bool)
                or not isinstance(category, str)
            ):
                raise ApplicationError("invalid balance correction target")
            state = await self.dic.get_balance_correction_state(employee_id, year, month, category)
            try:
                expected_previous = canonical_decimal_text(parameters.get("previous_value"))
            except ValueError as exc:
                raise ApplicationError("invalid previous balance value") from exc
            if validate_precondition and state.current_value != expected_previous:
                raise ApplicationError("previous balance value does not match the current resource")
            return (
                {
                    "year": state.year,
                    "month": state.month,
                    "category": state.category,
                    "previous_value": state.current_value,
                    "amount": state.current_value,
                },
                {},
                target,
            )

        if kind is ResourceSnapshotKind.ROLE:
            role_name = parameters.get("role_name")
            if not isinstance(role_name, str):
                raise ApplicationError("invalid role target")
            roles = await self.dic.get_roles(employee_id)
            role_matches = [
                role for role in roles.roles if role.name.casefold() == role_name.casefold()
            ]
            if len(role_matches) != 1:
                raise ApplicationError("role target must identify exactly one current role")
            current_role = role_matches[0]
            return (
                {
                    "role_name": current_role.name,
                    "enabled": current_role.enabled,
                },
                {},
                target,
            )

        if kind is ResourceSnapshotKind.DOCUMENT:
            document_id = parameters.get("document_id")
            if not isinstance(document_id, str):
                raise ApplicationError("invalid document target")
            documents = await self.dic.get_document_metadata(employee_id, DocumentQuery())
            document_matches = [record for record in documents if record.document_id == document_id]
            if len(document_matches) != 1:
                raise ApplicationError("document target must identify exactly one metadata record")
            document = document_matches[0]
            document_current: dict[str, Any] = document.model_dump(mode="json", exclude_none=False)
            document_before = {key: document_current.get(key) for key in parameters}
            document_before["state"] = document.state
            document_before["title"] = document.title_redacted
            return document_before, {}, target

        if kind is ResourceSnapshotKind.DOCUMENT_COLLECTION:
            if self.files is None:
                raise ApplicationError("file delivery boundary is unavailable")
            upload_id = parameters.get("upload_id")
            if not isinstance(upload_id, str):
                raise ApplicationError("upload identifier is invalid")
            resolved = await self.files.resolve_clean_upload(upload_id)
            documents = await self.dic.get_document_metadata(employee_id, DocumentQuery())
            upload_before: dict[str, Any] = {key: "[NOT_SET]" for key in parameters}
            upload_before["upload_state"] = UploadStatus.CLEAN.value
            upload_before["upload_size"] = resolved.size_bytes
            upload_before["existing_document_count"] = len(documents)
            local_cas: dict[str, Any] = {
                "upload_id": resolved.upload_id,
                "sha256": resolved.sha256,
                "size": resolved.size_bytes,
                "detected_mime": resolved.detected_mime,
            }
            return upload_before, local_cas, target

        raise ApplicationError("write resource snapshot kind is unsupported")

    async def _employee_list_item(self, employee_id: str) -> Any:
        result = await self.dic.list_employees(
            EmployeeListQuery(
                query=employee_id,
                employee_filter=EmployeeFilter.ALL,
                page=1,
                page_size=100,
            )
        )
        matches = [item for item in result.items if item.employee_id == employee_id]
        if len(matches) != 1:
            raise ApplicationError("employee target must identify exactly one current row")
        return matches[0]

    def _context(
        self,
        actor: DiscordActor,
        function_id: str,
        *,
        phase: PolicyPhase = PolicyPhase.PREPARE,
        target_employee_id: str | None = None,
        operation_scope: str = "default",
    ) -> PolicyContext:
        return PolicyContext(
            function_id=function_id,
            user_id=str(actor.user_id),
            guild_id=str(actor.guild_id),
            channel_id=str(actor.channel_id),
            allowed_guild_ids=self.scope.allowed_guild_ids,
            allowed_channel_ids=self.scope.allowed_channel_ids,
            roles=frozenset(actor.logical_roles),
            flags=self.flags,
            current_tenant_id=self.scope.current_tenant_id,
            allowed_tenant_ids=self.scope.allowed_tenant_ids,
            phase=phase,
            operation_scope=operation_scope,
            entitlements=actor.entitlements,
            target_employee_id=target_employee_id,
            system_capabilities=self.scope.capabilities,
        )

    @staticmethod
    def _operation_scope(intent: IntentEnvelope, request: str) -> str:
        if intent.function_id == "EMP-EXPORT-001":
            export_scope = intent.parameters.get("scope")
            if export_scope in {"employees", "balances", "documents"}:
                return str(export_scope)
        if intent.function_id == "EMP-READ-001" and is_employee_aggregate_request(request):
            return "aggregate"
        return "default"

    @staticmethod
    def _parse_dic_date(value: str) -> date | None:
        candidate = value.strip()
        for parser in (
            date.fromisoformat,
            lambda raw: datetime.strptime(raw, "%d/%m/%Y").date(),
        ):
            try:
                return parser(candidate)
            except ValueError:
                continue
        return None

    @staticmethod
    def _employee_display_name(item: EmployeeListItem) -> str:
        if item.display_name is None:
            return item.display_name_redacted
        try:
            value = normalize_text(
                item.display_name.get_secret_value(),
                max_length=256,
                allow_newlines=False,
            )
        except InputValidationError:
            return item.display_name_redacted
        return value or item.display_name_redacted

    @classmethod
    def _employee_export_rows(
        cls,
        items: tuple[EmployeeListItem, ...],
    ) -> tuple[EmployeeExportRow, ...]:
        rows: list[EmployeeExportRow] = []
        for item in items:
            first_name = (
                item.first_name.get_secret_value().strip() if item.first_name is not None else None
            )
            last_name = (
                item.last_name.get_secret_value().strip() if item.last_name is not None else None
            )
            if not first_name and not last_name and item.display_name is not None:
                # The DIC response normally exposes the separate fields. Preserve the complete
                # name without guessing compound-name boundaries if those optional keys vanish.
                first_name = cls._employee_display_name(item)
            contract_parts = [
                value
                for value in (item.contract_state, item.contract_label)
                if value is not None and value.strip()
            ]
            rows.append(
                EmployeeExportRow(
                    first_name=first_name or None,
                    last_name=last_name or None,
                    employee_id=item.employee_id,
                    status=item.employee_state.value,
                    contract_expiry=item.current_contract_valid_to,
                    contract_type=" · ".join(contract_parts) or None,
                    monthly_net=None,
                )
            )
        rows.sort(
            key=lambda row: (
                (row.last_name or "").casefold(),
                (row.first_name or "").casefold(),
                row.employee_id,
            )
        )
        return tuple(rows)

    @staticmethod
    def _require_employee(intent: IntentEnvelope) -> str:
        if intent.employee_id is None:
            raise ApplicationError("employee_id is required")
        return intent.employee_id

    @staticmethod
    def _redact_list_identifier(value: str | None) -> str:
        """Keep a short suffix useful for disambiguation without exposing the identifier."""

        if not value:
            return "—"
        visible = min(4, len(value))
        return f"{'*' * (len(value) - visible)}{value[-visible:]}"

    @staticmethod
    def _spec(function_id: str) -> FunctionSpec:
        spec = get_function_spec(function_id)
        if spec is None:
            raise ApplicationError("unknown Function ID")
        return spec

    @staticmethod
    def _operator_spec(function_id: str) -> FunctionSpec:
        spec = BHApplicationCoordinator._spec(function_id)
        if not spec.is_write or spec.expose_to_model or not spec.write_parameters:
            raise ApplicationError("Function ID is not available through operator commands")
        return spec

    @staticmethod
    def _validate_write_parameters(
        spec: FunctionSpec, parameters: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            return validate_write_parameters(spec, parameters)
        except WriteParameterValidationError as exc:
            raise ApplicationError(str(exc)) from exc

    @staticmethod
    def _build_write_preview(
        spec: FunctionSpec,
        redacted_parameters: Mapping[str, Any],
        redacted_before: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        fixed_after = dict(spec.fixed_preview_after)
        fields = set(redacted_before) | set(redacted_parameters) | set(fixed_after)
        result: dict[str, dict[str, Any]] = {}
        for field in sorted(fields):
            before = redacted_before.get(field, "[NOT_SET]")
            if spec.deletes_resource:
                after = "[DELETE]"
            elif field in fixed_after:
                after = fixed_after[field]
            else:
                after = redacted_parameters.get(field, "[UNCHANGED]")
            result[str(field)] = {"before": before, "after": after}
        return result

    @staticmethod
    def _redact_write_preview_values(
        spec: FunctionSpec,
        values: Mapping[str, Any],
        *,
        invalid_shape_message: str,
    ) -> dict[str, Any]:
        redacted = redact_structure(values)
        if not isinstance(redacted, Mapping):
            raise ApplicationError(invalid_shape_message)
        result = dict(redacted)
        structural_placeholders = {
            "[NOT_SET]",
            "[UNCHANGED]",
            "[DELETE]",
            "[REDACTED]",
            "[PII_REDACTED]",
        }
        for field, value in result.items():
            mask = spec.preview_mask(str(field))
            if mask is not None and value not in structural_placeholders:
                result[field] = mask
        return result

    @staticmethod
    def _preview_changes_resource(
        spec: FunctionSpec, preview: Mapping[str, Mapping[str, Any]]
    ) -> bool:
        if spec.deletes_resource or spec.always_effectful:
            return True
        return any(
            field not in spec.write_selectors
            and change.get("after") != "[UNCHANGED]"
            and change.get("before") != change.get("after")
            for field, change in preview.items()
        )

    @staticmethod
    def _direct_intent(
        function_id: str,
        *,
        employee_id: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> IntentEnvelope:
        spec = BHApplicationCoordinator._spec(function_id)
        return IntentEnvelope(
            intent=function_id.lower().replace("-", "_"),
            function_id=function_id,
            action_class=spec.action_class.value,
            employee_id=employee_id,
            query=None,
            parameters=dict(parameters or {}),
            date_from=date_from,
            date_to=date_to,
            requires_clarification=False,
            clarification_question=None,
            sensitivity=spec.sensitivity.value,
            confidence=1.0,
        )

    async def _audit_denial(
        self,
        actor: DiscordActor,
        correlation_id: str,
        intent: IntentEnvelope,
        decision: PolicyDecision,
    ) -> None:
        await self._audit(
            actor,
            correlation_id,
            "policy.denied",
            intent.function_id,
            AuditOutcome.DENIED,
            intent.employee_id,
            {"decision": decision.code.value},
        )

    async def _audit(
        self,
        actor: DiscordActor,
        correlation_id: str,
        event_type: str,
        function_id: str | None,
        outcome: AuditOutcome,
        employee_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        if self.audit is None:
            return
        target = pseudonymize_identifier(employee_id, self._pseudonym_key) if employee_id else None
        await self.audit.append(
            AuditEventInput(
                event_type=event_type,
                correlation_id=correlation_id,
                actor_discord_id=str(actor.user_id),
                guild_id=str(actor.guild_id),
                channel_id=str(actor.channel_id),
                function_id=function_id,
                target_pseudonym=target,
                outcome=outcome,
                payload=payload,
            )
        )

    @staticmethod
    def _unavailable(message: str) -> InteractionResult:
        return InteractionResult(
            title="Componente non disponibile",
            description=message,
            success=False,
        )
