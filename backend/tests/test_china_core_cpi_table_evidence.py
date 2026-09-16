import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.services import china_core_cpi_table_evidence as core_table
from app.services import china_inflation_evidence as inflation_evidence
from app.services.china_core_cpi_table_evidence import (
    CORE_CPI_ARCHIVE_SHARDS,
    CORE_CPI_REQUIRED_FROM,
    CORE_CPI_TABLE_CACHE_VERSION,
    CORE_CPI_TABLE_PARSER_VERSION,
    DEFAULT_RECENT_CORE_CPI_INDEX_PAGE_COUNT,
    CoreCpiTableEvidenceError,
    collect_core_cpi_table_evidence,
    collect_recent_core_cpi_table_evidence,
    core_cpi_index_url,
    parse_core_cpi_index,
    parse_core_cpi_release,
)
from app.services.china_inflation_evidence import (
    InflationEvidenceError,
    collect_inflation_release_evidence,
    validate_inflation_evidence_rows,
)


def release(
    title: str,
    *,
    headers: list[str],
    values: list[str],
    timestamp: str | None = "2025/07/09 09:30",
    row_label: str = "其中：不包括食品和能源",
    extra_head: str = "",
) -> str:
    byline = (
        f'<div class="detail-title-des"><p>{timestamp}</p></div>'
        if timestamp is not None
        else ""
    )
    header_html = "".join(f"<th>{item}</th>" for item in ["项目", *headers])
    value_html = "".join(f"<td>{item}</td>" for item in [row_label, *values])
    return f"""
    <html><head><title>{title}</title>{extra_head}</head><body>
      <h1>{title}</h1>{byline}
      <table><tr>{header_html}</tr><tr>{value_html}</tr></table>
    </body></html>
    """


def grouped_release(
    title: str,
    *,
    month: int,
    values: tuple[str, str, str] = ("0.1", "0.7", "0.5"),
    timestamp: str = "2025/07/09 09:30",
) -> str:
    return f"""
    <html><head><title>{title}</title></head><body>
      <h1>{title}</h1>
      <div class="detail-title-des"><p>{timestamp}</p></div>
      <table>
        <tr><th rowspan="2">项目</th><th colspan="2">{month}月</th>
            <th>1—{month}月</th></tr>
        <tr><th>环比涨跌幅（%）</th><th>同比涨跌幅（%）</th>
            <th>同比涨跌幅（%）</th></tr>
        <tr><td>其中：不包括食品和能源</td>
            <td>{values[0]}</td><td>{values[1]}</td><td>{values[2]}</td></tr>
      </table>
    </body></html>
    """


def index(*items: tuple[str, str]) -> str:
    anchors = "".join(
        f'<a href="{url}" title="{title}">{title}</a>'
        for url, title in items
    )
    return f"<html><body>{anchors}</body></html>"


