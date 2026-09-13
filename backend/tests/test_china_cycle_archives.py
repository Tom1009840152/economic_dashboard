import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd

from app.fetchers import china_cycle_data as cycle
from app.fetchers import nbs_cycle
from app.fetchers.akshare_source import FETCHERS


class ChinaCycleArchiveTests(unittest.TestCase):
    def tearDown(self) -> None:
        cycle._pboc_release_catalog.cache_clear()

    def test_pboc_catalog_uses_real_archive_pagination(self) -> None:
        current = """
        <a href="/current/index.html">2026年7月金融统计数据报告</a>
        <a href="/regional/index.html">2026年地区社会融资规模增量统计表</a>
        """
        archive = """
        <a href="old/index.html">2025年7月社会融资规模存量统计数据报告</a>
        """
        cycle._pboc_release_catalog.cache_clear()
        with patch.object(
            cycle,
            "_request_text",
            side_effect=lambda url: archive if "11871-1.html" in url else current,
        ) as request:
            releases = cycle._pboc_release_catalog(page_count=2)

        requested = [call.args[0] for call in request.call_args_list]
        self.assertTrue(any(url.endswith("11871-1.html") for url in requested))
        self.assertEqual(len(releases), 2)
        self.assertFalse(any("地区社会融资规模" in title for _, title in releases))

    def test_cn_ip_mirror_adds_long_history_without_inventing_january(self) -> None:
        raw = pd.DataFrame(
            {
                "月份": ["2008年02月份", "2008年03月份", "2009年02月份"],
                "同比增长": [15.4, 17.8, None],
                "累计增长": [15.4, 16.4, 11.0],
                # These values identify the observation month, not a release
                # timestamp, and therefore must never become available_at.
                "发布时间": [
                    dt.date(2008, 2, 1),
                    dt.date(2008, 3, 1),
                    dt.date(2009, 2, 1),
                ],
            }
        )
        with patch.object(cycle.ak, "macro_china_gyzjz", return_value=raw):
            result = cycle._load_cn_ip_mirror_backfill()

        self.assertEqual(
            result["date"].tolist(),
            [dt.date(2008, 2, 1), dt.date(2008, 3, 1), dt.date(2009, 2, 1)],
        )
        self.assertFalse(any(date.month == 1 for date in result["date"]))
        self.assertTrue(result["available_at"].isna().all())
        self.assertTrue(result["release_date"].isna().all())
        self.assertEqual(set(result["status"]), {"mirror_backfill"})
        self.assertEqual(set(result["source_url"]), {cycle._EASTMONEY_INDUSTRIAL_URL})

    def test_cn_ip_merge_keeps_history_and_prefers_official_overlap(self) -> None:
        mirror = cycle._frame(
            [
                {
                    "date": dt.date(2008, 2, 1),
                    "value": 15.4,
                    "release_date": None,
                    "available_at": None,
                    "source_url": cycle._EASTMONEY_INDUSTRIAL_URL,
                    "status": "mirror_backfill",
                },
                {
                    "date": dt.date(2026, 6, 1),
                    "value": 5.0,
                    "release_date": None,
                    "available_at": None,
                    "source_url": cycle._EASTMONEY_INDUSTRIAL_URL,
                    "status": "mirror_backfill",
                },
            ]
        )
        official = pd.DataFrame(
            {
                "date": [dt.date(2026, 6, 1), dt.date(2026, 7, 1)],
                "value": [5.3, 4.5],
            }
        )

        result = cycle._merge_cn_ip_history(official, mirror)
        by_date = result.set_index("date")

        self.assertEqual(by_date.loc[dt.date(2008, 2, 1), "value"], 15.4)
        self.assertEqual(by_date.loc[dt.date(2026, 6, 1), "value"], 5.3)
        self.assertEqual(by_date.loc[dt.date(2026, 6, 1), "status"], "published")
        self.assertEqual(
            by_date.loc[dt.date(2026, 6, 1), "source_url"], cycle._NBS_RELEASE_BASE
        )

    def test_final_cn_ip_registry_path_keeps_the_long_history_merge(self) -> None:
        official = pd.DataFrame(
            {"date": [dt.date(2026, 7, 1)], "value": [4.5]}
        )
        combined = cycle._frame(
            [
                {
                    "date": dt.date(2008, 2, 1),
                    "value": 15.4,
                    "release_date": None,
                    "available_at": None,
                    "source_url": cycle._EASTMONEY_INDUSTRIAL_URL,
                    "status": "mirror_backfill",
                },
                {
                    "date": dt.date(2026, 7, 1),
                    "value": 4.5,
                    "release_date": None,
                    "available_at": None,
                    "source_url": None,
                    "status": "published",
                },
            ]
        )
        with (
            patch.object(nbs_cycle, "_cached", return_value=official),
            patch.object(cycle, "_merge_cn_ip_history", return_value=combined) as merge,
        ):
            result = FETCHERS["CN_IP"]()

        merge.assert_called_once()
        self.assertEqual(result["date"].min(), dt.date(2008, 2, 1))

    def test_credit_cumulative_values_are_normalized_to_100m(self) -> None:
        text = (
            "2026年前七个月社会融资规模增量累计为22.25万亿元，其中，"
            "对实体经济发放的人民币贷款增加10.17万亿元，"
            "企业债券净融资2.52万亿元，政府债券净融资7.76万亿元。"
        )
        self.assertEqual(
            cycle._credit_ytd_values(text),
            {
                "CN_TSF": 222500.0,
                "CN_TSF_RMB_LOANS_FLOW": 101700.0,
                "CN_CORP_BOND_FINANCING": 25200.0,
                "CN_GOV_BOND_FINANCING": 77600.0,
            },
        )

    def test_monthly_difference_uses_current_release_and_never_crosses_gap(self) -> None:
        rows = [
            {
                "date": dt.date(2026, 1, 1),
                "value": 1000.0,
                "release_date": dt.date(2026, 2, 10),
                "available_at": dt.datetime(2026, 2, 10, 16),
                "source_url": "https://example.test/january",
                "status": "published",
            },
            {
                "date": dt.date(2026, 2, 1),
                "value": 1250.0,
                "release_date": dt.date(2026, 3, 10),
                "available_at": dt.datetime(2026, 3, 10, 16),
                "source_url": "https://example.test/february",
                "status": "published",
            },
            {
                "date": dt.date(2026, 4, 1),
                "value": 1800.0,
                "release_date": dt.date(2026, 5, 10),
                "available_at": dt.datetime(2026, 5, 10, 16),
                "source_url": "https://example.test/april",
                "status": "published",
            },
        ]

        monthly = cycle._derive_monthly_from_ytd(rows)

        self.assertEqual([row["date"] for row in monthly], [dt.date(2026, 1, 1), dt.date(2026, 2, 1)])
        self.assertEqual(monthly[1]["value"], 250.0)
        self.assertEqual(monthly[1]["available_at"], dt.datetime(2026, 3, 10, 16))
        self.assertEqual(monthly[1]["source_url"], "https://example.test/february")

    def test_wide_stock_pdf_text_keeps_backfill_availability_unknown(self) -> None:
        text = """
        2024.1 2024.2 2024.3
        100.0 8.1 101.0 8.2 102.0 8.3
        AFRE(stock)
        60.0 7.1 61.0 7.2 62.0 7.3
        RMB loans
        """
        rows = cycle._stock_rows_from_pdf_text(text, "https://example.test/stock.pdf")

        self.assertEqual(
            [row["value"] for row in rows["CN_TSF_STOCK_YOY"]],
            [8.1, 8.2, 8.3],
        )
        self.assertEqual(
            [row["value"] for row in rows["CN_TSF_RMB_LOAN_STOCK_YOY"]],
            [7.1, 7.2, 7.3],
        )
        self.assertTrue(
            all(
                row["available_at"] is None and row["release_date"] is None
                for series in rows.values()
                for row in series
            )
        )

    def test_legacy_stock_table_maps_rows_to_sequential_months(self) -> None:
        table = [[None, "人民币贷款"]]
        table.extend([[str(10 + index / 10), str(9 + index / 10)] for index in range(24)])
        rows = cycle._stock_rows_from_legacy_tables(
            [table], "https://example.test/legacy.pdf", dt.date(2017, 1, 1)
        )

        self.assertEqual(rows["CN_TSF_STOCK_YOY"][0]["date"], dt.date(2017, 1, 1))
        self.assertEqual(rows["CN_TSF_STOCK_YOY"][-1]["date"], dt.date(2018, 12, 1))
        self.assertIsNone(rows["CN_TSF_STOCK_YOY"][-1]["available_at"])

    def test_special_bond_parser_prefers_current_month_over_ytd(self) -> None:
        newer = (
            "2025年5月，全国发行新增地方政府债券5000亿元，其中一般债券"
            "1000亿元、专项债券4000亿元。（二）1-5月发行情况。"
            "2025年1-5月，发行专项债券12000亿元。"
        )
        older = (
            "2019年12月，全国发行地方政府债券379.87亿元。其中，发行一般"
            "债券39.87亿元，发行专项债券340亿元；按用途划分。"
        )

        self.assertEqual(
            cycle._special_bond_monthly_value(newer, dt.date(2025, 5, 1)), 4000.0
        )
        self.assertEqual(
            cycle._special_bond_monthly_value(older, dt.date(2019, 12, 1)), 340.0
        )

    def test_credit_loader_delegates_to_canonical_formula(self) -> None:
        tsf = pd.DataFrame({"date": [dt.date(2025, 12, 1)], "value": [100.0]})
        gdp = pd.DataFrame({"date": [dt.date(2025, 12, 1)], "value": [1000.0]})
        calculated = (
            pd.Series({pd.Period("2025-12", freq="M"): 10.0}),
            pd.Series({pd.Period("2025-12", freq="M"): 1.0}),
        )
        with (
            patch.object(cycle, "_cached", return_value={"CN_TSF": tsf}),
            patch.object(cycle, "_load_nominal_gdp", return_value=gdp),
            patch.object(
                cycle, "calculate_credit_metrics", return_value=calculated
            ) as canonical,
        ):
            result = cycle._load_credit_impulse()

        canonical.assert_called_once()
        self.assertEqual(
            result["CN_CREDIT_IMPULSE"]["formula_version"].iloc[0], "1.0.0"
        )

    def test_fiscal_loader_delegates_to_canonical_formula(self) -> None:
        broad = pd.DataFrame({"date": [dt.date(2025, 12, 1)], "value": [200.0]})
        gdp = pd.DataFrame({"date": [dt.date(2025, 12, 1)], "value": [1000.0]})
        calculated = (
            pd.Series({pd.Period("2025-12", freq="M"): 20.0}),
            pd.Series({pd.Period("2025-12", freq="M"): 2.0}),
        )
        with (
            patch.object(cycle, "_load_nominal_gdp", return_value=gdp),
            patch.object(
                cycle, "calculate_fiscal_metrics", return_value=calculated
            ) as canonical,
        ):
            result = cycle._build_fiscal_impulse(
                {"CN_FISCAL_BROAD_EXPENDITURE_YTD": broad}
            )

        canonical.assert_called_once()
        self.assertEqual(
            result["CN_FISCAL_IMPULSE_PROXY"]["formula_version"].iloc[0],
            "1.0.0",
        )


if __name__ == "__main__":
    unittest.main()
