import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator
from app.services.china_core_cpi_current_repair import (
    CORE_CPI_CODE,
    CoreCpiCurrentConflictError,
    CoreCpiEvidenceError,
    repair_core_cpi_current,
)
from app.services.indicator_service import upsert_points as real_upsert


class ChinaCoreCpiCurrentRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(
            Indicator(
                code=CORE_CPI_CODE,
                name="中国核心CPI同比",
                category="macro",
                unit="%",
                source="NBS",
                frequency="monthly",
                is_visible=True,
                sort_order=0,
                region="CN",
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _evidence() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": dt.date(2025, 2, 1),
                    "value": -0.1,
                    "release_date": dt.date(2025, 4, 10),
                    "available_at": dt.datetime(2025, 4, 10, 9, 30),
                    "source_url": (
                        "https://www.stats.gov.cn/sj/sjjd/202504/"
                        "t20250410_1959259.html"
                    ),
                    "status": "published_later_reference",
                    "formula_version": None,
                    "evidence_kind": "official_release",
                    "chain_verified": True,
                    "availability_precision": "exact_minute",
                    "provenance_json": {
                        "article_observation": "2025-03-01",
                        "evidence_semantics": "later_article_explicit_reference",
                        "parser_version": "nbs_inflation_release_v1",
                        "publication_time_source": "visible_nbs_detail_title",
                        "source_sha256": (
                            "f000bb6b66cd5e58419228984fbd1fa7"
                            "a6139179e8e6dc26bdd26902ab79ca48"
                        ),
                    },
                },
                {
                    "date": dt.date(2025, 6, 1),
                    "value": 0.7,
                    "release_date": dt.date(2025, 7, 9),
                    "available_at": dt.datetime(2025, 7, 9, 9, 30),
                    "source_url": (
                        "https://www.stats.gov.cn/sj/sjjd/202507/"
                        "t20250709_1960365.html"
                    ),
                    "status": "published",
                    "formula_version": None,
                    "evidence_kind": "official_release",
                    "chain_verified": True,
                    "availability_precision": "exact_minute",
                    "provenance_json": {
                        "article_observation": "2025-06-01",
                        "evidence_semantics": "subject_month_release",
                        "parser_version": "nbs_inflation_release_v1",
                        "publication_time_source": "visible_nbs_detail_title",
                        "source_sha256": (
                            "8839f7a436aca208e45286061fc1e73d3"
                            "1a7987161d327bd41f1808a1c75d651"
                        ),
                    },
                },
            ]
        )

    def _seed_wrong_june(self, value: float = 0.1) -> DataPoint:
        point = DataPoint(
            indicator_code=CORE_CPI_CODE,
            date=dt.date(2025, 6, 1),
            value=value,
            status="backfilled",
            version=1,
        )
        self.db.add(point)
        self.db.flush()
        self.db.add(
            DataPointVintage(
                data_point_id=point.id,
                indicator_code=CORE_CPI_CODE,
                date=point.date,
                value=point.value,
                release_date=None,
                available_at=None,
                retrieved_at=dt.datetime(2025, 7, 1),
                source_url=None,
                status=point.status,
                formula_version=None,
                version=1,
            )
        )
        self.db.commit()
        return point

    def test_dry_run_writes_nothing(self) -> None:
        self._seed_wrong_june()
        report = repair_core_cpi_current(self.db, self._evidence())
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["planned_changes"], 2)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 1
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 1
        )

    def test_apply_preserves_wrong_vintage_and_uses_real_later_availability(self) -> None:
        self._seed_wrong_june()
        first = repair_core_cpi_current(self.db, self._evidence(), apply=True)
        june = self.db.scalar(
            select(DataPoint).where(DataPoint.date == dt.date(2025, 6, 1))
        )
        february = self.db.scalar(
            select(DataPoint).where(DataPoint.date == dt.date(2025, 2, 1))
        )
        june_vintages = list(
            self.db.scalars(
                select(DataPointVintage)
                .where(DataPointVintage.data_point_id == june.id)
                .order_by(DataPointVintage.version)
            )
        )
        self.assertEqual(first["changed"], 2)
        self.assertEqual([float(row.value) for row in june_vintages], [0.1, 0.7])
        self.assertEqual(float(june.value), 0.7)
        self.assertEqual(float(february.value), -0.1)
        self.assertEqual(
            february.available_at, dt.datetime(2025, 4, 10, 9, 30)
        )
        self.assertEqual(february.status, "historical_backfill")

        vintage_count = self.db.scalar(
            select(func.count()).select_from(DataPointVintage)
        )
        second = repair_core_cpi_current(self.db, self._evidence(), apply=True)
        self.assertEqual(second["changed"], 0)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)),
            vintage_count,
        )

    def test_unexpected_current_value_fails_before_any_write(self) -> None:
        self._seed_wrong_june(9.9)
        with self.assertRaises(CoreCpiCurrentConflictError):
            repair_core_cpi_current(self.db, self._evidence(), apply=True)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 1
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 1
        )

    def test_wrong_later_reference_time_is_ineligible(self) -> None:
        evidence = self._evidence()
        evidence.loc[evidence["date"] == dt.date(2025, 2, 1), "available_at"] = (
            dt.datetime(2025, 3, 9, 9, 30)
        )
        with self.assertRaises(CoreCpiEvidenceError):
            repair_core_cpi_current(self.db, evidence, apply=True)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 0
        )

    def test_missing_or_forged_provenance_is_ineligible(self) -> None:
        mutations = {
            "missing": None,
            "wrong parser": {
                "article_observation": "2025-06-01",
                "evidence_semantics": "subject_month_release",
                "parser_version": "forged",
                "publication_time_source": "visible_nbs_detail_title",
                "source_sha256": "b" * 64,
            },
            "wrong semantics": {
                "article_observation": "2025-06-01",
                "evidence_semantics": "later_article_explicit_reference",
                "parser_version": "nbs_inflation_release_v1",
                "publication_time_source": "visible_nbs_detail_title",
                "source_sha256": (
                    "8839f7a436aca208e45286061fc1e73d3"
                    "1a7987161d327bd41f1808a1c75d651"
                ),
            },
        }
        for label, provenance in mutations.items():
            evidence = self._evidence()
            evidence.at[1, "provenance_json"] = provenance
            with self.subTest(label=label), self.assertRaises(CoreCpiEvidenceError):
                repair_core_cpi_current(self.db, evidence, apply=True)

    def test_forged_source_url_is_ineligible(self) -> None:
        evidence = self._evidence()
        evidence.at[1, "source_url"] = (
            "https://www.stats.gov.cn/sj/sjjd/202507/not-the-audited-page.html"
        )
        with self.assertRaises(CoreCpiEvidenceError):
            repair_core_cpi_current(self.db, evidence, apply=True)

    def test_existing_current_without_matching_vintage_fails_closed(self) -> None:
        self.db.add(
            DataPoint(
                indicator_code=CORE_CPI_CODE,
                date=dt.date(2025, 6, 1),
                value=0.1,
                status="backfilled",
                version=1,
            )
        )
        self.db.commit()
        with self.assertRaises(CoreCpiCurrentConflictError):
            repair_core_cpi_current(self.db, self._evidence(), apply=True)
        point = self.db.scalar(
            select(DataPoint).where(DataPoint.date == dt.date(2025, 6, 1))
        )
        self.assertEqual(float(point.value), 0.1)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_failure_after_flush_rolls_back_both_target_changes(self) -> None:
        self._seed_wrong_june()

        def write_then_fail(db, code, frame, commit=True):
            real_upsert(db, code, frame, commit=commit)
            raise RuntimeError("fault after flush")

        with patch(
            "app.services.china_core_cpi_current_repair.upsert_points",
            side_effect=write_then_fail,
        ):
            with self.assertRaises(RuntimeError):
                repair_core_cpi_current(self.db, self._evidence(), apply=True)
        points = list(self.db.scalars(select(DataPoint)))
        self.assertEqual(len(points), 1)
        self.assertEqual(float(points[0].value), 0.1)
        self.assertEqual(points[0].version, 1)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 1
        )


if __name__ == "__main__":
    unittest.main()
