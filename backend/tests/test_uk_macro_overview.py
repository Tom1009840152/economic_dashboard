import unittest
from datetime import date, datetime, timedelta

from app.main import app
from app.schemas import UKMacroOverviewOut
from app.services.uk_macro_overview import _build_uk_macro_overview, _trailing_mean


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

    add_monthly("GB_IP", [-0.7, 0.0, 0.1, 1.0, -0.2, 0.6])
    add_monthly("GB_CLI", [100.58, 100.61, 100.71, 100.89, 101.11])
    add_monthly("GB_CPI", [2.8, 2.8, 2.6, 2.9, 3.1])
    add_monthly("GB_CORE_CPI", [2.5, 2.6, 2.6, 2.6, 2.6])
    add_monthly("GB_M3", [4.19, 4.64, 5.54, 5.49])

    quarters = [_shift_month(months[-1], offset) for offset in (-21, -18, -15, -12, -9, -6, -3, 0)]
    rows.extend(
        {"indicator_code": "GB_GDP", "date": observed, "value": value}
        for observed, value in zip(quarters, [2.0, 1.8, 1.3, 1.2, 0.9, 0.9, 0.9, 1.2])
    )

    today = date.today()
    retrieved_at = datetime.combine(today, datetime.min.time())
    rows.extend(
        [
            {
                "indicator_code": "GB_BOE",
                "date": _shift_month(today, -15),
                "value": 4.25,
                "retrieved_at": retrieved_at,
            },
            {
                "indicator_code": "GB_BOE",
                "date": _shift_month(today, -13),
                "value": 4.0,
                "retrieved_at": retrieved_at,
            },
            {
                "indicator_code": "GB_BOE",
                "date": _shift_month(today, -9),
                "value": 3.75,
                "retrieved_at": retrieved_at,
            },
        ]
    )
    for index in range(80):
        observed = today - timedelta(days=79 - index)
        rows.extend(
            [
                {"indicator_code": "FTSE100", "date": observed, "value": 10_400 + index * 4},
                {"indicator_code": "GBPCNY", "date": observed, "value": 9.10 - index * 0.001},
            ]
        )
    return rows


def _employment() -> dict:
    periods = [item.strftime("%Y-%m") for item in _months(13)]

    def points(values: list[float]) -> list[dict]:
        return [
            {"period": period, "value": value}
            for period, value in zip(periods[-len(values) :], values)
        ]

    return {
        "region": "GB",
        "latest_month": periods[-1],
        "series": [
            {"key": "unemployment", "points": points([4.7, 4.8, 5.0, 5.1, 5.1, 5.2, 5.2, 4.9, 5.0, 4.9, 4.9, 4.9, 4.9])},
            {"key": "youth_unemployment", "points": points([14.3, 14.6, 15.3, 15.9, 15.9, 16.1, 16.1, 15.9, 16.2, 16.2, 16.4, 16.2, 16.4])},
            {"key": "labor_participation", "points": points([79.0, 79.0, 79.1, 79.0, 79.2, 79.1, 79.3, 79.0, 79.1, 79.0, 79.1, 79.1, 79.1])},
            {"key": "employment_ratio", "points": points([75.2, 75.1, 75.1, 74.9, 75.1, 75.0, 75.1, 75.0, 75.0, 75.1, 75.1, 75.1, 75.1])},
        ],
        "warnings": ["英国劳动力调查近期存在抽样波动。"],
    }


