import datetime as dt
import json
import unittest
from unittest.mock import Mock, patch

import requests

from app.fetchers import bok_source


def _history(count: int = 20) -> tuple[list[list[object]], list[list[str]]]:
    rows: list[list[object]] = []
    labels: list[list[str]] = []
    for index in range(count):
        year = 2008 + index // 12
        month = index % 12 + 1
        rows.append(
            [
                f"{year:04d}/{month:02d}/07 ",
                round(5.25 - index * 0.1, 2),
            ]
        )
        labels.append([f"[{year:04d}.{month:02d}]"])
    return rows, labels


def _page(
    rows: list[list[object]],
    labels: list[list[str]],
    *,
    extra_script: str = "",
) -> str:
    return f"""
    <html><body>
      <script>
        /* var chartObj2_s = [[\"1999/01/01\", 99.0]]; */
        var chartObj2_s = {json.dumps(rows)};
        /*
          var chartObj2_s = [[\"2000/01/01\", 88.0]];
          var chartObj2Labels = [[\"[2000.01]\"]];
        */
        var chartObj2Labels = {json.dumps(labels)};
        {extra_script}
      </script>
    </body></html>
    """


class BOKBaseRateParserTests(unittest.TestCase):
    def test_strips_unlabeled_same_rate_verification_row(self) -> None:
        rows, labels = _history()
        latest_rate = rows[-1][1]
        rows.append(["2026/09/17", latest_rate])

        frame = bok_source.parse_bok_base_rate_html(_page(rows, labels))

        self.assertEqual(len(frame), 20)
        self.assertEqual(frame.iloc[-1]["date"], dt.date(2009, 8, 7))
        self.assertEqual(frame.iloc[-1]["value"], latest_rate)
        self.assertEqual(frame.attrs["verified_through"], dt.date(2026, 9, 17))
        self.assertEqual(frame.attrs["latest_event_date"], dt.date(2009, 8, 7))
        self.assertTrue(frame.attrs["verification_extension_removed"])
        self.assertEqual(frame.attrs["source_url"], bok_source.BOK_BASE_RATE_URL)
        self.assertEqual(
            frame["source_url"].unique().tolist(),
            [bok_source.BOK_BASE_RATE_URL],
        )

    def test_keeps_fully_labeled_final_event(self) -> None:
        rows, labels = _history()

        frame = bok_source.parse_bok_base_rate_html(_page(rows, labels))

        self.assertEqual(len(frame), 20)
        self.assertEqual(frame.attrs["verified_through"], dt.date(2009, 8, 7))
        self.assertFalse(frame.attrs["verification_extension_removed"])

    def test_comment_markers_inside_strings_do_not_hide_active_data(self) -> None:
        rows, labels = _history()
        page = _page(
            rows,
            labels,
            extra_script='var harmless = "https://example.test/*not-comment*/";',
        )

        frame = bok_source.parse_bok_base_rate_html(page)

        self.assertEqual(len(frame), 20)

    def test_rejects_unlabeled_rate_change(self) -> None:
        rows, labels = _history()
        rows.append(["2026/09/17", float(rows[-1][1]) + 0.25])

        with self.assertRaisesRegex(ValueError, "unlabeled final row changes"):
            bok_source.parse_bok_base_rate_html(_page(rows, labels))

    def test_rejects_duplicate_active_series_declarations(self) -> None:
        rows, labels = _history()
        duplicate = f"var chartObj2_s = {json.dumps(rows)};"

        with self.assertRaisesRegex(ValueError, "exactly one active chartObj2_s"):
            bok_source.parse_bok_base_rate_html(
                _page(rows, labels, extra_script=duplicate)
            )

    def test_rejects_non_ascending_dates(self) -> None:
        rows, labels = _history()
        rows[10][0] = rows[9][0]

        with self.assertRaisesRegex(ValueError, "not strictly ascending"):
            bok_source.parse_bok_base_rate_html(_page(rows, labels))

    def test_rejects_rate_outside_sanity_bounds(self) -> None:
        rows, labels = _history()
        rows[10][1] = 99.0

        with self.assertRaisesRegex(ValueError, "outside the valid rate range"):
            bok_source.parse_bok_base_rate_html(_page(rows, labels))

    def test_rejects_too_few_events(self) -> None:
        rows, labels = _history(19)

        with self.assertRaisesRegex(ValueError, "fewer than 20 policy events"):
            bok_source.parse_bok_base_rate_html(_page(rows, labels))


class BOKBaseRateFetcherTests(unittest.TestCase):
    def test_fetches_official_page_with_bounded_timeout(self) -> None:
        rows, labels = _history()
        response = Mock()
        response.text = _page(rows, labels)
        response.raise_for_status.return_value = None

        with patch.object(bok_source._SESSION, "get", return_value=response) as get:
            frame = bok_source.fetch_bok_base_rate()

        get.assert_called_once_with(
            bok_source.BOK_BASE_RATE_URL,
            headers=bok_source._HEADERS,
            timeout=45,
        )
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(len(frame), 20)

    def test_retries_a_transient_truncated_response(self) -> None:
        rows, labels = _history()
        response = Mock()
        response.text = _page(rows, labels)
        response.raise_for_status.return_value = None
        transient = requests.exceptions.ChunkedEncodingError("truncated")

        with patch.object(
            bok_source._SESSION,
            "get",
            side_effect=[transient, response],
        ) as get:
            frame = bok_source.fetch_bok_base_rate()

        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(frame), 20)


if __name__ == "__main__":
    unittest.main()
