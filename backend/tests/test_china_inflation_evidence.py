import datetime as dt
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.fetchers import nbs_cycle
from app.fetchers.nbs_cycle import parse_core_cpi_values, parse_ppi_value
from app.models import DataPoint, Indicator, ReleaseEvidence
from app.services import china_inflation_evidence as inflation_evidence
from app.services.china_inflation_evidence import (
    DEFAULT_RECENT_INFLATION_INDEX_PAGE_COUNT,
    INFLATION_EVIDENCE_CODES,
    InflationCoverageError,
    InflationEvidenceError,
    collect_inflation_release_evidence,
    collect_recent_ppi_release_evidence,
    _get_nbs_inflation_archive_text,
    _request_nbs_inflation_text,
    inflation_index_url,
    parse_inflation_index,
    parse_inflation_release,
    store_inflation_evidence,
    validate_inflation_coverage,
)
from app.services.release_evidence import upsert_release_evidence as real_upsert
from scripts.backfill_china_inflation_evidence import build_parser, main


def article(title: str, body: str, *, timestamp: str = "2025/07/09 09:30") -> str:
    return f"""
    <html><head><title>{title}</title></head><body>
      <h1>{title}</h1>
      <div class="detail-title-des"><p>{timestamp}</p></div>
      {body}
    </body></html>
    """


def core_table_collector_kwargs() -> dict:
    title = "2025年6月份居民消费价格上涨0.1%"
    url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"
    index = f'<html><a href="{url}" title="{title}">{title}</a></html>'
    detail = f"""
    <html><head><title>{title}</title></head><body>
      <h1>{title}</h1>
      <div class="detail-title-des"><p>2025/07/09 09:30</p></div>
      <table>
        <tr><th>项目</th><th>环比涨跌幅（%）</th><th>同比涨跌幅（%）</th></tr>
        <tr><td>其中：不包括食品和能源</td><td>0.1</td><td>0.7</td></tr>
      </table>
    </body></html>
    """
    return {
        "core_page_count": 1,
        "fetch_core_index": lambda _: index,
        "fetch_core_release": lambda _: detail,
    }


