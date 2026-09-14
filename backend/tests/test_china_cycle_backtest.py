import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

import pandas as pd

from app.main import app
from app.schemas import ChinaCycleBacktestOut
from app.services.china_cycle_backtest import (
    MAX_BACKTEST_MONTHS,
    _build_backtest_from_rows,
    _decision_as_of,
    _latest_completed_period,
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
        ):
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

    def test_latest_completed_period_waits_for_fixed_cutoff(self) -> None:
        before = datetime(2026, 9, 14, 12, tzinfo=UTC)
        at_cutoff = datetime(2026, 9, 20, 10, tzinfo=UTC)

        self.assertEqual(str(_latest_completed_period(before)), "2026-07")
        self.assertEqual(str(_latest_completed_period(at_cutoff)), "2026-08")

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
