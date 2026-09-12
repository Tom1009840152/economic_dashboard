"""add data point publication metadata and vintages

Revision ID: 8b2d9f7346a1
Revises: f0840ec64b0b
Create Date: 2026-09-12 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8b2d9f7346a1"
down_revision: Union[str, None] = "f0840ec64b0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "indicators",
        sa.Column("frequency", sa.String(length=16), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "indicators", sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column("data_points", sa.Column("release_date", sa.Date(), nullable=True))
    op.add_column("data_points", sa.Column("available_at", sa.DateTime(), nullable=True))
    op.add_column(
        "data_points",
        sa.Column("retrieved_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("data_points", sa.Column("source_url", sa.String(length=512), nullable=True))
    op.add_column(
        "data_points",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="backfilled"),
    )
    op.add_column(
        "data_points", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )

    op.create_table(
        "data_point_vintages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("data_point_id", sa.Integer(), nullable=False),
        sa.Column("indicator_code", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("available_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="backfilled"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["data_point_id"], ["data_points.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["indicator_code"], ["indicators.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "data_point_id", "version", name="uq_data_point_vintage_version"
        ),
    )
    op.create_index(
        "ix_data_point_vintages_data_point_id", "data_point_vintages", ["data_point_id"]
    )
    op.create_index(
        "ix_data_point_vintages_indicator_code", "data_point_vintages", ["indicator_code"]
    )
    op.create_index("ix_data_point_vintages_date", "data_point_vintages", ["date"])

    # Existing values are final-value backfills: retain them as version 1 while
    # making it explicit that their original release-time vintage is unknown.
    op.execute(
        sa.text(
            """
            INSERT INTO data_point_vintages
                (data_point_id, indicator_code, date, value, release_date,
                 available_at, retrieved_at, source_url, status, version)
            SELECT id, indicator_code, date, value, release_date,
                   available_at, retrieved_at, source_url, status, version
            FROM data_points
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_data_point_vintages_date", table_name="data_point_vintages")
    op.drop_index("ix_data_point_vintages_indicator_code", table_name="data_point_vintages")
    op.drop_index("ix_data_point_vintages_data_point_id", table_name="data_point_vintages")
    op.drop_table("data_point_vintages")
    op.drop_column("data_points", "version")
    op.drop_column("data_points", "status")
    op.drop_column("data_points", "source_url")
    op.drop_column("data_points", "retrieved_at")
    op.drop_column("data_points", "available_at")
    op.drop_column("data_points", "release_date")
    op.drop_column("indicators", "is_visible")
    op.drop_column("indicators", "frequency")
