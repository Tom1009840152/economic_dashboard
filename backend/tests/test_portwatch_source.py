import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.fetchers import portwatch_source as source


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _row(region: str, day: str, *, shipment_gap: int = 0):
    row = {field: "1" for field in source.PORTWATCH_TRADE_FIELDS}
    row.update(
        {
            "ISO3": region,
            "country": "China" if region == "CHN" else "World",
            "date": day,
            "ObjectId": 1,
            "import_container": "2",
            "export_container": "3",
            "shipment": str(5 + shipment_gap),
            "import_container_30MA": "2",
            "export_container_30MA": "3",
            "shipment_30MA": "5",
            "shipment_30MA_yoy_doy": "0.125",
            "portcalls_container_30MA_yoy_doy": "-0.04",
            "import_container_30MA_yoy_doy": "0.05",
            "export_container_30MA_yoy_doy": "0.10",
        }
    )
    return row


class PortWatchSourceTests(unittest.TestCase):
    def tearDown(self):
        source.clear_portwatch_cache()

    def test_download_paginates_and_coerces_string_fields(self):
        rows = [
            _row("CHN", "2026-09-10"),
            _row("CHN", "2026-09-11"),
            _row("WLD", "2026-09-10"),
            _row("WLD", "2026-09-11"),
        ]
        offsets = []

        def fake_get(_url, *, params, timeout):
            self.assertEqual(timeout, 30)
            offset = params["resultOffset"]
            offsets.append(offset)
            page = rows[offset : offset + 2]
            return _Response(
                {
                    "features": [{"attributes": item} for item in page],
                    "exceededTransferLimit": offset == 0,
                }
            )

        panel = source._download_trade_panel(request_get=fake_get, page_size=2)

        self.assertEqual(offsets, [0, 2])
        self.assertEqual(len(panel), 4)
        self.assertTrue(pd.api.types.is_numeric_dtype(panel["shipment_30MA_yoy_doy"]))
        self.assertEqual(set(panel["ISO3"]), {"CHN", "WLD"})

    def test_download_supports_a_selected_country_with_world_fixed(self):
        rows = [
            _row("USA", "2026-09-10"),
            _row("USA", "2026-09-11"),
            _row("WLD", "2026-09-10"),
            _row("WLD", "2026-09-11"),
        ]
        where_clauses = []

        def fake_get(_url, *, params, timeout):
            self.assertEqual(timeout, 30)
            where_clauses.append(params["where"])
            return _Response({"features": [{"attributes": item} for item in rows]})

        panel = source._download_trade_panel(
            request_get=fake_get,
            country_code="USA",
        )

        self.assertEqual(set(panel["ISO3"]), {"USA", "WLD"})
        self.assertEqual(where_clauses, ["ISO3 IN ('USA','WLD')"])

    def test_download_supports_official_numeric_region_codes(self):
        rows = [
            _row("163", "2026-09-11"),
            _row("WLD", "2026-09-11"),
        ]

        def fake_get(_url, *, params, timeout):
            self.assertEqual(params["where"], "ISO3 IN ('163','WLD')")
            self.assertEqual(timeout, 30)
            return _Response({"features": [{"attributes": item} for item in rows]})

        panel = source._download_trade_panel(
            request_get=fake_get,
            geography_codes=("163",),
        )

        self.assertEqual(set(panel["ISO3"]), {"163", "WLD"})

    def test_real_contract_fixture_matches_expected_semantics(self):
        fixture_path = (
            Path(__file__).parent / "fixtures" / "portwatch_reg_2026_09_11.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        def fake_get(*_args, **_kwargs):
            return _Response(
                {
                    "features": [
                        {"attributes": row} for row in fixture["rows"]
                    ]
                }
            )

        panel = source._download_trade_panel(request_get=fake_get)

        self.assertEqual(set(panel["ISO3"]), {"CHN", "WLD"})
        self.assertTrue(
            (
                panel["shipment"]
                == panel["import_container"] + panel["export_container"]
            ).all()
        )
        self.assertEqual(panel["date"].max().isoformat(), "2026-09-11")

    def test_arcgis_error_fails_closed(self):
        def fake_get(*_args, **_kwargs):
            return _Response({"error": {"message": "bad query", "details": ["schema"]}})

        with self.assertRaisesRegex(RuntimeError, "bad query.*schema"):
            source._download_trade_panel(request_get=fake_get)

    def test_shipment_semantics_are_guarded(self):
        rows = [
            _row("CHN", "2026-09-11", shipment_gap=10),
            _row("WLD", "2026-09-11"),
        ]

        def fake_get(*_args, **_kwargs):
            return _Response(
                {"features": [{"attributes": item} for item in rows]}
            )

        with self.assertRaisesRegex(RuntimeError, "shipment semantics changed"):
            source._download_trade_panel(request_get=fake_get)

    def test_moving_average_shipment_semantics_are_guarded(self):
        rows = [
            _row("CHN", "2026-09-11"),
            _row("WLD", "2026-09-11"),
        ]
        rows[0]["shipment_30MA"] = "50"

        def fake_get(*_args, **_kwargs):
            return _Response({"features": [{"attributes": item} for item in rows]})

        with self.assertRaisesRegex(RuntimeError, "30MA shipment semantics changed"):
            source._download_trade_panel(request_get=fake_get)

    def test_region_date_grids_must_match(self):
        rows = [
            _row("CHN", "2026-09-10"),
            _row("WLD", "2026-09-10"),
            _row("WLD", "2026-09-11"),
        ]

        def fake_get(*_args, **_kwargs):
            return _Response({"features": [{"attributes": item} for item in rows]})

        with self.assertRaisesRegex(RuntimeError, "date grids diverged"):
            source._download_trade_panel(request_get=fake_get)

    def test_pulse_converts_ratio_to_percent_without_fake_release_time(self):
        panel = pd.DataFrame(
            [
                _row("CHN", "2026-09-11"),
                _row("WLD", "2026-09-11"),
            ]
        )
        panel["date"] = pd.to_datetime(panel["date"]).dt.date
        with patch.object(source, "fetch_portwatch_trade_panel", return_value=panel):
            frame = source._pulse_frame("PW_CHN_CNTR_SHIP_30D_YOY")

        self.assertEqual(frame.loc[0, "value"], 12.5)
        self.assertNotIn("available_at", frame.columns)
        self.assertNotIn("release_date", frame.columns)
        self.assertEqual(frame.attrs["verified_through"].isoformat(), "2026-09-11")

    def test_shared_cache_downloads_once(self):
        panel = pd.DataFrame(
            [
                _row("CHN", "2026-09-11"),
                _row("WLD", "2026-09-11"),
            ]
        )
        panel["date"] = pd.to_datetime(panel["date"]).dt.date
        source.clear_portwatch_cache()
        with patch.object(source, "_download_trade_panel", return_value=panel) as download:
            first = source.fetch_portwatch_trade_panel()
            second = source.fetch_portwatch_trade_panel()

        self.assertEqual(download.call_count, 1)
        self.assertIsNot(first, second)

    def test_shared_cache_is_isolated_by_selected_country(self):
        def panel_for(code: str) -> pd.DataFrame:
            panel = pd.DataFrame([_row(code, "2026-09-11"), _row("WLD", "2026-09-11")])
            panel["date"] = pd.to_datetime(panel["date"]).dt.date
            return panel

        source.clear_portwatch_cache()
        with patch.object(
            source,
            "_download_trade_panel",
            side_effect=lambda *, country_code: panel_for(country_code),
        ) as download:
            china = source.fetch_portwatch_trade_panel(country_code="CHN")
            usa = source.fetch_portwatch_trade_panel(country_code="USA")
            china_again = source.fetch_portwatch_trade_panel(country_code="CHN")

        self.assertEqual(download.call_count, 2)
        self.assertEqual(set(china["ISO3"]), {"CHN", "WLD"})
        self.assertEqual(set(usa["ISO3"]), {"USA", "WLD"})
        self.assertEqual(set(china_again["ISO3"]), {"CHN", "WLD"})

    def test_shared_failure_backoff_prevents_eight_identical_downloads(self):
        source.clear_portwatch_cache()
        with patch.object(
            source,
            "_download_trade_panel",
            side_effect=RuntimeError("upstream unavailable"),
        ) as download:
            with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                source.fetch_portwatch_trade_panel()
            with self.assertRaisesRegex(RuntimeError, "retry is temporarily paused"):
                source.fetch_portwatch_trade_panel()

        self.assertEqual(download.call_count, 1)

    def test_nonempty_schema_pollution_fails_closed(self):
        rows = [
            _row("CHN", "2026-09-11"),
            _row("WLD", "2026-09-11"),
        ]
        rows[0]["shipment_30MA_yoy_doy"] = "not-a-number"

        def fake_get(*_args, **_kwargs):
            return _Response({"features": [{"attributes": item} for item in rows]})

        with self.assertRaisesRegex(RuntimeError, "non-numeric shipment_30MA_yoy_doy"):
            source._download_trade_panel(request_get=fake_get)

    def test_non_finite_numeric_value_fails_closed(self):
        rows = [
            _row("CHN", "2026-09-11"),
            _row("WLD", "2026-09-11"),
        ]
        rows[0]["shipment_30MA_yoy_doy"] = "inf"

        def fake_get(*_args, **_kwargs):
            return _Response({"features": [{"attributes": item} for item in rows]})

        with self.assertRaisesRegex(RuntimeError, "non-finite shipment_30MA_yoy_doy"):
            source._download_trade_panel(request_get=fake_get)

    def test_invalid_observation_date_fails_closed(self):
        rows = [
            _row("CHN", "not-a-date"),
            _row("WLD", "2026-09-11"),
        ]

        def fake_get(*_args, **_kwargs):
            return _Response({"features": [{"attributes": item} for item in rows]})

        with self.assertRaisesRegex(RuntimeError, "invalid dates"):
            source._download_trade_panel(request_get=fake_get)

    def test_public_limited_fetchers_are_not_in_production_refresh(self):
        from app.fetchers.akshare_source import FETCHERS

        self.assertTrue(set(source.PORTWATCH_FETCHERS).isdisjoint(FETCHERS))


if __name__ == "__main__":
    unittest.main()
