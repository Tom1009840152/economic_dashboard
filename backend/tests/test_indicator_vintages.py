import datetime as dt
import unittest

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator
from app.services.indicator_service import upsert_points


class IndicatorVintageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(
            Indicator(
                code="TEST",
                name="Test",
                category="cycle_input",
                unit="%",
                source="test",
                frequency="monthly",
                is_visible=False,
                sort_order=0,
                region="CN",
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def frame(self, value: float, with_metadata: bool = True) -> pd.DataFrame:
        row = {"date": dt.date(2026, 1, 1), "value": value}
        if with_metadata:
            row.update(
                {
                    "release_date": dt.date(2026, 2, 1),
                    "available_at": dt.datetime(2026, 2, 1, 9, 30),
                    "source_url": "https://example.test/release",
                    "status": "published",
                }
            )
        return pd.DataFrame([row])

    def vintage_count(self) -> int:
        return self.db.scalar(select(func.count()).select_from(DataPointVintage)) or 0

    def test_identical_refresh_is_idempotent(self) -> None:
        self.assertEqual(upsert_points(self.db, "TEST", self.frame(1.0)), 1)
        self.assertEqual(upsert_points(self.db, "TEST", self.frame(1.0)), 0)
        self.assertEqual(self.vintage_count(), 1)

    def test_changed_value_appends_vintage(self) -> None:
        upsert_points(self.db, "TEST", self.frame(1.0))
        self.assertEqual(upsert_points(self.db, "TEST", self.frame(1.2)), 1)
        point = self.db.scalar(select(DataPoint))
        self.assertEqual(point.version, 2)
        self.assertEqual(float(point.value), 1.2)
        self.assertEqual(self.vintage_count(), 2)

    def test_sparse_refresh_does_not_erase_release_metadata(self) -> None:
        upsert_points(self.db, "TEST", self.frame(1.0))
        upsert_points(self.db, "TEST", self.frame(1.1, with_metadata=False))
        point = self.db.scalar(select(DataPoint))
        self.assertEqual(point.release_date, dt.date(2026, 2, 1))
        self.assertEqual(point.source_url, "https://example.test/release")

    def test_equal_backfill_does_not_downgrade_published_metadata(self) -> None:
        upsert_points(self.db, "TEST", self.frame(1.0))
        backfill = pd.DataFrame(
            [
                {
                    "date": dt.date(2026, 1, 1),
                    "value": 1.0,
                    "release_date": None,
                    "available_at": None,
                    "source_url": "https://example.test/final-table",
                    "status": "historical_backfill",
                }
            ]
        )
        self.assertEqual(upsert_points(self.db, "TEST", backfill), 0)
        point = self.db.scalar(select(DataPoint))
        self.assertEqual(point.status, "published")
        self.assertEqual(point.source_url, "https://example.test/release")
        self.assertEqual(self.vintage_count(), 1)

    def test_equal_sparse_published_row_does_not_clear_metadata(self) -> None:
        upsert_points(self.db, "TEST", self.frame(1.0))
        sparse = pd.DataFrame(
            [
                {
                    "date": dt.date(2026, 1, 1),
                    "value": 1.0,
                    "release_date": None,
                    "available_at": None,
                    "source_url": None,
                    "status": "published",
                }
            ]
        )

        self.assertEqual(upsert_points(self.db, "TEST", sparse), 0)
        point = self.db.scalar(select(DataPoint))
        self.assertEqual(point.release_date, dt.date(2026, 2, 1))
        self.assertEqual(point.available_at, dt.datetime(2026, 2, 1, 9, 30))
        self.assertEqual(point.source_url, "https://example.test/release")
        self.assertEqual(self.vintage_count(), 1)

    def test_formula_version_is_persisted_in_current_and_vintage_rows(self) -> None:
        frame = self.frame(1.0)
        frame["status"] = "derived_backfill"
        frame["formula_version"] = "1.0.0"

        self.assertEqual(upsert_points(self.db, "TEST", frame), 1)
        point = self.db.scalar(select(DataPoint))
        vintage = self.db.scalar(select(DataPointVintage))
        self.assertEqual(point.formula_version, "1.0.0")
        self.assertEqual(vintage.formula_version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
