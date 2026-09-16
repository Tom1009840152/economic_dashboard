import datetime as dt
import ssl
import unittest
from unittest.mock import patch

import pandas as pd

from app.services.china_cycle_backtest import _select_strict_vintages
from app.services.china_trade_evidence import (
    TradeEvidenceCollectionError,
    collect_gacc_trade_evidence,
    expected_trade_observation_through,
    validate_production_trade_evidence,
    validate_trade_evidence,
)


DETAIL_URL = "https://english.customs.gov.cn/Statics/november-2024.html"
TITLE = "(1) China's Total Export & Import Values, November 2024 (in USD)"
DETAIL = """
<!doctype html><html><head>
<title>(1) China's Total Export &amp; Import Values, November 2024 (in USD)</title>
<meta name="PubDate" content="2024-12-11">
</head><body>
<div class="article-date">2024/12/11</div>
<h1>China's Total Export &amp; Import Values, November 2024 (in USD)</h1>
<table>
<tr><th>Item</th><th>11</th><th>1-to-11</th><th>Month-on-Month +/- %</th><th>Year-on-Year +/- %</th><th>Year-on-Year +/- %</th></tr>
<tr><td>Total Export</td><td>3,123.1</td><td>32,407.1</td><td>1.1</td><td>6.7</td><td>5.4</td></tr>
<tr><td>Total Import</td><td>2,148.7</td><td>23,560.3</td><td>0.8</td><td>-3.9</td><td>1.2</td></tr>
</table><p>official preliminary USD release</p>
</body></html>
"""


