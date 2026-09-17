import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, Indicator, RefreshResult, RefreshRun
from app.services.data_quality import (
    DataQualityError,
    consumer_component_issues,
    validate_indicator_frame,
)
from app.services.indicator_service import refresh_all_indicators, upsert_points


class RefreshQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all(
            [
                Indicator(
                    code=code,
                    name=code,
                    category="macro",
                    unit="%",
                    source="test",
                    frequency="monthly",
                    is_visible=True,
                    sort_order=index,
                    region="CN",
                )
                for index, code in enumerate(("CN_PMI", "CN_CPI"))
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def frame(code: str = "CN_PMI", values: tuple[float, ...] = (49.0, 50.0)) -> pd.DataFrame:
        del code
        return pd.DataFrame(
            {
                "date": [dt.date(2020, month, 1) for month in range(1, len(values) + 1)],
                "value": values,
            }
        )

    def test_obvious_bad_batch_is_blocked_and_keeps_snapshot(self) -> None:
        upsert_points(self.db, "CN_PMI", self.frame(values=(49.0,)))

        with self.assertRaises(DataQualityError):
            upsert_points(self.db, "CN_PMI", self.frame(values=(149.0,)))

        point = self.db.scalar(select(DataPoint).where(DataPoint.indicator_code == "CN_PMI"))
        self.assertEqual(float(point.value), 49.0)
        self.assertEqual(point.version, 1)

    def test_structural_quality_checks(self) -> None:
        duplicate = pd.DataFrame(
            {"date": [dt.date(2020, 1, 1)] * 2, "value": [1.0, 2.0]}
        )
        report = validate_indicator_frame(self.db, "CN_CPI", duplicate)
        self.assertIn("duplicate_date", {issue.code for issue in report.blocking_issues})

        invalid = pd.DataFrame(
            {
                "date": [dt.date.today() + dt.timedelta(days=1), dt.date(2020, 1, 1)],
                "value": [1.0, "not-a-number"],
            }
        )
        report = validate_indicator_frame(self.db, "CN_CPI", invalid)
        codes = {issue.code for issue in report.blocking_issues}
        self.assertIn("future_date", codes)
        self.assertIn("non_numeric_value", codes)

        report = validate_indicator_frame(
            self.db, "CN_CPI", self.frame(values=(1.0, 2.0)), previous_row_count=20
        )
        self.assertIn("row_count_collapse", {issue.code for issue in report.blocking_issues})

    def test_formula_upgrade_allows_one_audited_history_reset(self) -> None:
        self.db.add(
            Indicator(
                code="CN_M1M2",
                name="CN_M1M2",
                category="macro",
                unit="pp",
                source="Derived",
                frequency="monthly",
                is_visible=True,
                sort_order=10,
                region="CN",
            )
        )
        self.db.add(
            DataPoint(
                indicator_code="CN_M1M2",
                date=dt.date(2023, 12, 1),
                value=-8.0,
                status="derived",
                formula_version="1.0.0",
                version=1,
            )
        )
        self.db.commit()
        incoming = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=31, freq="MS").date,
                "value": [-6.0] * 31,
                "status": ["derived"] * 31,
                "formula_version": ["1.1.0"] * 31,
            }
        )

        first = validate_indicator_frame(
            self.db, "CN_M1M2", incoming, previous_row_count=223
        )
        self.assertFalse(first.blocking_issues)
        self.assertIn(
            "formula_regime_history_reset",
            {issue.code for issue in first.issues if issue.severity == "warning"},
        )

        self.db.add(
            DataPoint(
                indicator_code="CN_M1M2",
                date=dt.date(2024, 1, 1),
                value=-6.0,
                status="derived",
                formula_version="1.1.0",
                version=1,
            )
        )
        self.db.commit()
        repeated = validate_indicator_frame(
            self.db, "CN_M1M2", incoming, previous_row_count=223
        )
        self.assertIn(
            "row_count_collapse", {issue.code for issue in repeated.blocking_issues}
        )

    def test_catalog_bounds_and_release_metadata_are_enforced(self) -> None:
        out_of_range = pd.DataFrame(
            {"date": [dt.date(2020, 1, 1)], "value": [-0.1]}
        )
        report = validate_indicator_frame(self.db, "USDCNY", out_of_range)
        self.assertIn("hard_range", {issue.code for issue in report.blocking_issues})

        bad_metadata = pd.DataFrame(
            {
                "date": [dt.date(2020, 1, 1)],
                "value": [2.0],
                "release_date": ["not-a-date"],
                "available_at": [dt.datetime(2099, 1, 1)],
                "status": ["guessed"],
            }
        )
        report = validate_indicator_frame(self.db, "CN_CPI", bad_metadata)
        codes = {issue.code for issue in report.blocking_issues}
        self.assertIn("invalid_release_date", codes)
        self.assertIn("future_available_at", codes)
        self.assertIn("invalid_status", codes)

    def test_likely_unit_scale_change_is_blocked(self) -> None:
        upsert_points(self.db, "CN_CPI", self.frame(values=(100.0,)))
        report = validate_indicator_frame(self.db, "CN_CPI", self.frame(values=(1.0,)))
        self.assertIn("scale_break", {issue.code for issue in report.blocking_issues})

    def test_statistical_jump_is_recorded_as_reviewable_warning(self) -> None:
        history = pd.DataFrame(
            {
                "date": [dt.date(2020, month, 1) for month in range(1, 13)],
                "value": list(range(1, 13)),
            }
        )
        upsert_points(self.db, "CN_CPI", history)
        incoming = pd.DataFrame({"date": [dt.date(2021, 1, 1)], "value": [100.0]})
        report = validate_indicator_frame(self.db, "CN_CPI", incoming)
        warnings = {issue.code for issue in report.issues if issue.severity == "warning"}
        self.assertIn("abnormal_jump", warnings)
        self.assertFalse(report.blocking_issues)

    def test_consumer_components_cannot_silently_duplicate(self) -> None:
        dates = [dt.date(2020, month, 1) for month in range(1, 7)]
        duplicate = pd.DataFrame({"date": dates, "value": [90.0] * 6})
        different = pd.DataFrame({"date": dates, "value": [91.0, 92.0, 93.0, 94.0, 95.0, 96.0]})
        issues = consumer_component_issues(
            {
                "CN_CONSUMER_CONFIDENCE": different,
                "CN_CONSUMER_SATISFACTION": duplicate,
                "CN_CONSUMER_EXPECTATIONS": duplicate.copy(),
            }
        )
        self.assertIn("CN_CONSUMER_SATISFACTION", issues)
        self.assertIn("CN_CONSUMER_EXPECTATIONS", issues)
        self.assertNotIn("CN_CONSUMER_CONFIDENCE", issues)

    def test_duplicate_consumer_components_are_not_written(self) -> None:
        dates = [dt.date(2020, month, 1) for month in range(1, 7)]
        duplicate = pd.DataFrame({"date": dates, "value": [90.0] * 6})
        different = pd.DataFrame({"date": dates, "value": list(range(91, 97))})
        refresh_all_indicators(
            self.db,
            trigger="test",
            fetchers={
                "CN_CONSUMER_CONFIDENCE": lambda: different,
                "CN_CONSUMER_SATISFACTION": lambda: duplicate,
                "CN_CONSUMER_EXPECTATIONS": lambda: duplicate.copy(),
            },
        )
        stored_codes = set(self.db.scalars(select(DataPoint.indicator_code)))
        self.assertIn("CN_CONSUMER_CONFIDENCE", stored_codes)
        self.assertNotIn("CN_CONSUMER_SATISFACTION", stored_codes)
        self.assertNotIn("CN_CONSUMER_EXPECTATIONS", stored_codes)
        run = self.db.scalar(select(RefreshRun))
        self.assertEqual(run.failed_count, 2)

    def test_refresh_ledger_records_success_no_change_and_failure(self) -> None:
        fetchers = {
            "CN_PMI": lambda: self.frame(values=(49.0, 50.0)),
            "CN_CPI": lambda: pd.DataFrame(columns=["date", "value"]),
        }
        refresh_all_indicators(self.db, trigger="test", fetchers=fetchers)

        first_run = self.db.scalar(select(RefreshRun))
        self.assertEqual(first_run.status, "partial_failure")
        self.assertEqual(first_run.success_count, 1)
        self.assertEqual(first_run.failed_count, 1)
        first_results = {
            result.indicator_code: result
            for result in self.db.execute(select(RefreshResult)).scalars()
        }
        self.assertEqual(first_results["CN_PMI"].status, "success")
        self.assertIsNotNone(first_results["CN_PMI"].last_success_at)
        self.assertEqual(first_results["CN_CPI"].status, "failed")
        self.assertIn("empty_data", first_results["CN_CPI"].quality_issues)
        previous_success = first_results["CN_PMI"].last_success_at

        refresh_all_indicators(
            self.db,
            trigger="test",
            fetchers={
                "CN_PMI": lambda: self.frame(values=(49.0, 50.0)),
                "CN_CPI": lambda: self.frame(values=(1.0, 1.1)),
            },
        )
        second_run = self.db.scalars(
            select(RefreshRun).order_by(RefreshRun.id.desc()).limit(1)
        ).one()
        self.assertEqual(second_run.status, "success")
        self.assertEqual(second_run.no_change_count, 1)
        self.assertEqual(second_run.success_count, 1)

        def failed_fetch():
            raise RuntimeError("upstream unavailable")

        refresh_all_indicators(
            self.db, trigger="test", fetchers={"CN_PMI": failed_fetch}
        )
        failed = self.db.scalar(
            select(RefreshResult)
            .where(RefreshResult.run_id == 3, RefreshResult.indicator_code == "CN_PMI")
        )
        self.assertEqual(failed.status, "failed")
        self.assertGreaterEqual(failed.last_success_at, previous_success)
        self.assertIn("upstream unavailable", failed.error)

    def test_refresh_ledger_keeps_source_verification_separate_from_fetch_time(self) -> None:
        frame = self.frame(values=(49.0, 50.0))
        frame.attrs["verified_through"] = dt.date.today()

        refresh_all_indicators(
            self.db,
            trigger="test",
            fetchers={"CN_PMI": lambda: frame},
        )

        result = self.db.scalar(select(RefreshResult))
        self.assertEqual(result.source_verified_through, dt.date.today())
        self.assertIsNotNone(result.last_success_at)
        self.assertNotEqual(
            result.last_success_at.date(),
            dt.date(2020, 2, 1),
        )

    def test_indicator_write_and_ledger_are_atomic(self) -> None:
        with (
            patch(
                "app.services.indicator_service._record_refresh_result",
                side_effect=RuntimeError("ledger write failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            refresh_all_indicators(
                self.db,
                trigger="test",
                fetchers={"CN_PMI": lambda: self.frame(values=(49.0,))},
            )

        self.assertIsNone(
            self.db.scalar(
                select(DataPoint).where(DataPoint.indicator_code == "CN_PMI")
            )
        )
        run = self.db.scalar(select(RefreshRun))
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.finished_at)


if __name__ == "__main__":
    unittest.main()
