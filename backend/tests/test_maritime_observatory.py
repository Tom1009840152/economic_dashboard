import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from app.schemas import MaritimeComparisonOut, MaritimeObservatoryOut
from app.services import maritime_observatory as observatory
from app.services.maritime_observatory import (
    _build_maritime_comparison,
    _build_maritime_observatory,
    _chokepoints,
)


def _panel(country_code: str = "CHN", country_name: str = "China") -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", "2026-09-11", freq="D")
    rows = []
    for region in (country_code, "WLD"):
        for timestamp in dates:
            lift = 1.10 if timestamp >= pd.Timestamp("2026-08-15") else 1.0
            base = 1_000_000 * lift
            row = {
                "ISO3": region,
                "country": country_name if region == country_code else "World",
                "date": timestamp.date(),
                "portcalls_container": 100 * lift,
                "portcalls_dry_bulk": 50,
                "portcalls_general_cargo": 25,
                "portcalls_roro": 10,
                "portcalls_tanker": 40,
                "portcalls_cargo": 185,
                "portcalls": 225,
                "import_container": base,
                "import_dry_bulk": base * 2,
                "import_general_cargo": base * 0.2,
                "import_roro": base * 0.1,
                "import_tanker": base * 1.5,
                "import_cargo": base * 3.3,
                "import": base * 4.8,
                "export_container": base * 1.2,
                "export_dry_bulk": base * 1.8,
                "export_general_cargo": base * 0.25,
                "export_roro": base * 0.1,
                "export_tanker": base * 1.0,
                "export_cargo": base * 3.35,
                "export": base * 4.35,
            }
            row["shipment"] = row["import_container"] + row["export_container"]
            rows.append(row)
    return pd.DataFrame(rows)


