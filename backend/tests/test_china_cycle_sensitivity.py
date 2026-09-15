import unittest

import pandas as pd

from app.schemas import CycleBacktestRobustnessOut
from app.services.china_cycle_backtest import (
    METHODOLOGY_VERSION,
    _build_robustness,
    _fixed_survey_matrix,
    _paired_slice,
    _sensitivity_gates,
)
from app.services.china_cycle_regime import (
    FIXED_SURVEY_CORE_CODES,
    _fixed_survey_core_panel,
)


class ChinaCycleSensitivityTests(unittest.TestCase):
    @staticmethod
    def matrix_month(period: str, scores: dict[str, float | None]) -> dict:
        return {
            "period": period,
            "coincident_index": 99.0,
            "coincident_coverage": 0.6,
            "blocks": [
                {
                    "key": "activity",
                    "role": "coincident",
                    "signals": [
                        {
                            "code": "CN_PMI_PRODUCTION",
                            "standardized_score": scores.get("CN_PMI_PRODUCTION"),
                        },
                        {
                            "code": "CN_NMI",
                            "standardized_score": scores.get("CN_NMI"),
                        },
                    ],
                },
                {
                    "key": "employment_external",
                    "role": "coincident",
                    "signals": [
                        {
                            "code": "CN_PMI_EMPLOYMENT",
                            "standardized_score": scores.get("CN_PMI_EMPLOYMENT"),
                        },
                        {
                            "code": "CN_NMI_EMPLOYMENT",
                            "standardized_score": scores.get("CN_NMI_EMPLOYMENT"),
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def comparison_row(
        period: str,
        *,
        realtime_phase: str = "recovery",
        final_phase: str = "recovery",
        comparable: bool = True,
    ) -> dict:
        realtime = {
            "phase": realtime_phase,
            "confirmed_phase": realtime_phase,
            "level_axis": "below",
            "momentum_axis": "rising",
        }
        final = {
            "phase": final_phase,
            "confirmed_phase": final_phase,
            "level_axis": "below",
            "momentum_axis": "rising",
        }
        return {
            "observation_period": period,
            "realtime": realtime,
            "final": final,
            "comparable": comparable,
            "phase_agreement": (
                realtime_phase == final_phase if comparable else None
            ),
            "decision_comparable": comparable,
            "decision_phase_agreement": (
                realtime_phase == final_phase if comparable else None
            ),
        }

    def test_fixed_survey_core_uses_four_equal_weights(self) -> None:
        scores = dict(zip(FIXED_SURVEY_CORE_CODES, (1.0, 2.0, 3.0, 4.0)))
        months = [
            self.matrix_month(str(pd.Period("2025-01", freq="M") + index), scores)
            for index in range(6)
        ]

        panel = _fixed_survey_core_panel(months, 5)
        matrix = _fixed_survey_matrix({"months": months})

        self.assertTrue(panel["valid"])
        self.assertEqual(panel["coverage"], 1.0)
        self.assertEqual(panel["level"], 125.0)
        self.assertEqual(panel["momentum"], 0.0)
        self.assertEqual(matrix["months"][-1]["coincident_index"], 125.0)
        self.assertEqual(matrix["months"][-1]["coincident_coverage"], 1.0)
        self.assertEqual(months[-1]["coincident_index"], 99.0)

    def test_fixed_survey_core_does_not_renormalize_missing_signal(self) -> None:
        complete = dict(zip(FIXED_SURVEY_CORE_CODES, (1.0, 2.0, 3.0, 4.0)))
        months = [
            self.matrix_month(str(pd.Period("2025-01", freq="M") + index), complete)
            for index in range(6)
        ]
        months[-1]["blocks"][1]["signals"][1]["standardized_score"] = None

        panel = _fixed_survey_core_panel(months, 5)
        matrix = _fixed_survey_matrix({"months": months})

        self.assertFalse(panel["valid"])
        self.assertEqual(panel["coverage"], 0.75)
        self.assertIsNone(panel["level"])
        self.assertIsNone(matrix["months"][-1]["coincident_index"])
        self.assertEqual(matrix["months"][-1]["coincident_coverage"], 0.75)

    def test_fixed_survey_core_requires_every_month_in_the_panel(self) -> None:
        complete = dict(zip(FIXED_SURVEY_CORE_CODES, (1.0, 2.0, 3.0, 4.0)))
        months = [
            self.matrix_month(str(pd.Period("2025-01", freq="M") + index), complete)
            for index in range(6)
        ]
        months[2]["blocks"][0]["signals"][0]["standardized_score"] = None

        panel = _fixed_survey_core_panel(months, 5)

        self.assertFalse(panel["valid"])
        self.assertNotIn("CN_PMI_PRODUCTION", panel["codes"])
        self.assertIsNone(panel["level"])

    def test_paired_comparison_uses_only_shared_months(self) -> None:
        periods = [str(pd.Period("2020-01", freq="M") + index) for index in range(25)]
        baseline = [self.comparison_row(period) for period in periods]
        diagnostic = [self.comparison_row(period) for period in periods]
        diagnostic[0] = self.comparison_row(
            periods[0],
            realtime_phase="contraction",
        )
        diagnostic[-1]["comparable"] = False
        diagnostic[-1]["decision_comparable"] = False

        paired = _paired_slice(baseline, diagnostic, decision=False)

        self.assertEqual(paired["common_months"], 24)
        self.assertEqual(paired["baseline_agreement_rate"], 1.0)
        self.assertEqual(paired["diagnostic_agreement_rate"], 0.9583)
        self.assertEqual(paired["agreement_rate_delta_percentage_points"], -4.17)
        self.assertEqual(
            [row["observation_period"] for row in paired["changed_judgement_months"]],
            [periods[0]],
        )
        self.assertEqual(paired["agreement_outcome_changed_months"], [periods[0]])

    def test_sensitivity_gates_require_sample_phases_and_transitions(self) -> None:
        phases = {
            "contraction": 6,
            "recovery": 6,
            "expansion": 6,
            "slowdown": 6,
        }
        distribution = {
            "display": {"sample_months": 24, "realtime": phases, "final": phases},
            "decision": {"sample_months": 24, "realtime": phases, "final": phases},
        }
        stability = {"decision_comparable_months": 24}

        passed = _sensitivity_gates(stability, {"matched_count": 5}, distribution)
        failed = _sensitivity_gates(stability, {"matched_count": 4}, distribution)

        self.assertTrue(passed["all_passed"])
        self.assertFalse(failed["all_passed"])
        self.assertEqual(failed["failed_gates"], ["matched_transitions"])

    def test_january_exclusion_is_evaluation_slice_only(self) -> None:
        rows = [
            self.comparison_row("2025-01"),
            self.comparison_row("2025-02"),
            self.comparison_row("2026-01"),
        ]

        robustness = _build_robustness(rows, rows)
        CycleBacktestRobustnessOut.model_validate(robustness)
        january = robustness["exclude_january_observation"]

        self.assertTrue(january["evaluation_window_only"])
        self.assertEqual(january["excluded_months"], ["2025-01", "2026-01"])
        self.assertEqual(january["stability"]["comparable_months"], 1)
        self.assertIn("不重算路径", january["note"])

    def test_january_exclusion_does_not_join_december_to_february(self) -> None:
        rows = [
            self.comparison_row(
                "2024-12",
                realtime_phase="contraction",
                final_phase="contraction",
            ),
            self.comparison_row(
                "2025-01",
                realtime_phase="recovery",
                final_phase="recovery",
            ),
            self.comparison_row(
                "2025-02",
                realtime_phase="expansion",
                final_phase="expansion",
            ),
        ]

        january = _build_robustness(rows, rows)["exclude_january_observation"]

        self.assertEqual(january["transitions"]["matched_count"], 0)
        self.assertEqual(january["transitions"]["events"], [])

    def test_methodology_version_includes_current_formula_readiness(self) -> None:
        self.assertEqual(METHODOLOGY_VERSION, "1.3.3")


if __name__ == "__main__":
    unittest.main()
