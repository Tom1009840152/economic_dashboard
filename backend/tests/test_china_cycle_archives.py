import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd

from app.fetchers import china_cycle_data as cycle
from app.fetchers import nbs_cycle
from app.fetchers.akshare_source import FETCHERS


_SEVENTY_CITIES = (
    "北京", "天津", "石家庄", "太原", "呼和浩特", "沈阳", "大连", "长春",
    "哈尔滨", "上海", "南京", "杭州", "宁波", "合肥", "福州", "厦门",
    "南昌", "济南", "青岛", "郑州", "武汉", "长沙", "广州", "深圳",
    "南宁", "海口", "重庆", "成都", "贵阳", "昆明", "西安", "兰州",
    "西宁", "银川", "乌鲁木齐", "唐山", "秦皇岛", "包头", "丹东", "锦州",
    "吉林", "牡丹江", "无锡", "徐州", "扬州", "温州", "金华", "蚌埠",
    "安庆", "泉州", "九江", "赣州", "烟台", "济宁", "洛阳", "平顶山",
    "宜昌", "襄阳", "岳阳", "常德", "韶关", "湛江", "惠州", "桂林",
    "北海", "三亚", "泸州", "南充", "遵义", "大理",
)


def _price_table(cities, values, *, unit: str = "上月=100") -> str:
    cells = []
    for index in range(35):
        row = []
        for city_index in (index, index + 35):
            if city_index < len(cities):
                row.extend(
                    [
                        f"<td>{cities[city_index]}</td>",
                        f"<td>{values[city_index]}</td>",
                        "<td>95.0</td>",
                        "<td>97.0</td>",
                    ]
                )
            else:
                row.extend(["<td></td>"] * 4)
        cells.append(f"<tr>{''.join(row)}</tr>")
    return f"""
      <table>
        <tr>
          <td rowspan="2">城市</td><td>环比</td><td>同比</td><td>年度平均</td>
          <td rowspan="2">城市</td><td>环比</td><td>同比</td><td>年度平均</td>
        </tr>
        <tr>
          <td>{unit}</td><td>上年同月=100</td><td>上年同月=100</td>
          <td>{unit}</td><td>上年同月=100</td><td>上年同月=100</td>
        </tr>
        {''.join(cells)}
      </table>
    """


def _house_price_release_html(
    cities=_SEVENTY_CITIES,
    values=None,
    *,
    table_number: int = 1,
    housing_type: str = "新建商品住宅",
    unit: str = "上月=100",
    include_second_hand: bool = False,
) -> str:
    values = values or ([100.5] * 14 + [99.5] * 56)
    table_one = _price_table(cities, values, unit=unit)
    second_hand = ""
    if include_second_hand:
        second_hand = f"""
          <p>表2：2024年12月70个大中城市二手住宅销售价格指数</p>
          {_price_table(_SEVENTY_CITIES, [110.0] * 70)}
        """
    return f"""
      <html>
        <head><title>2024年12月份70个大中城市商品住宅销售价格变动情况 - 国家统计局</title></head>
        <body>
          <h1>2024年12月份70个大中城市商品住宅销售价格变动情况</h1>
          <div class="detail-title-des"><p>2025/01/17 09:30</p></div>
          <p>表{table_number}：2024年12月70个大中城市{housing_type}销售价格指数</p>
          {table_one}
          {second_hand}
        </body>
      </html>
    """


