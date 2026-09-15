import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, Indicator, ReleaseEvidence
from app.services import china_credit_evidence as credit
from scripts.backfill_china_credit_evidence import (
    evidence_summary,
    main,
    store_credit_evidence,
    validate_apply_coverage,
)


class BackfillChinaCreditEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in credit.CREDIT_EVIDENCE_CODES:
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
                    category="cycle_input",
                    unit="",
                    source="official",
                    frequency="monthly",
                    is_visible=False,
                    sort_order=0,
                    region="CN",
                )
            )
        self.db.add(
            DataPoint(
                indicator_code="CN_TSF",
                date=dt.date(2025, 1, 1),
                value=65001.0,
                status="mirror_backfill",
                version=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _row(code: str, period: str = "2025-01") -> pd.DataFrame:
        observed = pd.Period(period, freq="M")
        row = credit._direct_row(
            code=code,
            observed=dt.date(observed.year, observed.month, 1),
            value=65000.0,
            available_at=dt.datetime(2025, 2, 14, 17),
            source_url="https://www.pbc.gov.cn/release.html",
            title="official release",
            source_sha256="a" * 64,
            reported_scope="month",
            preliminary=True,
            definition_version="test_v1",
        )
        return pd.DataFrame([row], columns=credit._EVIDENCE_COLUMNS)

    def test_store_is_idempotent_and_never_changes_current_value(self) -> None:
        evidence = {"CN_TSF": self._row("CN_TSF")}

        first = store_credit_evidence(
            self.db, evidence, expected_through="2025-01", allow_partial=True
        )
        second = store_credit_evidence(
            self.db, evidence, expected_through="2025-01", allow_partial=True
        )

        point = self.db.scalar(
            select(DataPoint).where(DataPoint.indicator_code == "CN_TSF")
        )
        count = self.db.scalar(select(func.count()).select_from(ReleaseEvidence))
        self.assertEqual(first["CN_TSF"], 1)
        self.assertTrue(all(value == 0 for value in second.values()))
        self.assertEqual(count, 1)
        self.assertEqual(float(point.value), 65001.0)
        self.assertEqual(point.version, 1)

    @staticmethod
    def _complete_gate_evidence() -> dict[str, pd.DataFrame]:
        through = pd.Period("2026-08", freq="M")
        ranges = {
            "CN_TSF": pd.period_range("2015-01", through, freq="M"),
            "CN_M1M2": pd.period_range("2024-01", through, freq="M"),
            "CN_CREDIT_IMPULSE": pd.period_range("2023-12", through, freq="M"),
        }
        recent = pd.period_range(through - 23, through, freq="M")
        for code in (
            "CN_TSF_RMB_LOANS_FLOW",
            "CN_CORP_BOND_FINANCING",
            "CN_GOV_BOND_FINANCING",
            "CN_TSF_STOCK_YOY",
            "CN_TSF_RMB_LOAN_STOCK_YOY",
        ):
            ranges[code] = recent
        return {
            code: pd.DataFrame(
                {"date": [dt.date(period.year, period.month, 1) for period in values]}
            )
            for code, values in ranges.items()
        }

    def test_apply_gate_requires_continuous_model_and_recent_decomposition(self) -> None:
        evidence = self._complete_gate_evidence()
        complete = validate_apply_coverage(evidence, expected_through="2026-08")
        evidence["CN_TSF_RMB_LOANS_FLOW"] = evidence[
            "CN_TSF_RMB_LOANS_FLOW"
        ].iloc[1:]
        incomplete = validate_apply_coverage(evidence, expected_through="2026-08")

        self.assertTrue(complete["ready"])
        self.assertFalse(incomplete["ready"])
        self.assertEqual(
            incomplete["missing"]["CN_TSF_RMB_LOANS_FLOW"], ["2024-09"]
        )

    def test_summary_reports_status_counts(self) -> None:
        summary = evidence_summary({"CN_TSF": self._row("CN_TSF")})

        self.assertEqual(summary["CN_TSF"]["rows"], 1)
        self.assertEqual(summary["CN_TSF"]["status_counts"], {"published": 1})
        self.assertEqual(summary["CN_M1M2"]["rows"], 0)

    def test_cli_apply_refuses_partial_before_opening_database(self) -> None:
        with (
            patch(
                "scripts.backfill_china_credit_evidence.collect_credit_evidence",
                return_value={},
            ),
            patch(
                "scripts.backfill_china_credit_evidence.SessionLocal"
            ) as session_local,
            patch(
                "sys.argv",
                [
                    "backfill_china_credit_evidence",
                    "--expected-through",
                    "2026-08",
                    "--apply",
                ],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing --apply"):
                main()

        session_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
