"""complete columns and tables used by the production ORM"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0033_schema_completeness"
down_revision = "0032_worker_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    account_columns = {item["name"] for item in inspector.get_columns("billing_accounts")}
    if "account_source" not in account_columns:
        op.add_column("billing_accounts", sa.Column("account_source", sa.String(24), nullable=False, server_default="admin"))
    model_columns = {item["name"] for item in inspector.get_columns("model_configs")}
    if "task_price_micros" not in model_columns:
        op.add_column("model_configs", sa.Column("task_price_micros", sa.BigInteger(), nullable=False, server_default="0"))
    channel_columns = {item["name"] for item in inspector.get_columns("model_channels")}
    if "last_latency_ms" not in channel_columns:
        op.add_column("model_channels", sa.Column("last_latency_ms", sa.Integer(), nullable=False, server_default="0"))
    if "last_status_code" not in channel_columns:
        op.add_column("model_channels", sa.Column("last_status_code", sa.Integer(), nullable=True))
    if "provider_task_cost_micros" not in channel_columns:
        op.add_column("model_channels", sa.Column("provider_task_cost_micros", sa.BigInteger(), nullable=True))
    if not inspector.has_table("model_change_records"):
        op.create_table(
            "model_change_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("model_config_id", sa.Integer(), sa.ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("actor_type", sa.String(24), nullable=False, server_default="admin"),
            sa.Column("actor_id", sa.String(120), nullable=True),
            sa.Column("change_type", sa.String(32), nullable=False),
            sa.Column("changed_fields_json", sa.Text(), nullable=True),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_model_change_records_model_config_id", "model_change_records", ["model_config_id"])
        op.create_index("ix_model_change_records_change_type", "model_change_records", ["change_type"])
        op.create_index("ix_model_change_records_created_at", "model_change_records", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_change_records_created_at", table_name="model_change_records")
    op.drop_index("ix_model_change_records_change_type", table_name="model_change_records")
    op.drop_index("ix_model_change_records_model_config_id", table_name="model_change_records")
    op.drop_table("model_change_records")
    op.drop_column("model_channels", "provider_task_cost_micros")
    op.drop_column("model_channels", "last_status_code")
    op.drop_column("model_channels", "last_latency_ms")
    op.drop_column("model_configs", "task_price_micros")
    op.drop_column("billing_accounts", "account_source")
