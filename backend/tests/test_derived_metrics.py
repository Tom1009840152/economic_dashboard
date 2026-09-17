import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.fetchers import china_monetary_transmission as transmission
from app.models import DataPoint, Indicator
from app.schemas import MonetaryTransmissionDashboardOut
from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    PBOC_7D_REVERSE_REPO,
    calculate_credit_metrics,
    calculate_fiscal_broad_expenditure,
    calculate_fiscal_metrics,
    calculate_spread,
    policy_rate_for_periods,
    trailing_nominal_gdp_from_ytd,
)


def _constant_ytd(start_year: int, end_year: int) -> pd.Series:
    values: dict[pd.Period, float] = {}
    for year in range(start_year, end_year + 1):
        for month, value in ((3, 25.0), (6, 50.0), (9, 75.0), (12, 100.0)):
            values[pd.Period(year=year, month=month, freq="M")] = value
    return pd.Series(values, dtype="float64")


class DerivedMetricTests(unittest.TestCase):
    def test_formula_registry_has_all_persisted_derived_cycle_metrics(self) -> None:
        self.assertEqual(
            set(DERIVED_METRIC_SPECS),
            {
                "CN_FISCAL_BROAD_EXPENDITURE_YTD",
                "CN_M1M2",
                "CN_CREDIT_INTENSITY",
                "CN_CREDIT_IMPULSE",
                "CN_FISCAL_SPEND_INTENSITY",
                "CN_FISCAL_IMPULSE_PROXY",
            },
        )
        self.assertTrue(all(spec.version for spec in DERIVED_METRIC_SPECS.values()))

    def test_broad_fiscal_spending_requires_both_same_month_inputs(self) -> None:
        general = pd.Series(
            [100.0, 120.0],
            index=pd.PeriodIndex(["2025-03", "2025-06"], freq="M"),
        )
        fund = pd.Series(
            [30.0, 40.0],
            index=pd.PeriodIndex(["2025-03", "2025-09"], freq="M"),
        )

        broad = calculate_fiscal_broad_expenditure(general, fund)

        self.assertEqual(
            broad.to_dict(), {pd.Period("2025-03", freq="M"): 130.0}
        )

    def test_m1_m2_spread_uses_explicit_index_alignment(self) -> None:
        left = pd.Series([7.0, 8.0], index=["2026-01", "2026-02"])
        right = pd.Series([5.0, 6.5], index=["2026-01", "2026-03"])

        spread = calculate_spread(left, right)

        self.assertEqual(spread.to_dict(), {"2026-01": 2.0})

    def test_trailing_gdp_reconstructs_four_quarter_level(self) -> None:
        ytd = pd.Series(
            {
                pd.Period("2023-03", freq="M"): 20.0,
                pd.Period("2023-06", freq="M"): 45.0,
                pd.Period("2023-09", freq="M"): 70.0,
                pd.Period("2023-12", freq="M"): 100.0,
                pd.Period("2024-03", freq="M"): 22.0,
                pd.Period("2024-06", freq="M"): 49.0,
            }
        )
        trailing = trailing_nominal_gdp_from_ytd(ytd)
        self.assertAlmostEqual(trailing[pd.Period("2024-03", freq="M")], 102.0)
        self.assertAlmostEqual(trailing[pd.Period("2024-06", freq="M")], 104.0)

    def test_credit_impulse_is_yoy_change_in_rolling_flow_to_gdp(self) -> None:
        months = pd.period_range("2022-01", "2024-12", freq="M")
        tsf = pd.Series(
            [1.0] * 12 + [2.0] * 12 + [3.0] * 12,
            index=months,
            dtype="float64",
        )
        intensity, impulse = calculate_credit_metrics(tsf, _constant_ytd(2021, 2024))
        self.assertAlmostEqual(intensity[pd.Period("2022-12", freq="M")], 12.0)
        self.assertAlmostEqual(intensity[pd.Period("2023-12", freq="M")], 24.0)
        self.assertAlmostEqual(impulse[pd.Period("2023-12", freq="M")], 12.0)

    def test_credit_impulse_does_not_bridge_a_missing_calendar_month(self) -> None:
        months = pd.period_range("2021-01", "2024-12", freq="M")
        tsf = pd.Series(1.0, index=months, dtype="float64").drop(
            pd.Period("2022-06", freq="M")
        )
        _, impulse = calculate_credit_metrics(tsf, _constant_ytd(2020, 2024))

        self.assertNotIn(pd.Period("2023-06", freq="M"), impulse.index)

    def test_fiscal_proxy_uses_same_quarter_yoy_difference(self) -> None:
        gdp = _constant_ytd(2022, 2023)
        spending = gdp * 0.20
        spending.loc[spending.index.year == 2023] = gdp.loc[gdp.index.year == 2023] * 0.22
        intensity, impulse = calculate_fiscal_metrics(spending, gdp)
        self.assertAlmostEqual(intensity[pd.Period("2023-03", freq="M")], 22.0)
        self.assertAlmostEqual(impulse[pd.Period("2023-03", freq="M")], 2.0)

    def test_fiscal_proxy_does_not_bridge_a_missing_calendar_quarter(self) -> None:
        gdp = _constant_ytd(2022, 2024)
        spending = gdp * 0.20
        missing_quarter = pd.Period("2023-06", freq="M")
        spending = spending.drop(missing_quarter)
        _, impulse = calculate_fiscal_metrics(spending, gdp)

        self.assertNotIn(pd.Period("2024-06", freq="M"), impulse.index)

    def test_policy_schedule_is_transparent_and_applied_at_month_end(self) -> None:
        periods = pd.PeriodIndex(
            ["2024-06", "2024-07", "2024-09", "2025-05", "2026-08", "2026-09"],
            freq="M",
        )
        result = policy_rate_for_periods(periods)
        self.assertEqual(result.iloc[:5].tolist(), [1.8, 1.7, 1.5, 1.4, 1.4])
        self.assertTrue(pd.isna(result.iloc[5]))
        self.assertEqual(PBOC_7D_REVERSE_REPO.maintenance, "manual")
        self.assertEqual(PBOC_7D_REVERSE_REPO.verified_through, dt.date(2026, 9, 17))
        self.assertTrue(PBOC_7D_REVERSE_REPO.source_url.startswith("https://www.pbc.gov.cn/"))


class MonetaryTransmissionStoredSeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in (
            "CN_CREDIT_INTENSITY",
            "CN_CREDIT_IMPULSE",
            "CN_TSF",
            "CN_GDP_NOMINAL_YTD",
        ):
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
                    category="cycle_input",
                    unit="pp",
                    source="Derived",
                    frequency="monthly",
                    is_visible=False,
                    sort_order=0,
                    region="CN",
                )
            )
        self.db.flush()
        self.db.add_all(
            [
                DataPoint(
                    indicator_code="CN_CREDIT_INTENSITY",
                    date=dt.date(2025, 12, 1),
                    value=24.0,
                    status="derived",
                    formula_version="1.0.0",
                    version=1,
                ),
                DataPoint(
                    indicator_code="CN_CREDIT_IMPULSE",
                    date=dt.date(2025, 12, 1),
                    value=1.25,
                    status="derived",
                    formula_version="1.0.0",
                    version=1,
                ),
                DataPoint(
                    indicator_code="CN_TSF",
                    date=dt.date(2025, 12, 1),
                    value=100.0,
                    status="published",
                    version=1,
                ),
                DataPoint(
                    indicator_code="CN_GDP_NOMINAL_YTD",
                    date=dt.date(2025, 12, 1),
                    value=1000.0,
                    status="historical_backfill",
                    version=1,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_stored_derived_series_are_used_before_fallback_formula(self) -> None:
        with patch.object(
            transmission,
            "calculate_credit_metrics",
            side_effect=AssertionError("fallback should not run"),
        ):
            intensity, impulse, origin = transmission._credit_metrics_from_database(self.db)
        self.assertEqual(origin, "stored")
        self.assertAlmostEqual(float(intensity.iloc[-1]), 24.0)
        self.assertAlmostEqual(float(impulse.iloc[-1]), 1.25)

    def test_liquidity_policy_rate_is_aligned_daily_before_monthly_average(self) -> None:
        frame = pd.DataFrame(
            {
                "date": [dt.date(2024, 7, 19), dt.date(2024, 7, 23)],
                "FDR007": [1.81, 1.71],
            }
        )
        with patch.object(transmission.ak, "repo_rate_query", return_value=frame):
            fdr007, policy_rate, last_day = transmission._fetch_fdr007()

        self.assertAlmostEqual(float(fdr007.iloc[0]), 1.76)
        self.assertAlmostEqual(float(policy_rate.iloc[0]), 1.75)
        self.assertEqual(last_day, "2024-07-23")

    def test_dashboard_keeps_market_policy_and_credit_watermarks_separate(self) -> None:
        core_cpi = pd.Series(
            [0.4, 0.5, 0.6],
            index=pd.PeriodIndex(["2025-10", "2025-11", "2025-12"], freq="M"),
            dtype="float64",
        )
        fdr007 = pd.Series(
            [1.45, 1.40],
            index=pd.PeriodIndex(["2025-12", "2026-09"], freq="M"),
            dtype="float64",
        )
        policy_for_liquidity = pd.Series(
            [1.40, 1.40],
            index=fdr007.index,
            dtype="float64",
        )
        credit_ratio = pd.Series(
            [24.0],
            index=pd.PeriodIndex(["2026-08"], freq="M"),
            dtype="float64",
        )
        credit_impulse = pd.Series(
            [0.75],
            index=credit_ratio.index,
            dtype="float64",
        )
        original_cache = transmission._cache
        transmission._cache = None
        try:
            with (
                patch.object(transmission, "_database_signature", return_value=()),
                patch.object(transmission, "_database_series", return_value=core_cpi),
                patch.object(
                    transmission,
                    "_fetch_fdr007",
                    return_value=(fdr007, policy_for_liquidity, "2026-09-16"),
                ),
                patch.object(
                    transmission,
                    "_credit_metrics_from_database",
                    return_value=(credit_ratio, credit_impulse, "stored"),
                ),
            ):
                result = transmission.fetch_china_monetary_transmission(object())
        finally:
            transmission._cache = original_cache

        self.assertEqual(result["as_of"], "2026-09-16")
        self.assertEqual(
            result["freshness"],
            {
                "market_observation_date": "2026-09-16",
                "policy_rate_verified_through": "2026-09-17",
                "credit_observation_period": "2026-08",
            },
        )
        periods = {signal["key"]: signal["period"] for signal in result["signals"]}
        self.assertEqual(periods["real_policy_rate"], "2025-12")
        self.assertEqual(periods["liquidity_gap"], "2026-09")
        self.assertEqual(periods["credit_impulse"], "2026-08")
        self.assertEqual(result["status"], "宽货币正向信用传导")
        policy_series = next(series for series in result["series"] if series["key"] == "policy_rate")
        self.assertEqual(policy_series["current_value"], 1.4)
        self.assertEqual(policy_series["effective_date"], "2025-05-08")
        self.assertEqual(policy_series["catalog_updated_at"], "2026-09-17")
        MonetaryTransmissionDashboardOut.model_validate(result)


if __name__ == "__main__":
    unittest.main()
