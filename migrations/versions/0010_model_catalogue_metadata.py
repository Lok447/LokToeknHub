"""Add model catalogue and official provider pricing references.

Revision ID: 0010_model_catalogue_metadata
Revises: 0009_workspaces_projects
Create Date: 2026-08-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_model_catalogue_metadata"
down_revision: Union[str, None] = "0009_workspaces_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("model_configs", sa.Column("catalog_metadata_json", sa.Text(), nullable=True))
    op.add_column("model_configs", sa.Column("official_pricing_json", sa.Text(), nullable=True))
    op.execute(
        "UPDATE model_configs SET active = false "
        "WHERE public_name IN ('lok-chat', 'lok-reason', 'lok-vision')"
    )


def downgrade() -> None:
    op.drop_column("model_configs", "official_pricing_json")
    op.drop_column("model_configs", "catalog_metadata_json")
