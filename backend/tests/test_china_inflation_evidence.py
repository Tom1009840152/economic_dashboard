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
    INFLATION_EVIDENCE_CODES,
    InflationCoverageError,
    InflationEvidenceError,
    collect_inflation_release_evidence,
    _get_nbs_inflation_archive_text,
    _request_nbs_inflation_text,
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
    def test_existing_core_cpi_fetcher_uses_the_strict_parser(self) -> None:
        title = "国家统计局解读2025年6月份CPI和PPI数据"
        source = article(
            title,
            """
            <h2>核心CPI同比继续回升</h2>
            <p>CPI同比上涨0.1%。</p>
            <p>扣除食品和能源价格的核心CPI同比上涨0.7%。</p>
            """,
        )
        with (
            patch.object(
                nbs_cycle,
                "_links",
                return_value=[("https://www.stats.gov.cn/release.html", title)],
            ),
            patch.object(nbs_cycle, "_get_text", return_value=source),
        ):
            frame = nbs_cycle._load_core_cpi()
        self.assertEqual(frame.to_dict("records"), [{"date": dt.date(2025, 6, 1), "value": 0.7}])

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

    def test_collector_uses_shared_article_once_for_both_series(self) -> None:
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
        )
        self.assertEqual(len(evidence["CN_CORE_CPI"]), 1)
        self.assertEqual(len(evidence["CN_PPI"]), 1)

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
            collect_inflation_release_evidence(page_count=1)
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
                        "parser_version": "nbs_inflation_release_v1",
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
