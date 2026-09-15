"""classify release evidence and record availability precision

Revision ID: a61d7c04e932
Revises: 3f64b9d27a10
Create Date: 2026-09-15 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a61d7c04e932"
down_revision: Union[str, None] = "3f64b9d27a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Defaults make a direct future insert explicitly untrusted.  The rows
    # already present at this revision are the audited NBS/MOF fiscal chain and
    # are upgraded below in one migration transaction.
    op.add_column(
        "release_evidence",
        sa.Column(
            "evidence_kind",
            sa.String(length=32),
            nullable=False,
            server_default="unclassified",
        ),
    )
    op.add_column(
        "release_evidence",
        sa.Column(
            "chain_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "release_evidence",
        sa.Column(
            "availability_precision",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    # Revision 3f64b9d27a10 was introduced for the six audited fiscal/GDP
    # chains below.  Restrict the legacy promotion to those exact indicators
    # and their official source domains.  This is deliberately not a blanket
    # UPDATE: after a downgrade/re-upgrade cycle the table may also contain
    # evidence created by newer collectors (for example a date-only mirror),
    # which must remain untrusted until a dedicated audit/reclassification
    # migration restores its source-specific label.
    legacy = sa.table(
        "release_evidence",
        sa.column("indicator_code", sa.String()),
        sa.column("status", sa.String()),
        sa.column("source_url", sa.String()),
        sa.column("evidence_kind", sa.String()),
        sa.column("chain_verified", sa.Boolean()),
        sa.column("availability_precision", sa.String()),
    )
    fiscal_codes = (
        "CN_GDP_NOMINAL_YTD",
        "CN_FISCAL_GENERAL_SPEND_YTD",
        "CN_FISCAL_FUND_EXPENDITURE_YTD",
        "CN_FISCAL_BROAD_EXPENDITURE_YTD",
        "CN_FISCAL_SPEND_INTENSITY",
        "CN_FISCAL_IMPULSE_PROXY",
    )
    official_source = sa.or_(
        legacy.c.source_url.like("https://mof.gov.cn/%"),
        legacy.c.source_url.like("https://%.mof.gov.cn/%"),
        legacy.c.source_url.like("https://stats.gov.cn/%"),
        legacy.c.source_url.like("https://%.stats.gov.cn/%"),
    )
    op.execute(
        legacy.update()
        .where(legacy.c.indicator_code.in_(fiscal_codes))
        .where(legacy.c.status.in_(("published", "derived")))
        .where(official_source)
        .values(
            evidence_kind="official_release",
            chain_verified=True,
            availability_precision="exact_minute",
        )
    )


def downgrade() -> None:
    op.drop_column("release_evidence", "availability_precision")
    op.drop_column("release_evidence", "chain_verified")
    op.drop_column("release_evidence", "evidence_kind")
