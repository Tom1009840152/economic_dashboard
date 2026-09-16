import datetime as dt
import unittest
from unittest.mock import Mock, patch

from app.fetchers.china_cycle_data import (
    _gacc_catalog_entries,
    _gacc_export_yoy,
    _gacc_period_date,
    _gacc_publication_metadata,
    _gacc_release_catalog,
    _is_gacc_total_usd_title,
    _load_gacc_exports,
    _request_gacc_text,
    _valid_gacc_index_source,
    _valid_gacc_release_source,
)


# Values, title and visible publication date are copied from the GACC English
# preliminary page for November 2024.  The page exposed by the public archive
# shows a date but no verified clock time, so this fixture deliberately does
# not invent one.
NOVEMBER_2024_PAGE = """
<!doctype html>
<html>
  <head>
    <title>(1) China's Total Export &amp; Import Values, November 2024 (in USD)</title>
    <meta name="PubDate" content="2024-12-11">
  </head>
  <body>
    <div class="article-date">2024/12/11</div>
    <h1>(1) China's Total Export &amp; Import Values, November 2024 (in USD)</h1>
    <table>
      <caption>Unit: USD 100 Million</caption>
      <tr><th>Item</th><th>11</th><th>1-to-11</th><th>Month-on-Month +/- %</th><th>Year-on-Year +/- %</th><th>Year-on-Year +/- %</th></tr>
      <tr><td>Total Export &amp; Import</td><td>5,271.8</td><td>55,967.4</td><td>0.9</td><td>2.1</td><td>3.6</td></tr>
      <tr><td>Total Export</td><td>3,123.1</td><td>32,407.1</td><td>1.1</td><td>6.7</td><td>5.4</td></tr>
      <tr><td>Total Import</td><td>2,148.7</td><td>23,560.3</td><td>0.8</td><td>-3.9</td><td>1.2</td></tr>
    </table>
    <p>Export-Import Balance plus indicates export more than import and minus indicates export less than import.</p>
  </body>
</html>
"""


