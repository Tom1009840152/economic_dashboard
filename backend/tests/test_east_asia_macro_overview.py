import unittest
from datetime import date, timedelta

from app.main import app
from app.schemas import JPMacroOverviewOut, KRMacroOverviewOut
from app.services.east_asia_macro_overview import (
    _build_japan_macro_overview,
    _build_korea_macro_overview,
    _trailing_mean,
)


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _months(count: int) -> list[date]:
    end = date.today().replace(day=1)
    return [_shift_month(end, offset) for offset in range(-count + 1, 1)]


def _add_monthly(rows: list[dict], code: str, values: list[float]) -> None:
    periods = _months(len(values))
    rows.extend(
        {"indicator_code": code, "date": observed, "value": value}
        for observed, value in zip(periods, values)
    )


def _add_market(
    rows: list[dict], *, equity_code: str, fx_code: str, equity_values: list[float]
) -> None:
    today = date.today()
    for index, equity_value in enumerate(equity_values):
        observed = today - timedelta(days=len(equity_values) - 1 - index)
        rows.extend(
            [
                {"indicator_code": equity_code, "date": observed, "value": equity_value},
                {"indicator_code": fx_code, "date": observed, "value": 4.2 + index * 0.001},
            ]
        )


def _age_rows(rows: list[dict], *codes: str, days: int = 730) -> None:
    selected = set(codes)
    for row in rows:
        if row["indicator_code"] in selected:
            row["date"] = row["date"] - timedelta(days=days)


def _age_employment(employment: dict, months: int = 24) -> None:
    for item in employment["series"]:
        for point in item["points"]:
            observed = date.fromisoformat(f"{point['period']}-01")
            point["period"] = _shift_month(observed, -months).strftime("%Y-%m")


def _drop_month(rows: list[dict], code: str, months_back: int) -> None:
    latest = max(row["date"] for row in rows if row["indicator_code"] == code)
    target = _shift_month(latest, -months_back)
    rows[:] = [
        row
        for row in rows
        if not (
            row["indicator_code"] == code
            and row["date"].year == target.year
            and row["date"].month == target.month
        )
    ]


def _drop_employment_month(employment: dict, key: str, months_back: int) -> None:
    item = next(series for series in employment["series"] if series["key"] == key)
    latest = date.fromisoformat(f"{item['points'][-1]['period']}-01")
    target = _shift_month(latest, -months_back).strftime("%Y-%m")
    item["points"] = [point for point in item["points"] if point["period"] != target]


def _japan_rows() -> list[dict]:
    rows: list[dict] = []
    _add_monthly(rows, "JP_IP", [0.1, 0.3, 0.4, 1.0, 2.0, 2.0])
    _add_monthly(rows, "JP_CLI", [99.9, 100.0, 100.1, 100.3])
    _add_monthly(rows, "JP_CPI", [1.4, 1.5, 1.6, 1.9])
    _add_monthly(rows, "JP_CORE_CPI", [1.2, 1.3, 1.4, 1.5])
    _add_monthly(
        rows,
        "JP_BOJ",
        [0.48, 0.48, 0.48, 0.55, 0.60, 0.65, 0.73, 0.73, 0.75, 0.80, 0.90, 0.98, 0.98],
    )
    _add_monthly(
        rows,
        "JP_BOJ_ASSETS",
        [7_000_000, 6_970_000, 6_920_000, 6_880_000, 6_830_000, 6_780_000, 6_720_000,
         6_670_000, 6_620_000, 6_570_000, 6_520_000, 6_480_000, 6_440_000],
    )
    _add_market(
        rows,
        equity_code="NKY",
        fx_code="JPYCNY",
        equity_values=[100 + index * 0.1 for index in range(80)],
    )
    return rows


