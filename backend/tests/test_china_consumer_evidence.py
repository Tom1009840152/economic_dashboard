import datetime as dt
import json
import unittest

from app.services.china_consumer_evidence import (
    collect_consumer_release_evidence,
    consumer_index_url,
    parse_consumer_index,
    parse_consumer_release,
    parse_consumer_release_history,
)


INDEX_URL = (
    "https://www.cei.cn/defaultsite/s/column/"
    "4028c7ca-37115425-0137-115646c5-00ec_2026.html?"
    "articleListType=1&coluOpenType=1"
)
ARTICLE_URL = (
    "https://www.cei.cn/defaultsite/s/article/2026/02/02/"
    "consumer_2026.html?columnId=4028c7ca-37115425-0137-115646c5-00ec"
)


def article_html(
    *,
    title: str = "消费者信心指数（2025年12月）",
    release_date: str = "2026-02-02",
    source: str = "国家统计局",
    title_row: tuple[str, str, str, str] = ("2025.12", "90.5", "87.9", "89.5"),
    old_row: tuple[str, str, str, str] = ("2025.11", "150", "150", "150"),
    second_table: bool = False,
) -> str:
    extra = (
        "<table><tr><td>日期</td><td>消费者预期指数</td>"
        "<td>消费者满意指数</td><td>消费者信心指数</td></tr>"
        "<tr><td>2020.01</td><td>100</td><td>100</td><td>100</td></tr></table>"
        if second_table
        else ""
    )
    rows = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in (old_row, title_row)
    )
    return f"""
    <html><body>
      <div class="xx_con_tile"><h2>{title}<span class="t_l">
        时间：{release_date} 来源：{source}
      </span></h2></div>
      <div id="content"><table>
        <tr><td>日期</td><td>消费者预期指数</td>
            <td>消费者满意指数</td><td>消费者信心指数</td></tr>
        {rows}
      </table>{extra}</div>
    </body></html>
    """


