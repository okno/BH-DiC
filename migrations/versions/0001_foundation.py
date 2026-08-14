"""Create the minimal BH-DiC workflow and audit schema.

Revision ID: 0001_foundation
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("actor_discord_id", sa.String(length=32), nullable=True),
        sa.Column("guild_id", sa.String(length=32), nullable=True),
        sa.Column("channel_id", sa.String(length=32), nullable=True),
        sa.Column("function_id", sa.String(length=64), nullable=True),
        sa.Column("target_pseudonym", sa.String(length=80), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "length(event_hash) = 64", name=op.f("ck_audit_events_event_hash_length")
        ),
        sa.CheckConstraint(
            "length(previous_hash) = 64",
            name=op.f("ck_audit_events_previous_hash_length"),
        ),
        sa.PrimaryKeyConstraint("sequence", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("event_hash", name=op.f("uq_audit_events_event_hash")),
        sa.UniqueConstraint("event_id", name=op.f("uq_audit_events_event_id")),
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_events_function_id", "audit_events", ["function_id"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp_utc"])

    op.create_table(
        "audit_chain_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_audit_chain_state_singleton")),
        sa.CheckConstraint(
            "last_sequence >= 0",
            name=op.f("ck_audit_chain_state_last_sequence_non_negative"),
        ),
        sa.CheckConstraint(
            "length(last_hash) = 64",
            name=op.f("ck_audit_chain_state_last_hash_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_state")),
    )
    audit_chain_state = sa.table(
        "audit_chain_state",
        sa.column("id", sa.Integer()),
        sa.column("last_sequence", sa.Integer()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        audit_chain_state,
        [
            {
                "id": 1,
                "last_sequence": 0,
                "last_hash": "0" * 64,
                "updated_at": datetime.now(UTC),
            }
        ],
    )

    op.create_table(
        "discord_requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("requester_discord_id", sa.String(length=32), nullable=False),
        sa.Column("guild_id", sa.String(length=32), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("sanitized_request", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_discord_requests")),
        sa.UniqueConstraint("correlation_id", name=op.f("uq_discord_requests_correlation_id")),
    )
    op.create_index("ix_discord_requests_created", "discord_requests", ["created_at"])

    op.create_table(
        "pending_actions",
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("function_id", sa.String(length=64), nullable=False),
        sa.Column("requester_discord_id", sa.String(length=32), nullable=False),
        sa.Column("guild_id", sa.String(length=32), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("target_employee_id", sa.String(length=128), nullable=True),
        sa.Column("encrypted_parameters", sa.LargeBinary(), nullable=False),
        sa.Column("redacted_diff", sa.JSON(), nullable=False),
        sa.Column("motivation", sa.Text(), nullable=True),
        sa.Column("state_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approvals_required", sa.Integer(), nullable=False),
        sa.Column("approvals_received", sa.Integer(), nullable=False),
        sa.Column("confirmation_salt", sa.LargeBinary(), nullable=False),
        sa.Column("confirmation_digest", sa.LargeBinary(), nullable=False),
        sa.Column("confirmation_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("postcondition_result", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "approvals_received >= 0",
            name=op.f("ck_pending_actions_approvals_received_non_negative"),
        ),
        sa.CheckConstraint(
            "approvals_required >= 0",
            name=op.f("ck_pending_actions_approvals_required_non_negative"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_pending_actions_version_positive")),
        sa.PrimaryKeyConstraint("action_id", name=op.f("pk_pending_actions")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_pending_actions_idempotency_key")),
    )
    op.create_index("ix_pending_actions_correlation_id", "pending_actions", ["correlation_id"])
    op.create_index("ix_pending_actions_function_id", "pending_actions", ["function_id"])
    op.create_index("ix_pending_actions_status_expiry", "pending_actions", ["status", "expires_at"])

    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("approver_discord_id", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("redacted_reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["pending_actions.action_id"],
            name=op.f("fk_approvals_action_id_pending_actions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("approval_id", name=op.f("pk_approvals")),
        sa.UniqueConstraint(
            "action_id",
            "approver_discord_id",
            name=op.f("uq_approval_action_approver"),
        ),
    )
    op.create_index("ix_approvals_action_id", "approvals", ["action_id"])

    op.create_table(
        "action_executions",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("postcondition", sa.JSON(), nullable=True),
        sa.Column("uncertain_outcome", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["pending_actions.action_id"],
            name=op.f("fk_action_executions_action_id_pending_actions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("execution_id", name=op.f("pk_action_executions")),
        sa.UniqueConstraint("action_id", name=op.f("uq_action_executions_action_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_action_executions_idempotency_key")),
    )

    op.create_table(
        "uploaded_files",
        sa.Column("upload_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("uploader_discord_id", sa.String(length=32), nullable=False),
        sa.Column("original_name_hash", sa.String(length=64), nullable=False),
        sa.Column("safe_name", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("declared_mime", sa.String(length=127), nullable=True),
        sa.Column("detected_mime", sa.String(length=127), nullable=True),
        sa.Column("storage_path", sa.String(length=1024), nullable=True),
        sa.Column("bucket", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("antivirus_result", sa.String(length=64), nullable=True),
        sa.Column("rejection_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name=op.f("ck_uploaded_files_sha256_length"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_uploaded_files_size_non_negative")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_uploaded_files_version_positive")),
        sa.PrimaryKeyConstraint("upload_id", name=op.f("pk_uploaded_files")),
    )
    op.create_index("ix_uploaded_files_correlation_id", "uploaded_files", ["correlation_id"])
    op.create_index("ix_uploaded_files_status_expiry", "uploaded_files", ["status", "expires_at"])

    op.create_table(
        "browser_jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("function_id", sa.String(length=64), nullable=False),
        sa.Column("target_pseudonym", sa.String(length=80), nullable=True),
        sa.Column("is_write", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redacted_result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_browser_jobs_attempt_count_non_negative")
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_browser_jobs")),
    )
    op.create_index("ix_browser_jobs_correlation_id", "browser_jobs", ["correlation_id"])
    op.create_index("ix_browser_jobs_status", "browser_jobs", ["status"])

    op.create_table(
        "feature_flags",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_discord_id", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_feature_flags")),
    )

    op.create_table(
        "schema_versions",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(checksum) = 64", name=op.f("ck_schema_versions_checksum_length")
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_schema_versions")),
    )


def downgrade() -> None:
    op.drop_table("schema_versions")
    op.drop_table("feature_flags")
    op.drop_index("ix_browser_jobs_status", table_name="browser_jobs")
    op.drop_index("ix_browser_jobs_correlation_id", table_name="browser_jobs")
    op.drop_table("browser_jobs")
    op.drop_index("ix_uploaded_files_status_expiry", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_correlation_id", table_name="uploaded_files")
    op.drop_table("uploaded_files")
    op.drop_table("action_executions")
    op.drop_index("ix_approvals_action_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_pending_actions_status_expiry", table_name="pending_actions")
    op.drop_index("ix_pending_actions_function_id", table_name="pending_actions")
    op.drop_index("ix_pending_actions_correlation_id", table_name="pending_actions")
    op.drop_table("pending_actions")
    op.drop_index("ix_discord_requests_created", table_name="discord_requests")
    op.drop_table("discord_requests")
    op.drop_table("audit_chain_state")
    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_function_id", table_name="audit_events")
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_table("audit_events")