def _korea_rows() -> list[dict]:
    rows: list[dict] = []
    _add_monthly(rows, "KR_IP", [-1.0, 0.5, 1.0, 0.5, 4.4, 5.5])
    _add_monthly(rows, "KR_CLI", [101.9, 102.1, 102.4, 102.9])
    _add_monthly(rows, "KR_CORE_CPI", [2.5, 2.6, 2.8, 3.4])
    event_month = date.today().replace(day=1)
    rows.extend(
        [
            {
                "indicator_code": "KR_BOK",
                "date": _shift_month(event_month, -13),
                "value": 2.5,
            },
            {
                "indicator_code": "KR_BOK",
                "date": _shift_month(event_month, -5),
                "value": 2.75,
            },
            {
                "indicator_code": "KR_BOK",
                "date": _shift_month(event_month, -1),
                "value": 3.0,
            },
        ]
    )
    _add_monthly(
        rows,
        "KR_RESERVES",
        [405_000, 406_000, 407_000, 409_000, 410_000, 412_000, 414_000,
         415_000, 416_000, 418_000, 419_000, 421_000, 422_000],
    )
    _add_market(
        rows,
        equity_code="KOSPI",
        fx_code="KRWCNY",
        equity_values=[120 - index * 0.4 for index in range(80)],
    )
    return rows


def _employment(region: str, *, youth_weakness: bool) -> dict:
    periods = [item.strftime("%Y-%m") for item in _months(13)]

    def points(values: list[float]) -> list[dict]:
        return [
            {"period": period, "value": value}
            for period, value in zip(periods[-len(values) :], values)
        ]

    if youth_weakness:
        unemployment = [2.63, 2.65, 2.66, 2.70, 2.71, 2.72, 2.73, 2.74, 2.75, 2.73, 2.76, 2.79, 2.82]
        youth = [5.85, 5.9, 6.0, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.8, 7.0, 7.2, 7.35]
        participation = [71.78 + index * 0.014 for index in range(13)]
        employment = [69.89 + index * 0.003 for index in range(13)]
    else:
        unemployment = [2.5, 2.6, 2.7, 2.7, 2.7, 2.7, 2.9, 2.8, 2.8, 2.7, 2.6, 2.6, 2.4]
        youth = [4.2, 4.1, 4.0, 4.0, 3.9, 3.8, 3.8, 3.7, 3.6, 3.6, 3.5, 3.5, 3.4]
        participation = [82.24 + index * 0.022 for index in range(13)]
        employment = [80.23 + index * 0.023 for index in range(13)]
    return {
        "region": region,
        "latest_month": periods[-1],
        "series": [
            {"key": "unemployment", "points": points(unemployment)},
            {"key": "youth_unemployment", "points": points(youth)},
            {"key": "labor_participation", "points": points(participation)},
            {"key": "employment_ratio", "points": points(employment)},
        ],
        "warnings": [],
    }