class ChinaConsumerEvidenceParserTests(unittest.TestCase):
    def test_index_parser_accepts_only_exact_consumer_titles(self) -> None:
        source = """
        <ul>
          <li><a href="//www.cei.cn:443//defaultsite/s/article/a.html"
                 title="消费者信心指数（2025年12月）">消费者信心指数</a>
              <em>2026-02-02</em></li>
          <li><a href="https://evil.example/a">消费者价格指数（2025年12月）</a>
              <em>2026-02-02</em></li>
        </ul>
        """
        refs = parse_consumer_index(source, INDEX_URL)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].observation_date, dt.date(2025, 12, 1))
        self.assertEqual(refs[0].release_date, dt.date(2026, 2, 2))
        self.assertEqual(
            refs[0].source_url,
            "https://www.cei.cn/defaultsite/s/article/a.html",
        )

    def test_release_uses_only_title_month_not_rolling_history(self) -> None:
        parsed = parse_consumer_release(article_html(), ARTICLE_URL)

        expectations = parsed["CN_CONSUMER_EXPECTATIONS"].iloc[0]
        satisfaction = parsed["CN_CONSUMER_SATISFACTION"].iloc[0]
        confidence = parsed["CN_CONSUMER_CONFIDENCE"].iloc[0]
        self.assertEqual(expectations["date"], dt.date(2025, 12, 1))
        self.assertEqual(expectations["value"], 90.5)
        self.assertEqual(satisfaction["value"], 87.9)
        self.assertEqual(confidence["value"], 89.5)
        self.assertEqual(confidence["release_date"], dt.date(2026, 2, 2))
        self.assertEqual(
            confidence["available_at"], dt.datetime(2026, 2, 3, 0, 0)
        )
        self.assertEqual(
            confidence["evidence_kind"], "official_distribution_mirror"
        )
        self.assertTrue(confidence["chain_verified"])
        self.assertEqual(confidence["availability_precision"], "date_upper_bound")
        provenance = json.loads(confidence["provenance_json"])
        self.assertFalse(provenance["direct_official"])
        self.assertEqual(provenance["row_policy"], "article_title_month_only")

    def test_split_date_cell_is_normalized_before_matching(self) -> None:
        source = article_html(
            title="消费者信心指数（2018年3月）",
            release_date="2018-05-10",
            title_row=("2018.0 <span>3</span>", "120", "110", "116"),
            old_row=("2018.02", "120", "110", "116"),
        )
        parsed = parse_consumer_release(source, ARTICLE_URL)
        self.assertEqual(
            parsed["CN_CONSUMER_CONFIDENCE"].iloc[0]["date"],
            dt.date(2018, 3, 1),
        )

    def test_spoofed_domain_or_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS cei.cn"):
            parse_consumer_release(
                article_html(), "https://cei.cn.evil.example/article.html"
            )
        with self.assertRaisesRegex(ValueError, "source must be"):
            parse_consumer_release(
                article_html(source="国家统计局转载"), ARTICLE_URL
            )

    def test_title_month_and_unique_four_column_table_are_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "title month"):
            parse_consumer_release(
                article_html().replace(
                    "消费者信心指数（2025年12月）",
                    "消费者信心指数（2025年11月）",
                    1,
                ),
                ARTICLE_URL,
                expected_observation=dt.date(2025, 12, 1),
            )
        with self.assertRaisesRegex(ValueError, "one four-column"):
            parse_consumer_release(
                article_html(second_table=True), ARTICLE_URL
            )
        future_row = article_html().replace(
            "</table>",
            "<tr><td>2026.01</td><td>92.1</td><td>88.4</td>"
            "<td>90.6</td></tr></table>",
            1,
        )
        with self.assertRaisesRegex(ValueError, "latest month"):
            parse_consumer_release(future_row, ARTICLE_URL)

    def test_out_of_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 200"):
            parse_consumer_release(
                article_html(
                    title_row=("2025.12", "201", "87.9", "89.5")
                ),
                ARTICLE_URL,
            )

    def test_weight_relationship_is_diagnostic_and_later_revision_is_retained(self) -> None:
        first_title = article_html(
            title="消费者信心指数（2019年4月）",
            release_date="2019-06-13",
            title_row=("2019.04", "128.7", "125.3", "125.3"),
            old_row=("2019.03", "124", "120", "122.4"),
        )
        first = parse_consumer_release(first_title, ARTICLE_URL)
        first_row = first["CN_CONSUMER_CONFIDENCE"].iloc[0]
        diagnostic = json.loads(first_row["provenance_json"])[
            "weighted_relationship_diagnostic"
        ]
        self.assertFalse(diagnostic["blocking"])
        self.assertEqual(
            diagnostic["residual_60pct_expectations_40pct_satisfaction"],
            "2.04",
        )

        corrected_later = article_html(
            title="消费者信心指数（2019年5月）",
            release_date="2019-07-15",
            old_row=("2019.04", "128.7", "120.3", "125.3"),
            title_row=("2019.05", "125", "120", "123"),
        )
        history = parse_consumer_release_history(corrected_later, ARTICLE_URL)
        april = history["CN_CONSUMER_CONFIDENCE"].loc[
            history["CN_CONSUMER_CONFIDENCE"]["date"]
            == dt.date(2019, 4, 1)
        ].iloc[0]
        self.assertEqual(april["available_at"], dt.datetime(2019, 7, 16))
        self.assertEqual(
            json.loads(april["provenance_json"])["row_policy"],
            "rolling_table_later_confirmation",
        )

    def test_official_row_is_not_rejected_by_unpublished_fixed_weights(self) -> None:
        parsed = parse_consumer_release(
            article_html(
                title="消费者信心指数（2022年3月）",
                release_date="2022-06-06",
                title_row=("2022.03", "116.2", "110.4", "113.2"),
                old_row=("2022.02", "120", "118", "119"),
            ),
            ARTICLE_URL,
        )
        row = parsed["CN_CONSUMER_CONFIDENCE"].iloc[0]
        diagnostic = json.loads(row["provenance_json"])[
            "weighted_relationship_diagnostic"
        ]

        self.assertEqual(row["value"], 113.2)
        self.assertEqual(
            diagnostic["residual_60pct_expectations_40pct_satisfaction"],
            "0.68",
        )
        self.assertEqual(
            diagnostic["residual_40pct_expectations_60pct_satisfaction"],
            "0.48",
        )

    def test_collector_unions_complementary_www_and_ibe_indexes(self) -> None:
        www_article = ARTICLE_URL.replace("consumer_2026", "www_article")
        ibe_article = ARTICLE_URL.replace("www.cei.cn", "ibe.cei.cn").replace(
            "consumer_2026", "ibe_article"
        )
        sources = {
            consumer_index_url(2026, host="www.cei.cn"): f"""
                <ul><li><a href="{www_article}"
                title="消费者信心指数（2025年12月）">a</a>
                <em>2026-02-02</em></li></ul>""",
            consumer_index_url(2026, host="ibe.cei.cn"): f"""
                <ul><li><a href="{ibe_article}"
                title="消费者信心指数（2026年1月）">b</a>
                <em>2026-03-09</em></li></ul>""",
            www_article: article_html(
                old_row=("2015.11", "150", "150", "150")
            ),
            ibe_article: article_html(
                title="消费者信心指数（2026年1月）",
                release_date="2026-03-09",
                title_row=("2026.01", "92.1", "88.4", "90.6"),
                old_row=("2015.11", "150", "150", "150"),
            ),
        }

        evidence = collect_consumer_release_evidence(
            start_year=2026,
            end_year=2026,
            fetch_text=lambda url: sources[url],
        )
        self.assertEqual(
            list(evidence["CN_CONSUMER_CONFIDENCE"]["date"]),
            [dt.date(2025, 12, 1), dt.date(2026, 1, 1)],
        )

    def test_collector_validates_duplicate_index_urls_before_deduplication(self) -> None:
        public_article = ARTICLE_URL.replace("consumer_2026", "public_uuid")
        legacy_article = ARTICLE_URL.replace("www.cei.cn", "ibe.cei.cn").replace(
            "consumer_2026", "login_uuid"
        )
        public_index = consumer_index_url(2026, host="www.cei.cn")
        legacy_index = consumer_index_url(2026, host="ibe.cei.cn")
        sources = {
            public_index: f"""
                <ul><li><a href="{public_article}"
                title="消费者信心指数（2025年12月）">a</a>
                <em>2026-02-02</em></li></ul>""",
            legacy_index: f"""
                <ul><li><a href="{legacy_article}"
                title="消费者信心指数（2025年12月）">a</a>
                <em>2026-02-02</em></li></ul>""",
            public_article: article_html(
                old_row=("2015.11", "150", "150", "150")
            ),
            legacy_article: "<html><body>login required</body></html>",
        }

        evidence = collect_consumer_release_evidence(
            start_year=2026,
            end_year=2026,
            fetch_text=lambda url: sources[url],
        )

        self.assertEqual(len(evidence["CN_CONSUMER_CONFIDENCE"]), 1)
        self.assertEqual(
            evidence["CN_CONSUMER_CONFIDENCE"].iloc[0]["source_url"],
            public_article,
        )
        mismatched = parse_consumer_release(
            article_html(title_row=("2025.12", "90.5", "87.9", "120")),
            ARTICLE_URL,
        )
        self.assertEqual(mismatched["CN_CONSUMER_CONFIDENCE"].iloc[0]["value"], 120)


if __name__ == "__main__":
    unittest.main()
