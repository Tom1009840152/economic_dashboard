import unittest
from unittest.mock import Mock, patch

import requests

from app.fetchers import uk_employment


class UKEmploymentResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        uk_employment._cache = None

    def tearDown(self) -> None:
        uk_employment._cache = None

    def test_ons_request_retries_at_most_three_times(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "months": [{"year": "2026", "date": "2026 AUG", "value": "4.2"}]
        }

        with (
            patch.object(
                uk_employment.requests,
                "get",
                side_effect=[
                    requests.exceptions.Timeout("first"),
                    requests.exceptions.ConnectionError("second"),
                    response,
                ],
            ) as get,
            patch.object(uk_employment.time, "sleep") as sleep,
        ):
            points = uk_employment._fetch_ons_points("/test")

        self.assertEqual(points, [{"period": "2026-08", "value": 4.2}])
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        with (
            patch.object(
                uk_employment.requests,
                "get",
                side_effect=requests.exceptions.Timeout("still unavailable"),
            ) as get,
            patch.object(uk_employment.time, "sleep"),
        ):
            with self.assertRaises(requests.exceptions.Timeout):
                uk_employment._fetch_ons_points("/test")

        self.assertEqual(get.call_count, 3)

    def test_partial_failure_keeps_available_series_without_derivatives_or_cache(self) -> None:
        metadata = {
            "unemployment": ("unemployment", "失业率", "test scope"),
            "labor_participation": ("participation", "劳动参与率", "test scope"),
            "employment_ratio": ("employment", "就业率", "test scope"),
            "female_employment": ("female", "女性就业率", "test scope"),
            "male_employment": ("male", "男性就业率", "test scope"),
        }

        def fake_fetch(path: str) -> list[dict]:
            if path in {"employment", "male"}:
                raise requests.exceptions.SSLError("temporary EOF")
            return [{"period": "2026-07", "value": 70.0}]

        with (
            patch.object(uk_employment, "SERIES_META", metadata),
            patch.object(uk_employment, "_fetch_ons_points", side_effect=fake_fetch),
        ):
            payload = uk_employment.fetch_uk_employment_dashboard()

        keys = [item["key"] for item in payload["series"]]
        self.assertEqual(
            keys,
            ["unemployment", "labor_participation", "female_employment"],
        )
        self.assertNotIn("gender_employment_gap", keys)
        self.assertNotIn("implied_unemployment", keys)
        self.assertTrue(
            any(
                "就业率、男性就业率" in warning and "缺失值未按0处理" in warning
                for warning in payload["warnings"]
            )
        )
        self.assertIsNone(uk_employment._cache)

    def test_all_failed_series_raise_value_error(self) -> None:
        metadata = {
            "unemployment": ("unemployment", "失业率", "test scope"),
            "youth_unemployment": ("youth", "青年失业率", "test scope"),
        }

        with (
            patch.object(uk_employment, "SERIES_META", metadata),
            patch.object(
                uk_employment,
                "_fetch_ons_points",
                side_effect=requests.exceptions.ConnectionError("offline"),
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "ONS returned no usable employment series",
            ):
                uk_employment.fetch_uk_employment_dashboard()

        self.assertIsNone(uk_employment._cache)

    def test_complete_result_builds_derivatives_and_is_cached(self) -> None:
        metadata = {
            "unemployment": ("unemployment", "失业率", "test scope"),
            "labor_participation": ("participation", "劳动参与率", "test scope"),
            "employment_ratio": ("employment", "就业率", "test scope"),
            "female_employment": ("female", "女性就业率", "test scope"),
            "male_employment": ("male", "男性就业率", "test scope"),
        }
        values = {
            "unemployment": 4.0,
            "participation": 80.0,
            "employment": 76.0,
            "female": 72.0,
            "male": 78.0,
        }

        def fake_fetch(path: str) -> list[dict]:
            return [{"period": "2026-07", "value": values[path]}]

        with (
            patch.object(uk_employment, "SERIES_META", metadata),
            patch.object(
                uk_employment,
                "_fetch_ons_points",
                side_effect=fake_fetch,
            ) as fetch,
        ):
            payload = uk_employment.fetch_uk_employment_dashboard()
            cached = uk_employment.fetch_uk_employment_dashboard()

        keyed = {item["key"]: item for item in payload["series"]}
        self.assertAlmostEqual(
            keyed["gender_employment_gap"]["points"][0]["value"],
            6.0,
        )
        self.assertAlmostEqual(
            keyed["implied_unemployment"]["points"][0]["value"],
            5.0,
        )
        self.assertIs(payload, cached)
        self.assertEqual(fetch.call_count, len(metadata))
        self.assertIsNotNone(uk_employment._cache)


if __name__ == "__main__":
    unittest.main()