class FakeResponse:
    apparent_encoding = "utf-8"

    def __init__(self, status: int, *, location: str = "", text: str = ""):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class CoreCpiTableParserTests(unittest.TestCase):
    url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"

    def test_header_semantics_survive_reordered_columns_and_never_use_mom(self) -> None:
        title = "2025年6月份居民消费价格同比上涨0.1%"
        source = release(
            title,
            headers=["同比涨跌幅（%）", "环比涨跌幅（%）"],
            values=["0.7", "9.9"],
        )
        row = parse_core_cpi_release(
            source, title=title, source_url=self.url
        )
        self.assertEqual(row["date"], dt.date(2025, 6, 1))
        self.assertEqual(row["value"], 0.7)
        self.assertIn("同比涨跌幅", row["provenance_json"]["column_label"])

    def test_grouped_header_selects_monthly_yoy_not_cumulative(self) -> None:
        title = "2025年6月份居民消费价格同比上涨0.1%"
        row = parse_core_cpi_release(
            grouped_release(title, month=6, values=("8.8", "0.7", "6.6")),
            title=title,
            source_url=self.url,
        )
        self.assertEqual(row["value"], 0.7)

    def test_2018_legacy_header_selects_yoy_not_mom_or_annual(self) -> None:
        title = "2018年12月份居民消费价格同比上涨1.9%"
        url = "https://www.stats.gov.cn/sj/zxfb/202302/legacy-201812.html"
        source = release(
            title,
            headers=["环比涨跌（%）", "同比涨跌（%）", "2018年 涨跌（%）"],
            values=["0.1", "1.8", "1.9"],
            timestamp="2019/01/10 09:30",
        )

        row = parse_core_cpi_release(source, title=title, source_url=url)

        self.assertEqual(row["date"], dt.date(2018, 12, 1))
        self.assertEqual(row["value"], 1.8)
        self.assertIn("同比涨跌（%）", row["provenance_json"]["column_label"])
        validate_inflation_evidence_rows(
            {
                "CN_CORE_CPI": pd.DataFrame([row]),
                "CN_PPI": pd.DataFrame(),
            }
        )

    def test_january_table_without_cumulative_column_is_valid(self) -> None:
        title = "2025年1月份居民消费价格上涨0.5%"
        source = release(
            title,
            headers=["环比涨跌幅（%）", "同比涨跌幅（%）"],
            values=["0.7", "0.6"],
            timestamp="2025/02/09 09:30",
        )
        row = parse_core_cpi_release(
            source,
            title=title,
            source_url="https://www.stats.gov.cn/sj/zxfb/202502/january.html",
        )
        self.assertEqual(row["date"], dt.date(2025, 1, 1))
        self.assertEqual(row["value"], 0.6)

    def test_title_discovery_does_not_require_the_word_yoy(self) -> None:
        title = "2024年7月份居民消费价格上涨0.5%"
        url = "https://www.stats.gov.cn/sj/zxfb/202408/release.html"
        refs = parse_core_cpi_index(
            index((url, title)), "https://www.stats.gov.cn/sj/zxfb/"
        )
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].observation_date, dt.date(2024, 7, 1))

    def test_bad_row_or_header_is_rejected_instead_of_guessing_position(self) -> None:
        title = "2025年6月份居民消费价格上涨0.1%"
        cases = (
            release(
                title,
                headers=["环比涨跌幅（%）", "同比（%）"],
                values=["0.1", "0.7"],
            ),
            release(
                title,
                headers=["环比涨跌幅（%）", "同比涨跌幅（%）"],
                values=["0.1", "0.7"],
                row_label="不包括食品和能源",
            ),
        )
        for source in cases:
            with self.subTest(source=source), self.assertRaises(
                CoreCpiTableEvidenceError
            ):
                parse_core_cpi_release(
                    source, title=title, source_url=self.url
                )

    def test_only_visible_title_byline_time_is_accepted(self) -> None:
        title = "2025年6月份居民消费价格上涨0.1%"
        valid = release(
            title,
            headers=["同比涨跌幅（%）"],
            values=["0.7"],
            timestamp="2025年7月9日 09:31",
        )
        row = parse_core_cpi_release(valid, title=title, source_url=self.url)
        self.assertEqual(row["available_at"], dt.datetime(2025, 7, 9, 9, 31))

        cms_only = release(
            title,
            headers=["同比涨跌幅（%）"],
            values=["0.7"],
            timestamp=None,
            extra_head='<meta name="createDate" content="2025/07/09 09:30">',
        )
        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "visible exact publication minute"
        ):
            parse_core_cpi_release(cms_only, title=title, source_url=self.url)

        second_precision = release(
            title,
            headers=["同比涨跌幅（%）"],
            values=["0.7"],
            timestamp="2025/07/09 09:30:45",
        )
        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "not minute-precision"
        ):
            parse_core_cpi_release(
                second_precision, title=title, source_url=self.url
            )

    def test_publication_must_follow_the_observation_month(self) -> None:
        title = "2025年6月份居民消费价格上涨0.1%"
        same_month = release(
            title,
            headers=["同比涨跌幅（%）"],
            values=["0.7"],
            timestamp="2025/06/30 09:30",
        )

        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError,
            "does not follow its observation month",
        ):
            parse_core_cpi_release(
                same_month, title=title, source_url=self.url
            )

    def test_row_contains_versioned_transport_and_table_provenance(self) -> None:
        title = "2025年6月份居民消费价格上涨0.1%"
        row = parse_core_cpi_release(
            release(
                title,
                headers=["同比涨跌幅（%）"],
                values=["0.7"],
            ),
            title=title,
            source_url=self.url,
        )
        provenance = row["provenance_json"]
        self.assertEqual(provenance["parser_version"], CORE_CPI_TABLE_PARSER_VERSION)
        self.assertEqual(provenance["cache_version"], CORE_CPI_TABLE_CACHE_VERSION)
        self.assertEqual(
            provenance["evidence_semantics"],
            "subject_month_official_cpi_table_row",
        )
        self.assertEqual(provenance["row_label"], "其中：不包括食品和能源")
        self.assertEqual(provenance["redirect_chain"], [self.url])
        self.assertRegex(provenance["table_assertion_sha256"], r"^[0-9a-f]{64}$")

        tampered = dict(row)
        tampered_provenance = dict(provenance)
        tampered_provenance["table_assertion_sha256"] = "f" * 64
        tampered["provenance_json"] = tampered_provenance
        with self.assertRaisesRegex(
            InflationEvidenceError, "table provenance is invalid"
        ):
            validate_inflation_evidence_rows(
                {
                    "CN_CORE_CPI": pd.DataFrame([tampered]),
                    "CN_PPI": pd.DataFrame(),
                }
            )

    def test_external_or_insecure_redirect_is_rejected(self) -> None:
        for location in (
            "https://evil.example/release.html",
            "http://www.stats.gov.cn/sj/zxfb/release.html",
        ):
            with self.subTest(location=location), patch.object(
                core_table.requests,
                "get",
                return_value=FakeResponse(302, location=location),
            ):
                with self.assertRaisesRegex(
                    CoreCpiTableEvidenceError, "outside official HTTPS"
                ):
                    core_table._fetch_page(self.url)


