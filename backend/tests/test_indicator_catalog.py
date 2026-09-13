import unittest

from app.indicator_catalog import (
    VALID_AGGREGATIONS,
    VALID_DIRECTIONS,
    VALID_FREQUENCIES,
    VALID_MEASURE_TYPES,
    VALID_SEASONAL_ADJUSTMENTS,
    VALID_TRANSFORMS,
    get_indicator_catalog,
)
from app.indicator_defs import INDICATOR_DEFS
from app.routers.indicators import indicator_catalog


class IndicatorCatalogContractTests(unittest.TestCase):
    def test_catalog_covers_all_current_indicators(self) -> None:
        catalog = get_indicator_catalog()
        codes = {definition["code"] for definition in INDICATOR_DEFS}

        self.assertEqual(len(INDICATOR_DEFS), 147)
        self.assertEqual(set(catalog), codes)
        self.assertEqual(len(catalog), len(codes))

    def test_database_metadata_is_explicit_and_matches_catalog(self) -> None:
        catalog = get_indicator_catalog()
        for definition in INDICATOR_DEFS:
            with self.subTest(code=definition["code"]):
                entry = catalog[definition["code"]]
                self.assertEqual(definition["source"], entry.source)
                self.assertEqual(definition["frequency"], entry.frequency)
                self.assertNotIn(definition["source"].lower(), {"unknown", "akshare"})
                self.assertNotEqual(definition["frequency"], "unknown")

    def test_model_metadata_uses_supported_values(self) -> None:
        for code, entry in get_indicator_catalog().items():
            with self.subTest(code=code):
                self.assertIn(entry.frequency, VALID_FREQUENCIES)
                self.assertIn(entry.seasonal_adjustment, VALID_SEASONAL_ADJUSTMENTS)
                self.assertIn(entry.measure_type, VALID_MEASURE_TYPES)
                self.assertIn(entry.aggregation, VALID_AGGREGATIONS)
                self.assertIn(entry.direction, VALID_DIRECTIONS)
                self.assertIn(entry.transform, VALID_TRANSFORMS)
                self.assertGreaterEqual(entry.release_lag_months, 0)
                self.assertTrue(entry.notes.strip())

    def test_known_non_comparable_series_are_documented(self) -> None:
        catalog = get_indicator_catalog()
        self.assertIn("不等同", catalog["US_BASE_ABS"].notes)
        self.assertIn("结构断点", catalog["US_M1_ABS"].notes)
        self.assertIn("不是货币供应量", catalog["KR_RESERVES"].notes)
        self.assertIn("并不准确", catalog["US_GDP"].notes)

    def test_representative_economic_semantics_are_not_just_enum_valid(self) -> None:
        catalog = get_indicator_catalog()
        self.assertEqual(catalog["CN_PPI"].measure_type, "yoy")
        self.assertEqual(catalog["CN_PPI"].transform, "yoy")
        self.assertEqual(catalog["CN_IND_INVENTORY_DAYS"].measure_type, "duration")
        for code in (
            "CN_FISCAL_GENERAL_SPEND_YOY",
            "CN_FISCAL_FUND_EXPENDITURE_YOY",
            "CN_FISCAL_BROAD_EXPENDITURE_YOY",
        ):
            self.assertTrue(catalog[code].cumulative)
            self.assertEqual(catalog[code].aggregation, "cumulative_ytd")
        self.assertEqual(catalog["EU_ECB"].aggregation, "end_of_period")

    def test_catalog_api_payload_exposes_formula_and_modelling_fields(self) -> None:
        rows = indicator_catalog(region="CN")
        by_code = {row["code"]: row for row in rows}

        self.assertEqual(by_code["CN_CREDIT_IMPULSE"]["formula_version"], "1.0.0")
        self.assertEqual(
            by_code["CN_CREDIT_IMPULSE"]["input_codes"],
            ["CN_CREDIT_INTENSITY"],
        )
        self.assertEqual(by_code["CN_PPI"]["measure_type"], "yoy")
        self.assertIn("release_lag_months", by_code["CN_PPI"])


if __name__ == "__main__":
    unittest.main()
