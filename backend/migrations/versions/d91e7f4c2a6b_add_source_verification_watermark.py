"""add source-side verification watermark to refresh results

Revision ID: d91e7f4c2a6b
Revises: a61d7c04e932
Create Date: 2026-09-17 17:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d91e7f4c2a6b"
down_revision: Union[str, None] = "a61d7c04e932"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "refresh_results",
        sa.Column("source_verified_through", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_results", "source_verified_through")
