import datetime as dt
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator
from app.services.china_core_cpi_current_backfill import (
    CORE_CPI_CODE,
    CORE_CPI_CURRENT_START,
    CoreCpiCurrentBackfillError,
    CoreCpiCurrentConflictError,
    backfill_core_cpi_current,
    inspect_core_cpi_current_candidates,
)
from app.services.china_core_cpi_table_evidence import (
    CORE_CPI_REQUIRED_FROM,
    CORE_CPI_TABLE_CACHE_VERSION,
    CORE_CPI_TABLE_PARSER_VERSION,
    core_cpi_table_assertion_digest,
)
from app.services.indicator_service import upsert_points as real_upsert


TODAY = dt.date(2017, 4, 20)


def evidence_frame(*periods: str) -> pd.DataFrame:
    rows = []
    for index, label in enumerate(periods, start=1):
        period = pd.Period(label, freq="M")
        observed = period.start_time.date()
        released = (period + 1).start_time.date()
        available = dt.datetime(released.year, released.month, 9, 9, 30)
        url = (
            "https://www.stats.gov.cn/sj/zxfb/"
            f"{released:%Y%m}/t{released:%Y%m%d}_{index}.html"
        )
        rows.append(
            {
                "date": observed,
                "value": round(0.1 * index, 6),
                "release_date": available.date(),
                "available_at": available,
                "source_url": url,
                "status": "published",
                "formula_version": None,
                "provenance_json": {
                    "article_observation": observed.isoformat(),
                    "cache_version": CORE_CPI_TABLE_CACHE_VERSION,
                    "column_label": "同比涨跌幅（%）",
                    "evidence_semantics": (
                        "subject_month_official_cpi_table_row"
                    ),
                    "parser_version": CORE_CPI_TABLE_PARSER_VERSION,
                    "publication_time_source": "visible_nbs_detail_title",
                    "redirect_chain": [url],
                    "row_label": "其中：不包括食品和能源",
                    "source_final_url": url,
                    "source_kind": "nbs_cpi_release_table",
                    "source_request_url": url,
                    "source_sha256": f"{index:064x}",
                    "table_assertion_sha256": core_cpi_table_assertion_digest(
                        observed,
                        round(0.1 * index, 6),
                        "同比涨跌幅（%）",
                    ),
                },
                "evidence_kind": "official_release",
                "chain_verified": True,
                "availability_precision": "exact_minute",
            }
        )
    return pd.DataFrame(rows)


class ChinaCoreCpiCurrentBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        # Match production SessionLocal: pending writes are invisible to
        # follow-up SELECTs unless the service explicitly flushes them.
        self.db = Session(self.engine, autoflush=False)
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
        self.evidence = evidence_frame("2017-01", "2017-02", "2017-03")

    def test_current_backfill_reuses_the_table_contract_start(self) -> None:
        self.assertIs(CORE_CPI_CURRENT_START, CORE_CPI_REQUIRED_FROM)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _seed_existing(self, *, value: float = 0.1, formula_version=None) -> DataPoint:
        point = DataPoint(
            indicator_code=CORE_CPI_CODE,
            date=dt.date(2017, 1, 1),
            value=value,
            release_date=dt.date(2017, 9, 1),
            available_at=dt.datetime(2017, 9, 1, 8),
            source_url="https://www.stats.gov.cn/legacy-proof.html",
            status="published",
            formula_version=formula_version,
            version=1,
        )
        self.db.add(point)
        self.db.flush()
        self.db.add(
            DataPointVintage(
                data_point_id=point.id,
                indicator_code=point.indicator_code,
                date=point.date,
                value=point.value,
                release_date=point.release_date,
                available_at=point.available_at,
                retrieved_at=dt.datetime(2017, 9, 2),
                source_url=point.source_url,
                status=point.status,
                formula_version=point.formula_version,
                version=point.version,
            )
        )
        self.db.commit()
        return point

    def test_source_dry_run_is_complete_and_database_free(self) -> None:
        report = inspect_core_cpi_current_candidates(
            self.evidence,
            today=TODAY,
        )
        self.assertEqual(report["mode"], "source_dry_run")
        self.assertTrue(report["ready"])
        self.assertFalse(report["database_checked"])
        self.assertEqual(report["candidate_count"], 3)
        self.assertEqual(report["required_through"], "2017-02")
        self.assertEqual(report["collected_through"], "2017-03")
        self.assertIsNone(report["existing_count"])
        self.assertIsNone(report["planned_insert_count"])
        self.assertIsNone(report["projected_current_count"])
        self.assertIsNone(report["projected_coverage_rate"])

    def test_current_gate_accepts_controlled_legacy_yoy_header(self) -> None:
        legacy = self.evidence.copy()
        for row_index, row in legacy.iterrows():
            provenance = dict(row["provenance_json"])
            provenance["column_label"] = "同比涨跌（%）"
            provenance["table_assertion_sha256"] = (
                core_cpi_table_assertion_digest(
                    row["date"],
                    row["value"],
                    provenance["column_label"],
                )
            )
            legacy.at[row_index, "provenance_json"] = provenance

        report = inspect_core_cpi_current_candidates(legacy, today=TODAY)

        self.assertTrue(report["ready"])

    def test_source_gate_rejects_gap_short_cutoff_future_and_forged_provenance(self) -> None:
        cases = {}
        cases["gap"] = self.evidence.iloc[[0, 2]].copy()
        cases["short"] = self.evidence.iloc[[0]].copy()
        future = self.evidence.copy()
        future.loc[len(future)] = evidence_frame("2017-04").iloc[0]
        cases["future"] = future
        forged = self.evidence.copy()
        provenance = dict(forged.at[1, "provenance_json"])
        provenance["parser_version"] = "forged"
        forged.at[1, "provenance_json"] = provenance
        cases["forged"] = forged

        for label, frame in cases.items():
            with self.subTest(label=label), self.assertRaises(
                CoreCpiCurrentBackfillError
            ):
                inspect_core_cpi_current_candidates(frame, today=TODAY)

    def test_source_gate_rejects_same_month_or_subminute_publication(self) -> None:
        same_month = self.evidence.copy()
        same_month.at[1, "release_date"] = dt.date(2017, 2, 20)
        same_month.at[1, "available_at"] = dt.datetime(2017, 2, 20, 9, 30)
        seconds = self.evidence.copy()
        seconds.at[1, "available_at"] = dt.datetime(2017, 3, 9, 9, 30, 1)
        micros = self.evidence.copy()
        micros.at[1, "available_at"] = dt.datetime(
            2017, 3, 9, 9, 30, 0, 1
        )

        for label, frame in (
            ("same month", same_month),
            ("seconds", seconds),
            ("microseconds", micros),
        ):
            with self.subTest(label=label), self.assertRaises(
                CoreCpiCurrentBackfillError
            ):
                inspect_core_cpi_current_candidates(frame, today=TODAY)

    def test_source_gate_rejects_nat_dates(self) -> None:
        for column in ("date", "release_date"):
            for invalid in (pd.NaT, None):
                frame = self.evidence.copy()
                frame.at[1, column] = invalid
                with self.subTest(
                    column=column, invalid=invalid
                ), self.assertRaises(CoreCpiCurrentBackfillError):
                    inspect_core_cpi_current_candidates(frame, today=TODAY)

    def test_source_gate_requires_complete_official_table_lineage(self) -> None:
        mutations = {
            "row": ("row_label", "headline CPI"),
            "column": ("column_label", "环比涨跌幅（%）"),
            "cache": ("cache_version", 999),
            "assertion digest": ("table_assertion_sha256", "f" * 64),
            "final": (
                "source_final_url",
                "https://evil.example/release.html",
            ),
            "external hop": (
                "redirect_chain",
                [
                    self.evidence.at[1, "source_url"],
                    "https://evil.example/release.html",
                ],
            ),
            "wrong head": (
                "redirect_chain",
                ["https://www.stats.gov.cn/wrong-request.html"],
            ),
        }
        for label, (key, value) in mutations.items():
            frame = self.evidence.copy()
            provenance = dict(frame.at[1, "provenance_json"])
            provenance[key] = value
            frame.at[1, "provenance_json"] = provenance
            with self.subTest(label=label), self.assertRaises(
                CoreCpiCurrentBackfillError
            ):
                inspect_core_cpi_current_candidates(frame, today=TODAY)

    def test_database_dry_run_writes_nothing_and_reports_plan(self) -> None:
        existing = self._seed_existing()
        metadata = (
            existing.release_date,
            existing.available_at,
            existing.source_url,
            existing.status,
            existing.version,
        )

        report = backfill_core_cpi_current(
            self.db,
            self.evidence,
            today=TODAY,
        )

        self.assertEqual(report["mode"], "database_dry_run")
        self.assertEqual(report["candidate_count"], 3)
        self.assertEqual(report["existing_count"], 1)
        self.assertEqual(report["consistent_overlap_count"], 1)
        self.assertEqual(report["conflict_count"], 0)
        self.assertEqual(report["planned_insert_count"], 2)
        self.assertEqual(report["projected_current_count"], 3)
        self.assertEqual(report["projected_coverage_rate"], 1.0)
        self.assertEqual(report["before"], {"current_rows": 1, "vintage_rows": 1})
        self.assertEqual(
            report["projected_after"],
            {"current_rows": 3, "vintage_rows": 3},
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 1
        )
        self.db.refresh(existing)
        self.assertEqual(
            (
                existing.release_date,
                existing.available_at,
                existing.source_url,
                existing.status,
                existing.version,
            ),
            metadata,
        )

    def test_apply_is_missing_only_preserves_metadata_and_is_idempotent(self) -> None:
        existing = self._seed_existing()
        metadata = (
            existing.release_date,
            existing.available_at,
            existing.source_url,
            existing.status,
            existing.version,
        )

        first = backfill_core_cpi_current(
            self.db,
            self.evidence,
            apply=True,
            today=TODAY,
        )
        self.assertEqual(first["changed"], 2)
        self.assertEqual(first["planned_insert_count"], 2)
        self.assertEqual(first["after"], {"current_rows": 3, "vintage_rows": 3})
        rows = list(
            self.db.scalars(
                select(DataPoint)
                .where(DataPoint.indicator_code == CORE_CPI_CODE)
                .order_by(DataPoint.date)
            )
        )
        self.assertEqual([row.date.month for row in rows], [1, 2, 3])
        self.assertEqual(
            [row.status for row in rows],
            ["published", "historical_backfill", "historical_backfill"],
        )
        self.assertTrue(all(row.formula_version is None for row in rows))
        self.assertTrue(
            all(
                row.source_url.startswith("https://www.stats.gov.cn/")
                for row in rows
            )
        )
        self.db.refresh(existing)
        self.assertEqual(
            (
                existing.release_date,
                existing.available_at,
                existing.source_url,
                existing.status,
                existing.version,
            ),
            metadata,
        )

        second = backfill_core_cpi_current(
            self.db,
            self.evidence,
            apply=True,
            today=TODAY,
        )
        self.assertEqual(second["changed"], 0)
        self.assertEqual(second["planned_insert_count"], 0)
        self.assertEqual(second["after"], {"current_rows": 3, "vintage_rows": 3})

    def test_existing_value_or_formula_conflict_fails_before_write(self) -> None:
        for label, kwargs in (
            ("value", {"value": 9.9}),
            ("formula", {"formula_version": "not-direct"}),
        ):
            with self.subTest(label=label):
                self.tearDown()
                self.setUp()
                self._seed_existing(**kwargs)
                dry_run = backfill_core_cpi_current(
                    self.db,
                    self.evidence,
                    today=TODAY,
                )
                self.assertFalse(dry_run["ready"])
                self.assertEqual(dry_run["conflict_count"], 1)
                self.assertEqual(len(dry_run["conflicts"]), 1)
                with self.assertRaises(CoreCpiCurrentConflictError):
                    backfill_core_cpi_current(
                        self.db,
                        self.evidence,
                        apply=True,
                        today=TODAY,
                    )
                self.assertEqual(
                    self.db.scalar(select(func.count()).select_from(DataPoint)),
                    1,
                )
                self.assertEqual(
                    self.db.scalar(
                        select(func.count()).select_from(DataPointVintage)
                    ),
                    1,
                )

    def test_failure_after_flush_rolls_back_current_and_vintages(self) -> None:
        def write_then_fail(db, code, frame, **kwargs):
            real_upsert(db, code, frame, **kwargs)
            raise RuntimeError("fault after flush")

        with patch(
            "app.services.china_core_cpi_current_backfill.upsert_points",
            side_effect=write_then_fail,
        ):
            with self.assertRaises(RuntimeError):
                backfill_core_cpi_current(
                    self.db,
                    self.evidence,
                    apply=True,
                    today=TODAY,
                )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 0
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_missing_only_count_mismatch_rolls_back(self) -> None:
        def write_but_report_wrong_count(db, code, frame, **kwargs):
            real_upsert(db, code, frame, **kwargs)
            return len(frame) - 1

        with patch(
            "app.services.china_core_cpi_current_backfill.upsert_points",
            side_effect=write_but_report_wrong_count,
        ):
            with self.assertRaisesRegex(
                CoreCpiCurrentBackfillError, "changed the missing-only plan"
            ):
                backfill_core_cpi_current(
                    self.db,
                    self.evidence,
                    apply=True,
                    today=TODAY,
                )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), 0
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )


class ChinaCoreCpiCurrentBackfillCliTests(unittest.TestCase):
    def test_default_cli_is_source_only_and_never_constructs_a_session(self) -> None:
        from scripts import backfill_china_core_cpi_current as command

        evidence = evidence_frame("2017-01", "2017-02", "2017-03")
        output = io.StringIO()
        with (
            patch.object(
                command,
                "collect_core_cpi_table_evidence",
                return_value=evidence,
            ) as collect,
            patch.object(
                command,
                "inspect_core_cpi_current_candidates",
                return_value={"mode": "source_dry_run", "ready": True},
            ) as inspect,
            patch("app.db.SessionLocal", side_effect=AssertionError("DB opened")),
            redirect_stdout(output),
        ):
            command.main(["--pages", "3"])

        collect.assert_called_once_with(page_count=3, start_page=0)
        inspect.assert_called_once_with(evidence)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"mode": "source_dry_run", "ready": True},
        )

    def test_check_and_apply_modes_open_database_after_collection(self) -> None:
        from scripts import backfill_china_core_cpi_current as command

        evidence = evidence_frame("2017-01", "2017-02", "2017-03")
        for flag, expected_apply in (("--check-current", False), ("--apply", True)):
            with self.subTest(flag=flag):
                session = MagicMock()
                session.__enter__.return_value = "db-session"
                session_factory = MagicMock(return_value=session)
                output = io.StringIO()
                with (
                    patch.object(
                        command,
                        "collect_core_cpi_table_evidence",
                        return_value=evidence,
                    ),
                    patch.object(
                        command,
                        "backfill_core_cpi_current",
                        return_value={"mode": flag, "ready": True},
                    ) as backfill,
                    patch("app.db.SessionLocal", session_factory),
                    redirect_stdout(output),
                ):
                    command.main([flag])
                session_factory.assert_called_once_with()
                backfill.assert_called_once_with(
                    "db-session", evidence, apply=expected_apply
                )


if __name__ == "__main__":
    unittest.main()
