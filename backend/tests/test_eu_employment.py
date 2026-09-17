import unittest
from unittest.mock import patch

import requests

from app.fetchers import eu_employment


class EUEmploymentResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        eu_employment._cache = None

    def tearDown(self) -> None:
        eu_employment._cache = None

    def test_one_failed_optional_series_does_not_fail_dashboard(self) -> None:
        metadata = {
            "unemployment": (
                "monthly",
                {},
                "失业率",
                "%",
                "月度",
                "test",
                "test scope",
            ),
            "youth_unemployment": (
                "youth",
                {},
                "青年失业率",
                "%",
                "月度",
                "test",
                "test scope",
            ),
        }

        def fake_fetch(dataset: str, params: dict[str, str]) -> list[dict]:
            del params
            if dataset == "youth":
                raise requests.exceptions.SSLError("temporary EOF")
            return [{"period": "2026-07", "value": 6.4}]

        with (
            patch.object(eu_employment, "SERIES_META", metadata),
            patch.object(eu_employment, "_fetch_eurostat", side_effect=fake_fetch),
        ):
            payload = eu_employment.fetch_eu_employment_dashboard()

        self.assertEqual(payload["latest_month"], "2026-07")
        self.assertEqual([item["key"] for item in payload["series"]], ["unemployment"])
        self.assertTrue(any("青年失业率" in warning for warning in payload["warnings"]))
        self.assertIsNone(eu_employment._cache)


if __name__ == "__main__":
    unittest.main()
