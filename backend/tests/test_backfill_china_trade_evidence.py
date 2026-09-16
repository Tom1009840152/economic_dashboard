import datetime as dt
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator, ReleaseEvidence
from app.services.release_evidence import upsert_release_evidence as real_upsert
from scripts.backfill_china_trade_evidence import (
    evidence_summary,
    main,
    store_trade_evidence,
)


class BackfillChinaTradeEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(
            Indicator(
                code="CN_EXPORTS",
                name="中国出口同比",
                category="macro",
                unit="%",
                source="GACC",
                frequency="monthly",
                is_visible=True,
                sort_order=0,
                region="CN",
            )
        )
        point = DataPoint(
            indicator_code="CN_EXPORTS",
            date=dt.date(2024, 11, 1),
            value=6.6,
            status="published",
            version=1,
        )
        self.db.add(point)
        self.db.flush()
        self.db.add(
            DataPointVintage(
                data_point_id=point.id,
                indicator_code="CN_EXPORTS",
                date=point.date,
                value=6.6,
                status="published",
                version=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _evidence(value: float = 6.7) -> dict[str, pd.DataFrame]:
        provenance = json.dumps(
            {
                "availability_policy": "start_of_day_after_release_date",
                "collector": "gacc_trade_release_v1",
                "direct_official": True,
                "preliminary": True,
                "reported_scope": "single_month_yoy_usd",
                "reported_unit": "%",
                "source_sha256": "a" * 64,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "CN_EXPORTS": pd.DataFrame(
                [
                    {
                        "date": dt.date(2024, 11, 1),
                        "value": value,
                        "release_date": dt.date(2024, 12, 11),
                        "available_at": dt.datetime(2024, 12, 12),
                        "source_url": (
                            "https://english.customs.gov.cn/Statics/release.html"
                        ),
                        "status": "published",
                        "formula_version": None,
                        "provenance_json": provenance,
                        "evidence_kind": "official_release",
                        "chain_verified": True,
                        "availability_precision": "date_upper_bound",
                    }
                ]
            )
        }

    @classmethod
    def _complete_evidence(cls, value: float = 6.7) -> dict[str, pd.DataFrame]:
        template = cls._evidence(value)["CN_EXPORTS"].iloc[0].to_dict()
        rows = []
        for period in pd.period_range("2020-03", "2024-11", freq="M"):
            if period.month in {1, 2}:
                continue
            row = dict(template)
            observed = period.to_timestamp().date()
            release_date = observed + dt.timedelta(days=20)
            row.update(
                date=observed,
                value=value if str(period) == "2024-11" else 1.0,
                release_date=release_date,
                available_at=dt.datetime.combine(
                    release_date + dt.timedelta(days=1), dt.time.min
                ),
            )
            rows.append(row)
        return {"CN_EXPORTS": pd.DataFrame(rows)}

    @staticmethod
    def _fixed_expected_through(period: str = "2024-11"):
        return patch(
            "app.services.china_trade_evidence.expected_trade_observation_through",
            return_value=pd.Period(period, freq="M"),
        )

    def test_store_is_idempotent_and_never_changes_current_or_vintages(self) -> None:
        evidence = self._complete_evidence()

        with self._fixed_expected_through():
            first = store_trade_evidence(self.db, evidence)
            second = store_trade_evidence(self.db, evidence)

        point = self.db.scalar(select(DataPoint))
        vintage = self.db.scalar(select(DataPointVintage))
        self.assertGreater(first["CN_EXPORTS"], 1)
        self.assertEqual(second, {"CN_EXPORTS": 0})
        self.assertEqual(float(point.value), 6.6)
        self.assertEqual(point.version, 1)
        self.assertEqual(float(vintage.value), 6.6)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 1
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 1
        )

    def test_initial_release_may_differ_from_current(self) -> None:
        with self._fixed_expected_through():
            store_trade_evidence(self.db, self._complete_evidence(6.7))

        stored = self.db.scalar(
            select(ReleaseEvidence).where(
                ReleaseEvidence.date == dt.date(2024, 11, 1)
            )
        )
        current = self.db.scalar(select(DataPoint))
        self.assertEqual(float(stored.value), 6.7)
        self.assertEqual(float(current.value), 6.6)

    def test_incomplete_or_invalid_chain_refuses_storage(self) -> None:
        evidence = self._evidence()
        evidence["CN_EXPORTS"].loc[0, "source_url"] = (
            "https://english.customs.gov.cn.evil.example/release.html"
        )

        with self.assertRaisesRegex(RuntimeError, "unsafe rows"):
            with self._fixed_expected_through():
                store_trade_evidence(self.db, evidence)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(ReleaseEvidence)), 0
        )

    def test_invalid_provenance_refuses_storage_even_when_window_is_complete(self) -> None:
        evidence = self._complete_evidence()
        evidence["CN_EXPORTS"].loc[0, "provenance_json"] = json.dumps(
            {"collector": "gacc_trade_release_v1", "direct_official": True}
        )

        with self.assertRaisesRegex(RuntimeError, "unsafe rows"):
            with self._fixed_expected_through():
                store_trade_evidence(self.db, evidence)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(ReleaseEvidence)), 0
        )

    def test_missing_continuity_has_no_storage_bypass(self) -> None:
        evidence = self._evidence()

        with self.assertRaisesRegex(RuntimeError, "incomplete or unsafe"):
            with self._fixed_expected_through():
                store_trade_evidence(self.db, evidence)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(ReleaseEvidence)), 0
        )

    def test_failure_after_flush_rolls_back_the_single_transaction(self) -> None:
        def insert_then_fail(db, code, frame, commit=False):
            real_upsert(db, code, frame, commit=False)
            raise RuntimeError("injected failure")

        with patch(
            "scripts.backfill_china_trade_evidence.upsert_release_evidence",
            side_effect=insert_then_fail,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                with self._fixed_expected_through():
                    store_trade_evidence(self.db, self._complete_evidence())

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(ReleaseEvidence)), 0
        )

    def test_dry_run_is_default_and_never_opens_database(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "scripts.backfill_china_trade_evidence.collect_gacc_trade_evidence",
                return_value=self._complete_evidence(),
            ),
            self._fixed_expected_through(),
            patch(
                "scripts.backfill_china_trade_evidence.SessionLocal"
            ) as session_local,
            patch(
                "sys.argv",
                [
                    "backfill_china_trade_evidence",
                    "--pages",
                    "1",
                ],
            ),
            redirect_stdout(output),
        ):
            main()

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "check_only")
        self.assertTrue(payload["apply_gate"]["ready"])
        session_local.assert_not_called()

    def test_removed_gate_overrides_are_rejected_before_collection(self) -> None:
        for option, value in (
            ("--required-from", "2024-11"),
            ("--expected-through", "2024-11"),
        ):
            with self.subTest(option=option):
                with (
                    patch(
                        "scripts.backfill_china_trade_evidence.collect_gacc_trade_evidence"
                    ) as collect,
                    patch(
                        "scripts.backfill_china_trade_evidence.SessionLocal"
                    ) as session_local,
                    patch("sys.argv", ["backfill_china_trade_evidence", option, value]),
                    redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    main()

                self.assertEqual(raised.exception.code, 2)
                collect.assert_not_called()
                session_local.assert_not_called()

    def test_apply_with_incomplete_fixed_window_never_opens_database(self) -> None:
        with (
            patch(
                "scripts.backfill_china_trade_evidence.collect_gacc_trade_evidence",
                return_value=self._evidence(),
            ),
            self._fixed_expected_through(),
            patch(
                "scripts.backfill_china_trade_evidence.SessionLocal"
            ) as session_local,
            patch("sys.argv", ["backfill_china_trade_evidence", "--apply"]),
            self.assertRaisesRegex(RuntimeError, "refusing --apply"),
        ):
            main()

        session_local.assert_not_called()

    def test_summary_separates_availability_precision(self) -> None:
        summary = evidence_summary(self._evidence())

        self.assertEqual(summary["CN_EXPORTS"]["rows"], 1)
        self.assertEqual(
            summary["CN_EXPORTS"]["availability_precision_counts"],
            {"date_upper_bound": 1},
        )


if __name__ == "__main__":
    unittest.main()