class FakeResponse:
    apparent_encoding = "utf-8"

    def __init__(self, status: int, *, location: str = "", text: str = ""):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class ChinaInflationParserTests(unittest.TestCase):
    def test_regular_core_cpi_refresh_uses_recent_tables_with_metadata(self) -> None:
        source_url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"
        available_at = dt.datetime(2025, 7, 9, 9, 30)
        evidence = pd.DataFrame(
            [
                {
                    "date": dt.date(2025, 6, 1),
                    "value": 0.7,
                    "release_date": available_at.date(),
                    "available_at": available_at,
                    "source_url": source_url,
                    "status": "published",
                    "formula_version": None,
                }
            ]
        )
        with (
            patch.dict(nbs_cycle._cache, {}, clear=True),
            patch(
                "app.services.china_core_cpi_table_evidence."
                "collect_recent_core_cpi_table_evidence",
                return_value=evidence,
            ) as collect,
            patch.object(
                nbs_cycle,
                "parse_core_cpi_values",
                side_effect=AssertionError("commentary parser must not run"),
            ),
        ):
            frame = nbs_cycle.fetch_cn_core_cpi()

        collect.assert_called_once_with()
        self.assertEqual(frame.to_dict("records"), evidence.to_dict("records"))

    def test_regular_ppi_refresh_keeps_long_history_and_official_metadata(
        self,
    ) -> None:
        source_url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        available_at = dt.datetime(2025, 7, 9, 9, 30)
        official = pd.DataFrame(
            [
                {
                    "date": dt.date(2025, 6, 1),
                    "value": -3.6,
                    "release_date": available_at.date(),
                    "available_at": available_at,
                    "source_url": source_url,
                    "status": "published",
                    "formula_version": None,
                }
            ]
        )
        history = pd.DataFrame(
            [
                {"date": dt.date(2000, 1, 1), "value": -2.7},
                # Official data must win the overlapping month.
                {"date": dt.date(2025, 6, 1), "value": -9.9},
            ]
        )
        with (
            patch.dict(nbs_cycle._cache, {}, clear=True),
            patch(
                "app.services.china_inflation_evidence."
                "collect_recent_ppi_release_evidence",
                return_value=official,
            ) as collect,
            patch.object(nbs_cycle, "_load_ppi_history", return_value=history),
        ):
            frame = nbs_cycle.fetch_cn_ppi()

        collect.assert_called_once_with()
        self.assertIs(
            nbs_cycle.NBS_CYCLE_FETCHERS["CN_PPI"], nbs_cycle.fetch_cn_ppi
        )
        from app.fetchers.akshare_source import FETCHERS

        self.assertIs(FETCHERS["CN_PPI"], nbs_cycle.fetch_cn_ppi)
        self.assertEqual(
            frame.columns.tolist(),
            [
                "date",
                "value",
                "release_date",
                "available_at",
                "source_url",
                "status",
                "formula_version",
            ],
        )
        by_date = frame.set_index("date")
        self.assertEqual(float(by_date.loc[dt.date(2000, 1, 1), "value"]), -2.7)
        self.assertEqual(
            by_date.loc[dt.date(2000, 1, 1), "status"],
            "historical_backfill",
        )
        self.assertEqual(float(by_date.loc[dt.date(2025, 6, 1), "value"]), -3.6)
        self.assertEqual(
            by_date.loc[dt.date(2025, 6, 1), "available_at"], available_at
        )
        self.assertEqual(
            by_date.loc[dt.date(2025, 6, 1), "source_url"], source_url
        )

    def test_ppi_history_loader_preserves_long_values_without_fake_timestamps(
        self,
    ) -> None:
        raw = pd.DataFrame(
            {
                "月份": ["1999年12月份", "2000年01月份", "2025年06月份", "bad"],
                "当月同比增长": [-1.0, "-2.7", "-3.6", 99.0],
            }
        )
        with patch("akshare.macro_china_ppi", return_value=raw):
            history = nbs_cycle._load_ppi_history()

        self.assertEqual(
            history[["date", "value", "status"]].to_dict("records"),
            [
                {
                    "date": dt.date(2000, 1, 1),
                    "value": -2.7,
                    "status": "historical_backfill",
                },
                {
                    "date": dt.date(2025, 6, 1),
                    "value": -3.6,
                    "status": "historical_backfill",
                },
            ],
        )
        self.assertTrue(history["release_date"].isna().all())
        self.assertTrue(history["available_at"].isna().all())
        self.assertTrue(history["source_url"].isna().all())
        self.assertTrue(history["formula_version"].isna().all())

    def test_ppi_merge_rejects_aggregate_only_latest_month(self) -> None:
        official = pd.DataFrame(
            [{"date": dt.date(2025, 6, 1), "value": -3.6, "status": "published"}]
        )
        history = pd.DataFrame(
            [
                {"date": dt.date(2025, 6, 1), "value": -3.6},
                {"date": dt.date(2025, 7, 1), "value": -2.8},
            ]
        )

        with self.assertRaisesRegex(
            RuntimeError, "newer than the official recent window"
        ):
            nbs_cycle._merge_ppi_history(official, history)

    def test_2025_06_does_not_turn_headline_cpi_into_core_cpi(self) -> None:
        observed = dt.date(2025, 6, 1)
        source = article(
            "国家统计局城市司首席统计师解读2025年6月份CPI和PPI数据",
            """
            <p>6月份，居民消费价格指数（CPI）同比由上月下降转为上涨0.1%；
            扣除食品和能源价格的核心CPI同比继续回升，上涨0.7%。
            工业生产者出厂价格指数（PPI）环比下降0.4%，同比下降3.6%。</p>
            """,
        )
        self.assertEqual(parse_core_cpi_values(source, observed), {observed: 0.7})
        self.assertEqual(parse_ppi_value(source), -3.6)

    def test_core_parser_never_crosses_a_block_or_second_cpi_token(self) -> None:
        observed = dt.date(2025, 6, 1)
        cross_paragraph = article(
            "解读2025年6月份CPI和PPI数据",
            "<h2>核心CPI同比继续回升</h2><p>CPI同比上涨0.1%。</p>",
        )
        same_paragraph_other_cpi = article(
            "解读2025年6月份CPI和PPI数据",
            "<p>核心CPI同比继续回升，CPI同比上涨0.1%。</p>",
        )
        self.assertEqual(parse_core_cpi_values(cross_paragraph, observed), {})
        self.assertEqual(parse_core_cpi_values(same_paragraph_other_cpi, observed), {})

    def test_explicit_later_reference_keeps_prior_observation(self) -> None:
        observed = dt.date(2025, 3, 1)
        source = article(
            "解读2025年3月份CPI和PPI数据",
            """
            <p>三是扣除食品和能源价格的核心CPI明显回升，
            同比由上月下降0.1%转为上涨0.5%。</p>
            <p>工业生产者出厂价格指数（PPI）环比下降0.4%，同比下降2.5%。</p>
            """,
            timestamp="2025/04/10 09:30",
        )
        result = parse_inflation_release(
            source,
            title="国家统计局解读2025年3月份CPI和PPI数据",
            source_url="https://www.stats.gov.cn/sj/sjjd/202504/release.html",
        )
        core = result["CN_CORE_CPI"].set_index("date")
        self.assertEqual(float(core.loc[dt.date(2025, 2, 1), "value"]), -0.1)
        self.assertEqual(float(core.loc[dt.date(2025, 3, 1), "value"]), 0.5)
        self.assertEqual(
            core.loc[dt.date(2025, 2, 1), "available_at"],
            dt.datetime(2025, 4, 10, 9, 30),
        )
        self.assertEqual(
            core.loc[dt.date(2025, 2, 1), "status"],
            "published_later_reference",
        )

    def test_2017_02_explicit_pair_proves_both_months_at_one_release_time(self) -> None:
        source = article(
            "国家统计局解读2017年2月份CPI、PPI数据",
            """
            <p>尽管2月份CPI同比涨幅回落，但扣除食品和能源价格的核心CPI走势平稳，
            2月份和1月份核心CPI同比分别上涨1.8%和2.2%。</p>
            <p>2月份，全国工业生产者出厂价格环比上涨0.6%，同比上涨7.8%。</p>
            """,
            timestamp="2017/03/09 09:30",
        )
        parsed = parse_inflation_release(
            source,
            title="国家统计局解读2017年2月份CPI、PPI数据",
            source_url="https://www.stats.gov.cn/sj/sjjd/202302/old.html",
        )
        values = dict(
            zip(
                parsed["CN_CORE_CPI"]["date"],
                parsed["CN_CORE_CPI"]["value"],
                strict=True,
            )
        )
        self.assertEqual(values[dt.date(2017, 1, 1)], 2.2)
        self.assertEqual(values[dt.date(2017, 2, 1)], 1.8)

    def test_ppi_parser_requires_factory_gate_and_yoy(self) -> None:
        only_purchase = "<p>工业生产者购进价格同比下降4.2%。</p>"
        self.assertIsNone(parse_ppi_value(only_purchase))
        headline = "<p>PPI环比下降0.4%，同比下降3.6%。购进价格同比下降4.2%。</p>"
        self.assertEqual(parse_ppi_value(headline), -3.6)
        new_wording = (
            "<p>工业生产者出厂价格指数（PPI）环比由降转涨，"
            "同比涨幅扩大至3.8%。</p>"
        )
        self.assertEqual(parse_ppi_value(new_wording), 3.8)
        prior_sentence_qualifier = (
            "<p>2017年全年CPI上涨1.6%。PPI环比上涨0.8%，"
            "同比上涨4.9%。2017年全年PPI上涨6.3%。</p>"
        )
        self.assertEqual(parse_ppi_value(prior_sentence_qualifier), 4.9)

    def test_ppi_parser_covers_eight_verified_legacy_release_forms(self) -> None:
        cases = {
            "2016-08 direct release": (
                "<p>2016年8月份全国居民消费价格指数（CPI）和"
                "工业生产者出厂价格指数（PPI）数据显示，CPI环比上涨0.1%，"
                "同比上涨1.3%；PPI环比上涨0.2%，同比下降0.8%。</p>",
                -0.8,
            ),
            "2019-06 transition to flat": (
                "<p>从同比看，PPI由上月上涨0.6%转为持平。其中，"
                "生产资料价格由上月上涨0.6%转为下降0.3%。</p>",
                0.0,
            ),
            "2019-07 transition from flat": (
                "<p>从同比看，PPI由上月持平转为下降0.3%。其中，"
                "生产资料价格下降0.7%。</p>",
                -0.3,
            ),
            "2020-01 from-yoy transition": (
                "<p>从同比看，PPI由上月下降0.5%转为上涨0.1%。其中，"
                "生产资料价格下降0.4%。</p>",
                0.1,
            ),
            "2020-02 mom then yoy transition": (
                "<p>PPI略有下降。2月份，受季节和疫情因素影响，全国PPI"
                "环比由上月持平转为下降0.5%，同比由上涨0.1%转为下降0.4%。</p>",
                -0.4,
            ),
            "2020-05 qualified from-yoy": (
                "<p>从同比看，受去年对比基数略高影响，PPI下降3.7%，"
                "降幅比上月扩大0.6个百分点。</p>",
                -3.7,
            ),
            "2021-01 from-yoy transition": (
                "<p>从同比看，PPI由上月下降0.4%转为上涨0.3%。其中，"
                "生产资料价格由上月下降0.5%转为上涨0.5%。</p>",
                0.3,
            ),
            "2022-10 from-yoy transition": (
                "<p>从同比看，PPI由上月上涨0.9%转为下降1.3%，"
                "主要受去年同期对比基数较高影响。</p>",
                -1.3,
            ),
        }
        for label, (source, expected) in cases.items():
            with self.subTest(label=label):
                self.assertEqual(parse_ppi_value(source), expected)

    def test_ppi_legacy_forms_still_reject_non_headline_rates(self) -> None:
        rejected = {
            "purchase price": "<p>从同比看，工业生产者购进价格下降1.7%。</p>",
            "month on month": "<p>从同比看，PPI环比下降0.4%。</p>",
            "cumulative average": "<p>从同比看，1—5月平均，PPI下降1.7%。</p>",
            "industry contribution": (
                "<p>从同比看，上述行业价格合计影响PPI下降0.44%。</p>"
            ),
            "producer-goods component": (
                "<p>工业生产者出厂价格中，生产资料价格同比下降2.1%。</p>"
            ),
            "consumer-goods component": (
                "<p>工业生产者出厂价格中，生活资料价格同比上涨0.8%。</p>"
            ),
            "mining component": (
                "<p>工业生产者出厂价格中，采掘工业价格同比下降3.2%。</p>"
            ),
            "month-range cumulative": (
                "<p>1—5月份，全国工业生产者出厂价格指数（PPI）"
                "同比下降1.7%。</p>"
            ),
        }
        for label, source in rejected.items():
            with self.subTest(label=label):
                self.assertIsNone(parse_ppi_value(source))

    def test_ppi_release_rejects_nonzero_publication_seconds(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        source = article(
            title,
            "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
            timestamp="2025/07/09 09:30:45",
        )
        with self.assertRaisesRegex(
            InflationEvidenceError, "not minute-precision"
        ):
            parse_inflation_release(
                source,
                title=title,
                source_url=(
                    "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
                ),
                include_core=False,
            )

    def test_ppi_same_month_exact_duplicate_is_deduplicated(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        first = "https://www.stats.gov.cn/sj/sjjd/202507/first.html"
        second = "https://www.stats.gov.cn/sj/sjjd/202507/second.html"
        source = article(
            title,
            "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
        )
        evidence = collect_inflation_release_evidence(
            page_count=1,
            fetch_index=lambda _: (
                f'<a href="{first}" title="{title}">{title}</a>'
                f'<a href="{second}" title="{title}">{title}</a>'
            ),
            fetch_release=lambda _: source,
            **core_table_collector_kwargs(),
        )
        self.assertEqual(len(evidence["CN_PPI"]), 1)
        self.assertEqual(float(evidence["CN_PPI"].iloc[0]["value"]), -3.6)

    def test_ppi_same_month_nonidentical_release_fails_closed(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        first = "https://www.stats.gov.cn/sj/sjjd/202507/first.html"
        second = "https://www.stats.gov.cn/sj/sjjd/202507/second.html"

        def detail(url: str) -> str:
            value = "3.6" if url == first else "3.5"
            timestamp = "2025/07/09 09:30" if url == first else "2025/07/09 09:31"
            return article(
                title,
                f"<p>工业生产者出厂价格指数（PPI）同比下降{value}%。</p>",
                timestamp=timestamp,
            )

        with self.assertRaisesRegex(
            InflationEvidenceError, "non-identical PPI subject-month"
        ):
            collect_inflation_release_evidence(
                page_count=1,
                fetch_index=lambda _: (
                    f'<a href="{first}" title="{title}">{title}</a>'
                    f'<a href="{second}" title="{title}">{title}</a>'
                ),
                fetch_release=detail,
                **core_table_collector_kwargs(),
            )

    def test_ppi_same_value_but_different_source_digest_fails_closed(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        first = "https://www.stats.gov.cn/sj/sjjd/202507/first.html"
        second = "https://www.stats.gov.cn/sj/sjjd/202507/second.html"
        base = article(
            title,
            "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
        )

        with self.assertRaisesRegex(
            InflationEvidenceError, "non-identical PPI subject-month"
        ):
            collect_inflation_release_evidence(
                page_count=1,
                fetch_index=lambda _: (
                    f'<a href="{first}" title="{title}">{title}</a>'
                    f'<a href="{second}" title="{title}">{title}</a>'
                ),
                fetch_release=lambda url: base if url == first else base + "<!-- mirror -->",
                **core_table_collector_kwargs(),
            )

    def test_index_rejects_matching_official_lookalike_domain(self) -> None:
        with self.assertRaises(InflationEvidenceError):
            parse_inflation_index(
                '<a href="https://www.stats.gov.cn.evil.test/x">'
                "解读2025年6月份CPI和PPI数据</a>",
                "https://www.stats.gov.cn/sj/sjjd/",
            )

    def test_release_requires_visible_exact_minute(self) -> None:
        source = article(
            "解读2025年6月份CPI和PPI数据",
            "<p>核心CPI同比上涨0.7%。PPI同比下降3.6%。</p>",
            timestamp="2025/07/09",
        )
        with self.assertRaises(InflationEvidenceError):
            parse_inflation_release(
                source,
                title="解读2025年6月份CPI和PPI数据",
                source_url="https://www.stats.gov.cn/sj/sjjd/x.html",
            )

    def test_cms_create_date_is_not_a_visible_publication_minute(self) -> None:
        title = "解读2025年6月份CPI和PPI数据"
        source = f"""
        <html><head><title>{title}</title>
        <meta name="createDate" content="2025/07/09 09:30"></head>
        <body><h1>{title}</h1><p>核心CPI同比上涨0.7%。PPI同比下降3.6%。</p></body></html>
        """
        with self.assertRaises(InflationEvidenceError):
            parse_inflation_release(
                source,
                title=title,
                source_url="https://www.stats.gov.cn/sj/sjjd/x.html",
            )

    def test_nbs_redirect_chain_rejects_external_or_http_hop(self) -> None:
        class Response:
            status_code = 302
            apparent_encoding = "utf-8"
            text = ""

            def __init__(self, location: str) -> None:
                self.headers = {"Location": location}

            def raise_for_status(self) -> None:
                return None

        for location in (
            "https://evil.example/release.html",
            "http://www.stats.gov.cn/sj/sjjd/release.html",
        ):
            with self.subTest(location=location), patch(
                "app.services.china_inflation_evidence.requests.get",
                return_value=Response(location),
            ) as get:
                with self.assertRaises(InflationEvidenceError):
                    _request_nbs_inflation_text(
                        "https://www.stats.gov.cn/sj/sjjd/index.html"
                    )
                self.assertEqual(get.call_count, 1)

    def test_nbs_redirect_chain_allows_only_official_https_hops(self) -> None:
        class Response:
            apparent_encoding = "utf-8"

            def __init__(self, status: int, *, location: str = "", text: str = ""):
                self.status_code = status
                self.headers = {"Location": location} if location else {}
                self.text = text

            def raise_for_status(self) -> None:
                return None

        with patch(
            "app.services.china_inflation_evidence.requests.get",
            side_effect=[
                Response(302, location="/sj/sjjd/release.html"),
                Response(200, text="<html>ok</html>"),
            ],
        ) as get:
            self.assertEqual(
                _request_nbs_inflation_text(
                    "https://www.stats.gov.cn/sj/sjjd/index.html"
                ),
                "<html>ok</html>",
            )
        self.assertEqual(
            get.call_args_list[1].args[0],
            "https://www.stats.gov.cn/sj/sjjd/release.html",
        )
        self.assertFalse(get.call_args_list[0].kwargs["allow_redirects"])

    def test_collector_fetches_commentary_once_for_ppi(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        index = f'<html><a href="{url}" title="{title}">{title}</a></html>'
        source = article(
            title,
            "<p>核心CPI同比上涨0.7%。工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
        )
        calls = []

        def fetch_release(requested: str) -> str:
            calls.append(requested)
            return source

        evidence = collect_inflation_release_evidence(
            page_count=1,
            fetch_index=lambda _: index,
            fetch_release=fetch_release,
            **core_table_collector_kwargs(),
        )
        self.assertEqual(calls, [url])
        self.assertEqual(len(evidence["CN_CORE_CPI"]), 1)
        self.assertEqual(len(evidence["CN_PPI"]), 1)

    def test_collector_continues_after_a_numbered_archive_hole(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        detail_url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        index = f'<a href="{detail_url}" title="{title}">{title}</a>'
        source = article(
            title,
            "<p>核心CPI同比上涨0.7%。PPI同比下降3.6%。</p>",
        )

        def fetch_index(url: str) -> str:
            if url.endswith("index_1.html"):
                raise FileNotFoundError(url)
            return index if url.endswith("index_2.html") else "<html></html>"

        evidence = collect_inflation_release_evidence(
            page_count=3,
            fetch_index=fetch_index,
            fetch_release=lambda _: source,
            **core_table_collector_kwargs(),
        )
        self.assertEqual(len(evidence["CN_CORE_CPI"]), 1)
        self.assertEqual(len(evidence["CN_PPI"]), 1)

    def test_default_collector_reaches_archive_index_140(self) -> None:
        title = "国家统计局解读2016年8月份CPI、PPI数据"
        detail_url = (
            "https://www.stats.gov.cn/sj/sjjd/202302/"
            "t20230202_1895770.html"
        )
        index = f'<a href="{detail_url}" title="{title}">{title}</a>'
        source = article(
            title,
            "<p>PPI环比上涨0.2%，同比下降0.8%。</p>",
            timestamp="2016/09/09 09:30",
        )
        requested_indexes: list[str] = []

        def fetch_index(url: str) -> str:
            requested_indexes.append(url)
            return index if url.endswith("index_140.html") else "<html></html>"

        evidence = collect_inflation_release_evidence(
            fetch_index=fetch_index,
            fetch_release=lambda _: source,
            **core_table_collector_kwargs(),
        )

        self.assertEqual(len(requested_indexes), 141)
        self.assertTrue(requested_indexes[-1].endswith("index_140.html"))
        self.assertEqual(
            evidence["CN_PPI"][["date", "value"]].to_dict("records"),
            [{"date": dt.date(2016, 8, 1), "value": -0.8}],
        )

    def test_collector_fails_closed_after_one_non_404_index_failure(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        detail_url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        index = f'<a href="{detail_url}" title="{title}">{title}</a>'

        def fetch_index(url: str) -> str:
            if url.endswith("index_1.html"):
                raise OSError("temporary index transport failure")
            return index

        with self.assertRaisesRegex(
            InflationEvidenceError, "index scan failed"
        ):
            collect_inflation_release_evidence(
                page_count=2,
                fetch_index=fetch_index,
                fetch_release=lambda _: self.fail(
                    "details must not be fetched after an incomplete index scan"
                ),
            )

    def test_collector_fails_closed_after_one_detail_failure(self) -> None:
        june_title = "国家统计局解读2025年6月份CPI和PPI数据"
        july_title = "国家统计局解读2025年7月份CPI和PPI数据"
        june_url = "https://www.stats.gov.cn/sj/sjjd/202507/june.html"
        july_url = "https://www.stats.gov.cn/sj/sjjd/202508/july.html"
        index = (
            f'<a href="{june_url}" title="{june_title}">{june_title}</a>'
            f'<a href="{july_url}" title="{july_title}">{july_title}</a>'
        )
        june_source = article(
            june_title,
            "<p>核心CPI同比上涨0.7%。PPI同比下降3.6%。</p>",
        )

        def fetch_release(url: str) -> str:
            if url == june_url:
                return june_source
            raise OSError("temporary detail transport failure")

        with self.assertRaisesRegex(
            InflationEvidenceError, "release scan failed"
        ):
            collect_inflation_release_evidence(
                page_count=1,
                fetch_index=lambda _: index,
                fetch_release=fetch_release,
            )


class RecentPpiReleaseCollectorTests(unittest.TestCase):
    title = "国家统计局城市司首席统计师解读2025年6月份CPI和PPI数据"
    url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"

    def test_recent_details_are_revalidated_without_persistent_cache(self) -> None:
        index = f'<a href="{self.url}" title="{self.title}">{self.title}</a>'
        detail = article(
            self.title,
            "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
        )
        requested: list[tuple[str, bool]] = []

        def load(url: str, *, cache: bool) -> str:
            requested.append((url, cache))
            if url == inflation_index_url(0):
                return index
            if url == self.url:
                return detail
            self.fail(f"unexpected URL: {url}")

        with patch.object(
            inflation_evidence,
            "_get_nbs_inflation_archive_text",
            side_effect=load,
        ):
            evidence = collect_recent_ppi_release_evidence(page_count=1)

        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            requested,
            [(inflation_index_url(0), False), (self.url, False)],
        )

    def test_default_scan_is_bounded_and_returns_refresh_metadata(self) -> None:
        requested_indexes: list[str] = []
        requested_details: list[str] = []
        index = f'<a href="{self.url}" title="{self.title}">{self.title}</a>'
        detail = article(
            self.title,
            (
                "<p>核心CPI同比上涨9.9%。核心CPI同比上涨8.8%。</p>"
                "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>"
            ),
        )

        def fetch_index(url: str) -> str:
            requested_indexes.append(url)
            return index if url == inflation_index_url(0) else "<html></html>"

        def fetch_release(url: str) -> str:
            requested_details.append(url)
            return detail

        evidence = collect_recent_ppi_release_evidence(
            fetch_index=fetch_index,
            fetch_release=fetch_release,
        )

        self.assertEqual(
            requested_indexes,
            [
                inflation_index_url(page)
                for page in range(DEFAULT_RECENT_INFLATION_INDEX_PAGE_COUNT)
            ],
        )
        self.assertFalse(
            any("index_1000" in url or "index_2000" in url for url in requested_indexes)
        )
        self.assertEqual(requested_details, [self.url])
        self.assertEqual(
            evidence[
                [
                    "date",
                    "value",
                    "release_date",
                    "available_at",
                    "source_url",
                    "status",
                    "formula_version",
                ]
            ].to_dict("records"),
            [
                {
                    "date": dt.date(2025, 6, 1),
                    "value": -3.6,
                    "release_date": dt.date(2025, 7, 9),
                    "available_at": dt.datetime(2025, 7, 9, 9, 30),
                    "source_url": self.url,
                    "status": "published",
                    "formula_version": None,
                }
            ],
        )

    def test_empty_recent_indexes_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no monthly releases"):
            collect_recent_ppi_release_evidence(
                page_count=1,
                fetch_index=lambda _: "<html></html>",
                fetch_release=lambda _: self.fail("empty discovery has no detail"),
            )

    def test_numbered_index_hole_is_allowed_after_primary_index_succeeds(
        self,
    ) -> None:
        index = f'<a href="{self.url}" title="{self.title}">{self.title}</a>'
        detail = article(
            self.title,
            "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
        )

        def fetch_index(url: str) -> str:
            if url == inflation_index_url(0):
                return index
            raise FileNotFoundError(url)

        evidence = collect_recent_ppi_release_evidence(
            page_count=2,
            fetch_index=fetch_index,
            fetch_release=lambda _: detail,
        )
        self.assertEqual(len(evidence), 1)

    def test_missing_primary_index_fails_even_if_later_page_exists(self) -> None:
        index = f'<a href="{self.url}" title="{self.title}">{self.title}</a>'

        def fetch_index(url: str) -> str:
            if url == inflation_index_url(0):
                raise FileNotFoundError(url)
            return index

        with self.assertRaisesRegex(RuntimeError, "primary index page failed"):
            collect_recent_ppi_release_evidence(
                page_count=2,
                fetch_index=fetch_index,
                fetch_release=lambda _: self.fail(
                    "primary-index failure must stop before detail fetch"
                ),
            )

    def test_matching_detail_without_headline_ppi_fails_closed(self) -> None:
        index = f'<a href="{self.url}" title="{self.title}">{self.title}</a>'
        detail = article(
            self.title,
            "<p>居民消费价格指数（CPI）同比上涨0.1%。</p>",
        )
        with self.assertRaisesRegex(
            InflationEvidenceError, "recent NBS PPI detail scan failed"
        ):
            collect_recent_ppi_release_evidence(
                page_count=1,
                fetch_index=lambda _: index,
                fetch_release=lambda _: detail,
            )

    def test_one_matching_bad_detail_rejects_the_whole_recent_batch(self) -> None:
        july_title = "国家统计局解读2025年7月份CPI和PPI数据"
        july_url = "https://www.stats.gov.cn/sj/sjjd/202508/release.html"
        index = (
            f'<a href="{self.url}" title="{self.title}">{self.title}</a>'
            f'<a href="{july_url}" title="{july_title}">{july_title}</a>'
        )

        def fetch_release(url: str) -> str:
            if url == self.url:
                return article(
                    self.title,
                    "<p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
                )
            return article(july_title, "<p>CPI同比上涨0.2%。</p>")

        with self.assertRaisesRegex(
            InflationEvidenceError, "recent NBS PPI detail scan failed"
        ):
            collect_recent_ppi_release_evidence(
                page_count=1,
                fetch_index=lambda _: index,
                fetch_release=fetch_release,
            )


class ChinaInflationArchiveCacheTests(unittest.TestCase):
    @staticmethod
    def _detail() -> str:
        return article(
            "国家统计局解读2025年6月份CPI和PPI数据",
            "<p>核心CPI同比上涨0.7%。工业生产者出厂价格指数（PPI）同比下降3.6%。</p>",
        )

    def test_verified_detail_cache_is_resumable_and_auditable(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "nbs-inflation-evidence-v2"
            with (
                patch.object(
                    inflation_evidence, "_NBS_INFLATION_CACHE", cache_root
                ),
                patch.object(
                    inflation_evidence, "_last_nbs_inflation_request", 0.0
                ),
                patch.object(
                    inflation_evidence.requests,
                    "get",
                    return_value=FakeResponse(200, text=self._detail()),
                ) as get,
            ):
                first = _get_nbs_inflation_archive_text(url, cache=True)
                second = _get_nbs_inflation_archive_text(url, cache=True)

            self.assertEqual(first, second)
            self.assertEqual(get.call_count, 1)
            source_files = list(cache_root.glob("*.html"))
            metadata_files = list(cache_root.glob("*.json"))
            self.assertEqual(len(source_files), 1)
            self.assertEqual(len(metadata_files), 1)
            source_bytes = source_files[0].read_bytes()
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["cache_version"], 2)
            self.assertEqual(metadata["request_url"], url)
            self.assertEqual(metadata["final_url"], url)
            self.assertEqual(metadata["redirect_chain"], [url])
            self.assertEqual(
                metadata["sha256"], hashlib.sha256(source_bytes).hexdigest()
            )

    def test_legacy_cache_namespace_is_never_reused(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "nbs-release"
            legacy.mkdir()
            (legacy / f"{digest}.html").write_text(
                self._detail(), encoding="utf-8"
            )
            cache_root = root / "nbs-inflation-evidence-v2"
            with (
                patch.object(
                    inflation_evidence, "_NBS_INFLATION_CACHE", cache_root
                ),
                patch.object(
                    inflation_evidence, "_last_nbs_inflation_request", 0.0
                ),
                patch.object(
                    inflation_evidence.requests,
                    "get",
                    return_value=FakeResponse(200, text=self._detail()),
                ) as get,
            ):
                _get_nbs_inflation_archive_text(url, cache=True)
            self.assertEqual(get.call_count, 1)
            self.assertTrue(cache_root.is_dir())

    def test_index_pages_are_never_cached(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/index_1.html"
        source = "<html><body>index</body></html>"
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "nbs-inflation-evidence-v2"
            with (
                patch.object(
                    inflation_evidence, "_NBS_INFLATION_CACHE", cache_root
                ),
                patch.object(
                    inflation_evidence, "_last_nbs_inflation_request", 0.0
                ),
                patch.object(inflation_evidence.time, "sleep"),
                patch.object(
                    inflation_evidence.requests,
                    "get",
                    return_value=FakeResponse(200, text=source),
                ) as get,
            ):
                self.assertEqual(
                    _get_nbs_inflation_archive_text(url, cache=False), source
                )
                self.assertEqual(
                    _get_nbs_inflation_archive_text(url, cache=False), source
                )
            self.assertEqual(get.call_count, 2)
            self.assertFalse(cache_root.exists())

    def test_collector_wires_mutable_index_and_cached_detail_separately(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        detail_url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        index = f'<html><a href="{detail_url}" title="{title}">{title}</a></html>'
        detail = self._detail()

        def load(url: str, *, cache: bool) -> str:
            return detail if cache else index

        with patch.object(
            inflation_evidence,
            "_get_nbs_inflation_archive_text",
            side_effect=load,
        ) as loader:
            collect_inflation_release_evidence(
                page_count=1,
                **core_table_collector_kwargs(),
            )
        self.assertFalse(loader.call_args_list[0].kwargs["cache"])
        self.assertEqual(loader.call_args_list[1].args[0], detail_url)
        self.assertTrue(loader.call_args_list[1].kwargs["cache"])

    def test_untrusted_or_invalid_detail_never_enters_cache(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        cases = {
            "external redirect": FakeResponse(
                302, location="https://evil.example/release.html"
            ),
            "http redirect": FakeResponse(
                302, location="http://www.stats.gov.cn/sj/sjjd/release.html"
            ),
            "challenge": FakeResponse(
                200,
                text="<html>Please enable JavaScript and refresh the page</html>",
            ),
            "wrong subject": FakeResponse(
                200, text="<html><head><title>其他文章</title></head></html>"
            ),
        }
        for label, response in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                cache_root = Path(temporary) / "nbs-inflation-evidence-v2"
                with (
                    patch.object(
                        inflation_evidence, "_NBS_INFLATION_CACHE", cache_root
                    ),
                    patch.object(
                        inflation_evidence,
                        "_last_nbs_inflation_request",
                        0.0,
                    ),
                    patch.object(inflation_evidence.time, "sleep"),
                    patch.object(
                        inflation_evidence.requests,
                        "get",
                        return_value=response,
                    ) as get,
                ):
                    with self.assertRaises(InflationEvidenceError):
                        _get_nbs_inflation_archive_text(url, cache=True)
                self.assertEqual(get.call_count, 4)
                self.assertFalse(cache_root.exists())

    def test_cache_hit_rechecks_body_hash_and_metadata(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "nbs-inflation-evidence-v2"
            with (
                patch.object(
                    inflation_evidence, "_NBS_INFLATION_CACHE", cache_root
                ),
                patch.object(
                    inflation_evidence, "_last_nbs_inflation_request", 0.0
                ),
                patch.object(inflation_evidence.time, "sleep"),
                patch.object(
                    inflation_evidence.requests,
                    "get",
                    return_value=FakeResponse(200, text=self._detail()),
                ) as get,
            ):
                _get_nbs_inflation_archive_text(url, cache=True)
                source_path = next(cache_root.glob("*.html"))
                metadata_path = next(cache_root.glob("*.json"))
                source_path.write_text(self._detail() + "tampered", encoding="utf-8")
                _get_nbs_inflation_archive_text(url, cache=True)
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["final_url"] = "https://evil.example/release.html"
                metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                _get_nbs_inflation_archive_text(url, cache=True)
            self.assertEqual(get.call_count, 3)

    def test_real_request_starts_are_throttled(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/index.html"
        clock = [10.0]
        waits: list[float] = []

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            clock[0] += seconds

        with (
            patch.object(
                inflation_evidence, "_last_nbs_inflation_request", 0.0
            ),
            patch.object(inflation_evidence.time, "monotonic", side_effect=monotonic),
            patch.object(inflation_evidence.time, "sleep", side_effect=sleep),
            patch.object(
                inflation_evidence.requests,
                "get",
                return_value=FakeResponse(200, text="<html>index</html>"),
            ) as get,
        ):
            _request_nbs_inflation_text(url)
            clock[0] += 0.1
            _request_nbs_inflation_text(url)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(waits), 1)
        self.assertGreaterEqual(waits[0], 0.35 - 1e-9)

    def test_failed_transport_attempt_is_also_throttled(self) -> None:
        url = "https://www.stats.gov.cn/sj/sjjd/index.html"
        clock = [20.0]
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            clock[0] += seconds

        with (
            patch.object(
                inflation_evidence, "_last_nbs_inflation_request", 0.0
            ),
            patch.object(
                inflation_evidence.time,
                "monotonic",
                side_effect=lambda: clock[0],
            ),
            patch.object(inflation_evidence.time, "sleep", side_effect=sleep),
            patch.object(
                inflation_evidence.requests,
                "get",
                side_effect=[
                    OSError("transport failed"),
                    FakeResponse(200, text="<html>index</html>"),
                ],
            ),
        ):
            with self.assertRaises(OSError):
                _request_nbs_inflation_text(url)
            clock[0] += 0.1
            _request_nbs_inflation_text(url)
        self.assertEqual(len(waits), 1)
        self.assertGreaterEqual(waits[0], 0.35 - 1e-9)


class ChinaInflationStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in INFLATION_EVIDENCE_CODES:
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
                    category="macro",
                    unit="%",
                    source="NBS",
                    frequency="monthly",
                    is_visible=True,
                    sort_order=0,
                    region="CN",
                )
            )
        self.db.add(
            DataPoint(
                indicator_code="CN_CORE_CPI",
                date=dt.date(2017, 1, 1),
                value=99,
                status="sentinel",
                version=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _frame(code: str, start: str, end: str) -> pd.DataFrame:
        rows = []
        for period in pd.period_range(start, end, freq="M"):
            available = dt.datetime(period.year, period.month, 10, 9, 30) + pd.offsets.MonthBegin(1)
            rows.append(
                {
                    "date": period.start_time.date(),
                    "value": 1.0 if code == "CN_CORE_CPI" else -1.0,
                    "release_date": available.date(),
                    "available_at": available.to_pydatetime(),
                    "source_url": f"https://www.stats.gov.cn/{code}/{period}.html",
                    "status": "published",
                    "formula_version": None,
                    "provenance_json": {
                        "article_observation": period.start_time.date().isoformat(),
                        "evidence_semantics": "subject_month_release",
                        "parser_version": "nbs_inflation_release_v2",
                        "publication_time_source": "visible_nbs_detail_title",
                        "source_sha256": "a" * 64,
                        "title": (
                            f"解读{period.year}年{period.month}月份CPI和PPI数据"
                        ),
                    },
                    "evidence_kind": "official_release",
                    "chain_verified": True,
                    "availability_precision": "exact_minute",
                }
            )
        return pd.DataFrame(rows)

    def _complete(self) -> dict[str, pd.DataFrame]:
        return {
            "CN_CORE_CPI": self._frame("CN_CORE_CPI", "2017-01", "2017-01"),
            "CN_PPI": self._frame("CN_PPI", "2016-08", "2017-01"),
        }

    def test_coverage_gate_does_not_hide_missing_months(self) -> None:
        evidence = self._complete()
        evidence["CN_PPI"] = evidence["CN_PPI"].iloc[:-1]
        gate = validate_inflation_coverage(
            evidence, as_of=dt.date(2017, 3, 20)
        )
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["missing"]["CN_PPI"], ["2017-01"])
        with self.assertRaises(InflationCoverageError):
            with patch(
                "app.services.china_inflation_evidence._latest_required_month",
                return_value=pd.Period("2017-01", freq="M"),
            ):
                store_inflation_evidence(self.db, evidence)

    def test_store_is_evidence_only_and_idempotent(self) -> None:
        evidence = self._complete()
        with patch(
            "app.services.china_inflation_evidence._latest_required_month",
            return_value=pd.Period("2017-01", freq="M"),
        ):
            first = store_inflation_evidence(self.db, evidence)
            second = store_inflation_evidence(self.db, evidence)
        self.assertEqual(first, {"CN_CORE_CPI": 1, "CN_PPI": 6})
        self.assertEqual(second, {"CN_CORE_CPI": 0, "CN_PPI": 0})
        sentinel = self.db.scalar(select(DataPoint))
        self.assertEqual(float(sentinel.value), 99.0)
        self.assertEqual(sentinel.version, 1)

    def test_second_series_failure_rolls_back_first_series(self) -> None:
        evidence = self._complete()
        calls = 0

        def fail_second(db, code, frame, commit=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fault")
            return real_upsert(db, code, frame, commit=commit)

        with patch(
            "app.services.china_inflation_evidence.upsert_release_evidence",
            side_effect=fail_second,
        ), patch(
            "app.services.china_inflation_evidence._latest_required_month",
            return_value=pd.Period("2017-01", freq="M"),
        ):
            with self.assertRaises(RuntimeError):
                store_inflation_evidence(self.db, evidence)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(ReleaseEvidence)), 0
        )

    def test_source_specific_validator_rejects_each_untrusted_assertion(self) -> None:
        mutations = {
            "missing provenance": ("provenance_json", None),
            "non-official URL": ("source_url", "https://stats.gov.cn.evil.test/x"),
            "unverified chain": ("chain_verified", False),
            "wrong evidence kind": ("evidence_kind", "official_distribution_mirror"),
            "derived formula": ("formula_version", "fake_v1"),
            "weak time": ("availability_precision", "date_upper_bound"),
            "wrong status": ("status", "backfilled"),
        }
        for label, (column, value) in mutations.items():
            evidence = self._complete()
            evidence["CN_CORE_CPI"].at[0, column] = value
            with self.subTest(label=label), self.assertRaises(
                InflationEvidenceError
            ):
                validate_inflation_coverage(
                    evidence, as_of=dt.date(2017, 3, 20)
                )

    def test_duplicate_or_conflicting_instant_fails_closed(self) -> None:
        evidence = self._complete()
        duplicate = evidence["CN_CORE_CPI"].iloc[[0]].copy()
        evidence["CN_CORE_CPI"] = pd.concat(
            [evidence["CN_CORE_CPI"], duplicate], ignore_index=True
        )
        with self.assertRaises(InflationEvidenceError):
            validate_inflation_coverage(
                evidence, as_of=dt.date(2017, 3, 20)
            )

    def test_exact_minute_rejects_nonzero_seconds(self) -> None:
        evidence = self._complete()
        available = evidence["CN_CORE_CPI"].at[0, "available_at"]
        evidence["CN_CORE_CPI"].at[0, "available_at"] = available.replace(
            second=30
        )

        with self.assertRaises(InflationEvidenceError):
            validate_inflation_coverage(
                evidence, as_of=dt.date(2017, 3, 20)
            )

    def test_apply_cli_has_no_historical_coverage_escape_hatch(self) -> None:
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["--apply", "--coverage-as-of", "2017-03-20"]
            )

    def test_cli_default_scan_includes_index_140(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.pages, 141)
        self.assertEqual(
            args.pages, inflation_evidence.DEFAULT_INFLATION_INDEX_PAGE_COUNT
        )

    def test_dry_run_is_default_and_never_opens_database(self) -> None:
        gate = {"ready": False, "missing": {"CN_CORE_CPI": ["2017-02"]}}
        with (
            patch(
                "scripts.backfill_china_inflation_evidence.collect_inflation_release_evidence",
                return_value={},
            ),
            patch(
                "scripts.backfill_china_inflation_evidence.validate_inflation_coverage",
                return_value=gate,
            ),
            patch(
                "scripts.backfill_china_inflation_evidence.evidence_summary",
                return_value={},
            ),
            patch(
                "scripts.backfill_china_inflation_evidence.SessionLocal"
            ) as session_local,
            patch("builtins.print") as output,
        ):
            main([])

        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["mode"], "dry_run")
        self.assertEqual(payload["apply_gate"], gate)
        session_local.assert_not_called()

    def test_apply_scan_failure_never_opens_database(self) -> None:
        with (
            patch(
                "scripts.backfill_china_inflation_evidence.collect_inflation_release_evidence",
                side_effect=InflationEvidenceError("incomplete scan"),
            ),
            patch(
                "scripts.backfill_china_inflation_evidence.SessionLocal"
            ) as session_local,
            self.assertRaisesRegex(InflationEvidenceError, "incomplete scan"),
        ):
            main(["--apply"])

        session_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