class CoreCpiTableCollectorTests(unittest.TestCase):
    title = "2025年6月份居民消费价格上涨0.1%"
    url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"

    def test_pre_contract_title_is_filtered_before_detail_fetch(self) -> None:
        title = "2016年12月份居民消费价格同比上涨2.1%"
        url = "https://www.stats.gov.cn/sj/zxfb/201701/legacy.html"
        requested_details: list[str] = []

        def fetch_release(requested: str) -> str:
            requested_details.append(requested)
            self.fail("a pre-2017 title must be filtered before detail fetch")

        evidence = collect_core_cpi_table_evidence(
            page_count=1,
            fetch_index=lambda _: index((url, title)),
            fetch_release=fetch_release,
        )

        self.assertEqual(CORE_CPI_REQUIRED_FROM, pd.Period("2017-01", freq="M"))
        self.assertIs(
            inflation_evidence.CORE_CPI_REQUIRED_FROM,
            CORE_CPI_REQUIRED_FROM,
        )
        self.assertTrue(evidence.empty)
        self.assertEqual(requested_details, [])

    def test_first_contract_month_without_core_row_fails_the_batch(self) -> None:
        title = "2017年1月份居民消费价格同比上涨2.5%"
        url = "https://www.stats.gov.cn/sj/zxfb/201702/bad.html"
        requested_details: list[str] = []

        def fetch_release(requested: str) -> str:
            requested_details.append(requested)
            return release(
                title,
                headers=["环比涨跌幅（%）", "同比涨跌幅（%）"],
                values=["0.1", "2.5"],
                timestamp="2017/02/14 09:30",
                row_label="居民消费价格",
            )

        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "detail scan failed"
        ):
            collect_core_cpi_table_evidence(
                page_count=1,
                fetch_index=lambda _: index((url, title)),
                fetch_release=fetch_release,
            )

        self.assertEqual(requested_details, [url])

    def test_scans_all_three_archive_shards(self) -> None:
        requested: list[str] = []

        def fetch_index(url: str) -> str:
            requested.append(url)
            return index((self.url, self.title))

        evidence = collect_core_cpi_table_evidence(
            page_count=1,
            fetch_index=fetch_index,
            fetch_release=lambda _: release(
                self.title,
                headers=["同比涨跌幅（%）"],
                values=["0.7"],
            ),
        )
        self.assertEqual(
            requested,
            [
                core_cpi_index_url(0, archive_shard=shard)
                for shard in CORE_CPI_ARCHIVE_SHARDS
            ],
        )
        self.assertEqual(len(evidence), 1)

    def test_identical_duplicate_months_are_deduplicated(self) -> None:
        other = "https://www.stats.gov.cn/sj/zxfb/202507/mirror.html"
        evidence = collect_core_cpi_table_evidence(
            page_count=1,
            fetch_index=lambda _: index(
                (self.url, self.title), (other, self.title)
            ),
            fetch_release=lambda _: release(
                self.title,
                headers=["同比涨跌幅（%）"],
                values=["0.7"],
            ),
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(float(evidence.iloc[0]["value"]), 0.7)

    def test_duplicate_page_chrome_can_differ_when_table_assertion_matches(self) -> None:
        other = "https://www.stats.gov.cn/sj/zxfb/202507/mirror.html"

        def detail(url: str) -> str:
            source = release(
                self.title,
                headers=["同比涨跌幅（%）"],
                values=["0.7"],
            )
            return source if url == self.url else source + "<!-- mirror chrome -->"

        evidence = collect_core_cpi_table_evidence(
            page_count=1,
            fetch_index=lambda _: index(
                (self.url, self.title), (other, self.title)
            ),
            fetch_release=detail,
        )
        self.assertEqual(len(evidence), 1)

    def test_same_value_with_different_publication_time_fails_closed(self) -> None:
        other = "https://www.stats.gov.cn/sj/zxfb/202507/mirror.html"

        def detail(url: str) -> str:
            return release(
                self.title,
                headers=["同比涨跌幅（%）"],
                values=["0.7"],
                timestamp=(
                    "2025/07/09 09:30"
                    if url == self.url
                    else "2025/07/09 09:31"
                ),
            )

        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "non-identical core-CPI table"
        ):
            collect_core_cpi_table_evidence(
                page_count=1,
                fetch_index=lambda _: index(
                    (self.url, self.title), (other, self.title)
                ),
                fetch_release=detail,
            )

    def test_same_value_with_different_table_assertion_fails_closed(self) -> None:
        other = "https://www.stats.gov.cn/sj/zxfb/202507/mirror.html"

        def detail(url: str) -> str:
            if url == self.url:
                return release(
                    self.title,
                    headers=["同比涨跌幅（%）"],
                    values=["0.7"],
                )
            return grouped_release(
                self.title,
                month=6,
                values=("0.1", "0.7", "0.5"),
            )

        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "non-identical core-CPI table"
        ):
            collect_core_cpi_table_evidence(
                page_count=1,
                fetch_index=lambda _: index(
                    (self.url, self.title), (other, self.title)
                ),
                fetch_release=detail,
            )

    def test_same_month_conflict_fails_the_whole_batch(self) -> None:
        other = "https://www.stats.gov.cn/sj/zxfb/202507/mirror.html"

        def detail(url: str) -> str:
            return release(
                self.title,
                headers=["同比涨跌幅（%）"],
                values=["0.7" if url == self.url else "0.8"],
            )

        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "non-identical core-CPI table"
        ):
            collect_core_cpi_table_evidence(
                page_count=1,
                fetch_index=lambda _: index(
                    (self.url, self.title), (other, self.title)
                ),
                fetch_release=detail,
            )

    def test_commentary_core_conflict_cannot_break_ppi_or_enter_core(self) -> None:
        commentary_title = "国家统计局解读2025年6月份CPI和PPI数据"
        commentary_url = "https://www.stats.gov.cn/sj/sjjd/202507/release.html"
        commentary_index = index((commentary_url, commentary_title))
        commentary = f"""
        <html><head><title>{commentary_title}</title></head><body>
          <h1>{commentary_title}</h1>
          <div class="detail-title-des"><p>2025/07/09 09:30</p></div>
          <p>核心CPI同比上涨9.9%。核心CPI同比上涨8.8%。</p>
          <p>工业生产者出厂价格指数（PPI）同比下降3.6%。</p>
        </body></html>
        """
        table_index = index((self.url, self.title))
        evidence = collect_inflation_release_evidence(
            page_count=1,
            fetch_index=lambda _: commentary_index,
            fetch_release=lambda _: commentary,
            core_page_count=1,
            fetch_core_index=lambda _: table_index,
            fetch_core_release=lambda _: release(
                self.title,
                headers=["环比涨跌幅（%）", "同比涨跌幅（%）"],
                values=["8.8", "0.7"],
            ),
        )
        self.assertEqual(
            evidence["CN_CORE_CPI"][["date", "value"]].to_dict("records"),
            [{"date": dt.date(2025, 6, 1), "value": 0.7}],
        )
        self.assertEqual(
            evidence["CN_PPI"][["date", "value"]].to_dict("records"),
            [{"date": dt.date(2025, 6, 1), "value": -3.6}],
        )
        self.assertEqual(
            evidence["CN_CORE_CPI"].iloc[0]["provenance_json"]["source_kind"],
            "nbs_cpi_release_table",
        )


