"""add stable client-facing model aliases

Revision ID: 0029_model_aliases
Revises: 0028_merge_heads
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_model_aliases"
down_revision = "0028_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alias", sa.String(length=120), nullable=False),
        sa.Column("model_config_id", sa.Integer(), sa.ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("alias", name="uq_model_aliases_alias"),
    )
    op.create_index("ix_model_aliases_alias", "model_aliases", ["alias"])
    op.create_index("ix_model_aliases_model_config_id", "model_aliases", ["model_config_id"])
    op.create_index("ix_model_aliases_active", "model_aliases", ["active"])
    op.add_column("api_keys", sa.Column("allowed_models_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "allowed_models_json")
    op.drop_index("ix_model_aliases_active", table_name="model_aliases")
    op.drop_index("ix_model_aliases_model_config_id", table_name="model_aliases")
    op.drop_index("ix_model_aliases_alias", table_name="model_aliases")
    op.drop_table("model_aliases")
