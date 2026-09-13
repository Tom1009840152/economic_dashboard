import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from app.main import app
from app.fetchers.macro_source import fetch_cn_gdp
from app.schemas import ChinaBusinessCycleMatrixOut
from app.services.china_business_cycle import (
    CHINA_CYCLE_BLOCKS,
    FORMULA_VERSIONED_CYCLE_INPUTS,
    VALIDATION_CODES,
    PreparedSignal,
    _annotate_comparability,
    _compose_month,
    _latest_summary,
    _matrix_from_rows,
    _one_sided_robust_zscore,
    _prepare_signal,
    _raw_series_from_rows,
    _signal_observations,
    cycle_indicator_codes,
)


class ChinaBusinessCycleTests(unittest.TestCase):
    def test_cn_gdp_periods_are_assigned_to_quarter_end_months(self) -> None:
        source = pd.DataFrame(
            {
                "季度": [
                    "2025年1季度",
                    "2025年1-2季度",
                    "2025年第1-3季度",
                    "2025年1-4季度",
                ],
                "国内生产总值-同比增长": [5.1, 5.2, 5.3, 5.4],
            }
        )
        with patch("app.fetchers.macro_source.ak.macro_china_gdp", return_value=source):
            result = fetch_cn_gdp()

        self.assertEqual(
            result["date"].tolist(),
            [
                date(2025, 3, 1),
                date(2025, 6, 1),
                date(2025, 9, 1),
                date(2025, 12, 1),
            ],
        )

    def test_current_formula_version_is_the_only_eligible_derived_history(self) -> None:
        for code, required_version in FORMULA_VERSIONED_CYCLE_INPUTS.items():
            rows = [
                {
                    "indicator_code": code,
                    "date": date(2024, 1, 31),
                    "value": 1.0,
                    "available_at": datetime(2024, 2, 1),
                    "formula_version": required_version,
                    "status": "derived",
                },
                {
                    "indicator_code": code,
                    "date": date(2024, 2, 29),
                    "value": 999.0,
                    "available_at": datetime(2024, 3, 1),
                    "formula_version": None,
                    "status": "derived_backfill",
                },
                {
                    "indicator_code": code,
                    "date": date(2024, 3, 31),
                    "value": 999.0,
                    "available_at": datetime(2024, 4, 1),
                    "formula_version": "0.9.0",
                    "status": "derived_backfill",
                },
            ]
            values, _, audit = _raw_series_from_rows(rows, code)

            self.assertEqual(values.to_dict(), {pd.Period("2024-01", freq="M"): 1.0})
            self.assertEqual(audit["required_formula_version"], required_version)
            self.assertEqual(audit["latest_formula_version"], required_version)
            self.assertEqual(audit["latest_status"], "derived")
            self.assertEqual(audit["latest_stored_observation"], "2024-03")
            self.assertEqual(audit["latest_stored_formula_version"], "0.9.0")
            self.assertEqual(audit["latest_stored_status"], "derived_backfill")
            self.assertEqual(audit["excluded_version_observations"], 2)
            self.assertEqual(audit["excluded_formula_versions"], ["0.9.0", "missing"])

    def test_formula_exclusions_are_reported_in_warning_and_latest_input(self) -> None:
        rows = self._complete_rows("2018-01", 72)
        m1m2_rows = [row for row in rows if row["indicator_code"] == "CN_M1M2"]
        m1m2_rows[-2]["formula_version"] = None
        m1m2_rows[-1]["formula_version"] = "0.9.0"
        m1m2_rows[-1]["status"] = "derived_backfill"

        payload = _matrix_from_rows(rows, end=date(2023, 12, 1), months=3)
        validated = ChinaBusinessCycleMatrixOut.model_validate(payload)
        item = next(
            value
            for value in validated.latest.input_latest_periods
            if value.code == "CN_M1M2"
        )

        required_version = FORMULA_VERSIONED_CYCLE_INPUTS["CN_M1M2"]
        self.assertEqual(item.required_formula_version, required_version)
        self.assertEqual(item.latest_formula_version, required_version)
        self.assertEqual(item.latest_status, "derived")
        self.assertEqual(item.latest_stored_observation, "2023-12")
        self.assertEqual(item.latest_stored_formula_version, "0.9.0")
        self.assertEqual(item.latest_stored_status, "derived_backfill")
        self.assertEqual(item.excluded_version_observations, 2)
        self.assertTrue(any("CN_M1M2 已排除 2 条" in warning for warning in payload["warnings"]))

    def test_m1m2_calibration_excludes_pre_2024_regime(self) -> None:
        m1m2 = next(
            signal
            for block in CHINA_CYCLE_BLOCKS
            for signal in block.signals
            if signal.key == "CN_M1M2"
        )
        periods = pd.period_range("2022-01", "2026-01", freq="M")
        values = pd.Series(
            [10_000.0 if period.year < 2024 else float(period.month) for period in periods],
            index=periods,
        )
        known = pd.Series(True, index=periods)
        observations, realtime = _signal_observations(
            m1m2,
            {"CN_M1M2": values},
            {"CN_M1M2": known},
        )
        prepared = _prepare_signal(
            observations,
            realtime,
            frequency="monthly",
            direction=1,
            calendar=periods,
        )
        expected = _one_sided_robust_zscore(
            values.loc[values.index >= pd.Period("2024-01", freq="M")],
            min_history=24,
        )

        self.assertEqual(m1m2.calibration_start, "2024-01")
        self.assertEqual(observations.index.min(), pd.Period("2024-01", freq="M"))
        self.assertEqual(prepared.score.loc[pd.Period("2026-01", freq="M")], expected.iloc[-1])
        self.assertTrue(
            all(
                signal.calibration_start is None
                for block in CHINA_CYCLE_BLOCKS
                for signal in block.signals
                if signal.key != "CN_M1M2"
            )
        )

    def test_month_comparability_detects_regular_january_composition_change(self) -> None:
        periods = pd.period_range("2024-12", "2025-01", freq="M")
        prepared = {}
        for block in CHINA_CYCLE_BLOCKS:
            for signal in block.signals:
                prepared[signal.key] = self._prepared_periods(periods, [1.0, 1.0])
        validations = {
            code: self._prepared_periods(periods, [0.0, 0.0])
            for code in VALIDATION_CODES
        }
        december = _compose_month(
            periods[0],
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations=validations,
        )
        january = _compose_month(
            periods[1],
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations=validations,
        )
        _annotate_comparability(
            [january], blocks=CHINA_CYCLE_BLOCKS, previous_month=december
        )
        self.assertTrue(january["comparable_to_previous"])
        self.assertFalse(january["composition_changed"])

        # Retail data is regularly absent in January.  The activity block can
        # still score, but its effective signal weights are no longer the same.
        prepared["CN_RETAIL"].score.loc[periods[1]] = float("nan")
        prepared["CN_RETAIL"].raw.loc[periods[1]] = float("nan")
        prepared["CN_RETAIL"].source_period.loc[periods[1]] = None
        january_gap = _compose_month(
            periods[1],
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations=validations,
        )
        _annotate_comparability(
            [january_gap], blocks=CHINA_CYCLE_BLOCKS, previous_month=december
        )
        self.assertFalse(january_gap["comparable_to_previous"])
        self.assertTrue(january_gap["composition_changed"])
        self.assertIn("available_signal_set_changed", january_gap["comparison_reasons"])
        self.assertIn("regular_january_data_gap", january_gap["comparison_reasons"])

    def test_quarterly_forward_fill_and_refresh_remain_comparable(self) -> None:
        periods = pd.period_range("2025-03", "2025-06", freq="M")
        prepared = {}
        for block in CHINA_CYCLE_BLOCKS:
            for signal in block.signals:
                prepared[signal.key] = self._prepared_periods(
                    periods, [1.0, 1.0, 1.0, 1.0]
                )
        validations = {
            code: self._prepared_periods(periods, [0.0, 0.0, 0.0, 0.0])
            for code in VALIDATION_CODES
        }
        # A quarterly value naturally ages through April/May and refreshes in
        # June. The participating signal and its effective weight do not
        # change, so those month-to-month comparisons remain like-for-like.
        fiscal = prepared["CN_FISCAL_IMPULSE_PROXY"]
        fiscal.source_period.loc[periods[1]] = str(periods[0])
        fiscal.source_period.loc[periods[2]] = str(periods[0])
        months = [
            _compose_month(
                period,
                blocks=CHINA_CYCLE_BLOCKS,
                prepared=prepared,
                validations=validations,
            )
            for period in periods
        ]
        _annotate_comparability(
            months[1:], blocks=CHINA_CYCLE_BLOCKS, previous_month=months[0]
        )

        for month in months[1:]:
            self.assertTrue(month["comparable_to_previous"])
            self.assertFalse(month["composition_changed"])
            self.assertEqual(month["comparison_reasons"], [])

    def test_finished_goods_inventory_is_not_a_duplicate_new_orders_input(self) -> None:
        demand = next(block for block in CHINA_CYCLE_BLOCKS if block.key == "demand_expectations")
        source_uses = [code for signal in demand.signals for code in signal.input_codes]
        pressure = next(
            signal
            for signal in demand.signals
            if signal.key == "CN_FINISHED_GOODS_INVENTORY_PRESSURE"
        )

        self.assertEqual(source_uses.count("CN_PMI_NEW_ORDERS"), 1)
        self.assertEqual(pressure.input_codes, ("CN_PMI_FINISHED_GOODS_INVENTORY",))
        self.assertEqual(pressure.direction, -1)
        self.assertIn("库存积压代理", pressure.name)

    def test_future_observation_cannot_change_an_earlier_score(self) -> None:
        periods = pd.period_range("2018-01", periods=40, freq="M")
        history = pd.Series(
            [float(index + (index % 5) * 0.2) for index in range(40)],
            index=periods,
        )
        original = _one_sided_robust_zscore(history, min_history=24)
        extended = pd.concat(
            [history, pd.Series([1_000_000.0], index=[pd.Period("2021-05", freq="M")])]
        )
        with_future = _one_sided_robust_zscore(extended, min_history=24)

        pd.testing.assert_series_equal(original, with_future.reindex(original.index))

    def test_future_validation_observation_does_not_extend_matrix_calendar(self) -> None:
        rows = self._complete_rows("2018-01", 72)
        rows.append(
            {
                "indicator_code": "CN_CLI",
                "date": date(2099, 1, 31),
                "value": 123.0,
                "available_at": datetime(2099, 2, 1),
                "status": "published",
            }
        )

        payload = _matrix_from_rows(rows, months=3)

        self.assertEqual(payload["months"][-1]["period"], "2023-12")
        self.assertEqual(payload["as_of"], "2023-12")

    def test_quarterly_signal_is_standardized_before_limited_forward_fill(self) -> None:
        periods = pd.period_range("2018-03", periods=10, freq="Q").asfreq("M")
        observations = pd.Series(range(10), index=periods, dtype=float)
        known = pd.Series(True, index=periods)
        calendar = pd.period_range(periods.min(), periods.max() + 3, freq="M")

        prepared = _prepare_signal(
            observations,
            known,
            frequency="quarterly",
            direction=1,
            calendar=calendar,
        )
        last = periods[-1]
        self.assertFalse(pd.isna(prepared.score.loc[last]))
        self.assertFalse(pd.isna(prepared.score.loc[last + 1]))
        self.assertFalse(pd.isna(prepared.score.loc[last + 2]))
        self.assertTrue(pd.isna(prepared.score.loc[last + 3]))
        self.assertEqual(prepared.source_period.loc[last + 2], str(last))

    def test_five_blocks_are_equal_weighted_and_missing_block_is_renormalized(self) -> None:
        period = pd.Period("2025-06", freq="M")
        prepared = {}
        for block_index, block in enumerate(CHINA_CYCLE_BLOCKS, start=1):
            for signal in block.signals:
                prepared[signal.key] = self._prepared(period, float(block_index))
        validations = {code: self._prepared(period, 0.0) for code in VALIDATION_CODES}

        complete = _compose_month(
            period,
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations=validations,
        )
        self.assertEqual(complete["composite_index"], 130.0)
        self.assertEqual(complete["coincident_index"], 130.0)
        self.assertEqual(complete["leading_index"], 130.0)
        self.assertEqual(complete["input_coverage"], 1.0)
        self.assertEqual(complete["overall_coverage"], 1.0)
        self.assertEqual(complete["confidence"], "high")
        self.assertAlmostEqual(
            sum(block["contribution"] for block in complete["blocks"]),
            30.0,
        )

        # Removing all of block 2 leaves four valid blocks. Dynamic
        # normalization produces mean(1, 3, 4, 5), not a zero-filled mean.
        for signal in CHINA_CYCLE_BLOCKS[1].signals:
            prepared[signal.key] = self._prepared(period, None)
        partial = _compose_month(
            period,
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations=validations,
        )
        self.assertEqual(partial["active_blocks"], 4)
        self.assertEqual(partial["composite_index"], 132.5)
        self.assertEqual(partial["input_coverage"], 0.8)
        self.assertEqual(partial["overall_coverage"], 0.8)
        self.assertEqual(partial["confidence"], "medium")

    def test_isolated_signal_in_ineligible_block_does_not_overstate_coverage(self) -> None:
        period = pd.Period("2025-06", freq="M")
        prepared = {
            signal.key: self._prepared(period, 1.0)
            for block in CHINA_CYCLE_BLOCKS
            for signal in block.signals
        }
        validations = {code: self._prepared(period, 0.0) for code in VALIDATION_CODES}
        # Credit impulse is 60% of its block, but one signal cannot pass the
        # block's separate minimum-two-signals gate.
        prepared["CN_M1M2"] = self._prepared(period, None)

        result = _compose_month(
            period,
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations=validations,
        )

        self.assertIsNotNone(result["composite_index"])
        self.assertEqual(result["active_blocks"], 4)
        self.assertEqual(result["input_coverage"], 0.92)
        self.assertEqual(result["overall_coverage"], 0.8)
        self.assertEqual(result["confidence"], "medium")

    def test_latest_uses_last_eligible_month_and_reports_true_quarter(self) -> None:
        rows = self._complete_rows("2018-01", 72)
        # A lone new activity release must not make an otherwise incomplete
        # month the headline month.
        rows.append(
            {
                "indicator_code": "CN_PMI_PRODUCTION",
                "date": date(2024, 1, 31),
                "value": 90.0,
                "available_at": datetime(2024, 1, 31, 9),
            }
        )
        payload = _matrix_from_rows(rows, end=date(2024, 1, 1), months=13)
        validated = ChinaBusinessCycleMatrixOut.model_validate(payload)

        self.assertEqual(validated.as_of, "2023-12")
        self.assertEqual(validated.latest.period, "2023-12")
        self.assertEqual(len(validated.blocks), 5)
        self.assertEqual(validated.mode, "current")
        self.assertEqual(validated.data_basis, "final")
        # M1-M2 is deliberately excluded before its 2024 comparable-regime
        # calibration start, accounting for its 8% total matrix weight.
        self.assertEqual(validated.realtime_coverage, 0.92)
        self.assertEqual(validated.latest.input_coverage, 0.92)
        self.assertEqual(validated.latest.overall_coverage, 0.8)
        self.assertEqual(validated.latest.confidence, "medium")

        fiscal = next(
            item
            for item in validated.latest.input_latest_periods
            if item.code == "CN_FISCAL_IMPULSE_PROXY"
        )
        self.assertEqual(fiscal.latest_observation, "2023-12")
        self.assertEqual(fiscal.used_observation, "2023-12")
        self.assertEqual(fiscal.lag_months, 0)

    def test_quarterly_latest_observation_is_not_forward_fill_month(self) -> None:
        matrix_period = pd.Period("2025-08", freq="M")
        prepared = {}
        for block in CHINA_CYCLE_BLOCKS:
            for signal in block.signals:
                prepared[signal.key] = self._prepared(matrix_period, 1.0)
        fiscal = prepared["CN_FISCAL_IMPULSE_PROXY"]
        fiscal.raw = pd.Series(
            [2.5, 2.5, 2.5],
            index=pd.period_range("2025-06", "2025-08", freq="M"),
        )
        fiscal.score = pd.Series(
            [0.5, 0.5, 0.5],
            index=pd.period_range("2025-06", "2025-08", freq="M"),
        )
        fiscal.source_period = pd.Series(
            ["2025-06", "2025-06", "2025-06"],
            index=pd.period_range("2025-06", "2025-08", freq="M"),
        )
        fiscal.realtime_known = pd.Series(
            [True, True, True],
            index=pd.period_range("2025-06", "2025-08", freq="M"),
        )
        latest_month = _compose_month(
            matrix_period,
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
            validations={code: self._prepared(matrix_period, 0.0) for code in VALIDATION_CODES},
        )
        latest = _latest_summary(
            latest_month,
            blocks=CHINA_CYCLE_BLOCKS,
            prepared=prepared,
        )
        fiscal_latest = next(
            item
            for item in latest["input_latest_periods"]
            if item["code"] == "CN_FISCAL_IMPULSE_PROXY"
        )
        self.assertEqual(fiscal_latest["latest_observation"], "2025-06")
        self.assertEqual(fiscal_latest["used_observation"], "2025-06")
        self.assertEqual(fiscal_latest["lag_months"], 2)

    def test_api_contract_is_registered_and_range_is_bounded(self) -> None:
        operation = app.openapi()["paths"]["/api/analysis/cn/business-cycle/matrix"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        self.assertEqual(parameters["months"]["schema"]["default"], 120)
        self.assertEqual(parameters["months"]["schema"]["maximum"], 240)
        with self.assertRaisesRegex(ValueError, "must not exceed 240 months"):
            _matrix_from_rows(
                self._complete_rows("2000-01", 300),
                start=date(2000, 1, 1),
                end=date(2024, 12, 1),
            )

    @staticmethod
    def _prepared(period: pd.Period, value: float | None) -> PreparedSignal:
        score = float("nan") if value is None else value
        raw = float("nan") if value is None else value * 10
        return PreparedSignal(
            score=pd.Series([score], index=[period]),
            raw=pd.Series([raw], index=[period]),
            source_period=pd.Series(
                [None if value is None else str(period)], index=[period], dtype=object
            ),
            realtime_known=pd.Series([value is not None], index=[period]),
        )

    @staticmethod
    def _prepared_periods(
        periods: pd.PeriodIndex, values: list[float | None]
    ) -> PreparedSignal:
        scores = pd.Series(
            [float("nan") if value is None else value for value in values],
            index=periods,
        )
        raw = pd.Series(
            [float("nan") if value is None else value * 10 for value in values],
            index=periods,
        )
        sources = pd.Series(
            [None if value is None else str(period) for period, value in zip(periods, values)],
            index=periods,
            dtype=object,
        )
        return PreparedSignal(
            score=scores,
            raw=raw,
            source_period=sources,
            realtime_known=pd.Series(
                [value is not None for value in values], index=periods
            ),
        )

    @staticmethod
    def _complete_rows(start: str, periods: int) -> list[dict]:
        calendar = pd.period_range(start, periods=periods, freq="M")
        rows = []
        all_codes = tuple(dict.fromkeys((*cycle_indicator_codes(), *VALIDATION_CODES)))
        quarterly = {"CN_FISCAL_IMPULSE_PROXY", "CN_GDP"}
        for code_index, code in enumerate(all_codes):
            for index, period in enumerate(calendar):
                if code in quarterly and period.month not in {3, 6, 9, 12}:
                    continue
                rows.append(
                    {
                        "indicator_code": code,
                        "date": period.end_time.date(),
                        "value": float(
                            index * (1 + code_index * 0.01)
                            + (index % 7) * 0.3
                            + code_index * 0.01
                        ),
                        "available_at": datetime.combine(period.end_time.date(), datetime.min.time()),
                        "formula_version": (
                            FORMULA_VERSIONED_CYCLE_INPUTS.get(code)
                        ),
                        "status": (
                            "derived"
                            if code in FORMULA_VERSIONED_CYCLE_INPUTS
                            else "published"
                        ),
                    }
                )
        return rows


if __name__ == "__main__":
    unittest.main()
