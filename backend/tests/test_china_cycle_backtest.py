import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

import pandas as pd

from app.main import app
from app.schemas import ChinaCycleBacktestOut
from app.services.china_cycle_backtest import (
    FORMULA_VERSIONED_CYCLE_INPUTS,
    MAX_BACKTEST_MONTHS,
    _authoritative_vintage_rows,
    _build_backtest_from_rows,
    _decision_as_of,
    _input_readiness,
    _latest_completed_period,
    _phase_snapshot,
    _regime_for_rows,
    _select_strict_vintages,
    _stability,
    _transition_summary,
)


class ChinaCycleBacktestTests(unittest.TestCase):
    @staticmethod
    def vintage(
        *,
        row_id: int,
        observation_date: date,
        value: float,
        available_at: datetime | None,
        version: int = 1,
        code: str = "CN_NMI",
        formula_version: str | None = None,
    ) -> dict:
        return {
            "id": row_id,
            "indicator_code": code,
            "date": observation_date,
            "value": value,
            "release_date": available_at.date() if available_at else None,
            "available_at": available_at,
            "retrieved_at": datetime(2026, 1, 1, 12) if available_at else datetime(2026, 1, 2, 12),
            "status": "published",
            "formula_version": formula_version,
            "version": version,
        }

    def test_fixed_decision_timestamp_is_next_month_twentieth_shanghai(self) -> None:
        utc_cutoff, display = _decision_as_of(pd.Period("2025-02", freq="M"))

        self.assertEqual(utc_cutoff, datetime(2025, 3, 20, 18))
        self.assertEqual(display, "2025-03-20T18:00:00+08:00")

    def test_latest_visible_revision_is_selected_without_future_leakage(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
            ),
            self.vintage(
                row_id=2,
                observation_date=date(2025, 1, 31),
                value=101,
                available_at=datetime(2025, 3, 1, 9),
                version=2,
            ),
        ]

        february, _ = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 2, 20),
            observation_end=date(2025, 1, 31),
        )
        march, _ = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 3, 20),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual([(row["value"], row["version"]) for row in february], [(100, 1)])
        self.assertEqual([(row["value"], row["version"]) for row in march], [(101, 2)])

    def test_verified_official_fiscal_evidence_replaces_generic_vintage(self) -> None:
        observation_date = date(2025, 1, 31)
        revised_current_vintage = self.vintage(
            row_id=1,
            observation_date=observation_date,
            value=999,
            available_at=datetime(2025, 2, 1, 9),
            code="CN_GOV_BOND_FINANCING",
        )
        initial_release_evidence = {
            **self.vintage(
                row_id=7,
                observation_date=observation_date,
                value=100,
                available_at=datetime(2025, 2, 1, 9),
                code="CN_GOV_BOND_FINANCING",
            ),
            "vintage_provenance": "release_evidence",
            "source_url": "https://www.stats.gov.cn/official-release.html",
            "provenance_json": '{"kind":"official_release"}',
            "evidence_kind": "official_release",
            "chain_verified": True,
            "availability_precision": "exact_minute",
        }

        authoritative = _authoritative_vintage_rows(
            [revised_current_vintage],
            [initial_release_evidence],
        )
        selected, _ = _select_strict_vintages(
            authoritative,
            as_of=datetime(2025, 2, 20),
            observation_end=observation_date,
        )

        self.assertEqual(len(authoritative), 1)
        self.assertEqual(selected[0]["value"], 100)
        self.assertEqual(selected[0]["vintage_provenance"], "release_evidence")
        self.assertEqual(
            selected[0]["source_url"],
            "https://www.stats.gov.cn/official-release.html",
        )
        self.assertEqual(selected[0]["evidence_kind"], "official_release")
        self.assertTrue(selected[0]["chain_verified"])
        self.assertEqual(selected[0]["availability_precision"], "exact_minute")

    def test_unverified_release_evidence_cannot_shadow_generic_vintage(self) -> None:
        observation_date = date(2025, 1, 31)
        generic = self.vintage(
            row_id=1,
            observation_date=observation_date,
            value=100,
            available_at=datetime(2025, 2, 1, 9),
        )
        disqualified_variants = (
            {
                "evidence_kind": "official_distribution_mirror",
                "chain_verified": False,
                "availability_precision": "date_upper_bound",
            },
            {
                "evidence_kind": "unclassified",
                "chain_verified": True,
                "availability_precision": "date_upper_bound",
            },
            {
                "evidence_kind": "official_release",
                "chain_verified": True,
                "availability_precision": "unknown",
            },
        )

        for row_id, qualification in enumerate(disqualified_variants, start=7):
            with self.subTest(qualification=qualification):
                disqualified = {
                    **self.vintage(
                        row_id=row_id,
                        observation_date=observation_date,
                        value=999,
                        available_at=datetime(2025, 2, 1, 9),
                    ),
                    "vintage_provenance": "release_evidence",
                    **qualification,
                }
                authoritative = _authoritative_vintage_rows(
                    [generic], [disqualified]
                )

                self.assertEqual(len(authoritative), 1)
                self.assertEqual(authoritative[0]["value"], 100)
                self.assertEqual(
                    authoritative[0]["vintage_provenance"],
                    "data_point_vintage",
                )

    def test_verified_distribution_mirror_can_shadow_generic_vintage(self) -> None:
        observation_date = date(2025, 1, 31)
        generic = self.vintage(
            row_id=1,
            observation_date=observation_date,
            value=100,
            available_at=datetime(2025, 2, 1, 9),
        )
        mirror = {
            **self.vintage(
                row_id=7,
                observation_date=observation_date,
                value=90.4,
                available_at=datetime(2025, 2, 2),
            ),
            "vintage_provenance": "release_evidence",
            "evidence_kind": "official_distribution_mirror",
            "chain_verified": True,
            "availability_precision": "date_upper_bound",
        }

        authoritative = _authoritative_vintage_rows([generic], [mirror])

        self.assertEqual(len(authoritative), 1)
        self.assertEqual(authoritative[0]["value"], 90.4)
        self.assertEqual(
            authoritative[0]["evidence_kind"], "official_distribution_mirror"
        )
        self.assertEqual(
            authoritative[0]["availability_precision"], "date_upper_bound"
        )

    def test_release_evidence_chronology_uses_availability_not_append_version(self) -> None:
        observation_date = date(2025, 1, 31)
        # The older publication was discovered second, so its append version is
        # higher.  Replay must still follow the official publication timeline.
        evidence = [
            {
                **self.vintage(
                    row_id=1,
                    observation_date=observation_date,
                    value=110,
                    available_at=datetime(2025, 3, 1, 9),
                    version=1,
                ),
                "vintage_provenance": "release_evidence",
                "evidence_kind": "official_release",
                "chain_verified": True,
                "availability_precision": "exact_minute",
            },
            {
                **self.vintage(
                    row_id=2,
                    observation_date=observation_date,
                    value=100,
                    available_at=datetime(2025, 2, 1, 9),
                    version=2,
                ),
                "vintage_provenance": "release_evidence",
                "evidence_kind": "official_release",
                "chain_verified": True,
                "availability_precision": "exact_minute",
            },
        ]
        authoritative = _authoritative_vintage_rows([], evidence)

        february, _ = _select_strict_vintages(
            authoritative,
            as_of=datetime(2025, 2, 20),
            observation_end=observation_date,
        )
        march, _ = _select_strict_vintages(
            authoritative,
            as_of=datetime(2025, 3, 20),
            observation_end=observation_date,
        )

        self.assertEqual(
            [(row["value"], row["version"]) for row in february],
            [(100, 2)],
        )
        self.assertEqual(
            [(row["value"], row["version"]) for row in march],
            [(110, 1)],
        )

    def test_unknown_available_at_is_strictly_excluded(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=None,
            )
        ]

        selected, audit = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 3, 20),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual(selected, [])
        self.assertEqual(audit["unknown_available_at_observations"], 1)

    def test_observation_month_is_locked_even_if_later_period_arrives_early(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 5),
            ),
            self.vintage(
                row_id=2,
                observation_date=date(2025, 2, 28),
                value=999,
                available_at=datetime(2025, 2, 10),
            ),
        ]

        selected, _ = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 2, 20),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual([row["date"] for row in selected], [date(2025, 1, 31)])

    def test_revision_reusing_old_available_at_is_excluded_as_ambiguous(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
            ),
            self.vintage(
                row_id=2,
                observation_date=date(2025, 1, 31),
                value=120,
                available_at=datetime(2025, 2, 1, 9),
                version=2,
            ),
        ]

        selected, audit = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 12, 31),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual(selected, [])
        self.assertEqual(audit["ambiguous_revisions_excluded"], 1)
        self.assertEqual(audit["non_reconstructable_observations_excluded"], 1)

    def test_changed_revision_without_publication_time_is_reported_and_excluded(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
            ),
            self.vintage(
                row_id=2,
                observation_date=date(2025, 1, 31),
                value=120,
                available_at=None,
                version=2,
            ),
        ]

        selected, audit = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 12, 31),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual(selected, [])
        self.assertEqual(audit["unknown_revisions_excluded"], 1)
        self.assertEqual(audit["non_reconstructable_observations_excluded"], 1)

    def test_metadata_only_revision_does_not_quarantine_observation(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
            ),
            self.vintage(
                row_id=2,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
                version=2,
            ),
        ]

        selected, audit = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 12, 31),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual([(row["value"], row["version"]) for row in selected], [(100, 1)])
        self.assertEqual(audit["ambiguous_revisions_excluded"], 1)
        self.assertEqual(audit["non_reconstructable_observations_excluded"], 0)

    def test_formula_change_without_new_publication_time_quarantines_observation(self) -> None:
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
                formula_version="1.0.0",
            ),
            self.vintage(
                row_id=2,
                observation_date=date(2025, 1, 31),
                value=100,
                available_at=datetime(2025, 2, 1, 9),
                formula_version="2.0.0",
                version=2,
            ),
        ]

        selected, audit = _select_strict_vintages(
            rows,
            as_of=datetime(2025, 12, 31),
            observation_end=date(2025, 1, 31),
        )

        self.assertEqual(selected, [])
        self.assertEqual(audit["non_reconstructable_observations_excluded"], 1)

    def test_current_a1_and_a2_cores_are_reused_with_identical_window(self) -> None:
        fake_matrix = {"months": [], "warnings": [], "methodology_version": "1.0.0"}
        fake_regime = {"months": [], "last_decision_period": None}
        rows = [
            self.vintage(
                row_id=1,
                observation_date=date(2025, 1, 31),
                value=50,
                available_at=datetime(2025, 2, 1),
            )
        ]
        with (
            patch(
                "app.services.china_cycle_backtest._matrix_from_rows",
                return_value=fake_matrix,
            ) as build_a1,
            patch(
                "app.services.china_cycle_backtest._build_regime_from_matrix",
                return_value=fake_regime,
            ) as build_a2,
        ):
            regime, month = _regime_for_rows(rows, pd.Period("2025-01", freq="M"))

        self.assertIs(regime, fake_regime)
        self.assertIsNone(month)
        self.assertEqual(build_a1.call_args.kwargs["end"], date(2025, 1, 31))
        self.assertEqual(build_a1.call_args.kwargs["months"], 240)
        self.assertEqual(build_a2.call_args.kwargs["output_months"], 240)

    def test_limited_payload_does_not_invent_accuracy_or_lag(self) -> None:
        unknown = self.vintage(
            row_id=1,
            observation_date=date(2025, 1, 31),
            value=49,
            available_at=None,
        )
        final = {**unknown, "available_at": datetime(2025, 2, 1)}
        final_month = {
            "period": "2025-01",
            "phase": "recovery",
            "phase_label": "复苏",
            "phase_status": "confirmed",
            "confirmed_phase": "recovery",
            "confirmed_since": "2025-01",
            "decision_eligible": True,
            "level_axis": "below",
            "momentum_axis": "rising",
            "level_3m": 98,
            "momentum_3m": 2,
            "coincident_index": 98,
            "leading_index": 99,
            "confidence": "medium",
        }
        with patch(
            "app.services.china_cycle_backtest._regime_for_rows",
            return_value=({"last_decision_period": "2025-01"}, final_month),
        ) as build_regime:
            payload = _build_backtest_from_rows(
                [unknown],
                [final],
                periods=pd.period_range("2025-01", periods=1, freq="M"),
                final_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        validated = ChinaCycleBacktestOut.model_validate(payload)
        self.assertEqual(validated.status, "unavailable")
        self.assertEqual(validated.coverage.display_evaluable_months, 0)
        self.assertIsNone(validated.stability.agreement_count)
        self.assertIsNone(validated.stability.agreement_rate)
        self.assertIsNone(validated.stability.flip_count)
        self.assertIsNone(validated.transitions.lag_median_months)
        self.assertEqual(validated.months[0].status, "unavailable")
        self.assertIn("no_known_available_at", validated.months[0].exclusion_reasons)
        self.assertEqual(
            validated.months[0].final_reference_status,
            "hidden_no_realtime_label",
        )
        self.assertIsNone(validated.months[0].final)
        self.assertIn(
            "final_reference_hidden_noncomparable",
            validated.months[0].exclusion_reasons,
        )
        self.assertNotIn(
            "final_same_month_unavailable",
            validated.months[0].exclusion_reasons,
        )
        self.assertIsNone(validated.months[0].coincident_revision_delta)
        self.assertIsNone(validated.months[0].leading_revision_delta)
        self.assertEqual(validated.latest, validated.months[0])
        build_regime.assert_not_called()

    def test_final_reference_is_rerun_at_the_same_observation_endpoint(self) -> None:
        vintage = self.vintage(
            row_id=1,
            observation_date=date(2025, 1, 31),
            value=49,
            available_at=datetime(2025, 2, 1, 9),
        )
        final = {**vintage, "value": 50}

        def month(phase: str) -> dict:
            return {
                "period": "2025-01",
                "phase": phase,
                "phase_label": "收缩" if phase == "contraction" else "复苏",
                "phase_status": "confirmed",
                "phase_basis": "active_decision",
                "confirmed_phase": phase,
                "confirmed_since": "2025-01",
                "last_decision_period": "2025-01",
                "carry_forward_months": 0,
                "decision_eligible": True,
                "level_axis": "below",
                "momentum_axis": "rising",
                "level_3m": 98,
                "momentum_3m": 2,
                "coincident_index": 98,
                "leading_index": 99,
                "confidence": "medium",
            }

        with (
            patch(
                "app.services.china_cycle_backtest._has_minimum_coincident_inputs",
                return_value=True,
            ),
            patch(
                "app.services.china_cycle_backtest._regime_for_rows",
                side_effect=[
                    ({"last_decision_period": "2025-01"}, month("contraction")),
                    ({"last_decision_period": "2025-01"}, month("recovery")),
                ],
            ) as build_regime,
        ):
            payload = _build_backtest_from_rows(
                [vintage],
                [final],
                periods=pd.period_range("2025-01", periods=1, freq="M"),
                final_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        validated = ChinaCycleBacktestOut.model_validate(payload)
        result = validated.months[0]
        self.assertEqual(build_regime.call_count, 2)
        self.assertTrue(
            all(
                call.args[1] == pd.Period("2025-01", freq="M")
                for call in build_regime.call_args_list
            )
        )
        self.assertEqual(result.final_reference_status, "same_endpoint_rerun")
        self.assertEqual(result.realtime.phase, "contraction")
        self.assertEqual(result.final.phase, "recovery")
        self.assertTrue(result.comparable)
        self.assertEqual(result.comparison_type, "phase_changed")

    def test_unclassified_realtime_hides_final_reference_without_losing_month(self) -> None:
        vintage = self.vintage(
            row_id=1,
            observation_date=date(2025, 1, 31),
            value=49,
            available_at=datetime(2025, 2, 1, 9),
        )
        final = {**vintage, "value": 50}
        realtime_month = {
            "period": "2025-01",
            "phase": None,
            "phase_label": "待判定",
            "phase_status": "candidate",
            "phase_basis": "pending_confirmation",
            "confirmed_phase": None,
            "confirmed_since": None,
            "last_decision_period": "2025-01",
            "carry_forward_months": 0,
            "decision_eligible": True,
            "level_axis": "below",
            "momentum_axis": "rising",
            "level_3m": 98,
            "momentum_3m": 2,
            "coincident_index": 98,
            "leading_index": 99,
            "confidence": "low",
        }

        with (
            patch(
                "app.services.china_cycle_backtest._has_minimum_coincident_inputs",
                return_value=True,
            ),
            patch(
                "app.services.china_cycle_backtest._regime_for_rows",
                return_value=({"last_decision_period": "2025-01"}, realtime_month),
            ) as build_regime,
        ):
            payload = _build_backtest_from_rows(
                [vintage],
                [final],
                periods=pd.period_range("2025-01", periods=1, freq="M"),
                final_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        validated = ChinaCycleBacktestOut.model_validate(payload)
        result = validated.months[0]
        self.assertEqual(build_regime.call_count, 1)
        self.assertEqual(result.status, "limited")
        self.assertEqual(result.final_reference_status, "hidden_no_realtime_label")
        self.assertIsNone(result.final)
        self.assertFalse(result.comparable)
        self.assertIsNone(result.coincident_revision_delta)
        self.assertIsNone(result.leading_revision_delta)
        self.assertIn(
            "final_reference_hidden_noncomparable",
            result.exclusion_reasons,
        )

    def test_exact_peer_without_same_month_result_is_reported_unavailable(self) -> None:
        vintage = self.vintage(
            row_id=1,
            observation_date=date(2025, 1, 31),
            value=49,
            available_at=datetime(2025, 2, 1, 9),
        )
        active_month = {
            "period": "2025-01",
            "phase": "contraction",
            "phase_label": "收缩",
            "phase_status": "confirmed",
            "phase_basis": "active_decision",
            "confirmed_phase": "contraction",
            "confirmed_since": "2025-01",
            "decision_eligible": True,
        }

        with (
            patch(
                "app.services.china_cycle_backtest._has_minimum_coincident_inputs",
                return_value=True,
            ),
            patch(
                "app.services.china_cycle_backtest._regime_for_rows",
                side_effect=[({"months": []}, active_month), ({"months": []}, None)],
            ) as build_regime,
        ):
            payload = _build_backtest_from_rows(
                [vintage],
                [{**vintage, "value": 50}],
                periods=pd.period_range("2025-01", periods=1, freq="M"),
                final_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        result = ChinaCycleBacktestOut.model_validate(payload).months[0]
        self.assertEqual(build_regime.call_count, 2)
        self.assertEqual(result.final_reference_status, "same_endpoint_unavailable")
        self.assertIsNone(result.final)
        self.assertIn("final_same_month_unavailable", result.exclusion_reasons)
        self.assertNotIn(
            "final_reference_hidden_noncomparable",
            result.exclusion_reasons,
        )

    def test_fixed_only_label_still_triggers_exact_endpoint_rerun(self) -> None:
        vintage = self.vintage(
            row_id=1,
            observation_date=date(2025, 1, 31),
            value=49,
            available_at=datetime(2025, 2, 1, 9),
        )

        def active_month(phase: str) -> dict:
            return {
                "period": "2025-01",
                "phase": phase,
                "phase_label": "收缩" if phase == "contraction" else "复苏",
                "phase_status": "confirmed",
                "phase_basis": "active_decision",
                "confirmed_phase": phase,
                "confirmed_since": "2025-01",
                "decision_eligible": True,
            }

        baseline_unclassified = {
            "period": "2025-01",
            "phase": None,
            "phase_label": "待判定",
            "phase_status": "candidate",
            "phase_basis": "pending_confirmation",
            "confirmed_phase": None,
            "confirmed_since": None,
            "decision_eligible": True,
        }
        baseline_final = active_month("recovery")
        regime_calls = 0

        def build_regime(rows, period, *, matrix_out=None):
            nonlocal regime_calls
            regime_calls += 1
            if matrix_out is not None:
                matrix_out["matrix"] = {"months": []}
            return (
                ({"months": [baseline_unclassified]}, baseline_unclassified)
                if regime_calls == 1
                else ({"months": [baseline_final]}, baseline_final)
            )

        with (
            patch(
                "app.services.china_cycle_backtest._has_minimum_coincident_inputs",
                return_value=True,
            ),
            patch(
                "app.services.china_cycle_backtest._regime_for_rows",
                side_effect=build_regime,
            ),
            patch(
                "app.services.china_cycle_backtest._fixed_survey_regime_from_matrix",
                side_effect=[
                    ({"months": [active_month("contraction")]}, active_month("contraction")),
                    ({"months": [active_month("recovery")]}, active_month("recovery")),
                ],
            ) as build_fixed,
        ):
            payload = _build_backtest_from_rows(
                [vintage],
                [{**vintage, "value": 50}],
                periods=pd.period_range("2025-01", periods=1, freq="M"),
                final_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

        validated = ChinaCycleBacktestOut.model_validate(payload)
        result = validated.months[0]
        self.assertEqual(regime_calls, 2)
        self.assertEqual(build_fixed.call_count, 2)
        self.assertEqual(result.final_reference_status, "same_endpoint_rerun")
        self.assertIsNone(result.realtime.phase)
        self.assertEqual(result.final.phase, "recovery")
        self.assertFalse(result.comparable)
        self.assertEqual(
            validated.robustness.fixed_survey_core.stability.comparable_months,
            1,
        )

    def test_phase_snapshot_preserves_carry_forward_explanation(self) -> None:
        snapshot = _phase_snapshot(
            {
                "phase": "contraction",
                "phase_label": "收缩",
                "phase_status": "stale",
                "phase_basis": "carried_forward",
                "confirmed_phase": "contraction",
                "confirmed_since": "2021-11",
                "last_decision_period": "2024-05",
                "carry_forward_months": 3,
                "decision_eligible": False,
            },
            {"last_decision_period": "2099-12"},
        )

        self.assertEqual(snapshot["phase_basis"], "carried_forward")
        self.assertEqual(snapshot["carry_forward_months"], 3)
        self.assertEqual(snapshot["last_decision_period"], "2024-05")
        self.assertEqual(snapshot["confirmed_phase"], "contraction")
        self.assertEqual(snapshot["confirmed_since"], "2021-11")
        self.assertFalse(snapshot["decision_eligible"])

    def test_latest_completed_period_waits_for_fixed_cutoff(self) -> None:
        before = datetime(2026, 9, 14, 12, tzinfo=UTC)
        at_cutoff = datetime(2026, 9, 20, 10, tzinfo=UTC)

        self.assertEqual(str(_latest_completed_period(before)), "2026-07")
        self.assertEqual(str(_latest_completed_period(at_cutoff)), "2026-08")

    def test_input_readiness_uses_each_inputs_model_target_month(self) -> None:
        observation_date = date(2025, 1, 31)
        published_after_source_cutoff = datetime(2025, 3, 1, 9)
        consumer = self.vintage(
            row_id=1,
            observation_date=observation_date,
            value=100,
            available_at=published_after_source_cutoff,
            code="CN_CONSUMER_EXPECTATIONS",
        )
        ordinary = self.vintage(
            row_id=2,
            observation_date=observation_date,
            value=50,
            available_at=published_after_source_cutoff,
            code="CN_NMI",
        )

        readiness = {
            item["code"]: item
            for item in _input_readiness(
                [consumer, ordinary],
                [consumer, ordinary],
            )
        }

        self.assertEqual(
            readiness["CN_CONSUMER_EXPECTATIONS"]["observation_lag_months"], 1
        )
        self.assertEqual(
            readiness["CN_CONSUMER_EXPECTATIONS"]["on_schedule_observations"], 1
        )
        self.assertEqual(readiness["CN_NMI"]["observation_lag_months"], 0)
        self.assertEqual(readiness["CN_NMI"]["on_schedule_observations"], 0)

    def test_input_readiness_uses_only_the_current_formula_regime(self) -> None:
        current_version = FORMULA_VERSIONED_CYCLE_INPUTS["CN_M1M2"]
        legacy = self.vintage(
            row_id=1,
            observation_date=date(2023, 12, 1),
            value=-8.0,
            available_at=datetime(2024, 1, 15, 9),
            code="CN_M1M2",
            formula_version="1.0.0",
        )
        legacy_same_period = self.vintage(
            row_id=2,
            observation_date=date(2024, 1, 1),
            value=-7.0,
            available_at=None,
            code="CN_M1M2",
            formula_version="1.0.0",
        )
        current_known = self.vintage(
            row_id=3,
            observation_date=date(2024, 1, 1),
            value=-5.4,
            available_at=datetime(2024, 2, 15),
            version=2,
            code="CN_M1M2",
            formula_version=current_version,
        )
        current_unknown = self.vintage(
            row_id=4,
            observation_date=date(2024, 2, 1),
            value=-6.1,
            available_at=None,
            code="CN_M1M2",
            formula_version=current_version,
        )

        readiness = {
            item["code"]: item
            for item in _input_readiness(
                [legacy, legacy_same_period, current_known, current_unknown],
                [legacy, current_known, current_unknown],
            )
        }["CN_M1M2"]

        self.assertEqual(readiness["final_observations"], 2)
        self.assertEqual(readiness["known_available_at_observations"], 1)
        self.assertEqual(readiness["on_schedule_observations"], 1)
        self.assertEqual(readiness["unknown_available_at_observations"], 1)
        self.assertEqual(readiness["unknown_revision_observations"], 0)
        self.assertEqual(readiness["non_reconstructable_observations"], 0)
        self.assertEqual(readiness["availability_rate"], 0.5)
        self.assertEqual(readiness["on_schedule_rate"], 0.5)
        self.assertEqual(readiness["first_known_period"], "2024-01")
        self.assertEqual(readiness["last_known_period"], "2024-01")

    def test_accuracy_rate_is_suppressed_below_24_comparable_months(self) -> None:
        def row(index: int) -> dict:
            snapshot = {
                "phase": "recovery",
                "confirmed_phase": "recovery",
                "level_axis": "below",
                "momentum_axis": "rising",
            }
            return {
                "observation_period": str(pd.Period("2020-01", freq="M") + index),
                "realtime": snapshot,
                "final": snapshot,
                "comparable": True,
                "phase_agreement": True,
                "decision_comparable": True,
                "decision_phase_agreement": True,
            }

        short = _stability([row(index) for index in range(23)])
        sufficient = _stability([row(index) for index in range(24)])

        self.assertEqual(short["agreement_count"], 23)
        self.assertIsNone(short["agreement_rate"])
        self.assertEqual(sufficient["agreement_rate"], 1.0)
        self.assertEqual(sufficient["flip_rate"], 0.0)

    def test_stability_separates_active_carry_pending_and_mixed_basis(self) -> None:
        def snapshot(
            phase: str,
            basis: str,
            *,
            decision_eligible: bool,
        ) -> dict:
            status = {
                "active_decision": "confirmed",
                "carried_forward": "stale",
                "pending_confirmation": "candidate",
            }[basis]
            return {
                "phase": phase,
                "confirmed_phase": (
                    None if basis == "pending_confirmation" else phase
                ),
                "phase_status": status,
                "phase_basis": basis,
                "decision_eligible": decision_eligible,
                "level_axis": "below",
                "momentum_axis": "rising",
            }

        def row(
            index: int,
            realtime_basis: str,
            final_basis: str,
            *,
            agrees: bool,
            decision_comparable: bool,
        ) -> dict:
            realtime_phase = "recovery"
            final_phase = realtime_phase if agrees else "contraction"
            realtime = snapshot(
                realtime_phase,
                realtime_basis,
                decision_eligible=realtime_basis == "active_decision",
            )
            final = snapshot(
                final_phase,
                final_basis,
                decision_eligible=final_basis == "active_decision",
            )
            return {
                "observation_period": str(pd.Period("2020-01", freq="M") + index),
                "realtime": realtime,
                "final": final,
                "comparable": True,
                "phase_agreement": agrees,
                "decision_comparable": decision_comparable,
                "decision_phase_agreement": agrees if decision_comparable else None,
            }

        rows = [
            row(
                index,
                "active_decision",
                "active_decision",
                agrees=True,
                decision_comparable=True,
            )
            for index in range(24)
        ]
        rows.extend(
            row(
                24 + index,
                "carried_forward",
                "carried_forward",
                agrees=index < 20,
                decision_comparable=False,
            )
            for index in range(24)
        )
        rows.append(
            row(
                48,
                "pending_confirmation",
                "active_decision",
                agrees=True,
                decision_comparable=False,
            )
        )
        rows.append(
            row(
                49,
                "carried_forward",
                "active_decision",
                agrees=False,
                decision_comparable=False,
            )
        )

        stability = _stability(rows)

        self.assertEqual(stability["decision_comparable_months"], 24)
        self.assertEqual(stability["decision_agreement_rate"], 1.0)
        self.assertEqual(stability["carried_forward_comparable_months"], 24)
        self.assertEqual(stability["carried_forward_agreement_count"], 20)
        self.assertEqual(stability["carried_forward_agreement_rate"], 0.8333)
        self.assertEqual(stability["carried_forward_flip_count"], 4)
        self.assertEqual(stability["carried_forward_flip_rate"], 0.1667)
        self.assertEqual(stability["pending_confirmation_comparable_months"], 1)
        self.assertEqual(stability["mixed_basis_comparable_months"], 1)
        self.assertEqual(
            stability["comparable_months"],
            stability["decision_comparable_months"]
            + stability["carried_forward_comparable_months"]
            + stability["pending_confirmation_comparable_months"]
            + stability["mixed_basis_comparable_months"],
        )
        short_carry = _stability(rows[24:47])
        self.assertEqual(short_carry["carried_forward_comparable_months"], 23)
        self.assertIsNone(short_carry["carried_forward_agreement_rate"])
        self.assertIsNone(short_carry["carried_forward_flip_rate"])

    def test_transitions_ignore_final_only_months(self) -> None:
        rows = [
            {
                "observation_period": "2025-01",
                "decision_comparable": False,
                "realtime": None,
                "final": {
                    "confirmed_phase": "recovery",
                    "confirmed_since": "2024-01",
                },
            },
            {
                "observation_period": "2025-02",
                "decision_comparable": False,
                "realtime": None,
                "final": {
                    "confirmed_phase": "expansion",
                    "confirmed_since": "2025-02",
                },
            },
        ]

        transitions = _transition_summary(rows)

        self.assertEqual(transitions["events"], [])
        self.assertEqual(transitions["matched_count"], 0)
        self.assertEqual(transitions["unmatched_final"], 0)

    def test_transition_time_is_first_observed_snapshot_not_backdated_confirmed_since(self) -> None:
        def snapshot(phase: str, confirmed_since: str) -> dict:
            return {"confirmed_phase": phase, "confirmed_since": confirmed_since}

        rows = [
            {
                "observation_period": "2025-01",
                "decision_comparable": True,
                "realtime": snapshot("recovery", "2020-01"),
                "final": snapshot("recovery", "2020-01"),
            },
            {
                "observation_period": "2025-02",
                "decision_comparable": True,
                "realtime": snapshot("expansion", "2024-12"),
                "final": snapshot("recovery", "2020-01"),
            },
            {
                "observation_period": "2025-03",
                "decision_comparable": True,
                "realtime": snapshot("expansion", "2024-12"),
                "final": snapshot("expansion", "2024-11"),
            },
        ]

        transitions = _transition_summary(rows)

        self.assertEqual(len(transitions["events"]), 1)
        event = transitions["events"][0]
        self.assertEqual(event["final_confirmation_period"], "2025-03")
        self.assertEqual(event["realtime_confirmation_period"], "2025-02")
        self.assertEqual(event["signed_lag_months"], -1)

    def test_transition_is_not_invented_across_noncomparable_gap(self) -> None:
        def snapshot(phase: str) -> dict:
            return {"confirmed_phase": phase, "confirmed_since": "2020-01"}

        rows = [
            {
                "observation_period": "2025-01",
                "decision_comparable": True,
                "realtime": snapshot("recovery"),
                "final": snapshot("recovery"),
            },
            {
                "observation_period": "2025-02",
                "decision_comparable": False,
                "realtime": None,
                "final": snapshot("expansion"),
            },
            {
                "observation_period": "2025-03",
                "decision_comparable": True,
                "realtime": snapshot("expansion"),
                "final": snapshot("expansion"),
            },
        ]

        transitions = _transition_summary(rows)

        self.assertEqual(transitions["events"], [])
        self.assertEqual(transitions["matched_count"], 0)

    def test_openapi_documents_endpoint_and_range_limit(self) -> None:
        operation = app.openapi()["paths"]["/api/analysis/cn/business-cycle/backtest"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}

        self.assertEqual(parameters["months"]["schema"]["default"], 120)
        self.assertEqual(parameters["months"]["schema"]["maximum"], MAX_BACKTEST_MONTHS)


if __name__ == "__main__":
    unittest.main()
