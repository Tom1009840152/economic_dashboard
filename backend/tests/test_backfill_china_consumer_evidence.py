import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, Indicator, ReleaseEvidence
from app.services.china_consumer_evidence import CEI_CONSUMER_EVIDENCE_CODES
from scripts.backfill_china_consumer_evidence import (
    expected_consumer_observation_through,
    main,
    store_consumer_evidence,
    validate_apply_coverage,
)


def complete_evidence() -> dict[str, pd.DataFrame]:
    periods = pd.period_range("2015-12", periods=24, freq="M")
    values = {
        "CN_CONSUMER_EXPECTATIONS": 100.0,
        "CN_CONSUMER_SATISFACTION": 90.0,
        "CN_CONSUMER_CONFIDENCE": 96.0,
    }
    result = {}
    for code, value in values.items():
        rows = []
        for period in periods:
            observed = dt.date(period.year, period.month, 1)
            release_date = observed + dt.timedelta(days=40)
            rows.append(
                {
                    "date": observed,
                    "value": value,
                    "release_date": release_date,
                    "available_at": dt.datetime.combine(
                        release_date + dt.timedelta(days=1), dt.time.min
                    ),
                    "source_url": (
                        "https://www.cei.cn/defaultsite/s/article/"
                        f"{period.year}/{period.month:02d}/{code}.html"
                    ),
                    "status": "published",
                    "formula_version": None,
                    "provenance_json": '{"row_policy":"article_title_month_only"}',
                    "evidence_kind": "official_distribution_mirror",
                    "chain_verified": True,
                    "availability_precision": "date_upper_bound",
                }
            )
        result[code] = pd.DataFrame(rows)
    return result


class BackfillChinaConsumerEvidenceTests(unittest.TestCase):
    EXPECTED_THROUGH = "2017-11"

    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in CEI_CONSUMER_EVIDENCE_CODES:
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
                    category="cycle_input",
                    unit="点",
                    source="eastmoney",
                    frequency="monthly",
                    is_visible=False,
                    sort_order=0,
                    region="CN",
                )
            )
        self.db.add(
            DataPoint(
                indicator_code="CN_CONSUMER_CONFIDENCE",
                date=dt.date(2017, 11, 1),
                value=111.0,
                status="mirror_backfill",
                version=3,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_gate_requires_continuous_aligned_three_series_and_24_months(self) -> None:
        evidence = complete_evidence()
        self.assertTrue(
            validate_apply_coverage(
                evidence, expected_through=self.EXPECTED_THROUGH
            )["ready"]
        )

        evidence["CN_CONSUMER_EXPECTATIONS"] = evidence[
            "CN_CONSUMER_EXPECTATIONS"
        ].drop(index=5)
        gate = validate_apply_coverage(
            evidence, expected_through=self.EXPECTED_THROUGH
        )
        self.assertFalse(gate["ready"])
        self.assertFalse(gate["series_aligned"])
        self.assertEqual(
            gate["missing"]["CN_CONSUMER_EXPECTATIONS"], ["2016-05"]
        )

    def test_store_is_idempotent_and_never_changes_current_point(self) -> None:
        evidence = complete_evidence()
        first = store_consumer_evidence(
            self.db, evidence, expected_through=self.EXPECTED_THROUGH
        )
        second = store_consumer_evidence(
            self.db, evidence, expected_through=self.EXPECTED_THROUGH
        )

        point = self.db.scalar(
            select(DataPoint).where(
                DataPoint.indicator_code == "CN_CONSUMER_CONFIDENCE"
            )
        )
        count = self.db.scalar(select(func.count()).select_from(ReleaseEvidence))
        self.assertEqual(sum(first.values()), 72)
        self.assertEqual(sum(second.values()), 0)
        self.assertEqual(count, 72)
        self.assertEqual(float(point.value), 111.0)
        self.assertEqual(point.version, 3)

    def test_incomplete_gate_leaves_database_empty(self) -> None:
        evidence = complete_evidence()
        evidence["CN_CONSUMER_CONFIDENCE"] = evidence[
            "CN_CONSUMER_CONFIDENCE"
        ].iloc[:-1]
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            store_consumer_evidence(
                self.db, evidence, expected_through=self.EXPECTED_THROUGH
            )
        count = self.db.scalar(select(func.count()).select_from(ReleaseEvidence))
        self.assertEqual(count, 0)

    def test_complete_but_stale_chain_cannot_pass_apply_gate(self) -> None:
        gate = validate_apply_coverage(
            complete_evidence(), expected_through="2026-06"
        )

        self.assertFalse(gate["ready"])
        self.assertFalse(gate["fresh_enough"])
        self.assertEqual(gate["latest_found"], "2017-11")
        self.assertEqual(gate["expected_through"], "2026-06")
        self.assertIn("2026-06", gate["missing"]["CN_CONSUMER_CONFIDENCE"])

    def test_production_freshness_allows_at_most_three_months(self) -> None:
        self.assertEqual(
            expected_consumer_observation_through(dt.date(2026, 9, 15)),
            pd.Period("2026-06", freq="M"),
        )

    def test_cli_rejects_incomplete_crawl_before_opening_database(self) -> None:
        with (
            patch(
                "scripts.backfill_china_consumer_evidence."
                "collect_consumer_release_evidence",
                return_value={},
            ),
            patch(
                "scripts.backfill_china_consumer_evidence.SessionLocal"
            ) as session_local,
            patch("sys.argv", ["backfill_china_consumer_evidence", "--apply"]),
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing --apply"):
                main()
        session_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
