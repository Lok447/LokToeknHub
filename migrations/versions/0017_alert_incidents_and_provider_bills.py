"""Persist alert incidents and provider bill reconciliation details."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017_alert_incidents_and_provider_bills"
down_revision: str = "0016_billing_ledger_bigint_and_route_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(24), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_notified_state", sa.String(24), nullable=True),
        sa.Column("pending_event", sa.String(24), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("fingerprint", name="uq_alert_incidents_fingerprint"),
    )
    op.create_index("ix_alert_incidents_fingerprint", "alert_incidents", ["fingerprint"])
    op.create_index("ix_alert_incidents_code", "alert_incidents", ["code"])
    op.create_index("ix_alert_incidents_state", "alert_incidents", ["state"])
    op.create_index("ix_alert_incidents_last_seen_at", "alert_incidents", ["last_seen_at"])
    op.create_index("ix_alert_incidents_pending_event", "alert_incidents", ["pending_event"])
    op.create_table(
        "provider_bill_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mismatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billed_cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recorded_cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "source_hash", name="uq_provider_bill_imports_provider_hash"),
    )
    op.create_index("ix_provider_bill_imports_provider", "provider_bill_imports", ["provider"])
    op.create_index("ix_provider_bill_imports_source_hash", "provider_bill_imports", ["source_hash"])
    op.create_table(
        "provider_bill_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_id", sa.Integer(), sa.ForeignKey("provider_bill_imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_key", sa.String(160), nullable=False),
        sa.Column("provider_request_id", sa.String(160), nullable=True),
        sa.Column("billed_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billed_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billed_cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recorded_cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("usage_record_id", sa.Integer(), sa.ForeignKey("usage_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("diff_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("import_id", "line_key", name="uq_provider_bill_lines_import_key"),
    )
    op.create_index("ix_provider_bill_lines_import_id", "provider_bill_lines", ["import_id"])
    op.create_index("ix_provider_bill_lines_provider_request_id", "provider_bill_lines", ["provider_request_id"])
    op.create_index("ix_provider_bill_lines_status", "provider_bill_lines", ["status"])


def downgrade() -> None:
    op.drop_table("provider_bill_lines")
    op.drop_table("provider_bill_imports")
    op.drop_table("alert_incidents")
