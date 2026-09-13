"""add formula provenance, as-of lookup index, and refresh run ledger

Revision ID: c72b1e34d9aa
Revises: 8b2d9f7346a1
Create Date: 2026-09-13 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c72b1e34d9aa"
down_revision: Union[str, None] = "8b2d9f7346a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "data_points",
        sa.Column("formula_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "data_point_vintages",
        sa.Column("formula_version", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_vintages_as_of_lookup",
        "data_point_vintages",
        ["indicator_code", "available_at", "date", "version"],
    )

    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("total_indicators", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_runs_started_at", "refresh_runs", ["started_at"])
    op.create_index("ix_refresh_runs_status", "refresh_runs", ["status"])

    op.create_table(
        "refresh_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("indicator_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("quality_issues", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["refresh_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "indicator_code", name="uq_refresh_result_run_indicator"),
    )
    op.create_index("ix_refresh_results_run_id", "refresh_results", ["run_id"])
    op.create_index("ix_refresh_results_indicator_code", "refresh_results", ["indicator_code"])
    op.create_index("ix_refresh_results_status", "refresh_results", ["status"])
    op.create_index(
        "ix_refresh_results_indicator_finished",
        "refresh_results",
        ["indicator_code", "finished_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_results_indicator_finished", table_name="refresh_results")
    op.drop_index("ix_refresh_results_status", table_name="refresh_results")
    op.drop_index("ix_refresh_results_indicator_code", table_name="refresh_results")
    op.drop_index("ix_refresh_results_run_id", table_name="refresh_results")
    op.drop_table("refresh_results")
    op.drop_index("ix_refresh_runs_status", table_name="refresh_runs")
    op.drop_index("ix_refresh_runs_started_at", table_name="refresh_runs")
    op.drop_table("refresh_runs")
    op.drop_index("ix_vintages_as_of_lookup", table_name="data_point_vintages")
    op.drop_column("data_point_vintages", "formula_version")
    op.drop_column("data_points", "formula_version")
