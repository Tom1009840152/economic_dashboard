import datetime as dt
import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator
from app.routers.as_of import snapshot_as_of
from app.services.as_of_service import (
    get_indicator_history_as_of,
    get_latest_snapshot_as_of,
)


class AsOfServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in ("TEST_A", "TEST_B"):
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
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

    def add_observation(self, code: str, observation_date: dt.date, vintages: list[dict]) -> None:
        current = vintages[-1]
        point = DataPoint(
            indicator_code=code,
            date=observation_date,
            value=Decimal(str(current["value"])),
            release_date=current.get("release_date"),
            available_at=current.get("available_at"),
            retrieved_at=current["retrieved_at"],
            source_url="https://example.test/data",
            status=current.get("status", "published"),
            version=len(vintages),
        )
        self.db.add(point)
        self.db.flush()
        for version, vintage in enumerate(vintages, start=1):
            self.db.add(
                DataPointVintage(
                    data_point_id=point.id,
                    indicator_code=code,
                    date=observation_date,
                    value=Decimal(str(vintage["value"])),
                    release_date=vintage.get("release_date"),
                    available_at=vintage.get("available_at"),
                    retrieved_at=vintage["retrieved_at"],
                    source_url="https://example.test/data",
                    status=vintage.get("status", "published"),
                    version=version,
                )
            )
        self.db.commit()

    def test_revision_is_not_visible_before_its_available_at(self) -> None:
        self.add_observation(
            "TEST_A",
            dt.date(2026, 1, 1),
            [
                {
                    "value": 100,
                    "available_at": dt.datetime(2026, 2, 1, 9),
                    "retrieved_at": dt.datetime(2026, 2, 1, 10),
                },
                {
                    "value": 101,
                    "available_at": dt.datetime(2026, 3, 1, 9),
                    "retrieved_at": dt.datetime(2026, 3, 1, 10),
                },
            ],
        )

        february, total = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 2, 15),
        )
        march, _ = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 3, 15),
        )

        self.assertEqual(total, 1)
        self.assertEqual(february[0].value, 100)
        self.assertEqual(february[0].version, 1)
        self.assertEqual(march[0].value, 101)
        self.assertEqual(march[0].version, 2)

    def test_official_availability_wins_over_out_of_order_ingestion(self) -> None:
        self.add_observation(
            "TEST_A",
            dt.date(2026, 1, 1),
            [
                {
                    "value": 101,
                    "available_at": dt.datetime(2026, 3, 1, 9),
                    "retrieved_at": dt.datetime(2026, 3, 1, 10),
                },
                {
                    "value": 100,
                    "available_at": dt.datetime(2026, 2, 1, 9),
                    "retrieved_at": dt.datetime(2026, 4, 1, 10),
                    "status": "historical_backfill",
                },
            ],
        )

        points, _ = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 4, 2),
        )

        self.assertEqual(points[0].value, 101)
        self.assertEqual(points[0].version, 1)

    def test_unknown_available_at_is_excluded_in_strict_mode(self) -> None:
        self.add_observation(
            "TEST_A",
            dt.date(2026, 1, 1),
            [
                {
                    "value": 88,
                    "available_at": None,
                    "retrieved_at": dt.datetime(2026, 2, 10, 8),
                    "status": "historical_backfill",
                }
            ],
        )

        strict, strict_total = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 2, 11),
            strict=True,
        )
        before_ingestion, _ = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 2, 9),
            strict=False,
        )
        fallback, fallback_total = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 2, 11),
            strict=False,
        )

        self.assertEqual(strict, [])
        self.assertEqual(strict_total, 0)
        self.assertEqual(before_ingestion, [])
        self.assertEqual(fallback_total, 1)
        self.assertEqual(fallback[0].value, 88)
        self.assertEqual(fallback[0].availability_basis, "retrieved_at_fallback")

    def test_snapshot_distinguishes_unknown_and_as_of_unavailable_codes(self) -> None:
        self.add_observation(
            "TEST_A",
            dt.date(2026, 1, 1),
            [
                {
                    "value": 88,
                    "available_at": None,
                    "retrieved_at": dt.datetime(2026, 2, 10, 8),
                    "status": "historical_backfill",
                }
            ],
        )

        response = snapshot_as_of(
            as_of=dt.datetime(2026, 2, 11),
            codes=["TEST_A", "UNKNOWN"],
            region=None,
            include_hidden=False,
            strict=True,
            observation_end=None,
            db=self.db,
        )

        self.assertEqual(response.unknown_codes, ["UNKNOWN"])
        self.assertEqual(response.unavailable_codes, ["TEST_A"])
        self.assertEqual(response.missing_codes, ["UNKNOWN", "TEST_A"])

    def test_history_date_range_and_pagination(self) -> None:
        for month in range(1, 4):
            self.add_observation(
                "TEST_A",
                dt.date(2026, month, 1),
                [
                    {
                        "value": month,
                        "available_at": dt.datetime(2026, month, 5),
                        "retrieved_at": dt.datetime(2026, month, 5, 1),
                    }
                ],
            )

        points, total = get_indicator_history_as_of(
            self.db,
            code="TEST_A",
            as_of=dt.datetime(2026, 4, 1),
            start=dt.date(2026, 2, 1),
            offset=1,
            limit=1,
        )

        self.assertEqual(total, 2)
        self.assertEqual([point.date for point in points], [dt.date(2026, 3, 1)])

    def test_batch_snapshot_uses_latest_visible_observation_per_indicator(self) -> None:
        for code, month, value in (
            ("TEST_A", 1, 10),
            ("TEST_A", 2, 20),
            ("TEST_B", 1, 30),
        ):
            self.add_observation(
                code,
                dt.date(2026, month, 1),
                [
                    {
                        "value": value,
                        "available_at": dt.datetime(2026, month, 10),
                        "retrieved_at": dt.datetime(2026, month, 10, 1),
                    }
                ],
            )

        points = get_latest_snapshot_as_of(
            self.db,
            codes=["TEST_A", "TEST_B"],
            as_of=dt.datetime(2026, 3, 1),
        )

        by_code = {point.indicator_code: point for point in points}
        self.assertEqual(by_code["TEST_A"].date, dt.date(2026, 2, 1))
        self.assertEqual(by_code["TEST_A"].value, 20)
        self.assertEqual(by_code["TEST_B"].value, 30)


if __name__ == "__main__":
    unittest.main()