class RecentCoreCpiTableCollectorTests(unittest.TestCase):
    title = "2025年6月份居民消费价格上涨0.1%"
    url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"

    def test_default_recent_scan_uses_only_first_fourteen_current_shard_pages(
        self,
    ) -> None:
        requested_indexes: list[str] = []
        requested_details: list[str] = []

        def fetch_index(url: str) -> str:
            requested_indexes.append(url)
            if url == core_cpi_index_url(0, archive_shard=0):
                return index((self.url, self.title))
            return "<html></html>"

        def fetch_release(url: str) -> str:
            requested_details.append(url)
            return release(
                self.title,
                headers=["环比涨跌幅（%）", "同比涨跌幅（%）"],
                values=["8.8", "0.7"],
            )

        evidence = collect_recent_core_cpi_table_evidence(
            fetch_index=fetch_index,
            fetch_release=fetch_release,
        )

        self.assertEqual(
            requested_indexes,
            [
                core_cpi_index_url(page, archive_shard=0)
                for page in range(DEFAULT_RECENT_CORE_CPI_INDEX_PAGE_COUNT)
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
                    "value": 0.7,
                    "release_date": dt.date(2025, 7, 9),
                    "available_at": dt.datetime(2025, 7, 9, 9, 30),
                    "source_url": self.url,
                    "status": "published",
                    "formula_version": None,
                }
            ],
        )

    def test_recent_empty_index_result_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no monthly CPI releases"):
            collect_recent_core_cpi_table_evidence(
                page_count=1,
                fetch_index=lambda _: "<html></html>",
                fetch_release=lambda _: self.fail("empty discovery has no detail"),
            )

    def test_recent_matching_bad_detail_fails_closed(self) -> None:
        bad = release(
            self.title,
            headers=["环比涨跌幅（%）", "同比涨跌幅（%）"],
            values=["0.1", "0.7"],
            row_label="居民消费价格",
        )
        with self.assertRaisesRegex(
            CoreCpiTableEvidenceError, "detail scan failed"
        ):
            collect_recent_core_cpi_table_evidence(
                page_count=1,
                fetch_index=lambda _: index((self.url, self.title)),
                fetch_release=lambda _: bad,
            )


