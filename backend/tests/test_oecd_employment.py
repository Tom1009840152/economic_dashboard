import unittest
from unittest.mock import patch

import pandas as pd
import requests

from app.fetchers import oecd_employment


def _frame(value: float, period: str = "2026-07-01") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime([period]),
            "value": [value],
        }
    )


def _fetcher_for(
    region: str,
    *,
    failed_keys: set[str] | None = None,
):
    metadata = oecd_employment._series_meta(region)
    key_by_series_id = {
        series_id: key
        for key, (series_id, *_rest) in metadata.items()
    }
    values = {
        "unemployment": 3.0,
        "youth_unemployment": 6.0,
        "labor_participation": 80.0,
        "employment_ratio": 76.0,
        "female_participation": 75.0,
        "male_participation": 85.0,
    }
    failures = failed_keys or set()

    def fake_fetch(series_id: str) -> pd.DataFrame:
        key = key_by_series_id[series_id]
        if key in failures:
            raise requests.exceptions.ConnectionError(f"{key} unavailable")
        return _frame(values[key])

    return metadata, fake_fetch


class OECDEmploymentResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        oecd_employment._cache = {}

    def tearDown(self) -> None:
        oecd_employment._cache = {}

    def test_complete_result_builds_both_derivatives_and_is_cached(self) -> None:
        metadata, fake_fetch = _fetcher_for("JP")

        with patch.object(
            oecd_employment,
            "_cached_fred_raw",
            side_effect=fake_fetch,
        ) as fetch:
            payload = oecd_employment.fetch_oecd_employment_dashboard("JP")
            cached = oecd_employment.fetch_oecd_employment_dashboard("jp")

        keyed = {item["key"]: item for item in payload["series"]}
        self.assertEqual(payload["latest_month"], "2026-07")
        self.assertEqual(payload["warnings"], [])
        self.assertAlmostEqual(
            keyed["gender_participation_gap"]["points"][0]["value"],
            10.0,
        )
        self.assertAlmostEqual(
            keyed["implied_unemployment"]["points"][0]["value"],
            5.0,
        )
        self.assertIs(payload, cached)
        self.assertEqual(fetch.call_count, len(metadata))
        self.assertIn("JP", oecd_employment._cache)

    def test_partial_failure_keeps_successful_series_and_skips_cache(self) -> None:
        metadata, fake_fetch = _fetcher_for(
            "KR",
            failed_keys={"youth_unemployment"},
        )
        failed_series_id = metadata["youth_unemployment"][0]

        with patch.object(
            oecd_employment,
            "_cached_fred_raw",
            side_effect=fake_fetch,
        ):
            payload = oecd_employment.fetch_oecd_employment_dashboard("KR")

        keys = [item["key"] for item in payload["series"]]
        self.assertNotIn("youth_unemployment", keys)
        self.assertIn("unemployment", keys)
        self.assertIn("gender_participation_gap", keys)
        self.assertIn("implied_unemployment", keys)
        self.assertTrue(
            any(
                "15—24岁失业率" in warning
                and failed_series_id in warning
                and "缺失值未按0处理" in warning
                for warning in payload["warnings"]
            )
        )
        self.assertNotIn("KR", oecd_employment._cache)

    def test_all_failed_series_raise_value_error(self) -> None:
        with patch.object(
            oecd_employment,
            "_cached_fred_raw",
            side_effect=requests.exceptions.Timeout("FRED unavailable"),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "FRED returned no usable employment series for JP",
            ):
                oecd_employment.fetch_oecd_employment_dashboard("JP")

        self.assertNotIn("JP", oecd_employment._cache)

    def test_missing_dependency_only_suppresses_affected_derivative(self) -> None:
        _, fake_fetch = _fetcher_for(
            "KR",
            failed_keys={"male_participation"},
        )

        with patch.object(
            oecd_employment,
            "_cached_fred_raw",
            side_effect=fake_fetch,
        ):
            payload = oecd_employment.fetch_oecd_employment_dashboard("KR")

        keys = [item["key"] for item in payload["series"]]
        self.assertNotIn("gender_participation_gap", keys)
        self.assertIn("implied_unemployment", keys)
        self.assertIn("female_participation", keys)
        self.assertNotIn("male_participation", keys)
        self.assertTrue(any("男性劳动参与率" in item for item in payload["warnings"]))
        self.assertNotIn("KR", oecd_employment._cache)


if __name__ == "__main__":
    unittest.main()
