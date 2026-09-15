"""add independent official release evidence storage

Revision ID: 3f64b9d27a10
Revises: c72b1e34d9aa
Create Date: 2026-09-15 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3f64b9d27a10"
down_revision: Union[str, None] = "c72b1e34d9aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "release_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evidence_key", sa.String(length=64), nullable=False),
        sa.Column("indicator_code", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column(
            "retrieved_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="published",
        ),
        sa.Column("formula_version", sa.String(length=32), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["indicator_code"], ["indicators.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_key", name="uq_release_evidence_key"),
        sa.UniqueConstraint(
            "indicator_code",
            "date",
            "version",
            name="uq_release_evidence_observation_version",
        ),
    )
    op.create_index(
        "ix_release_evidence_as_of_lookup",
        "release_evidence",
        ["indicator_code", "available_at", "date", "version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_release_evidence_as_of_lookup", table_name="release_evidence"
    )
    op.drop_table("release_evidence")