class GaccExportsArchiveTests(unittest.TestCase):
    def test_transport_refuses_insecure_or_foreign_redirect_before_following(self) -> None:
        for location in (
            "http://english.customs.gov.cn/insecure.html",
            "https://customs.gov.cn.evil.example/foreign.html",
        ):
            with self.subTest(location=location):
                redirect = Mock(
                    status_code=302,
                    headers={"Location": location},
                )
                with patch(
                    "app.fetchers.china_cycle_data.requests.get",
                    return_value=redirect,
                ) as request:
                    with self.assertRaisesRegex(ValueError, "outside official HTTPS"):
                        _request_gacc_text(
                            "https://english.customs.gov.cn/Statistics/Statistics"
                        )

                request.assert_called_once()
                self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_transport_rechecks_every_redirect_hop(self) -> None:
        first = Mock(
            status_code=302,
            headers={"Location": "/Statistics/next"},
        )
        second = Mock(
            status_code=302,
            headers={"Location": "https://evil.example/release.html"},
        )
        with patch(
            "app.fetchers.china_cycle_data.requests.get",
            side_effect=[first, second],
        ) as request:
            with self.assertRaisesRegex(ValueError, "outside official HTTPS"):
                _request_gacc_text(
                    "https://english.customs.gov.cn/Statistics/Statistics"
                )

        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertIs(call.kwargs["verify"], True)

    def test_transport_enforces_redirect_limit_and_tls_verification(self) -> None:
        redirect = Mock(
            status_code=302,
            headers={"Location": "/Statistics/loop"},
        )
        with patch(
            "app.fetchers.china_cycle_data.requests.get",
            return_value=redirect,
        ) as request:
            with self.assertRaisesRegex(RuntimeError, "redirect limit"):
                _request_gacc_text(
                    "https://english.customs.gov.cn/Statistics/Statistics"
                )

        self.assertEqual(request.call_count, 6)
        self.assertTrue(
            all(call.kwargs.get("verify") is True for call in request.call_args_list)
        )

    def test_real_november_page_extracts_monthly_export_yoy(self) -> None:
        title = "(1) China's Total Export & Import Values, November 2024 (in USD)"

        self.assertEqual(_gacc_period_date(title), dt.date(2024, 11, 1))
        self.assertEqual(_gacc_export_yoy(NOVEMBER_2024_PAGE, title), 6.7)
        self.assertTrue(_valid_gacc_release_source(NOVEMBER_2024_PAGE))

    def test_date_only_official_page_is_not_promoted_to_a_fake_minute(self) -> None:
        metadata = _gacc_publication_metadata(
            NOVEMBER_2024_PAGE,
            "https://english.customs.gov.cn/Statics/"
            "2ec2dd72-0e9e-40ff-9e59-16fec0393ac7.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2024, 12, 11))
        self.assertIsNone(metadata["available_at"])

    def test_exact_pubdate_minute_is_preserved_when_the_page_supplies_it(self) -> None:
        source = NOVEMBER_2024_PAGE.replace(
            'content="2024-12-11"', 'content="2024-12-11 10:05:00"'
        )

        metadata = _gacc_publication_metadata(
            source,
            "https://english.customs.gov.cn/Statics/example.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2024, 12, 11))
        self.assertEqual(metadata["available_at"], dt.datetime(2024, 12, 11, 10, 5))

    def test_conflicting_cms_clock_is_not_treated_as_release_time(self) -> None:
        source = NOVEMBER_2024_PAGE.replace(
            "</head>",
            '<meta name="createDate" content="2024-12-10 23:55:00"></head>',
        )

        metadata = _gacc_publication_metadata(
            source,
            "https://english.customs.gov.cn/Statics/example.html",
        )

        self.assertEqual(metadata["release_date"], dt.date(2024, 12, 11))
        self.assertIsNone(metadata["available_at"])

    def test_january_february_aggregate_is_not_a_monthly_observation(self) -> None:
        title = (
            "(1) China's Total Export & Import Values, "
            "January-February 2020 (in USD)"
        )
        source = """
        <html><body><h1>China's Total Export &amp; Import Values</h1>
        <table><caption>Unit: USD 100 Million</caption>
        <tr><th>Item</th><th>January-February</th><th>Year-on-Year +/- %</th></tr>
        <tr><td>Total Export</td><td>2,919.7</td><td>-17.4</td></tr>
        </table></body></html>
        """

        self.assertIsNone(_gacc_period_date(title))
        self.assertIsNone(_gacc_export_yoy(source, title))

        with (
            patch(
                "app.fetchers.china_cycle_data._gacc_release_catalog",
                return_value=(("https://english.customs.gov.cn/Statics/janfeb.html", title),),
            ),
            patch(
                "app.fetchers.china_cycle_data._get_gacc_archive_text",
                return_value=source,
            ),
        ):
            bundle = _load_gacc_exports(page_count=1)
        self.assertTrue(bundle["CN_EXPORTS"].empty)

    def test_february_title_with_only_ytd_header_is_not_monthly(self) -> None:
        title = "(1) China's Total Export & Import Values, Feb 2025 (in USD)"
        source = """
        <html><body><h1>China's Total Export &amp; Import Values</h1>
        <table><caption>Unit: USD 100 Million</caption>
        <tr><th>Item</th><th>1-to-2</th><th>Year-on-Year +/- %</th></tr>
        <tr><td>Total Export</td><td>5,399.4</td><td>2.3</td></tr>
        </table></body></html>
        """

        self.assertEqual(_gacc_period_date(title), dt.date(2025, 2, 1))
        self.assertIsNone(_gacc_export_yoy(source, title))

    def test_ambiguous_single_yoy_with_month_and_ytd_is_rejected(self) -> None:
        title = "(1) China's Total Export & Import Values, March 2025 (in USD)"
        source = """
        <html><body><table>
        <tr><th>Item</th><th>3</th><th>1-to-3</th>
            <th>Year-on-Year +/- %</th></tr>
        <tr><td>Total Export</td><td>3100</td><td>9000</td><td>7.2</td></tr>
        </table></body></html>
        """

        self.assertIsNone(_gacc_export_yoy(source, title))

    def test_real_multilevel_header_extracts_unique_monthly_yoy(self) -> None:
        title = "(1) China's Total Export & Import Values, November 2024 (in USD)"
        source = """
        <html><body><table>
        <tr><th rowspan="2">Item</th><th rowspan="2">11</th>
            <th rowspan="2">1-to-11</th><th colspan="2">11</th>
            <th>1-to-11</th></tr>
        <tr><th>Month-on-Month +/- %</th><th>Year-on-Year +/- %</th>
            <th>Year-on-Year +/- %</th></tr>
        <tr><td>Total Export</td><td>3,123.1</td><td>32,407.1</td>
            <td>1.1</td><td>6.7</td><td>5.4</td></tr>
        </table></body></html>
        """

        self.assertEqual(_gacc_export_yoy(source, title), 6.7)

    def test_catalog_raises_when_every_requested_page_fails(self) -> None:
        with patch(
            "app.fetchers.china_cycle_data._get_gacc_archive_text",
            side_effect=OSError("upstream unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "all requested"):
                _gacc_release_catalog(page_count=2, start_page=1)

    def test_index_validator_rejects_soft_error_pages(self) -> None:
        valid = (
            "<html><body><h1>China Customs Statistics</h1>"
            "<h2>Preliminary Release</h2>"
            + "<p>official archive</p>" * 20
            + "</body></html>"
        )

        self.assertTrue(_valid_gacc_index_source(valid))
        self.assertFalse(
            _valid_gacc_index_source("ACCESS DENIED by CAPTCHA" * 30)
        )

    def test_catalog_accepts_only_https_official_nationwide_usd_pages(self) -> None:
        source = """
        <html><body>
        <a href="/Statics/usd.html">(1) China's Total Export &amp; Import Values, December 2024 (in USD)</a>
        <a href="/Statics/cny.html">(1) China's Total Export &amp; Import Values, December 2024 (in CNY)</a>
        <a href="/Statics/mode.html">(2) China's Total Export &amp; Import Values by Trade Mode, December 2024 (in USD)</a>
        <a href="http://english.customs.gov.cn/Statics/http.html">(1) China's Total Export &amp; Import Values, November 2024 (in USD)</a>
        <a href="https://customs.gov.cn.evil.example/fake.html">(1) China's Total Export &amp; Import Values, October 2024 (in USD)</a>
        </body></html>
        """

        entries = _gacc_catalog_entries(
            source,
            "https://english.customs.gov.cn/Statistics/Statistics?ColumnId=1&page=1",
        )

        self.assertEqual(
            entries,
            (("https://english.customs.gov.cn/Statics/usd.html", "(1) China's Total Export & Import Values, December 2024 (in USD)"),),
        )

    def test_loader_keeps_source_and_does_not_emit_rounded_absolute_value(self) -> None:
        title = "(1) China's Total Export & Import Values, November 2024 (in USD)"
        url = "https://english.customs.gov.cn/Statics/example.html"
        source = NOVEMBER_2024_PAGE.replace(
            'content="2024-12-11"', 'content="2024-12-11 10:05:00"'
        )

        with (
            patch(
                "app.fetchers.china_cycle_data._gacc_release_catalog",
                return_value=((url, title),),
            ),
            patch(
                "app.fetchers.china_cycle_data._get_gacc_archive_text",
                return_value=source,
            ),
        ):
            bundle = _load_gacc_exports(page_count=1, start_page=1)

        self.assertEqual(set(bundle), {"CN_EXPORTS"})
        self.assertEqual(bundle["CN_EXPORTS"]["date"].tolist(), [dt.date(2024, 11, 1)])
        self.assertEqual(bundle["CN_EXPORTS"]["value"].tolist(), [6.7])
        self.assertEqual(bundle["CN_EXPORTS"]["source_url"].tolist(), [url])

    def test_title_variants_and_gateway_rejection(self) -> None:
        self.assertTrue(
            _is_gacc_total_usd_title(
                "(1) China's Total Export & Import Values, Feb 2026(in USD)"
            )
        )
        self.assertTrue(_is_gacc_total_usd_title("2024年12月全国进出口总值表（美元值）"))
        self.assertFalse(
            _is_gacc_total_usd_title(
                "(2) China's Total Export & Import Values by Trade Mode (in USD)"
            )
        )
        self.assertFalse(_valid_gacc_release_source("504 Gateway Time-out" * 30))


if __name__ == "__main__":
    unittest.main()