def _nominal_gdp_release_html(
    title: str,
    period_title: str,
    *,
    published: str,
    quarter_label: str,
    quarter_value: float,
    ytd_label: str | None = None,
    ytd_value: float | None = None,
    amount_header: str = "绝对额（亿元）",
    table_ytd_label: str | None = None,
    h1: str | None = None,
    caption: str | None = None,
) -> str:
    year = title[:4]
    table_caption = caption or f"表1 {year}年{period_title}GDP初步核算数据"
    if ytd_label is None:
        table = f"""
          <table>
            <tr>
              <td></td><td>{amount_header}</td><td>比上年同期增长（%）</td>
            </tr>
            <tr><td>GDP</td><td>{quarter_value}</td><td>4.8</td></tr>
          </table>
        """
    elif ytd_value is None:
        table = f"""
          <table>
            <tr>
              <td></td><td>{amount_header}</td><td>比上年同期增长（%）</td>
            </tr>
            <tr><td>GDP</td><td>{quarter_value}</td><td>4.8</td></tr>
          </table>
        """
    else:
        rendered_ytd_label = table_ytd_label or ytd_label
        table = f"""
          <table>
            <tr>
              <td rowspan="2"></td>
              <td colspan="2">{amount_header}</td>
              <td colspan="2">比上年同期增长（%）</td>
            </tr>
            <tr>
              <td>{quarter_label}</td><td>{rendered_ytd_label}</td>
              <td>{quarter_label}</td><td>{rendered_ytd_label}</td>
            </tr>
            <tr>
              <td>GDP</td><td>{quarter_value}</td><td>{ytd_value}</td>
              <td>4.8</td><td>5.2</td>
            </tr>
          </table>
        """
    return f"""
      <html>
        <body>
          <div class="detail-title">
            <h1>{h1 or title}</h1>
            <div class="detail-title-des"><p>{published}</p></div>
          </div>
          <p>{table_caption}</p>
          {table}
          <p>表2 GDP同比增长速度</p>
          <table><tr><td>年份</td><td>1季度</td></tr><tr><td>2025</td><td>5.4</td></tr></table>
        </body>
      </html>
    """


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

    def test_nbs_archive_reader_rejects_non_https_and_lookalike_hosts(self) -> None:
        for url in (
            "http://www.stats.gov.cn/sj/zxfb/release.html",
            "https://www.stats.gov.cn.evil.example/release.html",
            "https://stats.gov.cn@example.test/release.html",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                cycle._get_nbs_archive_text(url)

        self.assertTrue(
            cycle._is_nbs_https_url("https://data.stats.gov.cn/release.html")
        )

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

    def test_nbs_nominal_gdp_history_selects_ytd_amount_for_all_quarters(self) -> None:
        releases = [
            (
                "https://www.stats.gov.cn/sj/zxfb/202504/q1.html",
                "2025年一季度国内生产总值初步核算结果",
            ),
            (
                "https://www.stats.gov.cn/sj/zxfb/202507/q2.html",
                "2025年二季度和上半年国内生产总值初步核算结果",
            ),
            (
                "https://www.stats.gov.cn/sj/zxfb/202510/q3.html",
                "2025年三季度国内生产总值（GDP）初步核算结果",
            ),
            (
                "https://www.stats.gov.cn/sj/zxfb/202302/q4.html",
                "2021年四季度和全年国内生产总值（GDP）初步核算结果",
            ),
        ]
        pages = {
            releases[0][0]: _nominal_gdp_release_html(
                releases[0][1],
                "一季度",
                published="2025/04/17 09:30",
                quarter_label="一季度",
                quarter_value=318758,
            ),
            releases[1][0]: _nominal_gdp_release_html(
                releases[1][1],
                "二季度和上半年",
                published="2025/07/16 09:30",
                quarter_label="二季度",
                quarter_value=341778,
                ytd_label="上半年",
                ytd_value=660536,
            ),
            releases[2][0]: _nominal_gdp_release_html(
                releases[2][1],
                "三季度",
                published="2025/10/21 09:30",
                quarter_label="三季度",
                quarter_value=354500,
                ytd_label="前三季度",
                ytd_value=1015036,
            ),
            releases[3][0]: _nominal_gdp_release_html(
                releases[3][1],
                "四季度和全年",
                published="2022/01/18 09:30",
                quarter_label="4季度",
                quarter_value=324237,
                ytd_label="全年",
                ytd_value=1143670,
                amount_header="现价总量（亿元）",
            ),
        }

        with (
            patch.object(cycle, "_nbs_links", return_value=releases) as links,
            patch.object(
                cycle,
                "_get_nbs_archive_text",
                side_effect=lambda url: pages[url],
            ) as request,
        ):
            result = cycle._load_nbs_nominal_gdp_history(
                page_count=70,
                start_page=5,
            )

        links.assert_called_once_with(
            cycle._NBS_NOMINAL_GDP_LINK_PATTERN,
            70,
            archive=True,
            start_page=5,
            archive_shard=0,
        )
        self.assertEqual(request.call_count, 4)
        by_date = result["CN_GDP_NOMINAL_YTD"].set_index("date")
        self.assertEqual(by_date.loc[dt.date(2021, 12, 1), "value"], 1143670)
        self.assertEqual(by_date.loc[dt.date(2025, 3, 1), "value"], 318758)
        self.assertEqual(by_date.loc[dt.date(2025, 6, 1), "value"], 660536)
        self.assertEqual(by_date.loc[dt.date(2025, 9, 1), "value"], 1015036)
        self.assertNotIn(341778, by_date["value"].tolist())
        self.assertNotIn(5.2, by_date["value"].tolist())
        self.assertEqual(
            by_date.loc[dt.date(2025, 6, 1), "available_at"],
            dt.datetime(2025, 7, 16, 9, 30),
        )
        self.assertEqual(
            by_date.loc[dt.date(2025, 6, 1), "source_url"], releases[1][0]
        )
        self.assertTrue((by_date["status"] == "published").all())

    def test_nbs_nominal_gdp_history_rejects_unsafe_page_variants(self) -> None:
        title = "2025年二季度和上半年国内生产总值初步核算结果"
        source_url = "https://www.stats.gov.cn/sj/zxfb/202507/q2.html"
        valid_kwargs = {
            "published": "2025/07/16 09:30",
            "quarter_label": "二季度",
            "quarter_value": 341778,
            "ytd_label": "上半年",
            "ytd_value": 660536,
        }
        cases = {
            "single-quarter amount only": {
                **valid_kwargs,
                "ytd_value": None,
            },
            "growth column masquerades as amount": {
                **valid_kwargs,
                "amount_header": "比上年同期增长（%）",
            },
            "wrong cumulative period": {
                **valid_kwargs,
                "table_ytd_label": "前三季度",
            },
            "wrong h1": {
                **valid_kwargs,
                "h1": "2025年三季度国内生产总值初步核算结果",
            },
            "date only": {
                **valid_kwargs,
                "published": "2025/07/16",
            },
            "wrong release month": {
                **valid_kwargs,
                "published": "2025/08/16 09:30",
            },
            "not table one": {
                **valid_kwargs,
                "caption": "表2 2025年二季度和上半年GDP初步核算数据",
            },
        }

        for label, kwargs in cases.items():
            source = _nominal_gdp_release_html(
                title,
                "二季度和上半年",
                **kwargs,
            )
            with (
                self.subTest(label=label),
                patch.object(
                    cycle,
                    "_nbs_links",
                    return_value=[(source_url, title)],
                ),
                patch.object(
                    cycle,
                    "_get_nbs_archive_text",
                    return_value=source,
                ),
            ):
                result = cycle._load_nbs_nominal_gdp_history()

            self.assertTrue(result["CN_GDP_NOMINAL_YTD"].empty)

    def test_nbs_nominal_gdp_accepts_exact_article_title_in_scripted_h1_template(self) -> None:
        title = "2025年四季度和全年国内生产总值初步核算结果"
        source_url = "https://www.stats.gov.cn/sj/zxfb/202601/q4.html"
        source = _nominal_gdp_release_html(
            title,
            "四季度和全年",
            published="2026/01/20 09:30",
            quarter_label="四季度",
            quarter_value=387911,
            ytd_label="全年",
            ytd_value=1401879,
        )
        source = source.replace(
            "<html>",
            f'<html><head><meta name="ArticleTitle" content="{title}"></head>',
            1,
        ).replace(
            f"<h1>{title}</h1>",
            f"<h1><script>document.write('{title}')</script></h1>",
            1,
        )
        with (
            patch.object(cycle, "_nbs_links", return_value=[(source_url, title)]),
            patch.object(cycle, "_get_nbs_archive_text", return_value=source),
        ):
            result = cycle._load_nbs_nominal_gdp_history()

        row = result["CN_GDP_NOMINAL_YTD"].iloc[0]
        self.assertEqual(row["date"], dt.date(2025, 12, 1))
        self.assertEqual(row["value"], 1401879)

    def test_nbs_nominal_gdp_history_rejects_bad_title_and_nonofficial_url(self) -> None:
        releases = [
            (
                "https://www.stats.gov.cn/sj/zxfb/202507/invalid.html",
                "2025年上半年国内生产总值初步核算结果",
            ),
            (
                "https://www.stats.gov.cn.evil.example/q2.html",
                "2025年二季度和上半年国内生产总值初步核算结果",
            ),
        ]
        with (
            patch.object(cycle, "_nbs_links", return_value=releases),
            patch.object(cycle, "_get_nbs_archive_text") as request,
        ):
            result = cycle._load_nbs_nominal_gdp_history()

        request.assert_not_called()
        self.assertTrue(result["CN_GDP_NOMINAL_YTD"].empty)

    def test_nominal_gdp_mirror_uses_its_real_transport_source(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "季度": "2025年第2季度",
                    "国内生产总值-绝对值": 659861.6,
                }
            ]
        )
        with patch.object(cycle.ak, "macro_china_gdp", return_value=raw):
            result = cycle._load_nominal_gdp()

        row = result.iloc[0]
        self.assertEqual(row["date"], dt.date(2025, 6, 1))
        self.assertEqual(row["source_url"], cycle._EASTMONEY_GDP_URL)
        self.assertEqual(row["status"], "mirror_backfill")
        self.assertTrue(pd.isna(row["available_at"]))

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

    def test_nbs_house_price_parser_uses_only_table_one_new_home_mom_index(self) -> None:
        source = _house_price_release_html(include_second_hand=True)

        values = cycle._nbs_new_home_mom_values(
            source, dt.date(2024, 12, 1)
        )

        self.assertIsNotNone(values)
        self.assertEqual(len(values), 70)
        self.assertEqual(values["北京"], 100.5)
        self.assertEqual(values["大理"], 99.5)
        self.assertNotIn(110.0, values.values())

    def test_nbs_house_price_parser_rejects_incomplete_duplicate_or_ambiguous_table(
        self,
    ) -> None:
        cases = {
            "only 69 cities": _house_price_release_html(
                cities=_SEVENTY_CITIES[:-1],
                values=[100.0] * 69,
            ),
            "duplicate city": _house_price_release_html(
                cities=(*_SEVENTY_CITIES[:-1], _SEVENTY_CITIES[0]),
                values=[100.0] * 70,
            ),
            "second-hand table": _house_price_release_html(
                housing_type="二手住宅"
            ),
            "not table one": _house_price_release_html(table_number=2),
            "not previous-month index": _house_price_release_html(
                unit="上年同月=100"
            ),
        }

        for label, source in cases.items():
            with self.subTest(label=label):
                self.assertIsNone(
                    cycle._nbs_new_home_mom_values(
                        source, dt.date(2024, 12, 1)
                    )
                )

    def test_nbs_house_price_archive_derives_one_current_month_observation(self) -> None:
        source_url = "https://www.stats.gov.cn/sj/zxfb/202501/release.html"
        source = _house_price_release_html(include_second_hand=True)
        with (
            patch.object(
                cycle,
                "_nbs_links",
                return_value=[
                    (
                        source_url,
                        "2024年12月份70个大中城市商品住宅销售价格变动情况",
                    )
                ],
            ) as links,
            patch.object(
                cycle, "_get_nbs_archive_text", return_value=source
            ) as request,
        ):
            result = cycle._load_nbs_house_price_diffusion(
                page_count=70,
                start_page=20,
                archive_shard=1000,
            )

        links.assert_called_once_with(
            r"70个大中城市商品住宅销售价格变动情况",
            70,
            archive=True,
            start_page=20,
            archive_shard=1000,
        )
        request.assert_called_once_with(source_url)
        rising = result["CN_RE_PRICE_RISING_SHARE"].iloc[0]
        median = result["CN_RE_PRICE_MOM_MEDIAN"].iloc[0]
        self.assertEqual(rising["date"], dt.date(2024, 12, 1))
        self.assertAlmostEqual(rising["value"], 20.0)
        self.assertAlmostEqual(median["value"], -0.5)
        self.assertEqual(rising["status"], "derived")
        self.assertEqual(
            rising["formula_version"],
            cycle.NBS_70_CITY_FORMULA_VERSIONS["CN_RE_PRICE_RISING_SHARE"],
        )
        self.assertEqual(
            median["formula_version"],
            cycle.NBS_70_CITY_FORMULA_VERSIONS["CN_RE_PRICE_MOM_MEDIAN"],
        )
        self.assertEqual(
            rising["available_at"], dt.datetime(2025, 1, 17, 9, 30)
        )

    def test_nbs_house_price_archive_skips_untrusted_domain_before_fetch(self) -> None:
        with (
            patch.object(
                cycle,
                "_nbs_links",
                return_value=[
                    (
                        "https://www.stats.gov.cn.evil.example/release.html",
                        "2024年12月份70个大中城市商品住宅销售价格变动情况",
                    )
                ],
            ),
            patch.object(cycle, "_get_nbs_archive_text") as request,
        ):
            result = cycle._load_nbs_house_price_diffusion()

        request.assert_not_called()
        self.assertTrue(result["CN_RE_PRICE_RISING_SHARE"].empty)
        self.assertTrue(result["CN_RE_PRICE_MOM_MEDIAN"].empty)

    def test_nbs_house_price_archive_rejects_non_exact_release_title(self) -> None:
        with (
            patch.object(
                cycle,
                "_nbs_links",
                return_value=[
                    (
                        "https://www.stats.gov.cn/sj/zxfb/release.html",
                        "2024年12月份70个大中城市商品住宅销售价格变动情况解读",
                    )
                ],
            ),
            patch.object(cycle, "_get_nbs_archive_text") as request,
        ):
            result = cycle._load_nbs_house_price_diffusion()

        request.assert_not_called()
        self.assertTrue(result["CN_RE_PRICE_RISING_SHARE"].empty)

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

    def test_fiscal_parser_accepts_annual_compared_with_previous_year_wording(self) -> None:
        self.assertEqual(
            cycle._extract_yoy(
                "全国一般公共预算支出287395亿元，比上年增长1.0%",
                r"全国一般公共预算支出",
            ),
            (287395.0, 1.0),
        )
        self.assertEqual(
            cycle._extract_yoy(
                "全国政府性基金预算支出91234亿元，比上年下降8.5%",
                r"全国政府性基金预算支出",
            ),
            (91234.0, -8.5),
        )
        self.assertEqual(
            cycle._extract_yoy(
                "全国一般公共预算支出128887亿元，比上年同期增长5.9%",
                r"全国一般公共预算支出",
            ),
            (128887.0, 5.9),
        )

    def test_fiscal_broad_value_is_canonical_derived_output(self) -> None:
        source_url = "https://gks.mof.gov.cn/tongjishuju/202601/release.htm"
        source = """
          <html><body>
          2025年，全国一般公共预算支出287395亿元，比上年增长1.0%。
          全国政府性基金预算支出91234亿元，比上年下降8.5%。
          </body></html>
        """
        metadata = {
            "release_date": dt.date(2026, 1, 30),
            "available_at": dt.datetime(2026, 1, 30, 9, 30),
            "source_url": source_url,
        }
        with (
            patch.object(
                cycle,
                "_mof_catalog",
                return_value=((source_url, "2025年财政收支情况"),),
            ),
            patch.object(cycle, "_get_mof_archive_text", return_value=source),
            patch.object(cycle, "_publication_metadata", return_value=metadata),
            patch.object(
                cycle,
                "calculate_fiscal_broad_expenditure",
                wraps=cycle.calculate_fiscal_broad_expenditure,
            ) as canonical,
        ):
            result = cycle._load_fiscal(page_count=1)

        canonical.assert_called_once()
        broad = result["CN_FISCAL_BROAD_EXPENDITURE_YTD"].iloc[0]
        self.assertEqual(broad["date"], dt.date(2025, 12, 1))
        self.assertEqual(broad["value"], 378629.0)
        self.assertEqual(broad["status"], "derived")
        self.assertEqual(broad["formula_version"], "1.0.0")

    def test_mof_catalog_rejects_non_official_detail_links(self) -> None:
        source = """
          <html><body>
            <a href="/tongjishuju/release.htm">官方</a>
            <a href="https://mof.gov.cn.evil.test/release.htm">仿冒</a>
          </body></html>
        """
        with (
            patch.object(cycle, "_get_mof_archive_text", return_value=source),
        ):
            result = cycle._mof_catalog("https://gks.mof.gov.cn/tongjishuju/", 1)

        self.assertEqual(
            result,
            (("https://gks.mof.gov.cn/tongjishuju/release.htm", "官方"),),
        )

    def test_nbs_archive_catalog_uses_last_good_index_after_live_challenge(self) -> None:
        cached = """
          <html><head><meta name="ColumnName" content="数据发布"></head><body>
            <a href="./202601/release.html" title="2025年四季度和全年国内生产总值初步核算结果">
              GDP
            </a>
          </body></html>
        """
        with (
            patch.object(
                cycle,
                "_get_nbs_archive_text",
                side_effect=RuntimeError("verification challenge"),
            ),
            patch.object(cycle, "_cached_nbs_index_source", return_value=cached),
        ):
            result = cycle._nbs_release_catalog(page_count=1, archive=True)

        self.assertEqual(
            result,
            ((
                "https://www.stats.gov.cn/sj/zxfb/202601/release.html",
                "2025年四季度和全年国内生产总值初步核算结果",
            ),),
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
