import unittest
from datetime import date, timedelta

from app.main import app
from app.schemas import USMacroOverviewOut
from app.services.us_macro_overview import (
    _build_us_macro_overview,
    _moving_average_gap,
    _reference,
    _series,
)


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _periods(count: int, *, end: date | None = None) -> list[date]:
    end = (end or date.today()).replace(day=1)
    return [_shift_month(end, offset) for offset in range(-count + 1, 1)]


def _rows() -> list[dict]:
    months = _periods(18)
    rows: list[dict] = []

    def add_monthly(code: str, values: list[float]) -> None:
        rows.extend(
            {
                "indicator_code": code,
                "date": observed,
                "value": value,
            }
            for observed, value in zip(months[-len(values) :], values)
        )

    add_monthly("US_CPI", [3.9, 3.8, 3.7, 3.6, 3.5, 3.4, 3.4])
    add_monthly("US_CORE_CPI", [2.9, 2.8, 2.7, 2.6, 2.55, 2.5, 2.45])
    add_monthly("US_IP", [0.4, 0.7, 0.9, 1.0, 1.2, 1.1])
    add_monthly("US_CLI", [100.2, 100.3, 100.45, 100.6, 100.8, 100.95])
    add_monthly("US_NFP", [21.4, 14.8, 6.3, 3.1, 2.1, 16.2])
    add_monthly("US_FFR", [3.64, 3.64, 3.64, 3.63, 3.63, 3.63, 3.63])
    add_monthly("US_M2_YOY", [4.4, 4.5, 4.8, 5.0, 5.2, 5.4])

    quarters = [_shift_month(months[-1], offset) for offset in (-21, -18, -15, -12, -9, -6, -3, 0)]
    rows.extend(
        {"indicator_code": "US_GDP", "date": observed, "value": value}
        for observed, value in zip(quarters, [1.0, 1.4, 2.0, 2.5, 2.1, 1.8, 1.6, 1.5])
    )

    today = date.today()
    for index in range(80):
        observed = today - timedelta(days=79 - index)
        rows.extend(
            [
                {"indicator_code": "US_2Y", "date": observed, "value": 4.4 + index * 0.004},
                {"indicator_code": "US_10Y", "date": observed, "value": 4.7 + index * 0.004},
                {"indicator_code": "US_10Y2Y", "date": observed, "value": 0.3},
                {"indicator_code": "DJI", "date": observed, "value": 48_000 + index * 40},
            ]
        )
    return rows


def _employment(*, sahm_trigger: bool = False) -> dict:
    months = _periods(18)
    periods = [item.strftime("%Y-%m") for item in months]
    unemployment = [4.0] * 18
    if sahm_trigger:
        unemployment[-3:] = [4.5, 4.5, 4.5]
    payrolls = [220, 210, 200, 190, 180, 170, 160, 150, 140, 130, 120, 110, 160, 150, 140, 90, 70, 55]
    wages = [3.8 - index * 0.04 for index in range(18)]
    vacancies = [1.2 - index * 0.008 for index in range(18)]
    return {
        "region": "US",
        "latest_month": periods[-1],
        "series": [
            {
                "key": "unemployment",
                "points": [
                    {"period": period, "value": value}
                    for period, value in zip(periods, unemployment)
                ],
            },
            {
                "key": "payroll_change",
                "points": [
                    {"period": period, "value": value}
                    for period, value in zip(periods, payrolls)
                ],
            },
            {
                "key": "hourly_earnings_yoy",
                "points": [
                    {"period": period, "value": value}
                    for period, value in zip(periods, wages)
                ],
            },
            {
                "key": "vacancy_unemployed_ratio",
                "points": [
                    {"period": period, "value": value}
                    for period, value in zip(periods, vacancies)
                ],
            },
        ],
    }


