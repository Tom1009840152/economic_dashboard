import unittest

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from migrations.versions import a61d7c04e932_classify_release_evidence as migration


class ReleaseEvidenceClassificationMigrationTests(unittest.TestCase):
    def test_only_audited_fiscal_rows_are_promoted_across_reupgrade(self) -> None:
        engine = sa.create_engine("sqlite+pysqlite:///:memory:")
        metadata = sa.MetaData()
        table = sa.Table(
            "release_evidence",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("evidence_key", sa.String(64), nullable=False),
            sa.Column("indicator_code", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("source_url", sa.String(512), nullable=False),
        )
        metadata.create_all(engine)

        with engine.begin() as connection:
            connection.execute(
                table.insert(),
                [
                    {
                        "id": 1,
                        "evidence_key": "legacy-fiscal",
                        "indicator_code": "CN_FISCAL_IMPULSE_PROXY",
                        "status": "derived",
                        "source_url": "https://www.mof.gov.cn/release.html",
                    },
                    {
                        "id": 2,
                        "evidence_key": "legacy-unclassified",
                        "indicator_code": "CN_CONSUMER_EXPECTATIONS",
                        "status": "published",
                        "source_url": "https://www.cei.cn/release.html",
                    },
                    {
                        "id": 5,
                        "evidence_key": "fiscal-code-unofficial-source",
                        "indicator_code": "CN_FISCAL_IMPULSE_PROXY",
                        "status": "derived",
                        "source_url": "https://example.invalid/release.html",
                    },
                ],
            )
            operations = Operations(MigrationContext.configure(connection))
            original_op = migration.op
            migration.op = operations
            try:
                migration.upgrade()
            finally:
                migration.op = original_op

            columns = {
                column["name"]: column
                for column in sa.inspect(connection).get_columns("release_evidence")
            }
            self.assertFalse(columns["evidence_kind"]["nullable"])
            self.assertFalse(columns["chain_verified"]["nullable"])
            self.assertFalse(columns["availability_precision"]["nullable"])

            upgraded = sa.Table(
                "release_evidence", sa.MetaData(), autoload_with=connection
            )
            fiscal = connection.execute(
                sa.select(upgraded).where(upgraded.c.id == 1)
            ).mappings().one()
            self.assertEqual(fiscal["evidence_kind"], "official_release")
            self.assertTrue(fiscal["chain_verified"])
            self.assertEqual(fiscal["availability_precision"], "exact_minute")

            unclassified = connection.execute(
                sa.select(upgraded).where(upgraded.c.id == 2)
            ).mappings().one()
            self.assertEqual(unclassified["evidence_kind"], "unclassified")
            self.assertFalse(unclassified["chain_verified"])
            self.assertEqual(unclassified["availability_precision"], "unknown")

            wrong_source = connection.execute(
                sa.select(upgraded).where(upgraded.c.id == 5)
            ).mappings().one()
            self.assertEqual(wrong_source["evidence_kind"], "unclassified")
            self.assertFalse(wrong_source["chain_verified"])

            connection.execute(
                upgraded.insert().values(
                    id=3,
                    evidence_key="future-direct-insert",
                    indicator_code="CN_CONSUMER_EXPECTATIONS",
                    status="published",
                    source_url="https://www.cei.cn/future.html",
                )
            )
            future = connection.execute(
                sa.select(upgraded).where(upgraded.c.id == 3)
            ).mappings().one()
            self.assertEqual(future["evidence_kind"], "unclassified")
            self.assertFalse(future["chain_verified"])
            self.assertEqual(future["availability_precision"], "unknown")

            connection.execute(
                upgraded.insert().values(
                    id=4,
                    evidence_key="classified-mirror",
                    indicator_code="CN_CONSUMER_EXPECTATIONS",
                    status="published",
                    source_url="https://www.cei.cn/mirror.html",
                    evidence_kind="official_distribution_mirror",
                    chain_verified=True,
                    availability_precision="date_upper_bound",
                )
            )

            original_op = migration.op
            migration.op = operations
            try:
                migration.downgrade()
            finally:
                migration.op = original_op
            downgraded_columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns(
                    "release_evidence"
                )
            }
            self.assertNotIn("evidence_kind", downgraded_columns)
            self.assertNotIn("chain_verified", downgraded_columns)
            self.assertNotIn("availability_precision", downgraded_columns)

            original_op = migration.op
            migration.op = operations
            try:
                migration.upgrade()
            finally:
                migration.op = original_op

            reupgraded = sa.Table(
                "release_evidence", sa.MetaData(), autoload_with=connection
            )
            fiscal_after_reupgrade = connection.execute(
                sa.select(reupgraded).where(reupgraded.c.id == 1)
            ).mappings().one()
            mirror_after_reupgrade = connection.execute(
                sa.select(reupgraded).where(reupgraded.c.id == 4)
            ).mappings().one()
            self.assertTrue(fiscal_after_reupgrade["chain_verified"])
            self.assertEqual(
                fiscal_after_reupgrade["availability_precision"], "exact_minute"
            )
            self.assertFalse(mirror_after_reupgrade["chain_verified"])
            self.assertEqual(
                mirror_after_reupgrade["evidence_kind"], "unclassified"
            )
            self.assertEqual(
                mirror_after_reupgrade["availability_precision"], "unknown"
            )

        engine.dispose()


if __name__ == "__main__":
    unittest.main()
