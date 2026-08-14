"""Application-level vertical slice shared by every Discord interaction mode.

The model produces only an intent candidate.  This module repeats policy checks,
dispatches reads to deterministic services, prepares writes, and appends audit
events.  No provider object can reach the browser adapter.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from bh_dic.approvals.models import ActionStatus, PendingAction
from bh_dic.approvals.service import ApprovalService
from bh_dic.approvals.storage import ApprovalRepository
from bh_dic.audit.models import AuditEventInput, AuditOutcome
from bh_dic.audit.service import AuditService
from bh_dic.dic.errors import DicAmbiguousWriteOutcomeError, DicReconciliationRequiredError
from bh_dic.dic.models import (
    DocumentQuery,
    EmployeeFilter,
    EmployeeListQuery,
    ReconciliationState,
    SortDirection,
)
from bh_dic.discord.checks import DiscordActor
from bh_dic.discord.interactions import (
    AttachmentPayload,
    InteractionCoordinator,
    InteractionResult,
    ResponseSensitivity,
    ResultField,
)
from bh_dic.files.models import UploadStatus
from bh_dic.files.service import FileService
from bh_dic.logging import get_logger
from bh_dic.openai.intent_router import IntentRouter
from bh_dic.openai.redaction import redact_structure
from bh_dic.openai.schemas import ActionClass, IntentEnvelope
from bh_dic.policies.catalog import FUNCTION_CATALOG, FunctionSpec, get_function_spec
from bh_dic.policies.decisions import PolicyDecision
from bh_dic.policies.engine import PolicyContext, PolicyEngine, PolicyPhase
from bh_dic.policies.feature_flags import FeatureFlags
from bh_dic.policies.roles import LogicalRole, normalize_roles
from bh_dic.security.cipher import PayloadCipher
from bh_dic.security.pii import pseudonymize_identifier
from bh_dic.services.dic_service import DicService

logger = get_logger("application")


class ApplicationError(RuntimeError):
    pass


class ApplicationPolicyDenied(ApplicationError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True, slots=True)
class ApplicationScope:
    allowed_guild_ids: frozenset[str]
    allowed_channel_ids: frozenset[str]
    current_tenant_id: str
    allowed_tenant_ids: frozenset[str]
    capabilities: frozenset[str] = frozenset()


RequesterActorResolver = Callable[[PendingAction], Awaitable[DiscordActor]]


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
        requester_actor_resolver: RequesterActorResolver | None = None,
    ) -> None:
        if len(pseudonym_key) < 32:
            raise ValueError("pseudonym key must contain at least 32 bytes")
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
        self.requester_actor_resolver = requester_actor_resolver
        self._pseudonym_key = bytes(pseudonym_key)

    async def ask(self, actor: DiscordActor, request: str) -> InteractionResult:
        correlation_id = str(uuid.uuid4())
        exposure_context = self._context(
            actor,
            "EMP-READ-001",
            phase=PolicyPhase.EXPOSURE,
            operation_scope="aggregate",
        )
        visible = self.policy.visible_function_ids(exposure_context)
        routed = await self.router.route(request, visible)
        intent = routed.envelope
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
            return InteractionResult(
                title="Chiarimento necessario",
                description=intent.clarification_question or "Specifica meglio la richiesta.",
                correlation_id=correlation_id,
                success=False,
            )
        if intent.function_id == "UNSUPPORTED":
            return InteractionResult(
                title="Funzione non disponibile",
                description="La richiesta non corrisponde a una funzione autorizzata.",
                correlation_id=correlation_id,
                success=False,
            )

        scope = self._operation_scope(intent, request)
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
            raise ApplicationPolicyDenied(decision)

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

    async def status(self, actor: DiscordActor) -> InteractionResult:
        del actor
        write_enabled = self.flags.enabled("ENABLE_WRITE_ACTIONS")
        health = await self.dic.adapter.health()
        return InteractionResult(
            title="Stato BH-DiC",
            description="Stato operativo redatto.",
            fields=(
                ResultField("Adapter", "READY" if health.ready else "DEGRADED", True),
                ResultField(
                    "Browser", "available" if health.browser_available else "mock/unavailable", True
                ),
                ResultField("Write kill switch", "ENABLED" if write_enabled else "DISABLED", True),
            ),
        )

    async def health(self, actor: DiscordActor) -> InteractionResult:
        del actor
        health = await self.dic.adapter.health()
        return InteractionResult(
            title="Health check",
            description=health.detail,
            fields=(
                ResultField("Ready", str(health.ready), True),
                ResultField("Authenticated", str(health.authenticated), True),
            ),
            success=health.ready,
        )

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
            parameters={"upload_id": record.upload_id, "category": category},
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
        del actor
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
            result = await self.dic.list_employees(employee_query)
            if operation_scope == "aggregate":
                return InteractionResult(
                    title="Conteggio dipendenti",
                    description=f"Totale nel filtro richiesto: {result.total}",
                    correlation_id=correlation_id,
                    sensitivity=ResponseSensitivity.PUBLIC_AGGREGATE,
                )
            fields = tuple(
                ResultField(
                    f"Employee ID {item.employee_id}",
                    "\n".join(
                        (
                            f"Nome redatto: {item.display_name_redacted}",
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
            return InteractionResult(
                title="Dipendenti",
                description=(
                    f"Pagina {result.page}; mostrati {len(result.items)} su {result.total}; "
                    f"pagina successiva: {'sì' if result.has_next else 'no'}"
                ),
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
                    ResultField("Matricola", summary.payroll_number or "—", True),
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
            return InteractionResult(
                title=f"Maturazioni {employee_id}",
                description=f"Record: {len(maturation_records)}",
                fields=tuple(
                    ResultField(
                        record.category, f"{record.valid_from or '—'} → {record.valid_to or '—'}"
                    )
                    for record in maturation_records[:25]
                ),
                correlation_id=correlation_id,
            )
        if function_id == "EMP-BAL-001":
            employee_id = self._require_employee(intent)
            year = int(intent.parameters.get("year", datetime.now(UTC).year))
            balance = await self.dic.get_balance(employee_id, year)
            return InteractionResult(
                title=f"Bilancio {employee_id} — {year}",
                description=f"Categorie: {len(balance.lines)}",
                fields=tuple(
                    ResultField(line.category, f"Residuo corrente: {line.current_residual or '—'}")
                    for line in balance.lines[:25]
                ),
                correlation_id=correlation_id,
            )
        if function_id == "EMP-PAY-001":
            employee_id = self._require_employee(intent)
            payroll_year_raw = intent.parameters.get("year")
            payroll_records = await self.dic.get_payroll_metadata(
                employee_id,
                int(payroll_year_raw) if payroll_year_raw is not None else None,
            )
            return InteractionResult(
                title=f"Metadati buste paga {employee_id}",
                description=(
                    f"Record minimizzati: {len(payroll_records)}; "
                    f"mostrati: {min(len(payroll_records), 25)}"
                ),
                fields=tuple(
                    ResultField(
                        f"Record {index}",
                        (
                            f"Anno: {record.year} · "
                            f"mese: {record.month if record.month is not None else '—'} · "
                            f"stato: {record.status or '—'} · "
                            f"pubblicata: {record.published_at or '—'}"
                        ),
                    )
                    for index, record in enumerate(payroll_records[:25], start=1)
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
            return InteractionResult(
                title=f"Metadati documenti {employee_id}",
                description=f"Record: {len(document_records)}",
                fields=tuple(
                    ResultField(
                        record.document_id,
                        f"{record.category or '—'} · {record.state} · "
                        f"scadenza {record.expiry_date or '—'}",
                    )
                    for record in document_records[:25]
                ),
                correlation_id=correlation_id,
            )
        raise ApplicationError(f"read dispatcher missing for {function_id}")

    async def _render_contracts(
        self, intent: IntentEnvelope, correlation_id: str
    ) -> InteractionResult:
        employee_ids: list[str]
        if intent.employee_id:
            employee_ids = [intent.employee_id]
        else:
            employee_ids = []
            page = 1
            while True:
                result = await self.dic.list_employees(
                    EmployeeListQuery(employee_filter=EmployeeFilter.ALL, page=page, page_size=100)
                )
                employee_ids.extend(item.employee_id for item in result.items)
                if not result.has_next:
                    break
                page += 1
                if page > 100:
                    raise ApplicationError("employee pagination safety limit exceeded")
        fields: list[ResultField] = []
        for employee_id in employee_ids:
            for contract in await self.dic.get_contracts(employee_id):
                end: date | None = None
                if contract.end_date:
                    try:
                        end = date.fromisoformat(contract.end_date)
                    except ValueError:
                        end = None
                if intent.date_from and (end is None or end < intent.date_from):
                    continue
                if intent.date_to and (end is None or end > intent.date_to):
                    continue
                fields.append(
                    ResultField(
                        employee_id,
                        f"{contract.contract_type or 'contratto'} · "
                        f"fine {contract.end_date or 'indeterminato'}",
                    )
                )
        fields.sort(key=lambda item: item.value)
        return InteractionResult(
            title="Contratti e scadenze",
            description=f"Intervallo: {intent.date_from or 'inizio'} → {intent.date_to or 'fine'}",
            fields=tuple(fields[:25]),
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
        if spec.requires_target and not intent.employee_id:
            return InteractionResult(
                title="Employee ID necessario",
                description="Le modifiche non possono usare soltanto il nome.",
                correlation_id=correlation_id,
                success=False,
            )
        if not intent.parameters and intent.function_id not in {
            "EMP-INVITE-001",
            "EMP-INVITE-002",
            "EMP-CONNECT-001",
            "EMP-CONNECT-002",
            "EMP-STATUS-001",
            "EMP-STATUS-002",
        }:
            return InteractionResult(
                title="Parametri mancanti",
                description="Usa il comando deterministico dedicato o specifica i nuovi valori.",
                correlation_id=correlation_id,
                success=False,
            )
        motivation_value = intent.parameters.get("motivation")
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
            key: value for key, value in intent.parameters.items() if key != "motivation"
        }
        state_fingerprint, target_label = await self._target_snapshot(intent.employee_id)
        redacted = redact_structure(action_parameters)
        if not isinstance(redacted, Mapping):
            raise ApplicationError("redacted parameters must remain an object")
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
            redacted_diff={
                key: {"before": "[CURRENT]", "after": value} for key, value in redacted.items()
            },
            state_fingerprint=state_fingerprint,
            motivation=motivation,
        )
        fields = (
            ResultField("Target verificato", target_label),
            *(ResultField(str(key), f"[CURRENT] → {value}") for key, value in redacted.items()),
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
        requester = (
            await self.requester_actor_resolver(action)
            if self.requester_actor_resolver is not None
            else stored_requester
        )
        self._validate_requester_binding(requester, action)
        # Re-check the requester's current RBAC (when a live resolver is configured),
        # tenant, feature flags, target and system state.  Approver roles never
        # substitute for the requester's authorization.
        decision = self.policy.evaluate(
            self._context(
                requester,
                action.function_id,
                phase=PolicyPhase.EXECUTE,
                target_employee_id=action.target_employee_id,
            )
        )
        if not decision.allowed:
            raise ApplicationPolicyDenied(decision)
        current_fingerprint = await self._state_fingerprint(action.target_employee_id)
        executing = await self.approvals.begin_execution(
            action.action_id,
            current_state_fingerprint=current_fingerprint,
        )
        try:
            result = await self.dic.execute(executing, parameters)
            completed = await self.approvals.complete_success(
                action.action_id,
                execution_result=json.dumps(result.model_dump(mode="json"), sort_keys=True),
                postcondition_result=result.message,
                postcondition_verified=result.postcondition_verified,
            )
        except (DicAmbiguousWriteOutcomeError, DicReconciliationRequiredError):
            completed = await self.approvals.complete_failure(
                action.action_id,
                result="write outcome uncertain; reconciliation required",
                outcome_uncertain=True,
            )
            try:
                reconciliation = await self.dic.reconcile(completed, parameters)
            except Exception:
                # A reconciliation read may itself fail.  The durable UNKNOWN state
                # is retained and the mutating operation is never retried.
                logger.warning(
                    "Write reconciliation read failed",
                    extra={
                        "event_type": "write.reconciliation_failed",
                        "correlation_id": action.correlation_id,
                        "function_id": action.function_id,
                        "outcome": "UNCERTAIN",
                    },
                )
            else:
                if reconciliation.state is not ReconciliationState.UNKNOWN:
                    completed = await self.approvals.reconcile(
                        action.action_id,
                        postcondition_met=(
                            reconciliation.state is ReconciliationState.CONFIRMED_APPLIED
                        ),
                        result=reconciliation.detail,
                    )
        except Exception:
            await self.approvals.complete_failure(
                action.action_id,
                result="deterministic execution failed before a verified result",
                outcome_uncertain=False,
            )
            raise
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
            description=f"`{action.action_id}`: {completed.status.value}",
            correlation_id=action.correlation_id,
            success=applied,
        )

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

    async def _state_fingerprint(self, employee_id: str | None) -> str:
        fingerprint, _target = await self._target_snapshot(employee_id)
        return fingerprint

    async def _target_snapshot(self, employee_id: str | None) -> tuple[str, str]:
        import hashlib

        if employee_id is None:
            material = b"NEW_OR_TARGETLESS"
            target = "Nuovo record o operazione senza singolo target"
        else:
            summary = await self.dic.get_employee_summary(employee_id)
            material = summary.model_dump_json(exclude_none=False).encode("utf-8")
            redacted_name = " ".join(
                value
                for value in (summary.first_name_redacted, summary.last_name_redacted)
                if value
            )
            target = f"{summary.employee_id} · {redacted_name or '[NOME REDATTO]'}"
        return hashlib.sha256(material).hexdigest(), target

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
        if intent.function_id == "EMP-READ-001" and any(
            marker in request.casefold() for marker in ("quanti", "conteggio", "numero")
        ):
            return "aggregate"
        return "default"

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
