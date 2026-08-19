"""Merge model release heads and correct the DeepSeek V4 route.

Revision ID: 0012_provider_model_route_fix
Revises: 0011_channel_encrypted_api_key, 0010_model_catalogue_metadata
Create Date: 2026-08-18
"""

from typing import Sequence

from alembic import op


revision: str = "0012_provider_model_route_fix"
down_revision: tuple[str, str] = ("0011_channel_encrypted_api_key", "0010_model_catalogue_metadata")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE model_configs SET upstream_model = 'deepseek-v4-flash' "
        "WHERE public_name = 'deepseek-v4-flash' AND upstream_model = 'DeepSeek-V4-Flash-0731'"
    )
    op.execute(
        "UPDATE model_channels SET upstream_model = 'deepseek-v4-flash', status = 'unknown', "
        "health_source = 'unknown', consecutive_failures = 0, circuit_open_until = NULL, last_error = NULL "
        "WHERE model_config_id IN (SELECT id FROM model_configs WHERE public_name = 'deepseek-v4-flash') "
        "AND upstream_model = 'DeepSeek-V4-Flash-0731'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE model_configs SET upstream_model = 'DeepSeek-V4-Flash-0731' "
        "WHERE public_name = 'deepseek-v4-flash' AND upstream_model = 'deepseek-v4-flash'"
    )
    op.execute(
        "UPDATE model_channels SET upstream_model = 'DeepSeek-V4-Flash-0731', status = 'unknown', "
        "health_source = 'unknown', consecutive_failures = 0, circuit_open_until = NULL, last_error = NULL "
        "WHERE model_config_id IN (SELECT id FROM model_configs WHERE public_name = 'deepseek-v4-flash') "
        "AND upstream_model = 'deepseek-v4-flash'"
    )
