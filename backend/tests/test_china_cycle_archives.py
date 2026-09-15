import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd

from app.fetchers import china_cycle_data as cycle
from app.fetchers import nbs_cycle
from app.fetchers.akshare_source import FETCHERS


class ChinaCycleArchiveTests(unittest.TestCase):
    def tearDown(self) -> None:
        cycle._cache.clear()

    def test_nbs_archive_index_is_never_persistently_cached(self) -> None:
        with patch.object(
            cycle,
            "_get_nbs_archive_text",
            return_value="<html></html>",
        ) as request:
            self.assertEqual(cycle._nbs_release_catalog(1, archive=True), ())

        request.assert_called_once_with(cycle._NBS_RELEASE_BASE, cache=False)

    def test_mutable_nbs_index_failure_is_not_retried_in_a_burst(self) -> None:
        with patch.object(cycle, "_get_nbs_text", side_effect=RuntimeError("challenge")) as request:
            with self.assertRaises(RuntimeError):
                cycle._get_nbs_archive_text(
                    "https://www.stats.gov.cn/sj/zxfb/index_1000_40.html",
                    cache=False,
                )

        request.assert_called_once()

    def test_nbs_static_https_fallback_rejects_browser_challenge_body(self) -> None:
        challenged = type(
            "Response",
            (),
            {
                "status_code": 200,
                "content": b"Please enable JavaScript and refresh the page",
                "raise_for_status": lambda self: None,
            },
        )()
        with (
            patch.object(cycle._nbs_session, "get", return_value=challenged),
            patch.object(cycle, "_request_text", return_value="<html>official</html>"),
        ):
            self.assertEqual(
                cycle._get_nbs_text("https://www.stats.gov.cn/old.html"),
                "<html>official</html>",
            )

    def test_nbs_archive_can_resume_from_a_deep_page_without_rescanning(self) -> None:
        with patch.object(
            cycle,
            "_get_nbs_archive_text",
            return_value="<html></html>",
        ) as request:
            self.assertEqual(
                cycle._nbs_release_catalog(
                    2,
                    archive=True,
                    start_page=70,
                ),
                (),
            )

        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            [
                f"{cycle._NBS_RELEASE_BASE}index_70.html",
                f"{cycle._NBS_RELEASE_BASE}index_71.html",
            ],
        )
        self.assertTrue(all(call.kwargs == {"cache": False} for call in request.call_args_list))

    def test_nbs_archive_supports_official_thousand_item_shards(self) -> None:
        with patch.object(
            cycle,
            "_get_nbs_archive_text",
            return_value="<html></html>",
        ) as request:
            cycle._nbs_release_catalog(
                2,
                archive=True,
                archive_shard=1000,
            )

        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            [
                f"{cycle._NBS_RELEASE_BASE}index_1000.html",
                f"{cycle._NBS_RELEASE_BASE}index_1000_1.html",
            ],
        )

    def test_regular_nbs_catalog_is_shared_within_refresh_ttl(self) -> None:
        cycle._cache.clear()
        with patch.object(
            cycle,
            "_get_nbs_text",
            return_value="<html></html>",
        ) as request:
            first = cycle._nbs_release_catalog(1)
            second = cycle._nbs_release_catalog(1)

        self.assertEqual(first, ())
        self.assertEqual(second, ())
        request.assert_called_once_with(cycle._NBS_RELEASE_BASE)

    def test_date_only_publication_does_not_invent_midnight_availability(self) -> None:
        metadata = cycle._publication_metadata(
            '<meta name="PubDate" content="2026-07-31">',
            "https://www.stats.gov.cn/sj/zxfb/release.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2026, 7, 31))
        self.assertIsNone(metadata["available_at"])

    def test_nbs_migrated_title_clock_restores_original_availability(self) -> None:
        source = """
        <html>
          <body>
            <div class="detail-title-des">
              <h2><p>2021/09/15 10:00</p></h2>
            </div>
            <div class="detail-content"><p>正文提到 2024/06/07 12:00</p></div>
          </body>
        </html>
        """

        metadata = cycle._publication_metadata(
            source,
            "https://www.stats.gov.cn/sj/zxfb/202302/release.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2021, 9, 15))
        self.assertEqual(metadata["available_at"], dt.datetime(2021, 9, 15, 10, 0))

    def test_nbs_title_clock_fallback_is_not_used_for_other_hosts(self) -> None:
        source = """
        <div class="detail-title-des"><p>2021/09/15 10:00</p></div>
        """

        metadata = cycle._publication_metadata(
            source,
            "https://example.com/release.html",
        )

        self.assertIsNone(metadata["release_date"])
        self.assertIsNone(metadata["available_at"])

    def test_nbs_title_clock_fallback_requires_https(self) -> None:
        source = '<div class="detail-title-des"><p>2021/09/15 10:00</p></div>'

        metadata = cycle._publication_metadata(
            source,
            "http://www.stats.gov.cn/sj/zxfb/release.html",
        )

        self.assertIsNone(metadata["release_date"])
        self.assertIsNone(metadata["available_at"])

    def test_nbs_title_clock_beats_body_and_migration_times(self) -> None:
        source = """
        <meta name="createDate" content="2026/01/22 19:08:41">
        <div class="detail-title-des"><p>2021年9月15日 10:00</p></div>
        <div class="detail-content">发布时间：2024年6月7日 12:00</div>
        """

        metadata = cycle._publication_metadata(
            source,
            "https://www.stats.gov.cn/sj/zxfb/202302/release.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2021, 9, 15))
        self.assertEqual(metadata["available_at"], dt.datetime(2021, 9, 15, 10, 0))

    def test_pboc_mixed_label_formats_keep_numeric_pattern_priority(self) -> None:
        source = """
        <div>发布日期：2024/06/07 12:00</div>
        <div>发布时间：2021年9月15日 10:00</div>
        """

        metadata = cycle._publication_metadata(
            source,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2024, 6, 7))
        self.assertEqual(metadata["available_at"], dt.datetime(2024, 6, 7, 12, 0))

    def test_pboc_visible_original_time_beats_cms_migration_metadata(self) -> None:
        source = """
        <meta name="PubDate" content="2015-07-13">
        <meta name="createDate" content="2026-01-22 19:08:41">
        <div>文章来源： 2015-07-13 10:44:17</div>
        """

        metadata = cycle._publication_metadata(
            source,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2015, 7, 13))
        self.assertEqual(metadata["available_at"], dt.datetime(2015, 7, 13, 10, 44))

    def test_pboc_shijian_span_beats_migrated_metadata(self) -> None:
        source = """
        <html>
          <head>
            <meta name="PubDate" content="2025-09-22">
            <meta name="createDate" content="2025-09-22 12:55:26">
          </head>
          <body>
            <span id="shijian">2022-06-10 16:00:00</span>
          </body>
        </html>
        """

        metadata = cycle._publication_metadata(
            source,
            "https://www.pbc.gov.cn/goutongjiaoliu/release.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2022, 6, 10))
        self.assertEqual(metadata["available_at"], dt.datetime(2022, 6, 10, 16, 0))

    def test_pmi_archive_includes_nmi_headline_and_only_dates_current_release(self) -> None:
        source_url = "https://www.stats.gov.cn/sj/zxfb/202607/example.html"
        source = '<meta name="PubDate" content="2026-07-31 09:30">'
        basic = pd.DataFrame(
            [
                ["指标", "制造业PMI", "生产", "新订单", "原材料库存", "从业人员", "供应商配送"],
                ["2026年6月", 49.7, 51.2, 50.2, 48.0, 48.1, 50.1],
                ["2026年7月", 49.3, 51.4, 49.8, 47.7, 48.0, 49.9],
            ]
        )
        detail = pd.DataFrame(
            [
                ["指标", "新出口", "进口", "采购", "价格", "原料", "产成品", "在手", "预期"],
                ["2026年6月", 47.7, 47.0, 50.2, 57.1, 48.0, 48.2, 44.8, 52.0],
                ["2026年7月", 47.1, 46.8, 49.5, 55.8, 47.7, 47.4, 44.3, 52.3],
            ]
        )
        non_manufacturing = pd.DataFrame(
            [
                ["指标", "商务活动", "新订单", "投入价格", "销售价格", "从业人员", "业务活动预期"],
                ["2026年6月", 50.5, 46.6, 49.9, 48.6, 45.6, 56.0],
                ["2026年7月", 50.1, 46.6, 50.3, 48.7, 45.7, 55.8],
            ]
        )

        with (
            patch.object(
                cycle,
                "_nbs_links",
                return_value=[(source_url, "2026年7月中国采购经理指数运行情况")],
            ) as links,
            patch.object(cycle, "_get_nbs_archive_text", return_value=source),
            patch.object(
                cycle.pd,
                "read_html",
                return_value=[basic, detail, non_manufacturing],
            ),
        ):
            result = cycle._load_pmi(page_count=60)

        links.assert_called_once_with(
            r"中国采购经理指数运行情况|中国制造业采购经理指数|"
            r"中国非制造业商务活动指数",
            60,
            archive=True,
            start_page=0,
            archive_shard=0,
        )
        nmi = result["CN_NMI"].set_index("date")
        june = nmi.loc[dt.date(2026, 6, 1)]
        july = nmi.loc[dt.date(2026, 7, 1)]
        self.assertEqual(june["value"], 50.5)
        self.assertEqual(july["value"], 50.1)
        self.assertTrue(pd.isna(june["release_date"]))
        self.assertTrue(pd.isna(june["available_at"]))
        self.assertEqual(june["status"], "historical_backfill")
        self.assertEqual(july["release_date"], dt.date(2026, 7, 31))
        self.assertEqual(july["available_at"], dt.datetime(2026, 7, 31, 9, 30))
        self.assertEqual(july["source_url"], source_url)
        self.assertEqual(july["status"], "published")

    def test_pre_2018_separate_manufacturing_and_nmi_releases_are_merged(self) -> None:
        manufacturing_url = "https://www.stats.gov.cn/201712/manufacturing.html"
        nmi_url = "https://www.stats.gov.cn/201712/nmi.html"
        basic = pd.DataFrame(
            [
                ["指标", "PMI", "生产", "新订单", "原材料库存", "从业人员", "配送"],
                ["2017年12月", 51.6, 54.0, 53.4, 48.0, 48.5, 49.3],
            ]
        )
        detail = pd.DataFrame(
            [
                ["指标", "新出口", "进口", "采购", "购进价", "出厂价", "产成品", "在手", "预期"],
                ["2017年12月", 51.9, 51.2, 53.6, 62.4, 54.4, 47.4, 46.3, 58.7],
            ]
        )
        non_manufacturing = pd.DataFrame(
            [
                ["指标", "商务活动", "新订单", "投入价", "销售价", "从业人员", "预期"],
                ["2017年12月", 55.0, 52.0, 53.0, 50.0, 49.0, 60.0],
            ]
        )
        pages = {
            manufacturing_url: '<meta name="PubDate" content="2017-12-31 09:00">',
            nmi_url: '<meta name="PubDate" content="2017-12-31 09:00">',
        }

        with (
            patch.object(
                cycle,
                "_nbs_links",
                return_value=[
                    (manufacturing_url, "2017年12月中国制造业采购经理指数为51.6%"),
                    (nmi_url, "2017年12月中国非制造业商务活动指数为55.0%"),
                ],
            ),
            patch.object(
                cycle,
                "_get_nbs_archive_text",
                side_effect=lambda url: pages[url],
            ),
            patch.object(
                cycle.pd,
                "read_html",
                side_effect=[[basic, detail], [non_manufacturing]],
            ),
        ):
            result = cycle._load_pmi(page_count=10, archive_shard=1000)

        self.assertEqual(result["CN_PMI_PRODUCTION"].iloc[0]["value"], 54.0)
        self.assertEqual(result["CN_PMI_NEW_EXPORT_ORDERS"].iloc[0]["value"], 51.9)
        self.assertEqual(result["CN_NMI"].iloc[0]["value"], 55.0)
        self.assertEqual(result["CN_NMI_EMPLOYMENT"].iloc[0]["value"], 49.0)

    def test_hard_activity_evidence_parses_titles_and_publication_timestamps(self) -> None:
        ip_url = "https://www.stats.gov.cn/sj/zxfb/202601/ip.html"
        retail_url = "https://www.stats.gov.cn/sj/zxfb/202601/retail.html"
        releases = (
            (ip_url, "2025年12月份规模以上工业增加值增长5.2%"),
            (retail_url, "2025年12月份社会消费品零售总额下降1.3%"),
        )
        pages = {
            ip_url: '<meta name="PubDate" content="2026-01-19 10:00">',
            retail_url: '<meta name="createDate" content="2026-01-19 10:05">',
        }

        with (
            patch.object(cycle, "_nbs_release_catalog", return_value=releases) as catalog,
            patch.object(
                cycle, "_get_nbs_archive_text", side_effect=lambda url: pages[url]
            ),
        ):
            result = cycle._load_nbs_hard_activity_evidence(page_count=60)

        catalog.assert_called_once_with(
            60,
            archive=True,
            start_page=0,
            archive_shard=0,
        )
        ip = result["CN_IP"].iloc[0]
        retail = result["CN_RETAIL"].iloc[0]
        self.assertEqual(ip["date"], dt.date(2025, 12, 1))
        self.assertEqual(ip["value"], 5.2)
        self.assertEqual(ip["release_date"], dt.date(2026, 1, 19))
        self.assertEqual(ip["available_at"], dt.datetime(2026, 1, 19, 10, 0))
        self.assertEqual(ip["source_url"], ip_url)
        self.assertEqual(ip["status"], "published")
        self.assertEqual(retail["date"], dt.date(2025, 12, 1))
        self.assertEqual(retail["value"], -1.3)
        self.assertEqual(retail["release_date"], dt.date(2026, 1, 19))
        self.assertEqual(retail["available_at"], dt.datetime(2026, 1, 19, 10, 5))
        self.assertEqual(retail["source_url"], retail_url)
        self.assertEqual(retail["status"], "published")

    def test_cumulative_title_never_shadows_single_month_value_in_body(self) -> None:
        retail_title = "2026年1—6月份社会消费品零售总额增长3.7%"
        retail_text = (
            f"{retail_title} 6月份，社会消费品零售总额40732亿元，同比增长2.0%。"
        )
        industrial_title = "2026年1-4月份规模以上工业增加值增长5.6%"
        industrial_text = (
            f"{industrial_title} 4月份，规模以上工业增加值同比增长5.0%。"
        )

        self.assertEqual(
            cycle._growth_from_release(
                retail_title,
                retail_text,
                label="社会消费品零售总额",
            ),
            (dt.date(2026, 6, 1), 2.0),
        )
        self.assertEqual(
            cycle._growth_from_release(
                industrial_title,
                industrial_text,
                label="规模以上工业增加值",
            ),
            (dt.date(2026, 4, 1), 5.0),
        )

    def test_regular_nmi_refresh_preserves_long_history_and_release_metadata(self) -> None:
        history = pd.DataFrame(
            {
                "date": [
                    dt.date(2025, 12, 1),
                    dt.date(2026, 1, 1),
                    dt.date(2026, 2, 1),
                ],
                "value": [50.4, 50.2, 50.1],
            }
        )
        releases = {
            "CN_NMI": cycle._frame(
                [
                    {
                        "date": dt.date(2026, 2, 1),
                        "value": 50.1,
                        "status": "published",
                        "release_date": dt.date(2026, 2, 28),
                        "available_at": dt.datetime(2026, 2, 28, 9, 30),
                        "source_url": "https://www.stats.gov.cn/sj/zxfb/release.html",
                    },
                    {
                        "date": dt.date(2026, 3, 1),
                        "value": 50.3,
                        "status": "published",
                        "release_date": dt.date(2026, 3, 31),
                        "available_at": dt.datetime(2026, 3, 31, 9, 30),
                        "source_url": "https://www.stats.gov.cn/sj/zxfb/release.html",
                    },
                ]
            )
        }

        cycle._cache.clear()
        with (
            patch(
                "app.fetchers.macro_source.fetch_cn_non_manufacturing_pmi",
                return_value=history,
            ),
            patch.object(cycle, "_load_pmi", return_value=releases),
        ):
            result = cycle.CHINA_CYCLE_FETCHERS["CN_NMI"]()

        by_date = result.set_index("date")
        self.assertEqual(len(result), 4)
        self.assertEqual(by_date.loc[dt.date(2025, 12, 1), "value"], 50.4)
        self.assertEqual(
            by_date.loc[dt.date(2026, 2, 1), "status"], "published"
        )
        self.assertEqual(
            by_date.loc[dt.date(2026, 2, 1), "available_at"],
            dt.datetime(2026, 2, 28, 9, 30),
        )

    def test_property_archive_handles_historical_labels_and_uses_archive_cache(self) -> None:
        source_url = "https://www.stats.gov.cn/sj/zxfb/202209/property.html"
        source = '<meta name="PubDate" content="2022-09-16 10:00">'
        summary = pd.DataFrame(
            [
                ["指标", "绝对量", "比上年增长（%）"],
                ["房地产开发投资（亿元）", 90809, -7.4],
                ["房屋施工面积（万平方米）", 868649, -4.5],
                ["房屋新开工面积（万平方米）", 85062, -37.2],
                ["商品房销售面积（万平方米）", 87890, -23.0],
                ["商品房销售额（亿元）", 85870, -27.9],
                ["其中：住宅", 74400, -30.0],
            ]
        )

        with (
            patch.object(
                cycle,
                "_nbs_links",
                return_value=[
                    (
                        source_url,
                        "2022年1—8月份全国房地产开发投资下降7.4%",
                    )
                ],
            ) as links,
            patch.object(cycle, "_get_nbs_archive_text", return_value=source) as request,
            # Migrated NBS pages can render the same nationwide table twice
            # (desktop/mobile). The final series must still contain one row.
            patch.object(cycle.pd, "read_html", return_value=[summary, summary]),
        ):
            result = cycle._load_real_estate_activity(page_count=70)

        links.assert_called_once_with(
            r"全国房地产市场基本情况|全国房地产开发投资",
            70,
            archive=True,
            start_page=0,
            archive_shard=0,
        )
        request.assert_called_once_with(source_url)
        self.assertEqual(
            result["CN_RE_SALES_AREA_YTD_YOY"].iloc[0]["value"], -23.0
        )
        self.assertEqual(result["CN_RE_STARTS_YTD_YOY"].iloc[0]["value"], -37.2)
        self.assertEqual(result["CN_RE_INVEST_YTD_YOY"].iloc[0]["value"], -7.4)
        self.assertEqual(
            result["CN_RE_INVEST_YTD_YOY"].iloc[0]["available_at"],
            dt.datetime(2022, 9, 16, 10, 0),
        )
        self.assertEqual(len(result["CN_RE_INVEST_YTD_YOY"]), 1)

    def test_property_period_titles_keep_combined_and_annual_observations(self) -> None:
        self.assertEqual(
            cycle._period_date("2019年1-2月份全国房地产开发投资增长11.6%"),
            dt.date(2019, 2, 1),
        )
        self.assertEqual(
            cycle._period_date("2016年全国房地产开发投资和销售情况"),
            dt.date(2016, 12, 1),
        )

    def test_property_parser_accepts_new_labels_without_confusing_subrows(self) -> None:
        table = pd.DataFrame(
            [
                ["新建商品房销售面积（万平方米）", 1000, 2.5],
                ["其中：住宅", 900, 9.9],
                ["房地产新开工施工面积（万平方米）", 700, -3.0],
                ["房地产施工面积（万平方米）", 8000, -1.0],
            ]
        )
        rows = cycle._property_rows_from_tables(
            [table],
            dt.date(2026, 7, 1),
            {"source_url": "https://www.stats.gov.cn/release", "status": "published"},
        )

        self.assertEqual(rows["CN_RE_SALES_AREA_YTD_YOY"][0]["value"], 2.5)
        self.assertEqual(len(rows["CN_RE_SALES_AREA_YTD_YOY"]), 1)
        self.assertEqual(rows["CN_RE_STARTS_YTD_YOY"][0]["value"], -3.0)
        self.assertEqual(rows["CN_RE_CONSTRUCTION_YOY"][0]["value"], -1.0)

    def test_pboc_catalog_uses_real_archive_pagination(self) -> None:
        current = """
        <a href="/current/index.html">2026年7月金融统计数据报告</a>
        <a href="/regional/index.html">2026年地区社会融资规模增量统计表</a>
        """
        archive = """
        <a href="old/index.html">2025年7月社会融资规模存量统计数据报告</a>
        """
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

    def test_pboc_archive_catalog_bypasses_normal_ttl_cache(self) -> None:
        source = '<a href="old/index.html">2022年5月社会融资规模增量统计数据报告</a>'
        cycle._cache["pboc_release_catalog:1"] = (
            cycle.time.time(),
            (("https://cached.test", "cached"),),
        )
        with patch.object(cycle, "_request_text", return_value=source) as request:
            releases = cycle._pboc_release_catalog(1, archive=True)

        request.assert_called_once_with(f"{cycle._PBOC_RELEASE_BASE}index.html")
        self.assertNotEqual(releases[0][0], "https://cached.test")

    def test_pboc_news_catalog_discovers_total_pages_and_monthly_flow_titles(self) -> None:
        first = """
        <input type="hidden" totalpage="410">
        <a href="/current.html">2026年8月金融统计数据报告</a>
        """
        second = """
        <a href="/flow.html">2015年一季度社会融资规模增量统计数据报告</a>
        <a href="/stock.html">2015年3月社会融资规模存量统计数据报告</a>
        <a href="/regional.html">2015年3月地区社会融资规模增量统计表</a>
        """
        with patch.object(
            cycle,
            "_request_text",
            side_effect=[first, second],
        ) as request:
            releases = cycle._pboc_news_release_catalog(1, start_page=2)

        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            [
                f"{cycle._PBOC_NEWS_BASE}index.html",
                f"{cycle._PBOC_NEWS_BASE}11040-2.html",
            ],
        )
        self.assertEqual(
            releases,
            (("https://www.pbc.gov.cn/flow.html", "2015年一季度社会融资规模增量统计数据报告"),),
        )

    def test_pboc_news_catalog_adds_an_older_release_at_a_batch_boundary(self) -> None:
        first = '<input type="hidden" totalpage="9">'
        requested = (
            '<a href="february.html">2025年2月社会融资规模增量统计数据报告</a>'
        )
        no_release = '<a href="policy.html">货币政策报告</a>'
        older = '<a href="january.html">2025年1月社会融资规模增量统计数据报告</a>'
        with patch.object(
            cycle,
            "_request_text",
            side_effect=[first, requested, no_release, older],
        ) as request:
            releases = cycle._pboc_news_release_catalog(
                1,
                start_page=2,
                include_older_boundary=True,
            )

        self.assertEqual([title for _, title in releases], [
            "2025年2月社会融资规模增量统计数据报告",
            "2025年1月社会融资规模增量统计数据报告",
        ])
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            [
                f"{cycle._PBOC_NEWS_BASE}index.html",
                f"{cycle._PBOC_NEWS_BASE}11040-2.html",
                f"{cycle._PBOC_NEWS_BASE}11040-3.html",
                f"{cycle._PBOC_NEWS_BASE}11040-4.html",
            ],
        )

    def test_pboc_news_catalog_rejects_insecure_and_foreign_links(self) -> None:
        title = "2022年5月社会融资规模增量统计数据报告"
        source = f"""
        <input type="hidden" totalpage="1">
        <a href="/relative.html">{title}</a>
        <a href="https://xining.pbc.gov.cn/subdomain.html">{title}</a>
        <a href="http://www.pbc.gov.cn/insecure.html">{title}</a>
        <a href="https://www.pbc.gov.cn.evil.example/lookalike.html">{title}</a>
        <a href="https://example.test/foreign.html">{title}</a>
        """

        with patch.object(cycle, "_request_text", return_value=source):
            releases = cycle._pboc_news_release_catalog(page_count=1)

        self.assertEqual(
            {url for url, _ in releases},
            {
                "https://www.pbc.gov.cn/relative.html",
                "https://xining.pbc.gov.cn/subdomain.html",
            },
        )

    def test_annual_pboc_title_is_december_even_when_body_mentions_a_month(self) -> None:
        self.assertEqual(
            cycle._period_date(
                "2025年社会融资规模增量统计数据报告",
                "12月份社会融资规模增量为若干亿元",
            ),
            dt.date(2025, 12, 1),
        )

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
        self.assertEqual(
            by_date.loc[dt.date(2026, 6, 1), "status"],
            "historical_backfill",
        )
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

    def test_credit_direct_month_values_handle_units_and_negative_flows(self) -> None:
        text = (
            "初步统计，2022年5月社会融资规模增量为2.79万亿元。"
            "其中，对实体经济发放的人民币贷款增加1.82万亿元；"
            "企业债券融资净减少108亿元；政府债券净融资1.06万亿元。"
        )

        self.assertEqual(
            cycle._credit_monthly_values(text),
            {
                "CN_TSF": 27900.0,
                "CN_TSF_RMB_LOANS_FLOW": 18200.0,
                "CN_CORP_BOND_FINANCING": -108.0,
                "CN_GOV_BOND_FINANCING": 10600.0,
            },
        )

    def test_credit_direct_month_values_support_legacy_wording(self) -> None:
        text = (
            "7月份社会融资规模增量为1.04万亿元。"
            "其中，当月对实体经济发放的人民币贷款增加1.29万亿元；"
            "企业债券净融资2237亿元。"
        )

        self.assertEqual(
            cycle._credit_monthly_values(text),
            {
                "CN_TSF": 10400.0,
                "CN_TSF_RMB_LOANS_FLOW": 12900.0,
                "CN_CORP_BOND_FINANCING": 2237.0,
            },
        )

    def test_credit_direct_month_rejects_same_month_from_another_year(self) -> None:
        text = (
            "2021年5月社会融资规模增量为1.92万亿元。"
            "其中，对实体经济发放的人民币贷款增加1.43万亿元。"
        )

        self.assertEqual(
            cycle._credit_monthly_values(text, observed=dt.date(2022, 5, 1)),
            {},
        )

    def test_credit_direct_month_does_not_borrow_following_ytd_components(self) -> None:
        text = (
            "初步统计，2022年5月社会融资规模增量为2.79万亿元。"
            "2022年1-5月社会融资规模增量累计为15.20万亿元，其中，"
            "对实体经济发放的人民币贷款增加10.17万亿元；"
            "企业债券净融资2.52万亿元；政府债券净融资7.76万亿元。"
        )

        self.assertEqual(
            cycle._credit_monthly_values(text, observed=dt.date(2022, 5, 1)),
            {"CN_TSF": 27900.0},
        )

    def test_pboc_loader_prefers_direct_month_over_cumulative_difference(self) -> None:
        january = "https://www.pbc.gov.cn/january"
        february_ytd = "https://www.pbc.gov.cn/february-ytd"
        february_direct = "https://www.pbc.gov.cn/february-direct"
        releases = (
            (january, "2022年1月社会融资规模增量统计数据报告"),
            (february_ytd, "2022年2月社会融资规模增量统计数据报告"),
            (february_direct, "2022年2月社会融资规模增量统计数据报告"),
        )
        pages = {
            january: (
                '<meta name="PubDate" content="2022-02-10 16:00">'
                "社会融资规模增量累计为1000亿元"
            ),
            february_ytd: (
                '<meta name="PubDate" content="2022-03-10 16:00">'
                "社会融资规模增量累计为2500亿元"
            ),
            february_direct: (
                '<meta name="PubDate" content="2022-03-10 16:01">'
                "2月份社会融资规模增量为1400亿元"
            ),
        }

        with (
            patch.object(cycle, "_pboc_news_release_catalog", return_value=releases),
            patch.object(
                cycle,
                "_get_pboc_archive_text",
                side_effect=lambda url: pages[url],
            ),
        ):
            result = cycle._load_pboc_credit(page_count=10, archive=True)

        tsf = result["CN_TSF"].set_index("date")
        self.assertEqual(tsf.loc[dt.date(2022, 1, 1), "value"], 1000.0)
        self.assertEqual(tsf.loc[dt.date(2022, 1, 1), "status"], "derived")
        self.assertEqual(tsf.loc[dt.date(2022, 2, 1), "value"], 1400.0)
        self.assertEqual(tsf.loc[dt.date(2022, 2, 1), "status"], "published")

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
        self.assertEqual(
            monthly[1]["formula_version"], cycle.PBOC_YTD_DIFF_FORMULA_VERSION
        )

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
