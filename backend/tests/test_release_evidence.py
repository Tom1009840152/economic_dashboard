import datetime as dt
import json
import unittest
from decimal import Decimal

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, Indicator, ReleaseEvidence
from app.services.release_evidence import (
    ReleaseEvidenceConflictError,
    upsert_release_evidence,
)


class ReleaseEvidenceTests(unittest.TestCase):
    CODE = "CN_GDP_NOMINAL_YTD"

    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(
            Indicator(
                code=self.CODE,
                name="中国名义GDP累计",
                category="cycle_input",
                unit="亿元",
                source="NBS",
                frequency="quarterly",
                is_visible=False,
                sort_order=0,
                region="CN",
            )
        )
        self.db.add(
            DataPoint(
                indicator_code=self.CODE,
                date=dt.date(2024, 6, 1),
                value=616836.0,
                status="historical_backfill",
                version=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _row(
        *,
        available_at: dt.datetime,
        value: float = 616836.0,
        formula_version: str | None = None,
        status: str = "published",
        source_url: str = "https://www.stats.gov.cn/sj/zxfb/release.html",
        provenance=None,
    ) -> dict:
        return {
            "date": dt.date(2024, 6, 1),
            "value": value,
            "release_date": available_at.date(),
            "available_at": available_at,
            "source_url": source_url,
            "status": status,
            "formula_version": formula_version,
            "provenance_json": provenance,
        }

    def _evidence(self) -> list[ReleaseEvidence]:
        return list(
            self.db.scalars(
                select(ReleaseEvidence).order_by(ReleaseEvidence.version)
            )
        )

    def test_stores_metadata_and_lineage_without_changing_current_point(self) -> None:
        point_before = self.db.scalar(
            select(DataPoint).where(DataPoint.indicator_code == self.CODE)
        )
        self.assertIsNotNone(point_before)
        original_value = point_before.value
        original_version = point_before.version
        provenance = {
            "inputs": [
                {"code": "CN_GDP_NOMINAL_YTD", "table": "表1", "column": "上半年"}
            ],
            "parser": "nbs_nominal_gdp_v1",
        }
        frame = pd.DataFrame(
            [
                self._row(
                    available_at=dt.datetime(2024, 7, 16, 10, 0),
                    value=616835.75,
                    formula_version="nbs_nominal_gdp_v1",
                    status="derived",
                    provenance=provenance,
                )
            ]
        )

        self.assertEqual(upsert_release_evidence(self.db, self.CODE, frame), 1)

        point_after = self.db.scalar(
            select(DataPoint).where(DataPoint.indicator_code == self.CODE)
        )
        evidence = self._evidence()[0]
        self.assertEqual(point_after.value, original_value)
        self.assertEqual(point_after.version, original_version)
        self.assertEqual(evidence.value, Decimal("616835.750000"))
        self.assertEqual(evidence.release_date, dt.date(2024, 7, 16))
        self.assertEqual(evidence.available_at, dt.datetime(2024, 7, 16, 10, 0))
        self.assertEqual(evidence.status, "derived")
        self.assertEqual(evidence.formula_version, "nbs_nominal_gdp_v1")
        self.assertEqual(json.loads(evidence.provenance_json), provenance)
        self.assertEqual(len(evidence.evidence_key), 64)
        self.assertEqual(evidence.version, 1)

    def test_semantically_identical_records_are_idempotent_at_six_decimals(self) -> None:
        available_at = dt.datetime(2024, 7, 16, 10, 0)
        first = pd.DataFrame(
            [self._row(available_at=available_at, value=616836.0000001)]
        )
        repeated = pd.DataFrame(
            [
                self._row(
                    available_at=available_at,
                    value=616836.0000002,
                    # Retrieval metadata is not part of official-release
                    # identity and cannot mutate an already stored row.
                    source_url="https://www.stats.gov.cn/alternate.html",
                    provenance={"parser": "later_retrieval"},
                )
            ]
        )

        self.assertEqual(upsert_release_evidence(self.db, self.CODE, first), 1)
        self.assertEqual(upsert_release_evidence(self.db, self.CODE, repeated), 0)
        self.assertEqual(len(self._evidence()), 1)

    def test_same_instant_value_or_formula_conflict_rejects_the_whole_batch(self) -> None:
        first_at = dt.datetime(2024, 7, 16, 10, 0)
        self.assertEqual(
            upsert_release_evidence(
                self.db,
                self.CODE,
                pd.DataFrame(
                    [
                        self._row(
                            available_at=first_at,
                            formula_version="nbs_nominal_gdp_v1",
                        )
                    ]
                ),
            ),
            1,
        )
        later = self._row(
            available_at=dt.datetime(2025, 1, 18, 10, 0),
            value=620000.0,
            formula_version="nbs_nominal_gdp_v1",
        )

        for conflict in (
            self._row(
                available_at=first_at,
                value=616837.0,
                formula_version="nbs_nominal_gdp_v1",
            ),
            self._row(
                available_at=first_at,
                formula_version="nbs_nominal_gdp_v2",
            ),
            self._row(
                available_at=first_at,
                formula_version="nbs_nominal_gdp_v1",
                status="derived",
            ),
        ):
            with self.subTest(conflict=conflict):
                with self.assertRaises(ReleaseEvidenceConflictError):
                    upsert_release_evidence(
                        self.db, self.CODE, pd.DataFrame([later, conflict])
                    )
                rows = self._evidence()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].available_at, first_at)

    def test_different_release_times_create_versions_and_late_history_appends(self) -> None:
        first_at = dt.datetime(2024, 7, 16, 10, 0)
        second_at = dt.datetime(2025, 1, 18, 10, 0)
        # Deliberately reverse the input order; one batch is normalized by
        # publication time before assigning append versions.
        frame = pd.DataFrame(
            [
                self._row(available_at=second_at, value=620000.0),
                self._row(available_at=first_at, value=616836.0),
            ]
        )
        self.assertEqual(upsert_release_evidence(self.db, self.CODE, frame), 2)
        rows = self._evidence()
        self.assertEqual([row.version for row in rows], [1, 2])
        self.assertEqual([row.available_at for row in rows], [first_at, second_at])

        discovered_later = dt.datetime(2024, 10, 18, 10, 0)
        self.assertEqual(
            upsert_release_evidence(
                self.db,
                self.CODE,
                pd.DataFrame(
                    [self._row(available_at=discovered_later, value=618000.0)]
                ),
            ),
            1,
        )
        rows = self._evidence()
        self.assertEqual(rows[-1].version, 3)
        self.assertEqual(rows[-1].available_at, discovered_later)
        self.assertEqual(
            [row.available_at for row in sorted(rows, key=lambda item: item.available_at)],
            [first_at, discovered_later, second_at],
        )

    def test_missing_official_fields_or_available_at_is_rejected(self) -> None:
        frame = pd.DataFrame(
            [self._row(available_at=dt.datetime(2024, 7, 16, 10, 0))]
        ).drop(columns=["source_url"])
        with self.assertRaisesRegex(ValueError, "missing columns"):
            upsert_release_evidence(self.db, self.CODE, frame)

        missing_time = pd.DataFrame(
            [self._row(available_at=dt.datetime(2024, 7, 16, 10, 0))]
        )
        missing_time.loc[0, "available_at"] = None
        with self.assertRaisesRegex(ValueError, "available_at must not be empty"):
            upsert_release_evidence(self.db, self.CODE, missing_time)

        wrong_release_date = pd.DataFrame(
            [self._row(available_at=dt.datetime(2024, 7, 16, 10, 0))]
        )
        wrong_release_date.loc[0, "release_date"] = dt.date(2024, 7, 15)
        with self.assertRaisesRegex(ValueError, "release_date must equal"):
            upsert_release_evidence(self.db, self.CODE, wrong_release_date)

    def test_timezone_aware_timestamp_is_rejected_until_utc_contract_exists(self) -> None:
        aware = dt.datetime(
            2024,
            7,
            16,
            10,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            upsert_release_evidence(
                self.db,
                self.CODE,
                pd.DataFrame([self._row(available_at=aware)]),
            )

    def test_date_only_available_at_is_rejected_instead_of_assuming_midnight(self) -> None:
        for value in (dt.date(2024, 7, 16), "2024-07-16"):
            with self.subTest(value=value):
                row = self._row(
                    available_at=dt.datetime(2024, 7, 16, 10, 0)
                )
                row["available_at"] = value
                with self.assertRaisesRegex(ValueError, "explicit time"):
                    upsert_release_evidence(
                        self.db,
                        self.CODE,
                        pd.DataFrame([row]),
                    )


if __name__ == "__main__":
    unittest.main()
