"""add gateway route diagnostics and durable task retry state

Revision ID: 0030_gateway_reliability
Revises: 0029_model_aliases
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_gateway_reliability"
down_revision = "0029_model_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_records", sa.Column("route_decision_json", sa.Text(), nullable=True))
    op.add_column("usage_records", sa.Column("failure_class", sa.String(length=32), nullable=True))
    op.create_index("ix_usage_records_failure_class", "usage_records", ["failure_class"])
    op.add_column("generation_tasks", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("generation_tasks", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generation_tasks", sa.Column("failure_class", sa.String(length=32), nullable=True))
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
