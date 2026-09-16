import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch

import pandas as pd

from app.main import app
from app.schemas import ChinaCycleRegimeOut
from app.services.china_cycle_regime import (
    RegimeTracker,
    _absolute_anchor,
    _advance_tracker,
    _axis,
    _balanced_role_panels,
    _buffered_matrix,
    _build_regime_from_matrix,
    _confidence,
    _drivers,
    _fixed_survey_core_panel,
    _inflation_by_month,
    _leading_direction,
    _outlook,
    _state_change_context,
)


class ChinaCycleRegimeTests(unittest.TestCase):
    def test_two_confirmed_months_switch_without_backfill(self) -> None:
        tracker = RegimeTracker()
        first = _advance_tracker(
            tracker,
            period="2025-01",
            raw_phase="expansion",
            decision_eligible=True,
            leading_direction="up",
        )
        second = _advance_tracker(
            tracker,
            period="2025-02",
            raw_phase="expansion",
            decision_eligible=True,
            leading_direction="up",
        )

        self.assertEqual(first["phase_status"], "candidate")
        self.assertEqual(first["phase_basis"], "pending_confirmation")
        self.assertEqual(first["carry_forward_months"], 0)
        self.assertFalse(first["confirmed"])
        self.assertEqual(first["candidate_since"], "2025-01")
        self.assertEqual(second["phase_status"], "confirmed")
        self.assertEqual(second["phase_basis"], "active_decision")
        self.assertEqual(second["carry_forward_months"], 0)
        self.assertEqual(second["confirmed_phase"], "expansion")
        self.assertEqual(second["confirmed_since"], "2025-02")

    def test_divergent_leading_needs_three_months_and_opposite_is_not_relabelled(self) -> None:
        tracker = RegimeTracker(
            confirmed_phase="expansion",
            confirmed_since="2024-01",
        )
        states = [
            _advance_tracker(
                tracker,
                period=f"2025-0{month}",
                raw_phase="contraction",
                decision_eligible=True,
                leading_direction="up",
            )
            for month in (1, 2, 3)
        ]

        self.assertEqual(states[0]["candidate_phase"], "contraction")
        self.assertEqual(states[0]["required_confirmation_months"], 3)
        self.assertEqual(states[1]["confirmed_phase"], "expansion")
        self.assertEqual(states[2]["confirmed_phase"], "contraction")
        self.assertEqual(states[2]["confirmed_since"], "2025-03")

    def test_reverse_adjacent_transition_keeps_observed_direction(self) -> None:
        tracker = RegimeTracker(
            confirmed_phase="slowdown",
            confirmed_since="2024-01",
        )
        state = _advance_tracker(
            tracker,
            period="2025-01",
            raw_phase="expansion",
            decision_eligible=True,
            leading_direction="up",
        )

        self.assertEqual(state["phase"], "slowdown")
        self.assertEqual(state["phase_status"], "transition")
        self.assertEqual(state["candidate_phase"], "expansion")
        self.assertEqual(state["required_confirmation_months"], 2)

    def test_opposite_transition_needs_three_months_even_with_leading(self) -> None:
        tracker = RegimeTracker(
            confirmed_phase="expansion",
            confirmed_since="2024-01",
        )
        states = [
            _advance_tracker(
                tracker,
                period=f"2025-0{month}",
                raw_phase="contraction",
                decision_eligible=True,
                leading_direction="down",
            )
            for month in (1, 2, 3)
        ]

        self.assertEqual(states[0]["candidate_phase"], "contraction")
        self.assertEqual(states[0]["required_confirmation_months"], 3)
        self.assertEqual(states[1]["confirmed_phase"], "expansion")
        self.assertEqual(states[2]["confirmed_phase"], "contraction")

    def test_uncomparable_month_never_accumulates_or_switches(self) -> None:
        tracker = RegimeTracker(
            confirmed_phase="recovery",
            confirmed_since="2024-10",
            candidate_phase="expansion",
            candidate_since="2025-01",
            candidate_streak=1,
        )
        first = _advance_tracker(
            tracker,
            period="2025-02",
            raw_phase="expansion",
            decision_eligible=False,
            leading_direction="up",
        )
        second = _advance_tracker(
            tracker,
            period="2025-03",
            raw_phase="expansion",
            decision_eligible=False,
            leading_direction="up",
        )
        resumed = _advance_tracker(
            tracker,
            period="2025-04",
            raw_phase="expansion",
            decision_eligible=True,
            leading_direction="up",
        )

        self.assertEqual(first["phase_status"], "held_uncomparable")
        self.assertEqual(first["phase_basis"], "carried_forward")
        self.assertEqual(first["carry_forward_months"], 1)
        self.assertIsNone(first["candidate_phase"])
        self.assertIsNone(first["candidate_since"])
        self.assertEqual(first["candidate_streak"], 0)
        self.assertEqual(second["phase_status"], "stale")
        self.assertEqual(second["phase_basis"], "carried_forward")
        self.assertEqual(second["carry_forward_months"], 2)
        self.assertEqual(second["confirmed_phase"], "recovery")
        self.assertIsNone(second["candidate_phase"])
        self.assertEqual(second["candidate_streak"], 0)
        self.assertEqual(resumed["phase_status"], "transition")
        self.assertEqual(resumed["phase_basis"], "active_decision")
        self.assertEqual(resumed["carry_forward_months"], 0)
        self.assertEqual(resumed["candidate_phase"], "expansion")
        self.assertEqual(resumed["candidate_since"], "2025-04")
        self.assertEqual(resumed["candidate_streak"], 1)

    def test_paused_raw_target_cannot_override_leading_reference(self) -> None:
        tracker = RegimeTracker(
            confirmed_phase="expansion",
            confirmed_since="2024-01",
            candidate_phase="contraction",
            candidate_since="2025-01",
            candidate_streak=2,
        )
        state = _advance_tracker(
            tracker,
            period="2025-02",
            raw_phase="contraction",
            decision_eligible=False,
            leading_direction="up",
        )

        self.assertEqual(state["phase"], "expansion")
        self.assertIsNone(state["candidate_phase"])
        self.assertEqual(state["leading_confirmation"], "confirmed")

    def test_outlook_names_candidate_only_when_candidate_exists(self) -> None:
        supported = _outlook(
            "up",
            "confirmed",
            candidate_phase=None,
            confirmed_phase="expansion",
        )
        divergent = _outlook(
            "down",
            "divergent",
            candidate_phase=None,
            confirmed_phase="expansion",
        )
        candidate = _outlook(
            "up",
            "confirmed",
            candidate_phase="recovery",
            confirmed_phase="contraction",
        )

        self.assertIn("已确认的扩张阶段", supported)
        self.assertNotIn("候选", supported)
        self.assertIn("转向风险", divergent)
        self.assertIn("尚无候选切换", divergent)
        self.assertIn("复苏候选", candidate)

    def test_first_uncomparable_month_has_no_formal_phase(self) -> None:
        tracker = RegimeTracker()
        state = _advance_tracker(
            tracker,
            period="2025-01",
            raw_phase="expansion",
            decision_eligible=False,
            leading_direction="up",
        )
        second = _advance_tracker(
            tracker,
            period="2025-02",
            raw_phase="expansion",
            decision_eligible=False,
            leading_direction="up",
        )

        self.assertEqual(state["phase_status"], "insufficient")
        self.assertEqual(state["phase_basis"], "unclassified")
        self.assertEqual(state["carry_forward_months"], 0)
        self.assertIsNone(state["phase"])
        self.assertEqual(state["confirmed_phase"], None)
        self.assertEqual(second["phase_status"], "insufficient")
        self.assertIsNone(second["phase"])

    def test_leading_dead_zone_is_neutral_even_when_level_is_high(self) -> None:
        self.assertEqual(_leading_direction(1.5), "up")
        self.assertEqual(_leading_direction(-1.5), "down")
        self.assertEqual(_leading_direction(1.49), "neutral")
        self.assertEqual(_leading_direction(-1.49), "neutral")
        self.assertEqual(_leading_direction(None), "unavailable")

    def test_dead_zone_inherits_confirmed_axis(self) -> None:
        self.assertEqual(_axis(0.2, kind="level", fallback_phase="slowdown"), "above")
        self.assertEqual(_axis(-0.2, kind="momentum", fallback_phase="recovery"), "rising")
        self.assertEqual(_axis(0.2, kind="level", fallback_phase=None), "neutral")
        self.assertEqual(_axis(1.5, kind="level", fallback_phase=None), "above")
        self.assertEqual(_axis(-1.5, kind="level", fallback_phase=None), "below")
        self.assertEqual(_axis(1.5, kind="momentum", fallback_phase=None), "rising")
        self.assertEqual(_axis(-1.5, kind="momentum", fallback_phase=None), "falling")

    def test_one_uncomparable_month_is_low_but_two_are_insufficient(self) -> None:
        one_month = _confidence(
            coincident_confidence="high",
            phase_status="held_uncomparable",
            confirmed=True,
            leading_confirmation="unavailable",
            absolute_conflict=False,
            recent_two_comparable=False,
            diagnostics_available=False,
            undecidable_streak=1,
        )
        two_months = _confidence(
            coincident_confidence="high",
            phase_status="stale",
            confirmed=True,
            leading_confirmation="unavailable",
            absolute_conflict=False,
            recent_two_comparable=False,
            diagnostics_available=False,
            undecidable_streak=2,
        )

        self.assertEqual(one_month[0], "low")
        self.assertEqual(two_months[0], "insufficient")

    def test_unclassified_dead_zone_is_insufficient_not_low(self) -> None:
        confidence, reasons = _confidence(
            coincident_confidence="high",
            phase_status="insufficient",
            confirmed=False,
            leading_confirmation="neutral",
            absolute_conflict=False,
            recent_two_comparable=True,
            diagnostics_available=True,
            undecidable_streak=0,
        )

        self.assertEqual(confidence, "insufficient")
        self.assertTrue(reasons)

    def test_balanced_panel_keeps_january_hard_data_gap_decidable_after_change_month(self) -> None:
        periods = pd.period_range("2024-01", periods=18, freq="M")
        months = [
            self._a1_month(str(period), 82 + index, 83 + index)
            for index, period in enumerate(periods)
        ]
        january_index = next(index for index, period in enumerate(periods) if str(period) == "2025-01")
        for signal in months[january_index]["blocks"][0]["signals"]:
            if signal["code"] in {"CN_RETAIL", "CN_IP"}:
                signal["standardized_score"] = None
        panels = _balanced_role_panels(months, "coincident")

        self.assertTrue(panels[january_index]["valid"])
        self.assertTrue(panels[january_index]["changed"])
        for index in range(january_index + 1, january_index + 5):
            self.assertTrue(panels[index]["valid"])
            self.assertFalse(panels[index]["changed"])
            self.assertIsNotNone(panels[index]["momentum"])

        matrix = {
            "mode": "current",
            "data_basis": "final",
            "as_of": str(periods[-1]),
            "methodology_version": "1.0.0",
            "months": months,
            "warnings": [],
        }
        payload = _build_regime_from_matrix(matrix, [], output_months=18)
        rows = {item["period"]: item for item in payload["months"]}
        self.assertFalse(rows["2025-01"]["decision_eligible"])
        self.assertTrue(rows["2025-01"]["coincident_basis_changed"])
        self.assertEqual(rows["2025-01"]["phase_basis"], "carried_forward")
        self.assertEqual(rows["2025-01"]["carry_forward_months"], 1)
        self.assertEqual(rows["2025-01"]["last_decision_period"], "2024-12")
        for period in ("2025-02", "2025-03", "2025-04", "2025-05"):
            self.assertTrue(rows[period]["decision_eligible"])
            self.assertEqual(rows[period]["phase_basis"], "active_decision")
            self.assertEqual(rows[period]["carry_forward_months"], 0)
            self.assertEqual(rows[period]["last_decision_period"], period)

    def test_low_weight_two_signal_block_is_not_basis_eligible(self) -> None:
        periods = pd.period_range("2025-01", periods=6, freq="M")
        months = [self._a1_month(str(period), 95, 95) for period in periods]
        property_block_index = 3
        for month in months:
            for signal in month["blocks"][property_block_index]["signals"]:
                if signal["code"] not in {
                    "CN_RE_STARTS_YTD_YOY",
                    "CN_FISCAL_IMPULSE_PROXY",
                }:
                    signal["standardized_score"] = None
        panel = _balanced_role_panels(months, "leading")[-1]

        self.assertNotIn("CN_RE_STARTS_YTD_YOY", panel["codes"])
        self.assertNotIn("CN_FISCAL_IMPULSE_PROXY", panel["codes"])

    def test_leading_role_coverage_requires_the_complete_missing_data_package(self) -> None:
        periods = pd.period_range("2025-07", periods=6, freq="M")
        complete = [self._a1_month(str(period), 100, 100) for period in periods]

        def panel_with(*available: str) -> dict:
            months = deepcopy(complete)
            optional = {
                "CN_CONSUMER_EXPECTATIONS",
                "CN_CREDIT_IMPULSE",
                "CN_M1M2",
                "CN_RE_PRICE_RISING_SHARE",
                "CN_FISCAL_IMPULSE_PROXY",
            }
            for month in months:
                for block in month["blocks"]:
                    if block["role"] != "leading":
                        continue
                    for signal in block["signals"]:
                        if signal["code"] in optional and signal["code"] not in available:
                            signal["standardized_score"] = None
            return _balanced_role_panels(months, "leading")[-1]

        baseline = panel_with()
        house_price = panel_with("CN_RE_PRICE_RISING_SHARE")
        complete_non_credit = panel_with(
            "CN_CONSUMER_EXPECTATIONS",
            "CN_RE_PRICE_RISING_SHARE",
            "CN_FISCAL_IMPULSE_PROXY",
        )
        credit_without_house = panel_with("CN_CREDIT_IMPULSE", "CN_M1M2")
        credit_with_house = panel_with(
            "CN_CREDIT_IMPULSE",
            "CN_M1M2",
            "CN_RE_PRICE_RISING_SHARE",
        )

        self.assertEqual(baseline["coverage"], 0.5)
        self.assertFalse(baseline["valid"])
        self.assertEqual(house_price["coverage"], 0.5667)
        self.assertFalse(house_price["valid"])
        self.assertEqual(complete_non_credit["coverage"], 0.6667)
        self.assertTrue(complete_non_credit["valid"])
        self.assertEqual(credit_without_house["coverage"], 0.8333)
        self.assertTrue(credit_without_house["valid"])
        self.assertEqual(credit_with_house["coverage"], 0.9)
        self.assertTrue(credit_with_house["valid"])

    def test_credit_needs_consumer_expectations_when_property_ytd_is_absent(self) -> None:
        periods = pd.period_range("2025-01", periods=6, freq="M")
        complete = [self._a1_month(str(period), 100, 100) for period in periods]

        def january_window(*, consumer_available: bool) -> dict:
            months = deepcopy(complete)
            for month in months:
                for block in month["blocks"]:
                    if block["key"] == "property_fiscal":
                        for signal in block["signals"]:
                            if signal["code"] in {
                                "CN_RE_SALES_AREA_YTD_YOY",
                                "CN_RE_STARTS_YTD_YOY",
                                "CN_RE_INVEST_YTD_YOY",
                            }:
                                signal["standardized_score"] = None
                    if block["key"] == "demand_expectations" and not consumer_available:
                        for signal in block["signals"]:
                            if signal["code"] == "CN_CONSUMER_EXPECTATIONS":
                                signal["standardized_score"] = None
            return _balanced_role_panels(months, "leading")[-1]

        without_consumer = january_window(consumer_available=False)
        with_consumer = january_window(consumer_available=True)

        self.assertEqual(without_consumer["coverage"], 0.6167)
        self.assertFalse(without_consumer["valid"])
        self.assertEqual(with_consumer["coverage"], 0.6667)
        self.assertTrue(with_consumer["valid"])

    def test_absolute_breadth_is_separate_and_flags_hard_conflict(self) -> None:
        above = [self._a1_month(f"2025-0{month}", 100, 100, anchor_value=51) for month in (1, 2, 3)]
        supportive = _absolute_anchor(above, 2, "expansion")
        self.assertEqual(supportive["state"], "expansionary")
        self.assertEqual(supportive["breadth"], 1.0)
        self.assertFalse(supportive["conflict"])

        below = [self._a1_month(f"2025-0{month}", 100, 100, anchor_value=49) for month in (1, 2, 3)]
        conflicting = _absolute_anchor(below, 2, "expansion")
        self.assertEqual(conflicting["state"], "contractionary")
        self.assertTrue(conflicting["conflict"])

    def test_phase_drivers_exclude_leading_blocks(self) -> None:
        month = self._a1_month("2025-01", 105, 100)
        positives = _drivers(month, True)

        self.assertTrue(positives)
        self.assertTrue(all(item["block_key"] in {"activity", "employment_external"} for item in positives))
        self.assertFalse(any(item["block_key"] == "demand_expectations" for item in positives))
        self.assertAlmostEqual(sum(item["contribution"] for item in positives), 5.0)

    def test_balanced_panel_driver_decomposition_reconciles_both_axes(self) -> None:
        values = [95.0, 97.0, 99.0, 101.0, 103.0, 105.0]
        months = [
            self._a1_month(f"2025-0{index}", value, 100.0)
            for index, value in enumerate(values, start=1)
        ]

        panel = _balanced_role_panels(months, "coincident")[-1]
        decomposition = panel["decomposition"]

        self.assertTrue(panel["valid"])
        self.assertEqual(decomposition["status"], "available")
        self.assertEqual(decomposition["recent_window_start"], "2025-04")
        self.assertEqual(decomposition["comparison_window_end"], "2025-03")
        self.assertAlmostEqual(decomposition["level_gap"], 3.0)
        self.assertAlmostEqual(decomposition["momentum_3m"], 6.0)
        self.assertAlmostEqual(
            sum(item["effective_weight"] for item in decomposition["drivers"]),
            1.0,
        )
        self.assertAlmostEqual(
            sum(item["level_contribution"] for item in decomposition["drivers"]),
            decomposition["level_contribution_sum"],
            places=5,
        )
        self.assertAlmostEqual(
            sum(item["momentum_contribution"] for item in decomposition["drivers"]),
            decomposition["momentum_contribution_sum"],
            places=5,
        )
        self.assertEqual(decomposition["level_residual"], 0.0)
        self.assertEqual(decomposition["momentum_residual"], 0.0)
        self.assertTrue(decomposition["additivity_passed"])
        self.assertEqual(
            decomposition["drivers"][0]["recent_source_periods"],
            ["2025-04", "2025-05", "2025-06"],
        )

    def test_driver_decomposition_never_turns_missing_history_into_zero(self) -> None:
        months = [
            self._a1_month(f"2025-0{index}", 100.0, 100.0)
            for index in range(1, 6)
        ]

        decomposition = _balanced_role_panels(months, "coincident")[-1][
            "decomposition"
        ]

        self.assertEqual(decomposition["status"], "unavailable")
        self.assertEqual(decomposition["reason"], "insufficient_history")
        self.assertIsNone(decomposition["level_gap"])
        self.assertIsNone(decomposition["momentum_3m"])
        self.assertFalse(decomposition["additivity_passed"])

    def test_fixed_survey_decomposition_uses_four_equal_weights_and_reconciles(self) -> None:
        values = [95.0, 97.0, 99.0, 101.0, 103.0, 105.0]
        months = [
            self._a1_month(f"2025-0{index}", value, 100.0)
            for index, value in enumerate(values, start=1)
        ]

        panel = _fixed_survey_core_panel(months, 5)
        decomposition = panel["decomposition"]

        self.assertTrue(panel["valid"])
        self.assertEqual(decomposition["status"], "available")
        self.assertEqual(len(decomposition["drivers"]), 4)
        self.assertTrue(
            all(item["effective_weight"] == 0.25 for item in decomposition["drivers"])
        )
        self.assertAlmostEqual(decomposition["level_gap"], 3.0)
        self.assertAlmostEqual(decomposition["momentum_3m"], 6.0)
        self.assertEqual(decomposition["level_residual"], 0.0)
        self.assertEqual(decomposition["momentum_residual"], 0.0)
        self.assertTrue(decomposition["additivity_passed"])

    def test_fixed_survey_decomposition_is_unavailable_when_one_signal_is_missing(self) -> None:
        months = [
            self._a1_month(f"2025-0{index}", 100.0, 100.0)
            for index in range(1, 7)
        ]
        months[-1]["blocks"][0]["signals"][0]["standardized_score"] = None

        decomposition = _fixed_survey_core_panel(months, 5)["decomposition"]

        self.assertEqual(decomposition["status"], "unavailable")
        self.assertEqual(decomposition["reason"], "insufficient_common_basis")
        self.assertEqual(decomposition["drivers"], [])
        self.assertFalse(decomposition["additivity_passed"])

    def test_internal_replay_can_skip_explanations_without_changing_state(self) -> None:
        periods = pd.period_range("2025-01", periods=10, freq="M")
        matrix = {
            "mode": "current",
            "data_basis": "final",
            "methodology_version": "1.0.0",
            "months": [
                self._a1_month(str(period), 91.0 + index * 2, 100.0)
                for index, period in enumerate(periods)
            ],
            "warnings": [],
        }

        explained = _build_regime_from_matrix(matrix, [], output_months=10)
        replay = _build_regime_from_matrix(
            matrix,
            [],
            output_months=10,
            include_explanations=False,
        )

        self.assertEqual(
            [
                (month["phase"], month["phase_status"], month["candidate_phase"])
                for month in explained["months"]
            ],
            [
                (month["phase"], month["phase_status"], month["candidate_phase"])
                for month in replay["months"]
            ],
        )
        self.assertNotIn("coincident_decomposition", replay["latest"])
        self.assertNotIn("leading_decomposition", replay["latest"])
        self.assertNotIn("state_change", replay["latest"])

    def test_fixed_survey_replay_without_explanations_keeps_coordinates_and_state(self) -> None:
        periods = pd.period_range("2025-01", periods=10, freq="M")
        matrix = {
            "mode": "current",
            "data_basis": "final",
            "methodology_version": "1.0.0",
            "months": [
                self._a1_month(str(period), 91.0 + index * 2, 100.0)
                for index, period in enumerate(periods)
            ],
            "warnings": [],
        }
        shared = {
            "output_months": 10,
            "coincident_panel_mode": "fixed_survey_core_v1",
        }

        explained = _build_regime_from_matrix(matrix, [], **shared)
        replay = _build_regime_from_matrix(
            matrix,
            [],
            include_explanations=False,
            **shared,
        )
        compared_fields = (
            "phase",
            "confirmed_phase",
            "phase_status",
            "candidate_phase",
            "level_3m",
            "momentum_3m",
            "decision_eligible",
        )

        self.assertEqual(
            [tuple(month[field] for field in compared_fields) for month in explained["months"]],
            [tuple(month[field] for field in compared_fields) for month in replay["months"]],
        )

    def test_state_change_context_separates_axes_candidate_and_confirmation_rule(self) -> None:
        context = _state_change_context(
            previous_decision={
                "period": "2025-05",
                "level_axis": "below",
                "momentum_axis": "falling",
                "raw_phase": "contraction",
                "confirmed_phase": "contraction",
            },
            tracker_before={
                "confirmed_phase": "contraction",
                "candidate_phase": None,
            },
            state={
                "confirmed_phase": "contraction",
                "candidate_phase": "recovery",
                "phase_basis": "active_decision",
                "required_confirmation_months": 2,
            },
            decision_eligible=True,
            basis_changed=False,
            level_gap=-2.0,
            momentum=2.0,
            level_axis="below",
            momentum_axis="rising",
            raw_phase="recovery",
        )

        self.assertEqual(context["previous_decision_period"], "2025-05")
        self.assertNotIn("level_axis_changed", context["reason_codes"])
        self.assertIn("momentum_axis_changed", context["reason_codes"])
        self.assertIn("raw_phase_changed", context["reason_codes"])
        self.assertIn("candidate_started", context["reason_codes"])
        self.assertIn("leading_shortened_confirmation", context["reason_codes"])

    def test_state_change_labels_complete_dead_zone_as_unclassified_not_missing(self) -> None:
        context = _state_change_context(
            previous_decision=None,
            tracker_before={
                "confirmed_phase": None,
                "candidate_phase": None,
            },
            state={
                "confirmed_phase": None,
                "candidate_phase": None,
                "phase_basis": "unclassified",
                "required_confirmation_months": None,
            },
            decision_eligible=False,
            basis_changed=False,
            level_gap=0.2,
            momentum=-0.2,
            level_axis="neutral",
            momentum_axis="neutral",
            raw_phase=None,
        )

        self.assertIn("dead_zone_unclassified", context["reason_codes"])
        self.assertNotIn("insufficient_hold", context["reason_codes"])

    def test_missing_leading_role_does_not_block_three_month_confirmation(self) -> None:
        periods = pd.period_range("2025-01", periods=10, freq="M")
        months = []
        for index, period in enumerate(periods):
            month = self._a1_month(str(period), 80 + index, 100)
            month["leading_index"] = None
            month["leading_coverage"] = 0.0
            month["confidence"] = "insufficient"
            for block in month["blocks"]:
                if block["role"] != "leading":
                    continue
                block["score"] = None
                for signal in block["signals"]:
                    signal["standardized_score"] = None
                    signal["contribution"] = None
            months.append(month)
        matrix = {
            "mode": "current",
            "data_basis": "final",
            # Deliberately emulate A1 overall as_of falling behind.
            "as_of": "2025-06",
            "methodology_version": "1.0.0",
            "months": months,
            "warnings": [],
        }

        payload = _build_regime_from_matrix(matrix, [], output_months=10)

        self.assertEqual(payload["as_of"], "2025-10")
        self.assertEqual(payload["latest"]["period"], "2025-10")
        self.assertEqual(payload["latest"]["confirmed_phase"], "recovery")
        self.assertEqual(payload["latest"]["phase_status"], "confirmed")
        self.assertEqual(payload["latest"]["leading_direction"], "unavailable")
        self.assertEqual(payload["latest"]["confidence"], "medium")
        self.assertTrue(
            payload["latest"]["positive_contributions"]
            or payload["latest"]["negative_contributions"]
        )

    def test_inflation_uses_core_cpi_and_ppi_without_cpi_fallback(self) -> None:
        calendar = pd.period_range("2024-01", periods=24, freq="M")
        core_values = [-0.2] * 6 + [0.3] * 6 + [1.0] * 6 + [1.0, 1.0, 1.0, 1.7, 1.9, 2.1]
        ppi_values = [-2.0] * 6 + [0.2] * 6 + [0.0] * 6 + [0.0] * 6
        rows = []
        for code, values in (("CN_CORE_CPI", core_values), ("CN_PPI", ppi_values)):
            rows.extend(
                {
                    "indicator_code": code,
                    "date": period.end_time.date(),
                    "value": value,
                }
                for period, value in zip(calendar, values)
            )
        result = _inflation_by_month(rows, calendar)

        self.assertEqual(result["2024-06"]["state"], "deflation_pressure")
        self.assertEqual(result["2024-12"]["state"], "low_inflation")
        self.assertEqual(result["2025-06"]["state"], "moderate")
        self.assertEqual(result["2025-12"]["state"], "heating")
        self.assertEqual(result["2025-12"]["direction"], "reflation")

        stable_high = _inflation_by_month(
            [
                {
                    "indicator_code": "CN_CORE_CPI",
                    "date": period.end_time.date(),
                    "value": 2.0,
                }
                for period in pd.period_range("2025-01", periods=6, freq="M")
            ],
            pd.period_range("2025-01", periods=6, freq="M"),
        )
        self.assertEqual(stable_high["2025-06"]["state"], "moderate")
        self.assertEqual(stable_high["2025-06"]["direction"], "stable")

        no_core = _inflation_by_month(
            [
                {"indicator_code": "CN_CPI", "date": date(2025, 1, 31), "value": 3.0}
            ],
            pd.period_range("2025-01", periods=3, freq="M"),
        )
        self.assertEqual(no_core["2025-03"]["state"], "unavailable")

    def test_full_payload_validates_and_api_contract_is_registered(self) -> None:
        periods = pd.period_range("2023-01", periods=24, freq="M")
        months = [
            self._a1_month(
                str(period),
                94.0 + index * 0.7,
                95.0 + index * 0.8,
                anchor_value=49.0 + index * 0.1,
            )
            for index, period in enumerate(periods)
        ]
        matrix = {
            "mode": "current",
            "data_basis": "final",
            "as_of": str(periods[-1]),
            "methodology_version": "1.0.0",
            "months": months,
            "warnings": ["A1 warning"],
        }
        inflation_rows = []
        for code, base in (("CN_CORE_CPI", 0.8), ("CN_PPI", -0.5)):
            inflation_rows.extend(
                {
                    "indicator_code": code,
                    "date": period.end_time.date(),
                    "value": base + index * 0.01,
                }
                for index, period in enumerate(periods)
            )

        payload = _build_regime_from_matrix(matrix, inflation_rows, output_months=12)
        validated = ChinaCycleRegimeOut.model_validate(payload)
        self.assertEqual(len(validated.months), 12)
        self.assertEqual(validated.as_of, "2024-12")
        self.assertEqual(validated.data_basis, "final")
        self.assertEqual(validated.methodology_version, "1.1.0")
        self.assertEqual(validated.a1_warnings, ["A1 warning"])
        self.assertEqual(
            validated.latest.last_decision_period,
            validated.last_decision_period,
        )
        self.assertLessEqual(len(validated.trajectory), 12)
        self.assertIn("immediately resets", validated.methodology.basis_change_policy)
        self.assertTrue(any("清空未确认候选" in item for item in validated.change_conditions))

        operation = app.openapi()["paths"]["/api/analysis/cn/business-cycle/regime"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        self.assertEqual(parameters["months"]["schema"]["default"], 120)
        self.assertEqual(parameters["months"]["schema"]["maximum"], 240)

    def test_display_window_does_not_change_latest_state(self) -> None:
        periods = pd.period_range("2010-01", periods=180, freq="M")
        months = [
            self._a1_month(
                str(period),
                100 + 4 * ((index % 24) - 12) / 12,
                100 + 5 * (((index + 3) % 24) - 12) / 12,
                anchor_value=49.5 + (index % 5) * 0.25,
            )
            for index, period in enumerate(periods)
        ]
        matrix = {
            "mode": "current",
            "data_basis": "final",
            "as_of": str(periods[-1]),
            "methodology_version": "1.0.0",
            "months": months,
            "warnings": [],
        }

        short = _build_regime_from_matrix(matrix, [], output_months=24)
        long = _build_regime_from_matrix(matrix, [], output_months=120)

        for key in ("period", "phase", "phase_status", "confirmed_phase", "confirmed_since", "duration_months"):
            self.assertEqual(short["latest"][key], long["latest"][key])

    def test_appending_future_months_does_not_rewrite_prior_state(self) -> None:
        periods = pd.period_range("2018-01", periods=60, freq="M")
        months = [
            self._a1_month(
                str(period),
                96 + index * 0.18,
                95 + index * 0.2,
                anchor_value=49 + index * 0.02,
            )
            for index, period in enumerate(periods)
        ]
        base_matrix = {
            "mode": "current",
            "data_basis": "final",
            "as_of": str(periods[47]),
            "methodology_version": "1.0.0",
            "months": months[:48],
            "warnings": [],
        }
        extended_matrix = {
            **base_matrix,
            "as_of": str(periods[-1]),
            "months": months,
        }

        base = _build_regime_from_matrix(base_matrix, [], output_months=48)
        extended = _build_regime_from_matrix(extended_matrix, [], output_months=60)
        before = base["months"][-1]
        after = next(item for item in extended["months"] if item["period"] == before["period"])

        for key in (
            "phase",
            "raw_phase",
            "phase_status",
            "confirmed_phase",
            "candidate_phase",
            "confirmed_since",
            "level_3m",
            "momentum_3m",
        ):
            self.assertEqual(before[key], after[key])

    def test_matrix_loader_always_uses_maximum_history_even_with_start(self) -> None:
        fake = {
            "months": [{"period": "2025-12"}],
            "as_of": "2025-12",
        }
        db = object()
        with patch(
            "app.services.china_cycle_regime.build_china_business_cycle_matrix",
            return_value=fake,
        ) as build:
            matrix, output_start = _buffered_matrix(
                db,
                start=date(2025, 1, 1),
                end=None,
                months=24,
            )

        self.assertIs(matrix, fake)
        self.assertEqual(output_start, pd.Period("2025-01", freq="M"))
        build.assert_called_once_with(db, end=None, months=240)

    @staticmethod
    def _a1_month(
        period: str,
        coincident: float,
        leading: float | None,
        *,
        anchor_value: float = 51.0,
    ) -> dict:
        coincident_score = (coincident - 100.0) / 10.0
        leading_score = None if leading is None else (leading - 100.0) / 10.0

        def signal(
            code: str,
            weight: float,
            score: float | None,
            raw: float = anchor_value,
        ) -> dict:
            return {
                "code": code,
                "name": code,
                "raw_value": raw,
                "source_period": period,
                "weight": weight,
                "standardized_score": score,
                "contribution": 0.2,
            }

        return {
            "period": period,
            "coincident_index": coincident,
            "leading_index": leading,
            "confidence": "high",
            "realtime_coverage": 0.8,
            "coincident_coverage": 1.0,
            "leading_coverage": 1.0,
            "blocks": [
                {
                    "key": "activity",
                    "role": "coincident",
                    "weight": 0.2,
                    "score": coincident,
                    "signals": [
                        signal("CN_PMI_PRODUCTION", 0.30, coincident_score),
                        signal("CN_NMI", 0.30, coincident_score),
                        signal("CN_RETAIL", 0.25, coincident_score),
                        signal("CN_IP", 0.15, coincident_score),
                    ],
                },
                {
                    "key": "demand_expectations",
                    "role": "leading",
                    "weight": 0.2,
                    "score": leading,
                    "signals": [
                        signal("CN_DOMESTIC_NEW_ORDERS", 0.30, leading_score),
                        signal("CN_PMI_NEW_EXPORT_ORDERS", 0.20, leading_score),
                        signal("CN_FINISHED_GOODS_INVENTORY_PRESSURE", 0.15, leading_score),
                        signal("CN_BUSINESS_EXPECTATIONS", 0.20, leading_score),
                        signal("CN_CONSUMER_EXPECTATIONS", 0.15, leading_score),
                    ],
                },
                {
                    "key": "credit_policy",
                    "role": "leading",
                    "weight": 0.2,
                    "score": leading,
                    "signals": [
                        signal("CN_CREDIT_IMPULSE", 0.60, leading_score),
                        signal("CN_M1M2", 0.40, leading_score),
                    ],
                },
                {
                    "key": "property_fiscal",
                    "role": "leading",
                    "weight": 0.2,
                    "score": leading,
                    "signals": [
                        signal("CN_RE_SALES_AREA_YTD_YOY", 0.25, leading_score),
                        signal("CN_RE_STARTS_YTD_YOY", 0.20, leading_score),
                        signal("CN_RE_INVEST_YTD_YOY", 0.20, leading_score),
                        signal("CN_RE_PRICE_RISING_SHARE", 0.20, leading_score),
                        signal("CN_FISCAL_IMPULSE_PROXY", 0.15, leading_score),
                    ],
                },
                {
                    "key": "employment_external",
                    "role": "coincident",
                    "weight": 0.2,
                    "score": coincident,
                    "signals": [
                        signal("CN_PMI_EMPLOYMENT", 0.30, coincident_score),
                        signal("CN_NMI_EMPLOYMENT", 0.30, coincident_score),
                        signal("CN_EXPORTS_3M_AVG", 0.40, coincident_score),
                    ],
                },
            ],
        }


if __name__ == "__main__":
    unittest.main()
