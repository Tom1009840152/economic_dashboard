import datetime as dt
import json
import unittest
from unittest.mock import patch

import pandas as pd

from app.services import china_credit_evidence as credit
from app.services.china_money_definition import CN_M1_2024_COMPARABLE_SOURCE_URL


def _pboc_row(
    code: str,
    period: str,
    value: float,
    *,
    hour: int = 17,
    scope: str = "month",
) -> dict:
    observed = pd.Period(period, freq="M")
    released = (observed + 1).to_timestamp().to_pydatetime().replace(
        day=14, hour=hour
    )
    return credit._direct_row(
        code=code,
        observed=dt.date(observed.year, observed.month, 1),
        value=value,
        available_at=released,
        source_url=f"https://www.pbc.gov.cn/{code}/{period}.html",
        title=f"{period} official release",
        source_sha256="a" * 64,
        reported_scope=scope,
        preliminary=True,
        definition_version="test_v1",
    )


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=credit._EVIDENCE_COLUMNS)


class ChinaCreditEvidenceTests(unittest.TestCase):
    def test_money_and_stock_parsers_preserve_negative_growth(self) -> None:
        text = (
            "广义货币(M2)余额318.52万亿元，同比增长7%。"
            "狭义货币（M1）余额112.45万亿元，同比下降0.4%。"
            "社会融资规模存量为451.4万亿元，同比增长8.2%。"
            "对实体经济发放的人民币贷款余额为274.15万亿元，同比下降1.1%。"
        )

        self.assertEqual(
            credit._money_yoy_values(text),
            {"CN_M2_YOY": 7.0, "CN_M1_YOY": -0.4},
        )
        self.assertEqual(
            credit._stock_yoy_values(text),
            {
                "CN_TSF_STOCK_YOY": 8.2,
                "CN_TSF_RMB_LOAN_STOCK_YOY": -1.1,
            },
        )

    def test_ytd_residual_is_labelled_and_keeps_both_release_leaves(self) -> None:
        ytd = _frame(
            [
                _pboc_row("CN_TSF_YTD", "2025-01", 65000.0, scope="year_to_date"),
                _pboc_row("CN_TSF_YTD", "2025-02", 92900.0, scope="year_to_date"),
            ]
        )

        result = credit._build_monthly_flow(
            code="CN_TSF", direct=credit._empty_frame(), ytd=ytd
        )
        february = result.set_index("date").loc[dt.date(2025, 2, 1)]
        leaves = json.loads(february["provenance_json"])

        self.assertEqual(february["value"], 27900.0)
        self.assertEqual(february["status"], "derived")
        self.assertEqual(
            february["formula_version"], credit.PBOC_YTD_DIFF_FORMULA_VERSION
        )
        self.assertEqual(len(leaves), 2)
        self.assertEqual(
            {leaf["reported_scope"] for leaf in leaves}, {"year_to_date"}
        )

    def test_direct_month_is_preferred_to_rounded_ytd_residual(self) -> None:
        ytd = _frame(
            [
                _pboc_row("CN_TSF_YTD", "2025-01", 65000.0, scope="year_to_date"),
                _pboc_row("CN_TSF_YTD", "2025-02", 92900.0, scope="year_to_date"),
            ]
        )
        direct = _frame([_pboc_row("CN_TSF", "2025-02", 27873.0)])

        result = credit._build_monthly_flow(
            code="CN_TSF", direct=direct, ytd=ytd
        )
        february = result.set_index("date").loc[dt.date(2025, 2, 1)]

        self.assertEqual(february["value"], 27873.0)
        self.assertEqual(february["status"], "published")
        self.assertIsNone(february["formula_version"])

    def test_missing_prior_ytd_never_differences_across_a_gap(self) -> None:
        ytd = _frame(
            [
                _pboc_row("CN_TSF_YTD", "2025-01", 65000.0, scope="year_to_date"),
                _pboc_row("CN_TSF_YTD", "2025-03", 120000.0, scope="year_to_date"),
            ]
        )

        result = credit._build_monthly_flow(
            code="CN_TSF", direct=credit._empty_frame(), ytd=ytd
        )

        self.assertEqual(list(result["date"]), [dt.date(2025, 1, 1)])

    def test_credit_impulse_availability_and_provenance_use_all_leaves(self) -> None:
        months = pd.period_range("2020-01", "2024-12", freq="M")
        tsf = _frame(
            [
                _pboc_row("CN_TSF", str(period), 1000.0 + period.month)
                for period in months
            ]
        )
        quarters = pd.period_range("2019Q4", "2024Q4", freq="Q-DEC").asfreq(
            "M", how="end"
        )
        gdp_rows = []
        for period in quarters:
            released = (period + 1).to_timestamp().to_pydatetime().replace(
                day=18, hour=9, minute=30
            )
            gdp_rows.append(
                {
                    "date": dt.date(period.year, period.month, 1),
                    "value": float(period.quarter * 25000 + (period.year - 2019) * 1000),
                    "release_date": released.date(),
                    "available_at": released,
                    "source_url": f"https://www.stats.gov.cn/{period}.html",
                    "status": "published",
                }
            )

        gdp = credit._clean_nominal_gdp(pd.DataFrame(gdp_rows))
        intensity, impulse = credit._build_credit_metrics(tsf, gdp)

        self.assertFalse(intensity.empty)
        self.assertFalse(impulse.empty)
        latest = impulse.iloc[-1]
        leaves = json.loads(latest["provenance_json"])
        self.assertEqual(latest["formula_version"], "1.0.0")
        self.assertEqual(
            latest["available_at"],
            max(dt.datetime.fromisoformat(leaf["available_at"]) for leaf in leaves),
        )
        self.assertGreaterEqual(len(leaves), 26)
        self.assertIn("CN_GDP_NOMINAL_YTD", {leaf["indicator_code"] for leaf in leaves})

    def test_m1m2_uses_later_of_the_two_source_releases(self) -> None:
        m1 = _frame([_pboc_row("CN_M1_YOY", "2025-01", 0.4, hour=17)])
        m2 = _frame([_pboc_row("CN_M2_YOY", "2025-01", 7.0, hour=16)])

        result = credit._build_m1m2(m1, m2)
        row = result.iloc[0]

        self.assertAlmostEqual(row["value"], -6.6)
        self.assertEqual(row["available_at"].hour, 17)
        self.assertEqual(row["formula_version"], "1.1.0")
        self.assertEqual(len(json.loads(row["provenance_json"])), 2)

    def test_collector_uses_original_january_release_for_m1_backcast(self) -> None:
        source = """
        <html><body><span id="shijian">2025-02-14 17:00:00</span>
        <p>2025年1月金融统计数据报告</p>
        <p>1月末，广义货币(M2)余额318.52万亿元，同比增长7%。
        狭义货币(M1)余额112.45万亿元，同比增长0.4%。</p>
        <p>按可比口径回溯后，2024年各月末M1可比余额和增速分别为：</p>
        </body></html>
        """ + (" " * 600)
        with (
            patch.object(credit, "_pboc_release_catalog", return_value=()),
            patch.object(credit, "_get_pboc_archive_text", return_value=source),
        ):
            result = credit.collect_pboc_credit_release_inputs(page_count=1)

        m1 = result["CN_M1_YOY"]
        january_2024 = m1.set_index("date").loc[dt.date(2024, 1, 1)]
        self.assertEqual(len(m1), 13)
        self.assertEqual(january_2024["value"], 3.3)
        self.assertEqual(
            january_2024["available_at"], dt.datetime(2025, 2, 14, 17)
        )
        leaf = json.loads(january_2024["provenance_json"])[0]
        self.assertEqual(leaf["source_url"], CN_M1_2024_COMPARABLE_SOURCE_URL)
        self.assertEqual(len(leaf["source_sha256"]), 64)

    def test_same_minute_conflicting_release_values_are_rejected(self) -> None:
        first = _pboc_row("CN_TSF", "2025-01", 65000.0)
        second = _pboc_row("CN_TSF", "2025-01", 65001.0)
        second["source_url"] = "https://www.pbc.gov.cn/other.html"

        with self.assertRaisesRegex(ValueError, "conflicting first-release"):
            credit._first_release_frame([first, second], code="CN_TSF")


if __name__ == "__main__":
    unittest.main()