class CoreCpiTableCacheTests(unittest.TestCase):
    def test_verified_detail_cache_is_versioned_and_resumable(self) -> None:
        title = "2025年6月份居民消费价格上涨0.1%"
        url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"
        source = release(
            title,
            headers=["同比涨跌幅（%）"],
            values=["0.7"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "nbs-core-cpi-evidence-v1"
            with (
                patch.object(core_table, "_CACHE", cache_root),
                patch.object(core_table, "_last_request", 0.0),
                patch.object(core_table, "_REQUEST_INTERVAL", 0.0),
                patch.object(
                    core_table.requests,
                    "get",
                    return_value=FakeResponse(200, text=source),
                ) as get,
            ):
                first = core_table._get_detail(url)
                second = core_table._get_detail(url)

            self.assertEqual(first, second)
            self.assertEqual(get.call_count, 1)
            metadata_files = list(cache_root.glob("*.json"))
            self.assertEqual(len(metadata_files), 1)
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                metadata["cache_version"], CORE_CPI_TABLE_CACHE_VERSION
            )
            self.assertEqual(metadata["request_url"], url)
            self.assertEqual(metadata["final_url"], url)
            self.assertEqual(metadata["redirect_chain"], [url])

    def test_recent_detail_revalidates_a_cached_url(self) -> None:
        title = "2025年6月份居民消费价格上涨0.1%"
        url = "https://www.stats.gov.cn/sj/zxfb/202507/release.html"
        source = release(
            title,
            headers=["同比涨跌幅（%）"],
            values=["0.7"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "nbs-core-cpi-evidence-v1"
            with (
                patch.object(core_table, "_CACHE", cache_root),
                patch.object(core_table, "_last_request", 0.0),
                patch.object(core_table, "_REQUEST_INTERVAL", 0.0),
                patch.object(
                    core_table.requests,
                    "get",
                    return_value=FakeResponse(200, text=source),
                ) as get,
            ):
                core_table._get_detail(url)
                core_table._get_recent_detail(url)

            self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
