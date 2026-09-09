"""add gateway route diagnostics and durable task retry state

Revision ID: 0030_gateway_reliability
Revises: 0029_model_aliases
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0030_gateway_reliability"
down_revision = "0029_model_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("generation_tasks"):
        op.create_table(
            "generation_tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_id", sa.String(length=64), nullable=False),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("billing_accounts.id"), nullable=False),
            sa.Column("api_key_id", sa.Integer(), sa.ForeignKey("api_keys.id"), nullable=False),
            sa.Column("model_config_id", sa.Integer(), sa.ForeignKey("model_configs.id"), nullable=False),
            sa.Column("provider_channel_id", sa.Integer(), sa.ForeignKey("model_channels.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provider_task_id", sa.String(length=160), nullable=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=False),
            sa.Column("task_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("reserved_micros", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failure_class", sa.String(length=32), nullable=True),
            sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("task_id", name="uq_generation_tasks_task_id"),
            sa.UniqueConstraint("request_id", name="uq_generation_tasks_request_id"),
        )
        for name, column in {
            "ix_generation_tasks_task_id": ["task_id"],
            "ix_generation_tasks_account_id": ["account_id"],
            "ix_generation_tasks_api_key_id": ["api_key_id"],
            "ix_generation_tasks_model_config_id": ["model_config_id"],
            "ix_generation_tasks_provider_channel_id": ["provider_channel_id"],
            "ix_generation_tasks_provider_task_id": ["provider_task_id"],
            "ix_generation_tasks_request_id": ["request_id"],
            "ix_generation_tasks_trace_id": ["trace_id"],
            "ix_generation_tasks_task_type": ["task_type"],
            "ix_generation_tasks_status": ["status"],
            "ix_generation_tasks_created_at": ["created_at"],
        }.items():
            op.create_index(name, "generation_tasks", column)
    generation_columns = {item["name"] for item in inspect(bind).get_columns("generation_tasks")}
    op.add_column("usage_records", sa.Column("route_decision_json", sa.Text(), nullable=True))
    op.add_column("usage_records", sa.Column("failure_class", sa.String(length=32), nullable=True))
    op.create_index("ix_usage_records_failure_class", "usage_records", ["failure_class"])
    if "attempt_count" not in generation_columns:
        op.add_column("generation_tasks", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    if "next_retry_at" not in generation_columns:
        op.add_column("generation_tasks", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    if "failure_class" not in generation_columns:
        op.add_column("generation_tasks", sa.Column("failure_class", sa.String(length=32), nullable=True))
    if "dead_lettered_at" not in generation_columns:
        op.add_column("generation_tasks", sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_generation_tasks_next_retry_at", "generation_tasks", ["next_retry_at"])
    op.create_index("ix_generation_tasks_failure_class", "generation_tasks", ["failure_class"])
    op.create_index("ix_generation_tasks_dead_lettered_at", "generation_tasks", ["dead_lettered_at"])


def downgrade() -> None:
    op.drop_index("ix_generation_tasks_dead_lettered_at", table_name="generation_tasks")
    op.drop_index("ix_generation_tasks_failure_class", table_name="generation_tasks")
    op.drop_index("ix_generation_tasks_next_retry_at", table_name="generation_tasks")
    op.drop_column("generation_tasks", "dead_lettered_at")
    op.drop_column("generation_tasks", "failure_class")
    op.drop_column("generation_tasks", "next_retry_at")
    op.drop_column("generation_tasks", "attempt_count")
    op.drop_index("ix_usage_records_failure_class", table_name="usage_records")
    op.drop_column("usage_records", "failure_class")
    op.drop_column("usage_records", "route_decision_json")
