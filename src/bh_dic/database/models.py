"""Minimal local data model.

The schema deliberately stores workflow metadata, encrypted parameters, pseudonyms, and redacted
results rather than mirroring the employee registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=new_uuid)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_discord_id: Mapped[str | None] = mapped_column(String(32))
    guild_id: Mapped[str | None] = mapped_column(String(32))
    channel_id: Mapped[str | None] = mapped_column(String(32))
    function_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_pseudonym: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    __table_args__ = (
        CheckConstraint("length(previous_hash) = 64", name="previous_hash_length"),
        CheckConstraint("length(event_hash) = 64", name="event_hash_length"),
        Index("ix_audit_events_timestamp", "timestamp_utc"),
    )


class AuditChainState(Base):
    __tablename__ = "audit_chain_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("last_sequence >= 0", name="last_sequence_non_negative"),
        CheckConstraint("length(last_hash) = 64", name="last_hash_length"),
    )


class DiscordRequest(Base):
    __tablename__ = "discord_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    correlation_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    requester_discord_id: Mapped[str] = mapped_column(String(32), nullable=False)
    guild_id: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sanitized_request: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_discord_requests_created", "created_at"),)


class ModelUsageEvent(Base):
    """One retained model call whose lifecycle may advance only from STARTED once."""

    __tablename__ = "model_usage_events"

    usage_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STARTED")
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "correlation_id",
            "purpose",
            "ordinal",
            name="uq_model_usage_events_correlation_purpose_ordinal",
        ),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "status IN ('STARTED', 'REPORTED', 'UNAVAILABLE', 'UNKNOWN')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'STARTED' AND completed_at IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL) "
            "OR (status = 'REPORTED' AND completed_at IS NOT NULL "
            "AND input_tokens IS NOT NULL AND input_tokens >= 0 "
            "AND output_tokens IS NOT NULL AND output_tokens >= 0 "
            "AND total_tokens IS NOT NULL AND total_tokens = input_tokens + output_tokens) "
            "OR (status IN ('UNAVAILABLE', 'UNKNOWN') AND completed_at IS NOT NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL)",
            name="lifecycle_consistent",
        ),
        Index("ix_model_usage_events_created", "created_at"),
        Index("ix_model_usage_events_status", "status"),
    )


class PendingAction(Base):
    __tablename__ = "pending_actions"

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    function_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requester_discord_id: Mapped[str] = mapped_column(String(32), nullable=False)
    guild_id: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    target_employee_id: Mapped[str | None] = mapped_column(String(128))
    encrypted_parameters: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    redacted_diff: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    motivation: Mapped[str | None] = mapped_column(Text)
    state_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approvals_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approvals_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmation_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    confirmation_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    confirmation_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    postcondition_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    approvals: Mapped[list[Approval]] = relationship(
        back_populates="action", cascade="all, delete-orphan", lazy="selectin"
    )
    execution: Mapped[ActionExecution | None] = relationship(
        back_populates="action", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("approvals_required >= 0", name="approvals_required_non_negative"),
        CheckConstraint("approvals_received >= 0", name="approvals_received_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_pending_actions_status_expiry", "status", "expires_at"),
    )


class Approval(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("pending_actions.action_id", ondelete="CASCADE"), nullable=False, index=True
    )
    approver_discord_id: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    redacted_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    action: Mapped[PendingAction] = relationship(back_populates="approvals")

    __table_args__ = (
        UniqueConstraint("action_id", "approver_discord_id", name="uq_approval_action_approver"),
    )


class ActionExecution(Base):
    __tablename__ = "action_executions"

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("pending_actions.action_id", ondelete="CASCADE"), unique=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    postcondition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    uncertain_outcome: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    action: Mapped[PendingAction] = relationship(back_populates="execution")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    upload_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uploader_discord_id: Mapped[str] = mapped_column(String(32), nullable=False)
    original_name_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_mime: Mapped[str | None] = mapped_column(String(127))
    detected_mime: Mapped[str | None] = mapped_column(String(127))
    storage_path: Mapped[str | None] = mapped_column(String(1024))
    bucket: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUARANTINED")
    antivirus_result: Mapped[str | None] = mapped_column(String(64))
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="sha256_length"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_uploaded_files_status_expiry", "status", "expires_at"),
    )


class BrowserJob(Base):
    __tablename__ = "browser_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    function_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_pseudonym: Mapped[str | None] = mapped_column(String(80))
    is_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redacted_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        Index("ix_browser_jobs_status", "status"),
    )


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    updated_by_discord_id: Mapped[str | None] = mapped_column(String(32))


class SchemaVersion(Base):
    __tablename__ = "schema_versions"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (CheckConstraint("length(checksum) = 64", name="checksum_length"),)
