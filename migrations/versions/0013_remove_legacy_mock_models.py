"""Remove legacy mock models from production catalogues.

Revision ID: 0013_remove_legacy_mock_models
Revises: 0012_provider_model_route_fix
Create Date: 2026-08-18
"""

from typing import Sequence

from alembic import op


revision: str = "0013_remove_legacy_mock_models"
down_revision: str = "0012_provider_model_route_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    mock_names = (
        "'lok-chat', 'lok-reason', 'lok-vision', 'smoke-model', "
        "'deepseek/deepseek-chat', 'deepseek/deepseek-reasoner'"
    )
    op.execute(
        "DELETE FROM model_channels WHERE model_config_id IN ("
        f"SELECT id FROM model_configs WHERE public_name IN ({mock_names}))"
    )
    op.execute(f"DELETE FROM model_configs WHERE public_name IN ({mock_names})")


def downgrade() -> None:
    # Removed mock data is intentionally not recreated in production.
    pass
