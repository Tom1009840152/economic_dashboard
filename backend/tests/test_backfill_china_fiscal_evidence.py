import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, Indicator, ReleaseEvidence
from app.services.china_fiscal_evidence import FISCAL_EVIDENCE_CODES
from scripts.backfill_china_fiscal_evidence import (
    evidence_summary,
    main,
    store_fiscal_evidence,
    validate_apply_coverage,
)


class BackfillChinaFiscalEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in FISCAL_EVIDENCE_CODES:
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
                    category="cycle_input",
                    unit="",
                    source="official",
                    frequency="quarterly",
                    is_visible=False,
                    sort_order=0,
                    region="CN",
                )
            )
        self.db.add(
            DataPoint(
                indicator_code="CN_GDP_NOMINAL_YTD",
                date=dt.date(2024, 6, 1),
                value=616836.0,
                status="mirror_backfill",
                version=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _gdp_evidence() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": dt.date(2024, 6, 1),
                    "value": 616835.75,
                    "release_date": dt.date(2024, 7, 16),
                    "available_at": dt.datetime(2024, 7, 16, 10),
                    "source_url": "https://www.stats.gov.cn/sj/zxfb/release.html",
                    "status": "published",
                    "formula_version": None,
                    "provenance_json": '{"kind":"initial_release"}',
                }
            ]
        )

    def test_store_is_idempotent_and_does_not_change_current_value(self) -> None:
        evidence = {"CN_GDP_NOMINAL_YTD": self._gdp_evidence()}

        first = store_fiscal_evidence(self.db, evidence, allow_partial=True)
        second = store_fiscal_evidence(self.db, evidence, allow_partial=True)

        point = self.db.scalar(
            select(DataPoint).where(
                DataPoint.indicator_code == "CN_GDP_NOMINAL_YTD"
            )
        )
        evidence_count = self.db.scalar(
            select(func.count()).select_from(ReleaseEvidence)
        )
        self.assertEqual(first["CN_GDP_NOMINAL_YTD"], 1)
        self.assertTrue(all(value == 0 for value in second.values()))
        self.assertEqual(evidence_count, 1)
        self.assertEqual(float(point.value), 616836.0)
        self.assertEqual(point.version, 1)

    def test_store_rejects_partial_evidence_by_default(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            store_fiscal_evidence(
                self.db,
                {"CN_GDP_NOMINAL_YTD": self._gdp_evidence()},
                coverage_as_of=dt.date(2025, 9, 15),
            )
        evidence_count = self.db.scalar(
            select(func.count()).select_from(ReleaseEvidence)
        )
        self.assertEqual(evidence_count, 0)

    def test_summary_is_stable_for_populated_and_empty_series(self) -> None:
        result = evidence_summary(
            {"CN_GDP_NOMINAL_YTD": self._gdp_evidence()}
        )

        self.assertEqual(result["CN_GDP_NOMINAL_YTD"]["rows"], 1)
        self.assertEqual(
            result["CN_GDP_NOMINAL_YTD"]["first_observation"], "2024-06-01"
        )
        self.assertEqual(
            result["CN_GDP_NOMINAL_YTD"]["last_available_at"],
            "2024-07-16T10:00:00",
        )
        self.assertEqual(result["CN_FISCAL_IMPULSE_PROXY"]["rows"], 0)

    def test_apply_gate_requires_a_continuous_quarterly_chain(self) -> None:
        inputs = pd.period_range("2021Q4", "2025Q2", freq="Q-DEC").asfreq(
            "M", how="end"
        )
        impulses = pd.period_range("2022Q4", "2025Q2", freq="Q-DEC").asfreq(
            "M", how="end"
        )
        evidence = {
            code: pd.DataFrame(
                {
                    "date": [
                        dt.date(period.year, period.month, 1)
                        for period in (
                            impulses
                            if code == "CN_FISCAL_IMPULSE_PROXY"
                            else inputs
                        )
                    ]
                }
            )
            for code in FISCAL_EVIDENCE_CODES
        }

        complete = validate_apply_coverage(
            evidence, as_of=dt.date(2025, 9, 15)
        )
        evidence["CN_GDP_NOMINAL_YTD"] = evidence[
            "CN_GDP_NOMINAL_YTD"
        ].iloc[:-1]
        incomplete = validate_apply_coverage(
            evidence, as_of=dt.date(2025, 9, 15)
        )

        self.assertTrue(complete["ready"])
        self.assertFalse(incomplete["ready"])
        self.assertEqual(
            incomplete["missing"]["CN_GDP_NOMINAL_YTD"], ["2025-06"]
        )

    def test_cli_apply_refuses_partial_crawl_before_opening_database(self) -> None:
        with (
            patch(
                "scripts.backfill_china_fiscal_evidence.collect_fiscal_evidence",
                return_value={},
            ),
            patch(
                "scripts.backfill_china_fiscal_evidence.SessionLocal"
            ) as session_local,
            patch("sys.argv", ["backfill_china_fiscal_evidence", "--apply"]),
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing --apply"):
                main()

        session_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