class ChinaTradeEvidenceTests(unittest.TestCase):
    @staticmethod
    def _collect(source: str = DETAIL, *, url: str = DETAIL_URL, title: str = TITLE):
        with (
            patch(
                "app.services.china_trade_evidence._index_catalog",
                return_value=((url, title),),
            ),
            patch(
                "app.services.china_trade_evidence._get_gacc_archive_text",
                return_value=source,
            ),
        ):
            return collect_gacc_trade_evidence(page_count=1)

    def test_date_only_release_uses_next_day_upper_bound_and_provenance(self) -> None:
        row = self._collect()["CN_EXPORTS"].iloc[0]

        self.assertEqual(row["date"], dt.date(2024, 11, 1))
        self.assertEqual(row["value"], 6.7)
        self.assertEqual(row["release_date"], dt.date(2024, 12, 11))
        self.assertEqual(row["available_at"], dt.datetime(2024, 12, 12))
        self.assertEqual(row["availability_precision"], "date_upper_bound")
        self.assertEqual(row["evidence_kind"], "official_release")
        self.assertTrue(row["chain_verified"])
        self.assertIn('"collector":"gacc_trade_release_v1"', row["provenance_json"])

    def test_exact_minute_requires_independent_visible_date_agreement(self) -> None:
        exact = DETAIL.replace(
            'content="2024-12-11"', 'content="2024-12-11 10:05:00"'
        )
        exact_row = self._collect(exact)["CN_EXPORTS"].iloc[0]
        no_visible = exact.replace(
            '<div class="article-date">2024/12/11</div>', ""
        )
        no_visible_row = self._collect(no_visible)["CN_EXPORTS"].iloc[0]
        mismatched = exact.replace("2024/12/11", "2024/12/12")
        mismatched_row = self._collect(mismatched)["CN_EXPORTS"].iloc[0]

        self.assertEqual(exact_row["available_at"], dt.datetime(2024, 12, 11, 10, 5))
        self.assertEqual(exact_row["availability_precision"], "exact_minute")
        # The PubDate attribute itself is the clock source, not independent
        # visible-date corroboration.
        self.assertEqual(no_visible_row["available_at"], dt.datetime(2024, 12, 12))
        self.assertEqual(no_visible_row["availability_precision"], "date_upper_bound")
        self.assertEqual(mismatched_row["release_date"], dt.date(2024, 12, 12))
        self.assertEqual(
            mismatched_row["availability_precision"], "date_upper_bound"
        )

    def test_rendered_date_wins_when_date_only_cms_metadata_is_stale(self) -> None:
        stale_metadata = DETAIL.replace(
            'content="2024-12-11"', 'content="2024-12-10"'
        )

        row = self._collect(stale_metadata)["CN_EXPORTS"].iloc[0]

        self.assertEqual(row["release_date"], dt.date(2024, 12, 11))
        self.assertEqual(row["available_at"], dt.datetime(2024, 12, 12))
        self.assertEqual(row["availability_precision"], "date_upper_bound")

    def test_january_february_aggregate_creates_no_monthly_value(self) -> None:
        title = (
            "(1) China's Total Export & Import Values, "
            "January-February 2024 (in USD)"
        )
        source = DETAIL.replace(TITLE, title).replace(
            "<th>11</th><th>1-to-11</th>",
            "<th>January-February</th><th>1-to-2</th>",
        )

        frame = self._collect(source, title=title)["CN_EXPORTS"]

        self.assertTrue(frame.empty)

    def test_initial_release_value_does_not_need_to_match_current_snapshot(self) -> None:
        frame = self._collect()["CN_EXPORTS"]

        # The collector has no DataPoint/current-value dependency.  Its 6.7
        # first release remains valid even if a later snapshot is, e.g., 6.6.
        self.assertEqual(frame["value"].tolist(), [6.7])

    def test_non_official_domain_and_soft_error_fail_closed(self) -> None:
        with self.assertRaisesRegex(TradeEvidenceCollectionError, "non-official"):
            self._collect(
                url="https://english.customs.gov.cn.evil.example/release.html"
            )
        with self.assertRaisesRegex(TradeEvidenceCollectionError, "soft-error"):
            self._collect("504 Gateway Time-out" * 30)

    def test_tls_verification_failure_is_explicit_and_never_downgraded(self) -> None:
        with patch(
            "app.services.china_trade_evidence._get_gacc_archive_text",
            side_effect=ssl.SSLCertVerificationError(
                "certificate verify failed: unable to get local issuer certificate"
            ),
        ):
            with self.assertRaisesRegex(
                TradeEvidenceCollectionError,
                "TLS certificate verification failed.*TLS verification enabled",
            ):
                collect_gacc_trade_evidence(page_count=1)

    def test_same_release_instant_conflict_is_rejected(self) -> None:
        second_url = "https://english.customs.gov.cn/Statics/november-copy.html"
        conflicting = DETAIL.replace("<td>6.7</td>", "<td>6.8</td>")

        def detail(url: str, *, cache: bool = True) -> str:
            return conflicting if url == second_url else DETAIL

        with (
            patch(
                "app.services.china_trade_evidence._index_catalog",
                return_value=((DETAIL_URL, TITLE), (second_url, TITLE)),
            ),
            patch(
                "app.services.china_trade_evidence._get_gacc_archive_text",
                side_effect=detail,
            ),
        ):
            with self.assertRaisesRegex(TradeEvidenceCollectionError, "conflicting"):
                collect_gacc_trade_evidence(page_count=1)

    def test_continuity_gate_exempts_january_february_but_requires_other_months(self) -> None:
        source_row = self._collect()["CN_EXPORTS"].iloc[0].to_dict()
        rows = []
        for month in range(3, 13):
            row = dict(source_row)
            row["date"] = dt.date(2024, month, 1)
            row["release_date"] = dt.date(2024, month, 20)
            row["available_at"] = dt.datetime(2024, month, 21)
            rows.append(row)
        evidence = {"CN_EXPORTS": pd.DataFrame(rows)}

        complete = validate_trade_evidence(
            evidence, expected_through="2024-12", required_from="2024-03"
        )
        missing = validate_trade_evidence(
            {"CN_EXPORTS": pd.DataFrame(rows[:-1])},
            expected_through="2024-12",
            required_from="2024-03",
        )

        self.assertTrue(complete["ready"])
        self.assertNotIn("2024-01", complete["missing"])
        self.assertNotIn("2024-02", complete["missing"])
        self.assertFalse(missing["ready"])
        self.assertEqual(missing["missing"], ["2024-12"])

    def test_gate_rejects_incomplete_provenance_and_impossible_release_date(self) -> None:
        valid = self._collect()["CN_EXPORTS"].iloc[0].to_dict()
        impossible = dict(valid)
        impossible["release_date"] = dt.date(2024, 1, 1)
        impossible["available_at"] = dt.datetime(2024, 1, 2)
        incomplete = dict(valid)
        incomplete["provenance_json"] = (
            '{"collector":"gacc_trade_release_v1","direct_official":true}'
        )

        impossible_gate = validate_trade_evidence(
            {"CN_EXPORTS": pd.DataFrame([impossible])},
            expected_through="2024-11",
            required_from="2024-11",
        )
        provenance_gate = validate_trade_evidence(
            {"CN_EXPORTS": pd.DataFrame([incomplete])},
            expected_through="2024-11",
            required_from="2024-11",
        )

        self.assertFalse(impossible_gate["ready"])
        self.assertIn("release date precedes", impossible_gate["invalid"][0])
        self.assertFalse(provenance_gate["ready"])
        self.assertIn("missing GACC provenance", provenance_gate["invalid"][0])

    def test_expected_freshness_skips_structural_first_two_months(self) -> None:
        self.assertEqual(
            str(expected_trade_observation_through(dt.date(2025, 3, 20))),
            "2024-12",
        )
        self.assertEqual(
            str(expected_trade_observation_through(dt.date(2025, 4, 20))),
            "2025-03",
        )
        december = self._collect()["CN_EXPORTS"].iloc[0].to_dict()
        december.update(
            date=dt.date(2024, 12, 1),
            release_date=dt.date(2025, 1, 10),
            available_at=dt.datetime(2025, 1, 11),
        )
        gate = validate_trade_evidence(
            {"CN_EXPORTS": pd.DataFrame([december])},
            expected_through="2025-02",
            required_from="2024-12",
        )
        self.assertTrue(gate["ready"])
        self.assertEqual(gate["expected_through"], "2024-12")
        self.assertEqual(gate["requested_expected_through"], "2025-02")

    def test_production_gate_has_fixed_history_and_live_freshness_boundaries(self) -> None:
        evidence = self._collect()

        with patch(
            "app.services.china_trade_evidence.expected_trade_observation_through",
            return_value=pd.Period("2024-12", freq="M"),
        ):
            gate = validate_production_trade_evidence(evidence)

        self.assertEqual(gate["required_from"], "2020-03")
        self.assertEqual(gate["expected_through"], "2024-12")
        self.assertFalse(gate["ready"])
        self.assertIn("2020-03", gate["missing"])
        self.assertIn("2024-12", gate["missing"])

    def test_date_upper_bound_enters_a3_only_after_next_day_midnight(self) -> None:
        row = self._collect()["CN_EXPORTS"].iloc[0].to_dict()
        row.update(
            id=1,
            indicator_code="CN_EXPORTS",
            date=dt.date(2025, 2, 1),
            release_date=dt.date(2025, 3, 20),
            available_at=dt.datetime(2025, 3, 21),
            retrieved_at=dt.datetime(2026, 1, 1),
            version=1,
            vintage_provenance="release_evidence",
        )

        before, _ = _select_strict_vintages(
            [row],
            as_of=dt.datetime(2025, 3, 20, 18),
            observation_end=dt.date(2025, 2, 28),
        )
        after, _ = _select_strict_vintages(
            [row],
            as_of=dt.datetime(2025, 3, 21),
            observation_end=dt.date(2025, 2, 28),
        )

        self.assertEqual(before, [])
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["availability_precision"], "date_upper_bound")


if __name__ == "__main__":
    unittest.main()
