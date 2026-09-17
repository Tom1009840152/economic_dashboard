import unittest
from datetime import date, timedelta

from app.main import app
from app.schemas import EUMacroOverviewOut
from app.services.eu_macro_overview import (
    _build_eu_macro_overview,
    _trailing_mean,
    _year_ago_reference,
)


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _months(count: int) -> list[date]:
    end = date.today().replace(day=1)
    return [_shift_month(end, offset) for offset in range(-count + 1, 1)]


def _rows() -> list[dict]:
    months = _months(18)
    rows: list[dict] = []

    def add_monthly(code: str, values: list[float]) -> None:
        rows.extend(
            {"indicator_code": code, "date": observed, "value": value}
            for observed, value in zip(months[-len(values) :], values)
        )

    add_monthly("EU_IP", [-1.4, -1.0, -0.8, 0.3, -0.4, -0.2, -0.2])
    add_monthly("EU_CLI", [96.8, 93.7, 94.2, 95.6, 97.1, 98.4])
    add_monthly("EU_CPI", [2.55, 3.03, 3.17, 2.75, 2.94])
    add_monthly("EU_CORE_CPI", [2.28, 2.20, 2.56, 2.36, 2.48])
    add_monthly("EU_ECB", [2.15, 2.15, 2.15, 2.15, 2.40, 2.40, 2.40, 2.65])

    quarters = [_shift_month(months[-1], offset) for offset in (-21, -18, -15, -12, -9, -6, -3, 0)]
    rows.extend(
        {"indicator_code": "EU_GDP", "date": observed, "value": value}
        for observed, value in zip(quarters, [1.2, 1.5, 1.6, 1.4, 1.2, 1.1, 0.6, 1.2])
    )

    today = date.today()
    for index in range(80):
        observed = today - timedelta(days=79 - index)
        rows.extend(
            [
                {
                    "indicator_code": "STOXX50",
                    "date": observed,
                    "value": 6_200 + index * 0.8,
                },
                {
                    "indicator_code": "EURCNY",
                    "date": observed,
                    "value": 7.80 - index * 0.0008,
                },
            ]
        )
    rows.extend(
        {
            "indicator_code": "EU_ECB_ASSETS",
            "date": today - timedelta(days=7 * (79 - index)),
            "value": 6_080_000 - index * 2_100,
        }
        for index in range(80)
    )
    return rows


def _employment() -> dict:
    months = _months(18)
    month_periods = [item.strftime("%Y-%m") for item in months]
    quarters = [f"{item.year}-Q{((item.month - 1) // 3) + 1}" for item in months[::3]][-5:]

    def points(periods: list[str], values: list[float]) -> list[dict]:
        return [
            {"period": period, "value": value}
            for period, value in zip(periods[-len(values) :], values)
        ]

    return {
        "region": "EU",
        "latest_month": month_periods[-1],
        "series": [
            {
                "key": "unemployment",
                "points": points(month_periods, [6.3, 6.4, 6.4, 6.4]),
            },
            {
                "key": "youth_unemployment",
                "points": points(month_periods, [15.2, 15.1, 15.0, 14.9]),
            },
            {
                "key": "labor_participation",
                "points": points(quarters, [80.7, 80.7, 80.7, 80.8, 80.9]),
            },
            {
                "key": "employment_ratio",
                "points": points(quarters, [75.7, 75.6, 75.8, 75.9, 75.9]),
            },
            {
                "key": "labour_slack",
                "points": points(quarters, [11.9, 12.0, 11.9, 11.8, 11.9]),
            },
        ],
    }


