"""Add privacy-minimized provider token usage telemetry.

Revision ID: 0002_model_usage
Revises: 0001_foundation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_model_usage"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_usage_events",
        sa.Column("usage_id", sa.String(length=36), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ordinal >= 1",
            name=op.f("ck_model_usage_events_ordinal_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('STARTED', 'REPORTED', 'UNAVAILABLE', 'UNKNOWN')",
            name=op.f("ck_model_usage_events_status_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'STARTED' AND completed_at IS NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL) "
            "OR (status = 'REPORTED' AND completed_at IS NOT NULL "
            "AND input_tokens IS NOT NULL AND input_tokens >= 0 "
            "AND output_tokens IS NOT NULL AND output_tokens >= 0 "
            "AND total_tokens IS NOT NULL AND total_tokens = input_tokens + output_tokens) "
            "OR (status IN ('UNAVAILABLE', 'UNKNOWN') AND completed_at IS NOT NULL "
            "AND input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL)",
            name=op.f("ck_model_usage_events_lifecycle_consistent"),
        ),
        sa.PrimaryKeyConstraint("usage_id", name=op.f("pk_model_usage_events")),
        sa.UniqueConstraint(
            "correlation_id",
            "purpose",
            "ordinal",
            name="uq_model_usage_events_correlation_purpose_ordinal",
        ),
    )
    op.create_index(
        "ix_model_usage_events_created",
        "model_usage_events",
        ["created_at"],
    )
    op.create_index(
        "ix_model_usage_events_status",
        "model_usage_events",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_usage_events_status", table_name="model_usage_events")
    op.drop_index("ix_model_usage_events_created", table_name="model_usage_events")
    op.drop_table("model_usage_events")