class MaritimeObservatoryTests(unittest.TestCase):
    def tearDown(self):
        observatory.clear_maritime_observatory_cache()

    def test_dashboard_keeps_quantity_layers_and_proxy_labels_honest(self):
        current = {
            key: {"calls_sum": 2800, "capacity_sum": 280_000_000, "days_count": 28}
            for key in (
                "chokepoint1",
                "chokepoint2",
                "chokepoint4",
                "chokepoint5",
                "chokepoint6",
                "chokepoint7",
            )
        }
        previous = {
            key: {"calls_sum": 2500, "capacity_sum": 250_000_000, "days_count": 28}
            for key in current
        }

        dashboard = _build_maritime_observatory(
            _panel(),
            chokepoint_current=current,
            chokepoint_previous=previous,
            today=date(2026, 9, 18),
        )
        validated = MaritimeObservatoryOut.model_validate(dashboard)
        metrics = {metric.key: metric for metric in validated.metrics}

        self.assertEqual(validated.status, "ok")
        self.assertEqual(validated.as_of, "2026-09-11")
        self.assertAlmostEqual(metrics["world_container_flow"].yoy_pct, 10.0, places=1)
        self.assertAlmostEqual(metrics["country_all_exports"].yoy_pct, 10.0, places=1)
        self.assertIn("集装箱", metrics["world_container_flow"].label)
        self.assertIn("代理", metrics["country_bulk_energy_inputs"].label)
        self.assertEqual(validated.selected_country_code, "CHN")
        self.assertEqual(validated.selected_country_name, "中国")
        self.assertGreater(len(validated.trend), 180)
        self.assertFalse(validated.realtime_ready)
        self.assertEqual(
            next(layer for layer in validated.layers if layer.key == "price").status,
            "planned",
        )
        self.assertEqual(len(validated.chokepoints), 6)

    def test_missing_chokepoints_degrades_without_erasing_quantity(self):
        dashboard = _build_maritime_observatory(
            _panel(),
            today=date(2026, 9, 18),
            chokepoint_error="temporary source error",
        )

        self.assertEqual(dashboard["status"], "partial")
        self.assertTrue(dashboard["metrics"])
        self.assertFalse(dashboard["chokepoints"])
        self.assertTrue(any("temporary source error" in warning for warning in dashboard["warnings"]))

    def test_selected_country_changes_labels_but_keeps_global_baseline(self):
        dashboard = _build_maritime_observatory(
            _panel("USA", "United States"),
            country_code="USA",
            today=date(2026, 9, 18),
            chokepoint_error="optional layer unavailable",
        )
        metrics = {metric["key"]: metric for metric in dashboard["metrics"]}

        self.assertEqual(dashboard["selected_country_name"], "美国")
        self.assertIn("美国", metrics["country_all_exports"]["label"])
        self.assertIn("全球", metrics["world_container_flow"]["label"])
        self.assertEqual(dashboard["source_history_start"], "2025-01-01")

    def test_multi_geography_comparison_keeps_one_global_baseline(self):
        panel = pd.concat(
            [
                _panel(),
                _panel("USA", "United States").loc[lambda frame: frame["ISO3"] == "USA"],
            ],
            ignore_index=True,
        )

        comparison = MaritimeComparisonOut.model_validate(
            _build_maritime_comparison(
                panel,
                ("CHN", "USA"),
                today=date(2026, 9, 18),
            )
        )

        self.assertEqual(comparison.status, "ok")
        self.assertEqual([item.code for item in comparison.series], ["CHN", "USA"])
        self.assertEqual(len(comparison.global_trend), len(comparison.series[0].points))
        self.assertGreater(len(comparison.global_trend), 180)
        self.assertEqual(comparison.series[1].name, "美国")

    def test_null_chokepoint_aggregates_remain_missing_instead_of_zero(self):
        current = {
            "chokepoint1": {"calls_sum": None, "capacity_sum": None, "days_count": 28},
        }
        previous = {
            "chokepoint1": {"calls_sum": 100, "capacity_sum": 10_000, "days_count": 28},
        }

        rows = _chokepoints(current, previous)
        suez = next(row for row in rows if row["key"] == "chokepoint1")

        self.assertIsNone(suez["current_daily_calls"])
        self.assertIsNone(suez["current_daily_capacity_mn_t"])
        self.assertIsNone(suez["yoy_pct"])
        self.assertEqual(suez["state"], "unavailable")

    def test_partial_chokepoint_window_is_not_divided_by_full_window(self):
        current = {
            "chokepoint1": {"calls_sum": 140, "capacity_sum": 14_000, "days_count": 14},
        }
        previous = {
            "chokepoint1": {"calls_sum": 280, "capacity_sum": 28_000, "days_count": 28},
        }

        rows = _chokepoints(current, previous)
        suez = next(row for row in rows if row["key"] == "chokepoint1")

        self.assertIsNone(suez["current_daily_calls"])
        self.assertIsNone(suez["current_daily_capacity_mn_t"])
        self.assertIsNone(suez["yoy_pct"])
        self.assertEqual(suez["coverage"], 0.5)
        self.assertEqual(suez["state"], "unavailable")

    def test_unmatched_chokepoint_ids_do_not_mark_layer_available(self):
        dashboard = _build_maritime_observatory(
            _panel(),
            chokepoint_current={"new-id": {"calls_sum": 100, "capacity_sum": 10_000, "days_count": 28}},
            chokepoint_previous={"new-id": {"calls_sum": 90, "capacity_sum": 9_000, "days_count": 28}},
            today=date(2026, 9, 18),
        )

        congestion = next(layer for layer in dashboard["layers"] if layer["key"] == "congestion")
        self.assertEqual(dashboard["status"], "partial")
        self.assertEqual(congestion["status"], "planned")
        self.assertTrue(all(row["state"] == "unavailable" for row in dashboard["chokepoints"]))

    def test_empty_quantity_windows_do_not_report_available_layer(self):
        panel = _panel()
        quantity_fields = [
            "portcalls_container",
            "import_container",
            "import_dry_bulk",
            "import_general_cargo",
            "import_roro",
            "import_tanker",
            "export_container",
            "export_dry_bulk",
            "export_general_cargo",
            "export_roro",
            "export_tanker",
            "export",
        ]
        panel.loc[:, quantity_fields] = float("nan")
        current = {
            key: {"calls_sum": 2800, "capacity_sum": 280_000_000, "days_count": 28}
            for key in (
                "chokepoint1",
                "chokepoint2",
                "chokepoint4",
                "chokepoint5",
                "chokepoint6",
                "chokepoint7",
            )
        }

        dashboard = _build_maritime_observatory(
            panel,
            chokepoint_current=current,
            chokepoint_previous=current,
            today=date(2026, 9, 18),
        )
        quantity = next(layer for layer in dashboard["layers"] if layer["key"] == "quantity")

        self.assertEqual(dashboard["status"], "partial")
        self.assertEqual(quantity["status"], "pilot")
        self.assertTrue(all(metric["value"] is None for metric in dashboard["metrics"]))

    def test_incomplete_vessel_mix_does_not_renormalize_remaining_shares(self):
        panel = _panel()
        panel.loc[:, ["import_roro", "export_roro"]] = float("nan")

        dashboard = _build_maritime_observatory(
            panel,
            today=date(2026, 9, 18),
            chokepoint_error="optional layer unavailable",
        )

        self.assertTrue(all(row["share_pct"] is None for row in dashboard["vessel_mix"]))
        self.assertTrue(any("不归一化" in warning for warning in dashboard["warnings"]))

    def test_future_source_date_is_not_marked_current(self):
        dashboard = _build_maritime_observatory(
            _panel(),
            today=date(2026, 9, 10),
            chokepoint_error="optional layer unavailable",
        )

        self.assertEqual(dashboard["freshness"], "stale")
        self.assertEqual(dashboard["status"], "partial")

    def test_dashboard_cache_is_shared_but_callers_receive_isolated_copies(self):
        payload = _build_maritime_observatory(
            _panel(),
            today=date(2026, 9, 18),
            chokepoint_error="optional layer unavailable",
        )
        observatory.clear_maritime_observatory_cache()

        with patch.object(
            observatory,
            "_load_maritime_observatory",
            return_value=payload,
        ) as load:
            first = observatory.build_maritime_observatory()
            first["headline"] = "caller mutation"
            second = observatory.build_maritime_observatory()

        self.assertEqual(load.call_count, 1)
        self.assertNotEqual(second["headline"], "caller mutation")

    def test_expired_cache_falls_back_to_last_success_on_source_failure(self):
        payload = _build_maritime_observatory(
            _panel(),
            today=date(2026, 9, 18),
            chokepoint_error="optional layer unavailable",
        )
        observatory.clear_maritime_observatory_cache()

        with patch.object(
            observatory,
            "_load_maritime_observatory",
            side_effect=[payload, observatory._unavailable("upstream offline")],
        ) as load:
            first = observatory.build_maritime_observatory()
            _, cached = observatory._dashboard_caches["CHN"]
            observatory._dashboard_caches["CHN"] = (0.0, cached)
            fallback = observatory.build_maritime_observatory()
            cached_fallback = observatory.build_maritime_observatory()

        self.assertEqual(first["status"], "partial")
        self.assertEqual(fallback["status"], "partial")
        self.assertEqual(fallback["freshness"], "stale")
        self.assertEqual(cached_fallback["freshness"], "stale")
        self.assertEqual(load.call_count, 2)
        self.assertTrue(any("upstream offline" in warning for warning in fallback["warnings"]))

    def test_cold_start_failure_uses_short_negative_cache(self):
        unavailable = observatory._unavailable("upstream offline")
        observatory.clear_maritime_observatory_cache()

        with patch.object(
            observatory,
            "_load_maritime_observatory",
            return_value=unavailable,
        ) as load:
            first = observatory.build_maritime_observatory()
            second = observatory.build_maritime_observatory()
            _, cached = observatory._dashboard_caches["CHN"]
            observatory._dashboard_caches["CHN"] = (0.0, cached)
            third = observatory.build_maritime_observatory()

        self.assertEqual(first["status"], "unavailable")
        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(third["status"], "unavailable")
        self.assertEqual(load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
