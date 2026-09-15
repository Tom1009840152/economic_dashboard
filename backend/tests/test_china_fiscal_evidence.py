import datetime as dt
import json
import unittest

import pandas as pd

from app.services.china_fiscal_evidence import build_fiscal_release_evidence


def _release_frame(
    periods: pd.PeriodIndex,
    values: list[float],
    *,
    host: str,
    day: int,
) -> pd.DataFrame:
    rows = []
    for period, value in zip(periods, values, strict=True):
        released = dt.datetime(
            (period + 1).year,
            (period + 1).month,
            day,
            9,
            30,
        )
        rows.append(
            {
                "date": dt.date(period.year, period.month, 1),
                "value": value,
                "release_date": released.date(),
                "available_at": released,
                "source_url": f"https://www.{host}/{period}.html",
                "status": "published",
            }
        )
    return pd.DataFrame(rows)


class ChinaFiscalReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.periods = pd.period_range("2022-12", "2026-06", freq="Q-DEC").asfreq(
            "M", how="end"
        )
        quarters = [(period.quarter) for period in self.periods]
        self.general = _release_frame(
            self.periods,
            [float(quarter * 100) for quarter in quarters],
            host="mof.gov.cn",
            day=20,
        )
        self.fund = _release_frame(
            self.periods,
            [float(quarter * 50) for quarter in quarters],
            host="mof.gov.cn",
            day=20,
        )
        self.gdp = _release_frame(
            self.periods,
            [float(quarter * 1000) for quarter in quarters],
            host="stats.gov.cn",
            day=18,
        )

    def test_builds_ordered_impulse_with_six_leaf_provenance(self) -> None:
        result = build_fiscal_release_evidence(
            {
                "CN_FISCAL_GENERAL_SPEND_YTD": self.general,
                "CN_FISCAL_FUND_EXPENDITURE_YTD": self.fund,
            },
            self.gdp,
        )

        self.assertEqual(len(result["CN_FISCAL_BROAD_EXPENDITURE_YTD"]), 15)
        self.assertEqual(len(result["CN_FISCAL_SPEND_INTENSITY"]), 15)
        self.assertEqual(len(result["CN_FISCAL_IMPULSE_PROXY"]), 11)
        first = result["CN_FISCAL_IMPULSE_PROXY"].iloc[0]
        self.assertEqual(first["date"], dt.date(2023, 12, 1))
        self.assertEqual(first["available_at"], dt.datetime(2024, 1, 20, 9, 30))
        self.assertEqual(first["formula_version"], "1.0.0")
        leaves = json.loads(first["provenance_json"])
        self.assertEqual(len(leaves), 6)
        self.assertEqual(
            {leaf["indicator_code"] for leaf in leaves},
            {
                "CN_GDP_NOMINAL_YTD",
                "CN_FISCAL_GENERAL_SPEND_YTD",
                "CN_FISCAL_FUND_EXPENDITURE_YTD",
            },
        )

    def test_latest_of_all_six_leaf_times_controls_impulse_availability(self) -> None:
        prior_index = self.gdp.index[
            self.gdp["date"].eq(dt.date(2022, 12, 1))
        ][0]
        self.gdp.loc[prior_index, "release_date"] = dt.date(2024, 2, 1)
        self.gdp.loc[prior_index, "available_at"] = dt.datetime(2024, 2, 1, 11)

        result = build_fiscal_release_evidence(
            {
                "CN_FISCAL_GENERAL_SPEND_YTD": self.general,
                "CN_FISCAL_FUND_EXPENDITURE_YTD": self.fund,
            },
            self.gdp,
        )

        first = result["CN_FISCAL_IMPULSE_PROXY"].iloc[0]
        self.assertEqual(first["available_at"], dt.datetime(2024, 2, 1, 11))
        self.assertEqual(first["source_url"], "https://www.stats.gov.cn/2022-12.html")

    def test_missing_quarter_does_not_bridge_the_annual_lag(self) -> None:
        general = self.general[
            ~self.general["date"].eq(dt.date(2023, 6, 1))
        ].copy()

        result = build_fiscal_release_evidence(
            {
                "CN_FISCAL_GENERAL_SPEND_YTD": general,
                "CN_FISCAL_FUND_EXPENDITURE_YTD": self.fund,
            },
            self.gdp,
        )

        impulse_dates = set(result["CN_FISCAL_IMPULSE_PROXY"]["date"])
        self.assertNotIn(dt.date(2024, 6, 1), impulse_dates)

    def test_rejects_conflicting_direct_first_release_values(self) -> None:
        duplicate = self.gdp.iloc[[0]].copy()
        duplicate["value"] = duplicate["value"] + 1
        conflicting = pd.concat([self.gdp, duplicate], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "conflicting first-release"):
            build_fiscal_release_evidence(
                {
                    "CN_FISCAL_GENERAL_SPEND_YTD": self.general,
                    "CN_FISCAL_FUND_EXPENDITURE_YTD": self.fund,
                },
                conflicting,
            )

    def test_filters_unknown_times_and_non_official_hosts(self) -> None:
        gdp = self.gdp.copy()
        gdp.loc[gdp.index[0], "available_at"] = None
        gdp.loc[gdp.index[1], "source_url"] = "https://stats.gov.cn.evil.test/x"

        result = build_fiscal_release_evidence(
            {
                "CN_FISCAL_GENERAL_SPEND_YTD": self.general,
                "CN_FISCAL_FUND_EXPENDITURE_YTD": self.fund,
            },
            gdp,
        )

        dates = set(result["CN_GDP_NOMINAL_YTD"]["date"])
        self.assertNotIn(dt.date(2022, 12, 1), dates)
        self.assertNotIn(dt.date(2023, 3, 1), dates)

    def test_rejects_timezone_aware_source_time_instead_of_stripping_offset(self) -> None:
        gdp = self.gdp.copy()
        gdp["available_at"] = gdp["available_at"].astype(object)
        gdp.loc[gdp.index[0], "available_at"] = dt.datetime(
            2023,
            1,
            18,
            1,
            30,
            tzinfo=dt.timezone.utc,
        )

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            build_fiscal_release_evidence(
                {
                    "CN_FISCAL_GENERAL_SPEND_YTD": self.general,
                    "CN_FISCAL_FUND_EXPENDITURE_YTD": self.fund,
                },
                gdp,
            )


if __name__ == "__main__":
    unittest.main()