class USMacroOverviewTests(unittest.TestCase):
    def test_current_snapshot_keeps_five_independent_pillars(self) -> None:
        payload = _build_us_macro_overview(_rows(), _employment())
        validated = USMacroOverviewOut.model_validate(payload)

        self.assertEqual(validated.status, "ok")
        self.assertEqual(validated.confidence, "medium")
        self.assertFalse(validated.realtime_ready)
        self.assertEqual(len(validated.pillars), 5)
        self.assertEqual(
            {pillar.key: pillar.state_key for pillar in validated.pillars},
            {
                "growth": "moderate_expansion",
                "labour": "cooling_resilient",
                "inflation": "cooling_above_benchmark",
                "financial_conditions": "mixed",
                "monetary_policy": "stable_restrictive_proxy",
            },
        )
        self.assertEqual(validated.recession_breadth.state_key, "limited")
        self.assertEqual(validated.recession_breadth.active_signals, 0)
        self.assertEqual(validated.recession_breadth.total_signals, 6)
        self.assertTrue(any("最终快照" in warning for warning in validated.warnings))

    def test_employment_failure_falls_back_to_stored_nfp_without_zero_fill(self) -> None:
        payload = _build_us_macro_overview(
            _rows(),
            None,
            employment_error="TimeoutError",
        )
        validated = USMacroOverviewOut.model_validate(payload)
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")
        payroll = next(metric for metric in labour.metrics if metric.key == "payroll_change_3m_average")
        unemployment = next(metric for metric in labour.metrics if metric.key == "unemployment_rate")

        self.assertEqual(validated.status, "partial")
        self.assertAlmostEqual(payroll.value, (3.1 + 2.1 + 16.2) * 10 / 3, places=4)
        self.assertIsNone(unemployment.value)
        self.assertEqual(unemployment.freshness, "missing")
        self.assertTrue(any("降级展示" in warning for warning in validated.warnings))

    def test_missing_calendar_month_does_not_form_fake_three_month_average(self) -> None:
        rows = _rows()
        nfp_rows = [row for row in rows if row["indicator_code"] == "US_NFP"]
        missing_date = nfp_rows[-2]["date"]
        rows = [
            row
            for row in rows
            if not (row["indicator_code"] == "US_NFP" and row["date"] == missing_date)
        ]
        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(rows, None, employment_error="TimeoutError")
        )
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")
        payroll = next(metric for metric in labour.metrics if metric.key == "payroll_change_3m_average")

        self.assertIsNone(payroll.value)
        self.assertEqual(payroll.freshness, "missing")

    def test_missing_calendar_period_does_not_shift_monthly_or_quarterly_reference(self) -> None:
        rows = _rows()
        industrial_rows = sorted(
            (row for row in rows if row["indicator_code"] == "US_IP"),
            key=lambda row: row["date"],
        )
        gdp_rows = sorted(
            (row for row in rows if row["indicator_code"] == "US_GDP"),
            key=lambda row: row["date"],
        )
        missing_industrial_month = _shift_month(industrial_rows[-1]["date"], -3)
        missing_previous_quarter = gdp_rows[-2]["date"]
        rows = [
            row
            for row in rows
            if not (
                row["indicator_code"] == "US_IP"
                and row["date"] == missing_industrial_month
            )
            and not (
                row["indicator_code"] == "US_GDP"
                and row["date"] == missing_previous_quarter
            )
        ]

        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(rows, _employment())
        )
        growth = next(pillar for pillar in validated.pillars if pillar.key == "growth")
        industrial = next(
            metric for metric in growth.metrics if metric.key == "industrial_production_yoy"
        )
        gdp = next(
            metric for metric in growth.metrics if metric.key == "real_gdp_qoq_annualized"
        )

        self.assertIsNone(industrial.reference_value)
        self.assertIsNone(industrial.reference_period)
        self.assertIsNone(gdp.reference_value)
        self.assertIsNone(gdp.reference_period)

    def test_missing_six_month_rate_reference_is_not_called_stable(self) -> None:
        rows = _rows()
        rate_rows = sorted(
            (row for row in rows if row["indicator_code"] == "US_FFR"),
            key=lambda row: row["date"],
        )
        missing_rate_month = _shift_month(rate_rows[-1]["date"], -6)
        rows = [
            row
            for row in rows
            if not (
                row["indicator_code"] == "US_FFR"
                and row["date"] == missing_rate_month
            )
        ]

        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(rows, _employment())
        )
        policy = next(
            pillar for pillar in validated.pillars if pillar.key == "monetary_policy"
        )
        rate = next(
            metric
            for metric in policy.metrics
            if metric.key == "effective_federal_funds_rate"
        )

        self.assertIsNone(rate.reference_value)
        self.assertEqual(policy.state_key, "restrictive")

    def test_stale_readings_do_not_drive_states_breadth_or_watch_items(self) -> None:
        rows = [
            {
                **row,
                "date": row["date"].replace(year=row["date"].year - 10),
            }
            for row in _rows()
        ]
        employment = _employment()
        for item in employment["series"]:
            item["points"] = [
                {
                    **point,
                    "period": f"{int(point['period'][:4]) - 10}{point['period'][4:]}",
                }
                for point in item["points"]
            ]
        employment["latest_month"] = (
            f"{int(employment['latest_month'][:4]) - 10}"
            f"{employment['latest_month'][4:]}"
        )

        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(rows, employment)
        )

        self.assertEqual(validated.status, "unavailable")
        self.assertEqual(validated.confidence, "unavailable")
        self.assertEqual(validated.coverage, 0)
        self.assertEqual(validated.tone, "neutral")
        self.assertTrue(
            all(pillar.state_key == "unavailable" for pillar in validated.pillars)
        )
        self.assertEqual(validated.recession_breadth.state_key, "unavailable")
        self.assertEqual(validated.recession_breadth.total_signals, 0)
        self.assertEqual(
            validated.watch_items,
            ["等待下一批增长、就业和通胀数据，按各自发布节奏更新判断。"],
        )

    def test_current_nominal_rate_without_current_core_does_not_claim_restriction(self) -> None:
        rows = [
            {
                **row,
                "date": (
                    row["date"].replace(year=row["date"].year - 10)
                    if row["indicator_code"] == "US_CORE_CPI"
                    else row["date"]
                ),
            }
            for row in _rows()
        ]

        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(rows, _employment())
        )
        policy = next(
            pillar for pillar in validated.pillars if pillar.key == "monetary_policy"
        )
        real_proxy = next(
            metric for metric in policy.metrics if metric.key == "ex_post_real_rate_proxy"
        )

        self.assertEqual(policy.state_key, "nominal_rate_only")
        self.assertEqual(policy.state_label, "仅有名义利率证据")
        self.assertEqual(real_proxy.freshness, "stale")

    def test_external_employment_references_require_exact_calendar_month(self) -> None:
        employment = _employment()
        latest = date.fromisoformat(f"{employment['latest_month']}-01")
        missing_period = _shift_month(latest, -3).strftime("%Y-%m")
        for item in employment["series"]:
            if item["key"] in {
                "unemployment",
                "hourly_earnings_yoy",
                "vacancy_unemployed_ratio",
            }:
                item["points"] = [
                    point for point in item["points"] if point["period"] != missing_period
                ]

        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(_rows(), employment)
        )
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")
        for key in ("unemployment_rate", "wage_growth", "vacancy_unemployed_ratio"):
            metric = next(metric for metric in labour.metrics if metric.key == key)
            self.assertIsNone(metric.reference_value)
            self.assertIsNone(metric.reference_period)

    def test_daily_reference_remains_observation_based_across_calendar_gaps(self) -> None:
        rows = _rows()
        ten_year_rows = sorted(
            (row for row in rows if row["indicator_code"] == "US_10Y"),
            key=lambda row: row["date"],
        )
        dates_to_remove = {ten_year_rows[-5]["date"], ten_year_rows[-12]["date"]}
        rows = [
            row
            for row in rows
            if not (
                row["indicator_code"] == "US_10Y" and row["date"] in dates_to_remove
            )
        ]
        grouped = _series(rows)

        reference = _reference(grouped, "US_10Y", 20)

        self.assertIsNotNone(reference)
        self.assertEqual(reference, grouped["US_10Y"][-21])

    def test_sparse_equity_history_is_not_called_a_three_month_return(self) -> None:
        rows = _rows()
        equity_rows = sorted(
            (row for row in rows if row["indicator_code"] == "DJI"),
            key=lambda row: row["date"],
        )
        latest_date = equity_rows[-1]["date"]
        rows = [
            {
                **row,
                "date": (
                    row["date"].replace(year=row["date"].year - 3)
                    if row["indicator_code"] == "DJI"
                    and row["date"] != latest_date
                    else row["date"]
                ),
            }
            for row in rows
        ]

        validated = USMacroOverviewOut.model_validate(
            _build_us_macro_overview(rows, _employment())
        )
        financial = next(
            pillar for pillar in validated.pillars if pillar.key == "financial_conditions"
        )
        equity_return = next(
            metric for metric in financial.metrics if metric.key == "equity_return_3m"
        )

        self.assertIsNone(equity_return.value)
        self.assertEqual(equity_return.freshness, "missing")

    def test_sahm_style_threshold_uses_contiguous_three_month_windows(self) -> None:
        payload = _build_us_macro_overview(_rows(), _employment(sahm_trigger=True))
        validated = USMacroOverviewOut.model_validate(payload)
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")
        gap = next(metric for metric in labour.metrics if metric.key == "sahm_style_gap")

        self.assertAlmostEqual(gap.value, 0.5)
        self.assertEqual(validated.recession_breadth.state_key, "broad")
        self.assertIn("失业率三月均值缺口达到0.50个百分点", validated.recession_breadth.triggers)

    def test_historical_gap_invalidates_only_crossing_windows(self) -> None:
        months = _periods(18)
        points = [
            {"period": observed.strftime("%Y-%m"), "value": 4.0}
            for observed in months
            if observed != months[5]
        ]
        gap, low, period = _moving_average_gap(points)

        self.assertEqual(gap, 0)
        self.assertEqual(low, 4)
        self.assertIsNotNone(period)

    def test_api_contract_is_registered(self) -> None:
        operation = app.openapi()["paths"]["/api/analysis/us/overview"]["get"]
        self.assertEqual(operation["responses"]["200"]["description"], "Successful Response")


if __name__ == "__main__":
    unittest.main()
