"""Normalize legacy DeepSeek prices to the ledger's per-1K storage unit.

Revision ID: 0014_normalize_token_price_unit
Revises: 0013_remove_legacy_mock_models
Create Date: 2026-08-18
"""

from typing import Sequence

from alembic import op


revision: str = "0014_normalize_token_price_unit"
down_revision: str = "0013_remove_legacy_mock_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE model_configs SET input_price_micros_per_1k = 3000, output_price_micros_per_1k = 9000 "
        "WHERE public_name = 'deepseek-v4-flash' "
        "AND input_price_micros_per_1k = 3000000 AND output_price_micros_per_1k = 9000000"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE model_configs SET input_price_micros_per_1k = 3000000, output_price_micros_per_1k = 9000000 "
        "WHERE public_name = 'deepseek-v4-flash' "
        "AND input_price_micros_per_1k = 3000 AND output_price_micros_per_1k = 9000"
    )