class EastAsiaMacroOverviewTests(unittest.TestCase):
    def test_japan_snapshot_keeps_policy_proxy_and_missing_gdp_explicit(self) -> None:
        validated = JPMacroOverviewOut.model_validate(
            _build_japan_macro_overview(_japan_rows(), _employment("JP", youth_weakness=False))
        )

        self.assertEqual(validated.status, "partial")
        self.assertEqual(validated.confidence, "medium")
        self.assertAlmostEqual(validated.coverage, 14 / 19, places=4)
        self.assertFalse(validated.realtime_ready)
        self.assertEqual(
            {pillar.key: pillar.state_key for pillar in validated.pillars},
            {
                "growth": "improving_partial",
                "labour": "resilient",
                "inflation": "near_target_rising",
                "financial_conditions": "balance_sheet_contracting",
                "monetary_policy": "normalising_real_negative",
            },
        )
        growth = next(pillar for pillar in validated.pillars if pillar.key == "growth")
        policy = next(pillar for pillar in validated.pillars if pillar.key == "monetary_policy")
        self.assertIsNone(next(metric for metric in growth.metrics if metric.key == "real_gdp_yoy").value)
        self.assertIn("不是日本银行最新会议", policy.metrics[0].interpretation)
        self.assertEqual(validated.downturn_breadth.active_signals, 0)
        self.assertEqual(validated.downturn_breadth.total_signals, 5)

    def test_korea_snapshot_uses_official_policy_events_and_two_downturn_signals(self) -> None:
        validated = KRMacroOverviewOut.model_validate(
            _build_korea_macro_overview(
                _korea_rows(),
                _employment("KR", youth_weakness=True),
                policy_verified_at=date.today(),
            )
        )

        self.assertEqual(validated.status, "partial")
        self.assertEqual(validated.confidence, "medium")
        self.assertAlmostEqual(validated.coverage, 13 / 19, places=4)
        self.assertEqual(
            {pillar.key: pillar.state_key for pillar in validated.pillars},
            {
                "growth": "improving_partial",
                "labour": "stable_youth_weakness",
                "inflation": "core_reaccelerating_partial",
                "financial_conditions": "buffer_stable_market_stress",
                "monetary_policy": "tightening_real_negative",
            },
        )
        policy = next(pillar for pillar in validated.pillars if pillar.key == "monetary_policy")
        policy_metrics = {metric.key: metric for metric in policy.metrics}
        self.assertEqual(policy_metrics["bok_base_rate"].value, 3.0)
        self.assertEqual(policy_metrics["six_month_rate_change"].value, 0.5)
        self.assertEqual(policy_metrics["twelve_month_rate_change"].value, 0.5)
        self.assertAlmostEqual(policy_metrics["ex_post_real_rate_proxy"].value, -0.4)
        self.assertEqual(policy_metrics["bok_base_rate"].frequency, "event")
        self.assertEqual(policy_metrics["bok_base_rate"].freshness, "current")
        self.assertTrue(any("事件型变更历史" in warning for warning in validated.warnings))
        self.assertEqual(validated.downturn_breadth.state_key, "elevated")
        self.assertEqual(validated.downturn_breadth.active_signals, 2)
        self.assertEqual(validated.downturn_breadth.total_signals, 6)

    def test_stale_core_cpi_cannot_change_current_policy_state(self) -> None:
        rows = _korea_rows()
        _age_rows(rows, "KR_CORE_CPI")

        validated = KRMacroOverviewOut.model_validate(
            _build_korea_macro_overview(
                rows,
                _employment("KR", youth_weakness=True),
                policy_verified_at=date.today(),
            )
        )

        pillars = {pillar.key: pillar for pillar in validated.pillars}
        policy_metrics = {metric.key: metric for metric in pillars["monetary_policy"].metrics}
        real_proxy = policy_metrics["ex_post_real_rate_proxy"]
        self.assertEqual(pillars["inflation"].state_key, "unavailable")
        self.assertEqual(pillars["monetary_policy"].state_key, "tightening")
        self.assertEqual(real_proxy.freshness, "stale")
        self.assertEqual(real_proxy.period, pillars["inflation"].metrics[1].period)
        self.assertAlmostEqual(validated.coverage, 11 / 19, places=4)
        self.assertTrue(any(real_proxy.label in warning for warning in validated.warnings))

    def test_stale_japan_core_cpi_does_not_create_real_negative_policy_state(self) -> None:
        rows = _japan_rows()
        _age_rows(rows, "JP_CORE_CPI")

        validated = JPMacroOverviewOut.model_validate(
            _build_japan_macro_overview(
                rows,
                _employment("JP", youth_weakness=False),
            )
        )

        policy = next(
            pillar for pillar in validated.pillars if pillar.key == "monetary_policy"
        )
        real_proxy = next(
            metric for metric in policy.metrics if metric.key == "ex_post_real_rate_proxy"
        )
        self.assertEqual(policy.state_key, "normalising")
        self.assertEqual(real_proxy.freshness, "stale")

    def test_local_retrieval_time_is_not_used_as_source_verification_date(self) -> None:
        rows = _korea_rows()
        for row in rows:
            if row["indicator_code"] == "KR_BOK":
                row["retrieved_at"] = date.today()

        validated = KRMacroOverviewOut.model_validate(
            _build_korea_macro_overview(
                rows,
                _employment("KR", youth_weakness=True),
            )
        )

        policy = next(
            pillar for pillar in validated.pillars if pillar.key == "monetary_policy"
        )
        self.assertEqual(policy.state_key, "stale_policy_input")
        self.assertIn("核验至 未知", policy.summary)

    def test_stale_financial_inputs_are_displayed_but_do_not_drive_state(self) -> None:
        cases = (
            (
                JPMacroOverviewOut,
                _build_japan_macro_overview,
                _japan_rows(),
                _employment("JP", youth_weakness=False),
                ("JP_BOJ_ASSETS", "NKY"),
            ),
            (
                KRMacroOverviewOut,
                _build_korea_macro_overview,
                _korea_rows(),
                _employment("KR", youth_weakness=True),
                ("KR_RESERVES", "KOSPI"),
            ),
        )
        for schema, builder, rows, employment, codes in cases:
            with self.subTest(region=employment["region"]):
                _age_rows(rows, *codes)
                kwargs = (
                    {"policy_verified_at": date.today()}
                    if employment["region"] == "KR"
                    else {}
                )
                validated = schema.model_validate(builder(rows, employment, **kwargs))
                financial = next(
                    pillar
                    for pillar in validated.pillars
                    if pillar.key == "financial_conditions"
                )

                self.assertEqual(financial.state_key, "unavailable")
                self.assertEqual(financial.confidence, "unavailable")
                self.assertTrue(any(metric.value is not None for metric in financial.metrics))

    def test_stale_growth_labour_and_equity_are_excluded_from_downturn_breadth(self) -> None:
        rows = _japan_rows()
        _age_rows(rows, "JP_IP", "JP_CLI", "NKY")
        employment = _employment("JP", youth_weakness=False)
        _age_employment(employment)

        validated = JPMacroOverviewOut.model_validate(
            _build_japan_macro_overview(rows, employment)
        )

        pillars = {pillar.key: pillar for pillar in validated.pillars}
        self.assertEqual(pillars["growth"].state_key, "unavailable")
        self.assertEqual(pillars["labour"].state_key, "unavailable")
        self.assertEqual(validated.downturn_breadth.state_key, "unavailable")
        self.assertEqual(validated.downturn_breadth.active_signals, 0)
        self.assertEqual(validated.downturn_breadth.total_signals, 0)

    def test_employment_failure_never_zero_fills_labour(self) -> None:
        validated = JPMacroOverviewOut.model_validate(
            _build_japan_macro_overview(
                _japan_rows(), None, employment_error="TimeoutError"
            )
        )
        labour = next(pillar for pillar in validated.pillars if pillar.key == "labour")

        self.assertEqual(labour.state_key, "unavailable")
        self.assertTrue(all(metric.value is None for metric in labour.metrics))
        self.assertTrue(any("不解释为中性或零" in warning for warning in validated.warnings))

    def test_missing_calendar_month_does_not_form_fake_average(self) -> None:
        periods = _months(4)
        points = [
            {"period": observed.strftime("%Y-%m"), "value": value}
            for observed, value in zip(periods, [1.0, 1.1, 1.2, 1.3])
            if observed != periods[-2]
        ]
        self.assertIsNone(_trailing_mean(points, 3))

    def test_missing_month_never_substitutes_an_older_observation_for_a_lag(self) -> None:
        rows = _japan_rows()
        for code, lag in (
            ("JP_CLI", 3),
            ("JP_CPI", 3),
            ("JP_CORE_CPI", 3),
            ("JP_BOJ", 6),
            ("JP_BOJ_ASSETS", 12),
        ):
            _drop_month(rows, code, lag)
        employment = _employment("JP", youth_weakness=False)
        _drop_employment_month(employment, "unemployment", 3)
        _drop_employment_month(employment, "employment_ratio", 12)

        validated = JPMacroOverviewOut.model_validate(
            _build_japan_macro_overview(rows, employment)
        )
        pillars = {pillar.key: pillar for pillar in validated.pillars}
        metrics = {
            metric.key: metric
            for pillar in validated.pillars
            for metric in pillar.metrics
        }

        self.assertIsNone(metrics["composite_leading_indicator"].reference_value)
        self.assertIsNone(metrics["headline_cpi_yoy"].reference_value)
        self.assertIsNone(metrics["six_month_rate_change"].value)
        self.assertIsNone(metrics["boj_assets_yoy"].value)
        self.assertEqual(pillars["labour"].state_key, "unavailable")

    def test_api_contracts_are_registered(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("/api/analysis/jp/overview", paths)
        self.assertIn("/api/analysis/kr/overview", paths)


if __name__ == "__main__":
    unittest.main()