class EUMacroOverviewTests(unittest.TestCase):
    def test_current_snapshot_keeps_five_euro_area_pillars(self) -> None:
        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(_rows(), _employment())
        )

        self.assertEqual(validated.status, "partial")
        self.assertEqual(validated.confidence, "medium")
        self.assertAlmostEqual(validated.coverage, 15 / 19, places=4)
        self.assertFalse(validated.realtime_ready)
        self.assertEqual(validated.country, "欧元区（EA21）")
        self.assertEqual(len(validated.pillars), 5)
        self.assertEqual(
            {pillar.key: pillar.state_key for pillar in validated.pillars},
            {
                "growth": "slow_expansion_repairing",
                "labour": "resilient",
                "inflation": "core_reaccelerating",
                "financial_conditions": "mixed_limited",
                "monetary_policy": "tightening",
            },
        )
        self.assertEqual(validated.downturn_breadth.state_key, "limited")
        self.assertEqual(validated.downturn_breadth.active_signals, 1)
        self.assertEqual(validated.downturn_breadth.total_signals, 6)
        self.assertTrue(any("EA21" in warning and "EU27" in warning for warning in validated.warnings))

    def test_employment_failure_does_not_zero_fill_labour(self) -> None:
        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(
                _rows(),
                None,
                employment_error="TimeoutError",
            )
        )
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")

        self.assertEqual(validated.status, "partial")
        self.assertEqual(labour.state_key, "unavailable")
        self.assertTrue(all(metric.value is None for metric in labour.metrics))
        self.assertTrue(any("不解释为中性或零" in warning for warning in validated.warnings))

    def test_missing_calendar_month_does_not_form_fake_trailing_average(self) -> None:
        periods = _months(4)
        points = [
            {"period": observed.strftime("%Y-%m"), "value": value}
            for observed, value in zip(periods, [-1.0, -0.5, 0.1, 0.2])
            if observed != periods[-2]
        ]

        self.assertIsNone(_trailing_mean(points, 3))

    def test_missing_calendar_period_does_not_shift_monthly_or_quarterly_reference(self) -> None:
        rows = _rows()
        latest_month = max(
            row["date"] for row in rows if row["indicator_code"] == "EU_CLI"
        )
        latest_quarter = max(
            row["date"] for row in rows if row["indicator_code"] == "EU_GDP"
        )
        rows = [
            row
            for row in rows
            if not (
                (row["indicator_code"] == "EU_CLI" and row["date"] == _shift_month(latest_month, -3))
                or (
                    row["indicator_code"] == "EU_GDP"
                    and row["date"] == _shift_month(latest_quarter, -3)
                )
            )
        ]

        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(rows, _employment())
        )
        growth = next(pillar for pillar in validated.pillars if pillar.key == "growth")
        metrics = {metric.key: metric for metric in growth.metrics}

        self.assertIsNone(metrics["economic_sentiment"].reference_value)
        self.assertIsNone(metrics["economic_sentiment"].reference_period)
        self.assertIsNone(metrics["real_gdp_yoy"].reference_value)
        self.assertIsNone(metrics["real_gdp_yoy"].reference_period)

    def test_employment_references_require_the_true_month_or_quarter(self) -> None:
        employment = _employment()
        for item in employment["series"]:
            if item["key"] == "unemployment":
                latest = date.fromisoformat(f"{item['points'][-1]['period']}-01")
                target = _shift_month(latest, -3).strftime("%Y-%m")
                item["points"] = [point for point in item["points"] if point["period"] != target]
            elif item["key"] == "employment_ratio":
                latest_year, latest_quarter = item["points"][-1]["period"].split("-Q")
                target = f"{int(latest_year) - 1}-Q{latest_quarter}"
                item["points"] = [point for point in item["points"] if point["period"] != target]

        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(_rows(), employment)
        )
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")
        metrics = {metric.key: metric for metric in labour.metrics}

        self.assertIsNone(metrics["unemployment_rate"].reference_value)
        self.assertIsNone(metrics["employment_rate"].reference_value)

    def test_fifty_two_daily_observations_are_not_called_one_year(self) -> None:
        today = date.today()
        points = [
            {
                "period": (today - timedelta(days=52 - index)).isoformat(),
                "value": float(index + 1),
            }
            for index in range(53)
        ]

        self.assertIsNone(_year_ago_reference(points))

    def test_stale_inputs_remain_visible_but_cannot_drive_states_or_breadth(self) -> None:
        old_rows = [
            {**row, "date": row["date"] - timedelta(days=3650)}
            for row in _rows()
        ]
        old_employment = _employment()
        for item in old_employment["series"]:
            for point in item["points"]:
                if "-Q" in point["period"]:
                    year, quarter = point["period"].split("-Q")
                    point["period"] = f"{int(year) - 10}-Q{quarter}"
                else:
                    observed = date.fromisoformat(f"{point['period']}-01")
                    point["period"] = observed.replace(
                        year=observed.year - 10
                    ).strftime("%Y-%m")

        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(old_rows, old_employment)
        )

        self.assertEqual(validated.status, "unavailable")
        self.assertEqual(validated.coverage, 0)
        self.assertEqual(validated.tone, "neutral")
        self.assertTrue(
            all(pillar.state_key == "unavailable" for pillar in validated.pillars)
        )
        self.assertTrue(
            all(pillar.confidence == "unavailable" for pillar in validated.pillars)
        )
        self.assertEqual(validated.downturn_breadth.state_key, "unavailable")
        self.assertEqual(validated.downturn_breadth.active_signals, 0)
        self.assertEqual(validated.downturn_breadth.total_signals, 0)
        self.assertTrue(
            any(
                metric.value is not None and metric.freshness == "stale"
                for pillar in validated.pillars
                for metric in pillar.metrics
            )
        )

    def test_stale_core_cpi_does_not_drive_current_policy_proxy(self) -> None:
        rows = []
        for row in _rows():
            updated = dict(row)
            if row["indicator_code"] == "EU_CORE_CPI":
                updated["date"] = row["date"] - timedelta(days=3650)
            elif row["indicator_code"] == "EU_ECB":
                updated["value"] = 2.65
            rows.append(updated)

        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(rows, _employment())
        )
        policy = next(
            pillar for pillar in validated.pillars if pillar.key == "monetary_policy"
        )
        metrics = {metric.key: metric for metric in policy.metrics}

        self.assertEqual(metrics["ex_post_real_rate_proxy"].freshness, "stale")
        self.assertEqual(policy.state_key, "nominal_rate_only")
        self.assertEqual(policy.state_label, "仅有名义利率证据")
        self.assertTrue(any("事后实际利率代理" in warning for warning in validated.warnings))

    def test_sparse_old_equity_history_is_not_called_three_month_return(self) -> None:
        rows = _rows()
        latest_equity_date = max(
            row["date"] for row in rows if row["indicator_code"] == "STOXX50"
        )
        rows = [
            {
                **row,
                "date": (
                    row["date"] - timedelta(days=1095)
                    if row["indicator_code"] == "STOXX50"
                    and row["date"] != latest_equity_date
                    else row["date"]
                ),
            }
            for row in rows
        ]

        validated = EUMacroOverviewOut.model_validate(
            _build_eu_macro_overview(rows, _employment())
        )
        financial = next(
            pillar
            for pillar in validated.pillars
            if pillar.key == "financial_conditions"
        )
        equity = next(
            metric for metric in financial.metrics if metric.key == "equity_return_60d"
        )

        self.assertIsNone(equity.value)
        self.assertEqual(equity.freshness, "missing")
        self.assertEqual(validated.downturn_breadth.total_signals, 5)
        self.assertFalse(
            any(
                "STOXX" in text
                for text in (
                    validated.downturn_breadth.triggers
                    + validated.downturn_breadth.offsets
                )
            )
        )

    def test_api_contract_is_registered(self) -> None:
        operation = app.openapi()["paths"]["/api/analysis/eu/overview"]["get"]
        self.assertEqual(operation["responses"]["200"]["description"], "Successful Response")


if __name__ == "__main__":
    unittest.main()