class UKMacroOverviewTests(unittest.TestCase):
    def test_current_snapshot_keeps_five_uk_pillars(self) -> None:
        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(_rows(), _employment())
        )

        self.assertEqual(validated.status, "partial")
        self.assertEqual(validated.confidence, "medium")
        self.assertAlmostEqual(validated.coverage, 15 / 19, places=4)
        self.assertFalse(validated.realtime_ready)
        self.assertEqual(validated.country, "英国")
        self.assertEqual(len(validated.pillars), 5)
        self.assertEqual(
            {pillar.key: pillar.state_key for pillar in validated.pillars},
            {
                "growth": "slow_expansion_improving",
                "labour": "stable_youth_weakness",
                "inflation": "headline_reaccelerating",
                "financial_conditions": "mixed_limited",
                "monetary_policy": "easing_then_hold",
            },
        )
        self.assertEqual(validated.downturn_breadth.state_key, "limited")
        self.assertEqual(validated.downturn_breadth.active_signals, 0)
        self.assertEqual(validated.downturn_breadth.total_signals, 6)

    def test_event_rate_uses_calendar_windows_and_is_not_stale_by_change_date(self) -> None:
        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(_rows(), _employment())
        )
        policy = next(pillar for pillar in validated.pillars if pillar.key == "monetary_policy")
        metrics = {metric.key: metric for metric in policy.metrics}

        self.assertEqual(metrics["bank_rate"].freshness, "current")
        self.assertEqual(metrics["six_month_rate_change"].value, 0.0)
        self.assertEqual(metrics["twelve_month_rate_change"].value, -0.25)
        self.assertTrue(any("不是过期日期" in warning for warning in validated.warnings))

    def test_employment_failure_does_not_zero_fill_labour(self) -> None:
        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(_rows(), None, employment_error="TimeoutError")
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
            row["date"] for row in rows if row["indicator_code"] == "GB_CLI"
        )
        latest_quarter = max(
            row["date"] for row in rows if row["indicator_code"] == "GB_GDP"
        )
        rows = [
            row
            for row in rows
            if not (
                (row["indicator_code"] == "GB_CLI" and row["date"] == _shift_month(latest_month, -3))
                or (
                    row["indicator_code"] == "GB_GDP"
                    and row["date"] == _shift_month(latest_quarter, -3)
                )
            )
        ]

        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(rows, _employment())
        )
        growth = next(pillar for pillar in validated.pillars if pillar.key == "growth")
        metrics = {metric.key: metric for metric in growth.metrics}

        self.assertIsNone(metrics["composite_leading_indicator"].reference_value)
        self.assertIsNone(metrics["composite_leading_indicator"].reference_period)
        self.assertIsNone(metrics["real_gdp_yoy"].reference_value)
        self.assertIsNone(metrics["real_gdp_yoy"].reference_period)

    def test_employment_references_require_the_true_calendar_month(self) -> None:
        employment = _employment()
        for item in employment["series"]:
            if item["key"] == "unemployment":
                latest = date.fromisoformat(f"{item['points'][-1]['period']}-01")
                target = _shift_month(latest, -3).strftime("%Y-%m")
                item["points"] = [point for point in item["points"] if point["period"] != target]
            elif item["key"] == "employment_ratio":
                latest = date.fromisoformat(f"{item['points'][-1]['period']}-01")
                target = _shift_month(latest, -12).strftime("%Y-%m")
                item["points"] = [point for point in item["points"] if point["period"] != target]

        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(_rows(), employment)
        )
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")
        metrics = {metric.key: metric for metric in labour.metrics}

        self.assertIsNone(metrics["unemployment_rate"].reference_value)
        self.assertIsNone(metrics["employment_rate"].reference_value)

    def test_stale_inputs_remain_visible_but_cannot_drive_states_or_breadth(self) -> None:
        old_rows = []
        for row in _rows():
            updated = {**row, "date": row["date"] - timedelta(days=3650)}
            if row.get("retrieved_at") is not None:
                updated["retrieved_at"] = row["retrieved_at"] - timedelta(days=3650)
            old_rows.append(updated)
        old_employment = _employment()
        for item in old_employment["series"]:
            for point in item["points"]:
                observed = date.fromisoformat(f"{point['period']}-01")
                point["period"] = observed.replace(
                    year=observed.year - 10
                ).strftime("%Y-%m")

        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(old_rows, old_employment)
        )
        pillars = {pillar.key: pillar for pillar in validated.pillars}

        self.assertEqual(validated.status, "unavailable")
        self.assertEqual(validated.coverage, 0)
        self.assertEqual(validated.tone, "neutral")
        self.assertEqual(pillars["growth"].state_key, "unavailable")
        self.assertEqual(pillars["labour"].state_key, "unavailable")
        self.assertEqual(pillars["inflation"].state_key, "unavailable")
        self.assertEqual(pillars["financial_conditions"].state_key, "unavailable")
        self.assertEqual(pillars["monetary_policy"].state_key, "stale_policy_input")
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
            if row["indicator_code"] == "GB_CORE_CPI":
                updated["date"] = row["date"] - timedelta(days=3650)
            elif row["indicator_code"] == "GB_BOE":
                updated["value"] = 3.75
            rows.append(updated)

        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(rows, _employment())
        )
        policy = next(
            pillar for pillar in validated.pillars if pillar.key == "monetary_policy"
        )
        metrics = {metric.key: metric for metric in policy.metrics}

        self.assertEqual(metrics["ex_post_real_rate_proxy"].freshness, "stale")
        self.assertEqual(policy.state_key, "stable")
        self.assertNotEqual(policy.state_key, "stable_restrictive_proxy")
        self.assertTrue(any("事后实际利率代理" in warning for warning in validated.warnings))

    def test_sparse_old_equity_history_is_not_called_three_month_return(self) -> None:
        rows = _rows()
        latest_equity_date = max(
            row["date"] for row in rows if row["indicator_code"] == "FTSE100"
        )
        rows = [
            {
                **row,
                "date": (
                    row["date"] - timedelta(days=1095)
                    if row["indicator_code"] == "FTSE100"
                    and row["date"] != latest_equity_date
                    else row["date"]
                ),
            }
            for row in rows
        ]

        validated = UKMacroOverviewOut.model_validate(
            _build_uk_macro_overview(rows, _employment())
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
                "富时100" in text
                for text in (
                    validated.downturn_breadth.triggers
                    + validated.downturn_breadth.offsets
                )
            )
        )

    def test_api_contract_is_registered(self) -> None:
        operation = app.openapi()["paths"]["/api/analysis/uk/overview"]["get"]
        self.assertEqual(operation["responses"]["200"]["description"], "Successful Response")


if __name__ == "__main__":
    unittest.main()
