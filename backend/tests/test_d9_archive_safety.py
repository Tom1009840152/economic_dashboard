import datetime as dt
import io
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from scripts import backfill_china_cycle as backfill


class _FakeSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.closed = False

    def scalars(self, _query):
        return self.rows

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class D9ArchiveSafetyTests(unittest.TestCase):
    def test_archive_mode_never_calls_a_regular_fetcher_without_evidence(self) -> None:
        fallback = Mock(
            return_value=pd.DataFrame([{"date": "2024-01-01", "value": 1}])
        )
        session = _FakeSession()
        output = io.StringIO()

        with (
            patch.dict(backfill.BACKFILL_FETCHERS, {"CN_TEST": fallback}, clear=True),
            patch.dict(backfill.GROUPS, {"confidence": {"CN_TEST"}}),
            patch.object(backfill, "SessionLocal", return_value=session),
            patch.object(
                sys,
                "argv",
                [
                    "backfill_china_cycle.py",
                    "--group",
                    "confidence",
                    "--archive",
                    "--check-only",
                ],
            ),
            redirect_stdout(output),
        ):
            backfill.main()

        fallback.assert_not_called()
        self.assertTrue(session.closed)
        self.assertIn("skipped; no official archive evidence", output.getvalue())

    def test_trade_archive_check_uses_gacc_evidence_not_regular_fetcher(self) -> None:
        observed = dt.date(2024, 11, 1)
        fallback = Mock()
        archive_loader = Mock(
            return_value={
                "CN_EXPORTS": pd.DataFrame(
                    [
                        self._row(
                            observed,
                            6.7,
                            "https://english.customs.gov.cn/Statics/release.html",
                        )
                    ]
                )
            }
        )
        session = _FakeSession(
            [SimpleNamespace(date=observed, value=6.7)]
        )
        output = io.StringIO()

        with (
            patch.dict(
                backfill.BACKFILL_FETCHERS,
                {"CN_EXPORTS": fallback},
                clear=True,
            ),
            patch.dict(backfill.GROUPS, {"trade": {"CN_EXPORTS"}}, clear=True),
            patch.object(backfill, "_load_gacc_exports", archive_loader),
            patch.object(backfill, "SessionLocal", return_value=session),
            patch.object(
                sys,
                "argv",
                [
                    "backfill_china_cycle.py",
                    "--group",
                    "trade",
                    "--archive",
                    "--gacc-archive-start-page",
                    "3",
                    "--gacc-archive-pages",
                    "2",
                    "--check-only",
                ],
            ),
            redirect_stdout(output),
        ):
            backfill.main()

        archive_loader.assert_called_once_with(page_count=2, start_page=3)
        fallback.assert_not_called()
        self.assertTrue(session.closed)
        self.assertIn("CN_EXPORTS: 1 verified releases", output.getvalue())

    def test_trade_archive_reports_catalog_failure_instead_of_zero_success(self) -> None:
        fallback = Mock()
        session = _FakeSession()
        output = io.StringIO()

        with (
            patch.dict(
                backfill.BACKFILL_FETCHERS,
                {"CN_EXPORTS": fallback},
                clear=True,
            ),
            patch.dict(backfill.GROUPS, {"trade": {"CN_EXPORTS"}}, clear=True),
            patch.object(
                backfill,
                "_load_gacc_exports",
                side_effect=RuntimeError("all requested pages failed"),
            ),
            patch.object(backfill, "SessionLocal", return_value=session),
            patch.object(
                sys,
                "argv",
                [
                    "backfill_china_cycle.py",
                    "--group",
                    "trade",
                    "--archive",
                    "--check-only",
                ],
            ),
            redirect_stdout(output),
        ):
            backfill.main()

        fallback.assert_not_called()
        self.assertTrue(session.closed)
        self.assertIn(
            "CN_EXPORTS: FAILED: all requested pages failed",
            output.getvalue(),
        )

    def test_property_archive_includes_official_70_city_diffusion_loader(self) -> None:
        code = "CN_RE_PRICE_RISING_SHARE"
        observed = dt.date(2024, 12, 1)
        fallback = Mock()
        row = self._row(
            observed,
            20.0,
            "https://www.stats.gov.cn/sj/zxfb/202501/release.html",
        )
        row.update(
            status="derived",
            formula_version=backfill.NBS_70_CITY_FORMULA_VERSIONS[code],
        )
        house_price_loader = Mock(
            return_value={code: pd.DataFrame([row])}
        )
        session = _FakeSession(
            [SimpleNamespace(date=observed, value=20.0)]
        )
        output = io.StringIO()

        with (
            patch.dict(backfill.BACKFILL_FETCHERS, {code: fallback}, clear=True),
            patch.dict(backfill.GROUPS, {"property": {code}}, clear=True),
            patch.object(backfill, "_load_nbs_property_history", return_value={}),
            patch.object(backfill, "_load_real_estate_activity", return_value={}),
            patch.object(
                backfill, "_load_nbs_house_price_diffusion", house_price_loader
            ),
            patch.object(backfill, "SessionLocal", return_value=session),
            patch.object(
                sys,
                "argv",
                [
                    "backfill_china_cycle.py",
                    "--group",
                    "property",
                    "--archive",
                    "--archive-pages",
                    "70",
                    "--archive-start-page",
                    "20",
                    "--archive-shard",
                    "1000",
                    "--check-only",
                ],
            ),
            redirect_stdout(output),
        ):
            backfill.main()

        house_price_loader.assert_called_once_with(
            page_count=70,
            start_page=20,
            archive_shard=1000,
        )
        fallback.assert_not_called()
        self.assertTrue(session.closed)
        self.assertIn(f"{code}: 1 verified releases", output.getvalue())

    def test_all_approved_official_domains_are_accepted(self) -> None:
        urls = (
            "https://www.stats.gov.cn/sj/zxfb/release.html",
            "https://data.stats.gov.cn/release.html",
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
            "https://xining.pbc.gov.cn/release.html",
            "https://gks.mof.gov.cn/tongjishuju/release.html",
            "https://english.customs.gov.cn/Statics/release.html",
        )
        observed = dt.date(2024, 1, 1)
        for source_url in urls:
            with self.subTest(source_url=source_url):
                frame = pd.DataFrame([self._row(observed, 5.0, source_url)])
                accepted, rejected = backfill._verified_release_evidence(
                    _FakeSession([SimpleNamespace(date=observed, value=5.0)]),
                    "CN_TEST",
                    frame,
                    require_existing=True,
                )
                self.assertEqual(len(accepted), 1)
                self.assertEqual(rejected, [])

    def test_pboc_derived_formula_is_code_status_and_publisher_bound(self) -> None:
        derived_date = dt.date(2024, 1, 1)
        missing_formula_date = dt.date(2024, 2, 1)
        unknown_formula_date = dt.date(2024, 3, 1)
        backfill_date = dt.date(2024, 4, 1)
        derived = self._row(
            derived_date,
            5.0,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )
        derived["status"] = "derived"
        derived["formula_version"] = "pboc_ytd_diff_v1"
        missing_formula = self._row(
            missing_formula_date,
            6.0,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )
        missing_formula["status"] = "derived"
        unknown_formula = self._row(
            unknown_formula_date,
            7.0,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )
        unknown_formula["status"] = "derived"
        unknown_formula["formula_version"] = "unreviewed_formula_v1"
        derived_backfill = self._row(
            backfill_date,
            8.0,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )
        derived_backfill["status"] = "derived_backfill"
        derived_backfill["formula_version"] = "pboc_ytd_diff_v1"

        accepted, rejected = backfill._verified_release_evidence(
            _FakeSession(
                [
                    SimpleNamespace(date=derived_date, value=5.0),
                    SimpleNamespace(date=missing_formula_date, value=6.0),
                    SimpleNamespace(date=unknown_formula_date, value=7.0),
                    SimpleNamespace(date=backfill_date, value=8.0),
                ]
            ),
            "CN_TSF",
            pd.DataFrame(
                [derived, missing_formula, unknown_formula, derived_backfill]
            ),
            require_existing=True,
        )
        wrong_code, wrong_code_rejected = backfill._verified_release_evidence(
            _FakeSession([SimpleNamespace(date=derived_date, value=5.0)]),
            "CN_TEST",
            pd.DataFrame([derived]),
            require_existing=True,
        )

        self.assertEqual(accepted["date"].tolist(), [derived_date])
        self.assertEqual(accepted["status"].tolist(), ["derived"])
        self.assertEqual(
            accepted["formula_version"].tolist(),
            ["pboc_ytd_diff_v1"],
        )
        self.assertEqual(rejected, [])
        self.assertTrue(wrong_code.empty)
        self.assertEqual(wrong_code_rejected, [])

    def test_nbs_70_city_derived_formulas_are_code_and_publisher_bound(self) -> None:
        code = "CN_RE_PRICE_RISING_SHARE"
        formula = backfill.NBS_70_CITY_FORMULA_VERSIONS[code]
        accepted_date = dt.date(2024, 1, 1)
        wrong_code_date = dt.date(2024, 2, 1)
        wrong_host_date = dt.date(2024, 3, 1)
        rows = []
        for observed, source_url in (
            (
                accepted_date,
                "https://www.stats.gov.cn/sj/zxfb/release.html",
            ),
            (
                wrong_code_date,
                "https://www.stats.gov.cn/sj/zxfb/release.html",
            ),
            (
                wrong_host_date,
                "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
            ),
        ):
            row = self._row(observed, 5.0, source_url)
            row.update(status="derived", formula_version=formula)
            rows.append(row)

        accepted, rejected = backfill._verified_release_evidence(
            _FakeSession(
                [
                    SimpleNamespace(date=accepted_date, value=5.0),
                    SimpleNamespace(date=wrong_code_date, value=5.0),
                    SimpleNamespace(date=wrong_host_date, value=5.0),
                ]
            ),
            code,
            pd.DataFrame([rows[0], rows[2]]),
            require_existing=True,
        )
        wrong_code, wrong_code_rejected = backfill._verified_release_evidence(
            _FakeSession([SimpleNamespace(date=wrong_code_date, value=5.0)]),
            "CN_RE_PRICE_MOM_MEDIAN",
            pd.DataFrame([rows[1]]),
            require_existing=True,
        )

        self.assertEqual(accepted["date"].tolist(), [accepted_date])
        self.assertEqual(rejected, [])
        self.assertTrue(wrong_code.empty)
        self.assertEqual(wrong_code_rejected, [])

    def test_lookalike_or_non_https_domain_is_rejected(self) -> None:
        urls = (
            "https://www.stats.gov.cn.evil.example/release.html",
            "https://pbc.gov.cn@example.test/release.html",
            "https://mof.gov.cn.example.test/release.html",
            "https://customs.gov.cn.example.test/release.html",
            "http://www.stats.gov.cn/release.html",
            "http://english.customs.gov.cn/Statics/release.html",
        )
        observed = dt.date(2024, 1, 1)
        for source_url in urls:
            with self.subTest(source_url=source_url):
                frame = pd.DataFrame([self._row(observed, 5.0, source_url)])
                accepted, rejected = backfill._verified_release_evidence(
                    _FakeSession([SimpleNamespace(date=observed, value=5.0)]),
                    "CN_TEST",
                    frame,
                    require_existing=True,
                )
                self.assertTrue(accepted.empty)
                self.assertEqual(rejected, [])

    def test_missing_or_date_only_timestamp_is_rejected(self) -> None:
        observed = dt.date(2024, 1, 1)
        rows = [
            self._row(observed, 5.0, "https://www.stats.gov.cn/release.html"),
            self._row(observed, 5.0, "https://www.stats.gov.cn/release.html"),
        ]
        rows[0]["available_at"] = None
        rows[1]["available_at"] = dt.date(2024, 4, 16)

        accepted, rejected = backfill._verified_release_evidence(
            _FakeSession([SimpleNamespace(date=observed, value=5.0)]),
            "CN_TEST",
            pd.DataFrame(rows),
            require_existing=True,
        )

        self.assertTrue(accepted.empty)
        self.assertEqual(rejected, [])

    def test_archive_gate_is_repeatable_and_requires_an_exact_current_value(self) -> None:
        exact_date = dt.date(2024, 1, 1)
        mismatch_date = dt.date(2024, 2, 1)
        missing_date = dt.date(2024, 3, 1)
        frame = pd.DataFrame(
            [
                self._row(exact_date, 5.0, "https://www.pbc.gov.cn/release.html"),
                self._row(mismatch_date, 6.0, "https://www.pbc.gov.cn/release.html"),
                self._row(missing_date, 7.0, "https://www.pbc.gov.cn/release.html"),
            ]
        )
        session = _FakeSession(
            [
                SimpleNamespace(date=exact_date, value=5.0),
                SimpleNamespace(date=mismatch_date, value=6.5),
            ]
        )

        first = backfill._verified_release_evidence(
            session, "CN_TEST", frame, require_existing=True
        )
        second = backfill._verified_release_evidence(
            session, "CN_TEST", frame, require_existing=True
        )

        self.assertEqual(first[0]["date"].tolist(), [exact_date])
        self.assertEqual(first[1], ["2024-02-01", "2024-03-01"])
        pd.testing.assert_frame_equal(first[0], second[0])
        self.assertEqual(first[1], second[1])

    @staticmethod
    def _row(observed: dt.date, value: float, source_url: str) -> dict:
        released = dt.datetime(2024, 4, 16, 10, 0)
        return {
            "date": observed,
            "value": value,
            "release_date": released.date(),
            "available_at": released,
            "source_url": source_url,
            "status": "published",
        }


if __name__ == "__main__":
    unittest.main()
