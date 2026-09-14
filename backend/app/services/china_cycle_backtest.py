"""Strict pseudo-real-time backtest for the China A1/A2 cycle model.

The service deliberately contains no second scoring or regime model.  For each
fixed historical decision timestamp it reconstructs the latest *safe* vintage
visible at that timestamp, then calls the existing A1 calculation core and A2
state machine.  The ex-post reference uses the same observation-month cutoff
and the same A1/A2 code, changing only the data vintage.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from statistics import median
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indicator_defs import INDICATOR_DEFS
from app.models import DataPoint, DataPointVintage
from app.services.china_business_cycle import (
    BLOCK_MIN_COVERAGE,
    CHINA_CYCLE_BLOCKS,
    FORMULA_VERSIONED_CYCLE_INPUTS,
    MAX_OUTPUT_MONTHS,
    METHODOLOGY_VERSION as A1_METHODOLOGY_VERSION,
    VALIDATION_CODES,
    _matrix_from_rows,
    cycle_indicator_codes,
)
from app.services.china_cycle_regime import (
    METHODOLOGY_VERSION as A2_METHODOLOGY_VERSION,
    _build_regime_from_matrix,
)


METHODOLOGY_VERSION = "1.0.0"
DEFAULT_BACKTEST_MONTHS = 120
MAX_BACKTEST_MONTHS = 120
MIN_RATE_SAMPLE = 24
MIN_TRANSITION_SAMPLE = 5
DECISION_DAY = 20
DECISION_HOUR = 18
DECISION_TIMEZONE = "Asia/Shanghai"
INFLATION_CODES = ("CN_CORE_CPI", "CN_PPI")
CYCLE_CODES = cycle_indicator_codes()
MODEL_CODES = tuple(dict.fromkeys((*CYCLE_CODES, *VALIDATION_CODES, *INFLATION_CODES)))
REQUIRED_CODES = tuple(dict.fromkeys((*CYCLE_CODES, *INFLATION_CODES)))
_DEFINITIONS = {row["code"]: row for row in INDICATOR_DEFS}


def _period_distance(left: str | pd.Period, right: str | pd.Period) -> int:
    left_period = pd.Period(left, freq="M")
    right_period = pd.Period(right, freq="M")
    return (left_period.year - right_period.year) * 12 + left_period.month - right_period.month


def _decision_as_of(period: pd.Period) -> tuple[datetime, str]:
    """Return the fixed next-month 20th 18:00 Shanghai decision timestamp.

    China-source publication timestamps are currently stored as naive local
    clock values.  Compare them with the Shanghai wall clock, while the public
    representation retains the explicit +08:00 offset.
    """

    next_month = period + 1
    local = datetime(
        next_month.year,
        next_month.month,
        DECISION_DAY,
        DECISION_HOUR,
        tzinfo=ZoneInfo(DECISION_TIMEZONE),
    )
    local_naive = local.replace(tzinfo=None)
    return local_naive, local.isoformat()


def _row_value(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _plain_row(row) -> dict:
    return {
        "id": _row_value(row, "id", None) or id(row),
        "indicator_code": _row_value(row, "indicator_code"),
        "date": _row_value(row, "date"),
        "value": float(_row_value(row, "value")),
        "release_date": _row_value(row, "release_date"),
        "available_at": _row_value(row, "available_at"),
        "retrieved_at": _row_value(row, "retrieved_at"),
        "status": _row_value(row, "status", "published"),
        "formula_version": _row_value(row, "formula_version"),
        "version": int(_row_value(row, "version", 1)),
    }


def _safe_vintage_groups(
    vintage_rows: Iterable[dict],
) -> tuple[
    dict[tuple[str, date], list[dict]],
    set[int],
    set[int],
    set[tuple[str, date]],
]:
    """Return rows grouped by observation and revisions safe for strict replay.

    A later value can inherit an old ``available_at`` when sparse refresh
    metadata is preserved. If a value or formula revision lacks a strictly
    later publication timestamp, hindsight cannot prove when the older value
    ceased to be current; the entire observation group is non-reconstructable.
    Metadata-only duplicates do not quarantine an otherwise safe observation.
    """

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for raw in vintage_rows:
        row = _plain_row(raw)
        grouped[(row["indicator_code"], row["date"])].append(row)

    ambiguous_ids: set[int] = set()
    unknown_revision_ids: set[int] = set()
    non_reconstructable: set[tuple[str, date]] = set()
    for key, rows in grouped.items():
        rows.sort(key=lambda item: (item["version"], item["retrieved_at"] or datetime.min, item["id"]))
        greatest_prior_availability: datetime | None = None
        prior_semantics: tuple[float, str | None] | None = None
        for row in rows:
            available_at = row["available_at"]
            semantics = (row["value"], row["formula_version"])
            semantic_change = bool(
                prior_semantics is not None
                and (
                    abs(semantics[0] - prior_semantics[0]) > 1e-9
                    or semantics[1] != prior_semantics[1]
                )
            )
            if available_at is None:
                if semantic_change:
                    unknown_revision_ids.add(row["id"])
                    non_reconstructable.add(key)
                prior_semantics = semantics
                continue
            if (
                greatest_prior_availability is not None
                and available_at <= greatest_prior_availability
            ):
                ambiguous_ids.add(row["id"])
                if semantic_change:
                    non_reconstructable.add(key)
            greatest_prior_availability = max(
                greatest_prior_availability or available_at,
                available_at,
            )
            prior_semantics = semantics
    return grouped, ambiguous_ids, unknown_revision_ids, non_reconstructable


def _select_strict_vintages(
    vintage_rows: Iterable[dict],
    *,
    as_of: datetime,
    observation_end: date,
    audit_codes: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """Select one latest publication-safe vintage per code/observation date."""

    (
        grouped,
        ambiguous_ids,
        unknown_revision_ids,
        non_reconstructable,
    ) = _safe_vintage_groups(vintage_rows)
    selected: list[dict] = []
    unknown_observations = 0
    future_observations = 0
    relevant_ambiguous = 0
    relevant_unknown_revisions = 0
    relevant_non_reconstructable = 0
    for key, rows in grouped.items():
        code, observation_date = key
        if observation_date > observation_end:
            continue
        audit_this_code = audit_codes is None or code in audit_codes
        if audit_this_code and any(row["id"] in ambiguous_ids for row in rows):
            relevant_ambiguous += sum(row["id"] in ambiguous_ids for row in rows)
        if audit_this_code and any(row["id"] in unknown_revision_ids for row in rows):
            relevant_unknown_revisions += sum(
                row["id"] in unknown_revision_ids for row in rows
            )
        if key in non_reconstructable:
            if audit_this_code:
                relevant_non_reconstructable += 1
            continue
        safe = [
            row
            for row in rows
            if row["id"] not in ambiguous_ids and row["available_at"] is not None
        ]
        if not safe:
            if audit_this_code:
                unknown_observations += 1
            continue
        visible = [row for row in safe if row["available_at"] <= as_of]
        if not visible:
            if audit_this_code:
                future_observations += 1
            continue
        selected.append(
            max(
                visible,
                key=lambda item: (
                    item["available_at"],
                    item["version"],
                    item["retrieved_at"] or datetime.min,
                    item["id"],
                ),
            )
        )
    selected.sort(key=lambda item: (item["indicator_code"], item["date"]))
    return selected, {
        "unknown_available_at_observations": unknown_observations,
        "not_yet_available_observations": future_observations,
        "ambiguous_revisions_excluded": relevant_ambiguous,
        "unknown_revisions_excluded": relevant_unknown_revisions,
        "non_reconstructable_observations_excluded": relevant_non_reconstructable,
    }


def _model_role(code: str) -> str:
    if code in INFLATION_CODES:
        return "inflation"
    if code in VALIDATION_CODES:
        return "validation"
    roles = {
        block.role
        for block in CHINA_CYCLE_BLOCKS
        for signal in block.signals
        if code in signal.input_codes
    }
    return "+".join(sorted(roles)) if roles else "other"


def _has_minimum_coincident_inputs(rows: list[dict]) -> bool:
    """Cheap proof that A2 cannot exist, before running the expensive model."""

    available_codes = {row["indicator_code"] for row in rows}
    for block in (item for item in CHINA_CYCLE_BLOCKS if item.role == "coincident"):
        available_signals = [
            signal
            for signal in block.signals
            if all(code in available_codes for code in signal.input_codes)
        ]
        if (
            len(available_signals) < 2
            or sum(signal.weight for signal in available_signals) < BLOCK_MIN_COVERAGE
        ):
            return False
    return True


def _input_readiness(vintage_rows: list[dict], final_rows: list[dict]) -> list[dict]:
    (
        grouped,
        ambiguous_ids,
        unknown_revision_ids,
        non_reconstructable,
    ) = _safe_vintage_groups(vintage_rows)
    final_by_code: dict[str, set[date]] = defaultdict(set)
    for raw in final_rows:
        row = _plain_row(raw)
        if row["indicator_code"] in REQUIRED_CODES:
            final_by_code[row["indicator_code"]].add(row["date"])

    result = []
    for code in REQUIRED_CODES:
        known_dates: set[date] = set()
        on_schedule_dates: set[date] = set()
        unknown_dates: set[date] = set()
        ambiguous_dates: set[date] = set()
        unknown_revision_dates: set[date] = set()
        non_reconstructable_dates: set[date] = set()
        for key, rows in grouped.items():
            row_code, observation_date = key
            if row_code != code:
                continue
            safe_known = any(
                row["available_at"] is not None and row["id"] not in ambiguous_ids
                for row in rows
            ) and key not in non_reconstructable
            if key in non_reconstructable:
                non_reconstructable_dates.add(observation_date)
            elif safe_known:
                known_dates.add(observation_date)
                decision_cutoff, _ = _decision_as_of(
                    pd.Period(observation_date, freq="M")
                )
                if any(
                    row["available_at"] is not None
                    and row["available_at"] <= decision_cutoff
                    and row["id"] not in ambiguous_ids
                    for row in rows
                ):
                    on_schedule_dates.add(observation_date)
            else:
                unknown_dates.add(observation_date)
            if any(row["id"] in ambiguous_ids for row in rows):
                ambiguous_dates.add(observation_date)
            if any(row["id"] in unknown_revision_ids for row in rows):
                unknown_revision_dates.add(observation_date)
        final_count = len(final_by_code[code])
        known_count = len(known_dates)
        result.append(
            {
                "code": code,
                "name": _DEFINITIONS.get(code, {}).get("name", code),
                "role": _model_role(code),
                "final_observations": final_count,
                "known_available_at_observations": known_count,
                "on_schedule_observations": len(on_schedule_dates),
                "unknown_available_at_observations": len(unknown_dates),
                "ambiguous_revision_observations": len(ambiguous_dates),
                "unknown_revision_observations": len(unknown_revision_dates),
                "non_reconstructable_observations": len(non_reconstructable_dates),
                "availability_rate": (
                    round(min(known_count / final_count, 1.0), 4) if final_count else None
                ),
                "on_schedule_rate": (
                    round(min(len(on_schedule_dates) / final_count, 1.0), 4)
                    if final_count
                    else None
                ),
                "first_known_period": (
                    str(pd.Period(min(known_dates), freq="M")) if known_dates else None
                ),
                "last_known_period": (
                    str(pd.Period(max(known_dates), freq="M")) if known_dates else None
                ),
            }
        )
    return result


def _regime_for_rows(rows: list[dict], period: pd.Period) -> tuple[dict, dict | None]:
    """Run the production A1 core and production A2 state machine unchanged."""

    matrix = _matrix_from_rows(
        rows,
        end=period.end_time.date(),
        months=MAX_OUTPUT_MONTHS,
    )
    inflation_rows = [row for row in rows if row["indicator_code"] in INFLATION_CODES]
    regime = _build_regime_from_matrix(
        matrix,
        inflation_rows,
        output_months=MAX_OUTPUT_MONTHS,
    )
    month = next(
        (item for item in reversed(regime.get("months", [])) if item["period"] == str(period)),
        None,
    )
    return regime, month


def _phase_snapshot(month: dict | None, regime: dict) -> dict | None:
    if month is None:
        return None
    return {
        "phase": month.get("phase"),
        "phase_label": month.get("phase_label", "待判定"),
        "phase_status": month.get("phase_status", "insufficient"),
        "confirmed_phase": month.get("confirmed_phase"),
        "confirmed_since": month.get("confirmed_since"),
        "last_decision_period": regime.get("last_decision_period"),
        "decision_eligible": bool(month.get("decision_eligible")),
        "level_axis": month.get("level_axis", "unavailable"),
        "momentum_axis": month.get("momentum_axis", "unavailable"),
        "level_3m": month.get("level_3m"),
        "momentum_3m": month.get("momentum_3m"),
        "coincident_index": month.get("coincident_index"),
        "leading_index": month.get("leading_index"),
        "confidence": month.get("confidence", "insufficient"),
    }


def _revision_audit(strict_rows: list[dict], final_rows: list[dict]) -> tuple[int, int]:
    final_map = {
        (row["indicator_code"], row["date"]): row
        for row in final_rows
        if row["indicator_code"] in REQUIRED_CODES
    }
    strict_map = {
        (row["indicator_code"], row["date"]): row
        for row in strict_rows
        if row["indicator_code"] in REQUIRED_CODES
    }
    revised = 0
    for key in strict_map.keys() & final_map.keys():
        realtime = strict_map[key]
        final = final_map[key]
        if (
            abs(float(realtime["value"]) - float(final["value"])) > 1e-9
            or realtime.get("formula_version") != final.get("formula_version")
        ):
            revised += 1
    return revised, len(final_map.keys() - strict_map.keys())


def _exclusion_reasons(
    realtime_month: dict | None,
    final_month: dict | None,
    strict_rows: list[dict],
    *,
    formula_ineligible: bool,
) -> list[str]:
    reasons: list[str] = []
    if not strict_rows:
        reasons.append("no_known_available_at")
    if realtime_month is None:
        reasons.append("a1_coverage_insufficient")
    else:
        decision_text = " ".join(realtime_month.get("decision_reasons", []))
        if "历史" in decision_text or "连续6个月" in decision_text:
            reasons.append("warmup_insufficient")
        if realtime_month.get("phase") is None:
            reasons.append("a2_display_phase_unavailable")
        if realtime_month.get("confirmed_phase") is None:
            reasons.append("a2_confirmed_phase_unavailable")
        if not realtime_month.get("decision_eligible"):
            reasons.append("a2_decision_ineligible")
    if final_month is None:
        reasons.append("final_same_month_unavailable")
    if formula_ineligible:
        reasons.append("formula_version_ineligible")
    return list(dict.fromkeys(reasons))


def _formula_ineligible(strict_rows: list[dict], final_rows: list[dict]) -> bool:
    for code, required in FORMULA_VERSIONED_CYCLE_INPUTS.items():
        final_has_required = any(
            row["indicator_code"] == code and row.get("formula_version") == required
            for row in final_rows
        )
        strict_has_required = any(
            row["indicator_code"] == code and row.get("formula_version") == required
            for row in strict_rows
        )
        if final_has_required and not strict_has_required:
            return True
    return False


def _stability(rows: list[dict]) -> dict:
    comparable = [row for row in rows if row["comparable"]]
    decision_comparable = [row for row in rows if row["decision_comparable"]]
    level_rows = [
        row
        for row in comparable
        if row["realtime"]["level_axis"] != "unavailable"
        and row["final"]["level_axis"] != "unavailable"
    ]
    momentum_rows = [
        row
        for row in comparable
        if row["realtime"]["momentum_axis"] != "unavailable"
        and row["final"]["momentum_axis"] != "unavailable"
    ]
    confusion_counter = Counter(
        (row["realtime"]["phase"], row["final"]["phase"]) for row in comparable
    )

    def safe_rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator >= MIN_RATE_SAMPLE else None

    agreement_count = sum(bool(row["phase_agreement"]) for row in comparable)
    decision_agreement_count = sum(
        bool(row["decision_phase_agreement"]) for row in decision_comparable
    )
    return {
        "comparable_months": len(comparable),
        "agreement_count": agreement_count if comparable else None,
        "agreement_rate": safe_rate(agreement_count, len(comparable)),
        "flip_count": len(comparable) - agreement_count if comparable else None,
        "flip_rate": safe_rate(len(comparable) - agreement_count, len(comparable)),
        "decision_comparable_months": len(decision_comparable),
        "decision_agreement_count": (
            decision_agreement_count if decision_comparable else None
        ),
        "decision_agreement_rate": safe_rate(
            decision_agreement_count, len(decision_comparable)
        ),
        "level_axis_comparable_months": len(level_rows),
        "level_axis_agreement_rate": safe_rate(
            sum(row["realtime"]["level_axis"] == row["final"]["level_axis"] for row in level_rows),
            len(level_rows),
        ),
        "momentum_axis_comparable_months": len(momentum_rows),
        "momentum_axis_agreement_rate": safe_rate(
            sum(
                row["realtime"]["momentum_axis"] == row["final"]["momentum_axis"]
                for row in momentum_rows
            ),
            len(momentum_rows),
        ),
        "confusion": [
            {"realtime_phase": key[0], "final_phase": key[1], "count": count}
            for key, count in sorted(confusion_counter.items())
        ],
        "minimum_rate_sample": MIN_RATE_SAMPLE,
        "sample_note": (
            None
            if len(comparable) >= MIN_RATE_SAMPLE
            else f"可比月份少于{MIN_RATE_SAMPLE}，只报告案例与计数，不报告稳定率。"
        ),
    }


def _transition_summary(rows: list[dict]) -> dict:
    # A transition is an observable change across consecutive scheduled
    # snapshots, not the state machine's retrospectively reconstructed
    # ``confirmed_since``. Restrict both sides to exact-endpoint decision
    # comparisons; final-only paths must never masquerade as backtest events.
    comparable_rows = [row for row in rows if row["decision_comparable"]]

    def events(kind: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        previous: str | None = None
        previous_period: str | None = None
        for row in comparable_rows:
            snapshot = row.get(kind)
            phase = snapshot.get("confirmed_phase") if snapshot else None
            period = row["observation_period"]
            consecutive = bool(
                previous_period is not None
                and _period_distance(period, previous_period) == 1
            )
            if (
                phase is not None
                and previous is not None
                and consecutive
                and phase != previous
            ):
                result.append((row["observation_period"], phase))
            if phase is not None:
                previous = phase
                previous_period = period
        return result

    realtime_events = events("realtime")
    final_events = events("final")
    used_realtime: set[int] = set()
    output_events = []
    lags: list[int] = []
    for final_period, phase in final_events:
        candidates = [
            (index, period, _period_distance(period, final_period))
            for index, (period, realtime_phase) in enumerate(realtime_events)
            if index not in used_realtime
            and realtime_phase == phase
            and abs(_period_distance(period, final_period)) <= 3
        ]
        if candidates:
            index, realtime_period, lag = min(candidates, key=lambda item: abs(item[2]))
            used_realtime.add(index)
            lags.append(lag)
            match_status = "matched"
        else:
            realtime_period = None
            lag = None
            match_status = "unmatched"
        output_events.append(
            {
                "final_phase": phase,
                "final_confirmation_period": final_period,
                "realtime_confirmation_period": realtime_period,
                "signed_lag_months": lag,
                "match_status": match_status,
            }
        )
    enough = len(lags) >= MIN_TRANSITION_SAMPLE
    lag_series = pd.Series(lags, dtype=float)
    return {
        "matched_count": len(lags),
        "lag_median_months": round(float(median(lags)), 2) if enough else None,
        "lag_q1_months": round(float(lag_series.quantile(0.25)), 2) if enough else None,
        "lag_q3_months": round(float(lag_series.quantile(0.75)), 2) if enough else None,
        "unmatched_realtime": len(realtime_events) - len(used_realtime),
        "unmatched_final": sum(item["match_status"] == "unmatched" for item in output_events),
        "minimum_lag_sample": MIN_TRANSITION_SAMPLE,
        "events": output_events,
    }


def _build_backtest_from_rows(
    vintage_rows: list[dict],
    final_rows: list[dict],
    *,
    periods: pd.PeriodIndex,
    final_cutoff_at: datetime,
) -> dict:
    if final_cutoff_at.tzinfo is None:
        final_cutoff_at = final_cutoff_at.replace(tzinfo=UTC)
    plain_vintages = [_plain_row(row) for row in vintage_rows]
    plain_finals = [_plain_row(row) for row in final_rows]
    months = []
    reason_counts: Counter[str] = Counter()

    # A1 scores and A2 panels are one-sided, so future observation months do
    # not rewrite earlier coordinates.  Build the final reference path once.
    # If a strict snapshot actually produces a comparable phase, we still
    # rerun its final peer with the exact same endpoint below, preserving the
    # production 240-month warm-start semantics for reported accuracy.
    full_final_regime: dict = {}
    final_months_by_period: dict[str, dict] = {}
    if plain_finals and len(periods):
        final_model_rows = [
            row
            for row in plain_finals
            if row["indicator_code"] in MODEL_CODES
            and row["date"] <= periods[-1].end_time.date()
        ]
        full_final_regime, _ = _regime_for_rows(final_model_rows, periods[-1])
        final_months_by_period = {
            row["period"]: row for row in full_final_regime.get("months", [])
        }

    for period in periods:
        cutoff_utc, cutoff_display = _decision_as_of(period)
        observation_end = period.end_time.date()
        strict_rows, selection_audit = _select_strict_vintages(
            plain_vintages,
            as_of=cutoff_utc,
            observation_end=observation_end,
            audit_codes=set(REQUIRED_CODES),
        )
        strict_rows = [row for row in strict_rows if row["indicator_code"] in MODEL_CODES]
        target_final_rows = [
            row
            for row in plain_finals
            if row["indicator_code"] in MODEL_CODES and row["date"] <= observation_end
        ]

        if strict_rows and _has_minimum_coincident_inputs(strict_rows):
            realtime_regime, realtime_month = _regime_for_rows(strict_rows, period)
        else:
            realtime_regime, realtime_month = {}, None
        realtime = _phase_snapshot(realtime_month, realtime_regime)

        final_regime = full_final_regime
        final_month = final_months_by_period.get(str(period))
        if realtime and realtime["phase"] and target_final_rows:
            # Exact peer evaluation: same observation endpoint, model window,
            # and state-machine warmup; only the vintage basis differs.
            final_regime, final_month = _regime_for_rows(target_final_rows, period)
        elif final_month is not None:
            final_regime = {
                **full_final_regime,
                "last_decision_period": next(
                    (
                        row["period"]
                        for row in reversed(full_final_regime.get("months", []))
                        if row["period"] <= str(period) and row.get("decision_eligible")
                    ),
                    None,
                ),
            }
        final = _phase_snapshot(final_month, final_regime)
        comparable = bool(realtime and final and realtime["phase"] and final["phase"])
        decision_comparable = bool(
            realtime
            and final
            and realtime["decision_eligible"]
            and final["decision_eligible"]
            and realtime["confirmed_phase"]
            and final["confirmed_phase"]
        )
        phase_agreement = (
            realtime["phase"] == final["phase"] if comparable else None
        )
        decision_phase_agreement = (
            realtime["confirmed_phase"] == final["confirmed_phase"]
            if decision_comparable
            else None
        )
        formula_ineligible = _formula_ineligible(strict_rows, target_final_rows)
        reasons = _exclusion_reasons(
            realtime_month,
            final_month,
            strict_rows,
            formula_ineligible=formula_ineligible,
        )
        reason_counts.update(reasons)
        revised_count, missing_count = _revision_audit(strict_rows, target_final_rows)
        final_observations = sum(
            row["indicator_code"] in REQUIRED_CODES for row in target_final_rows
        )
        strict_observations = sum(
            row["indicator_code"] in REQUIRED_CODES for row in strict_rows
        )
        available_codes = len({row["indicator_code"] for row in strict_rows if row["indicator_code"] in REQUIRED_CODES})
        realtime_coincident = realtime["coincident_index"] if realtime else None
        final_coincident = final["coincident_index"] if final else None
        realtime_leading = realtime["leading_index"] if realtime else None
        final_leading = final["leading_index"] if final else None
        status = "evaluable" if comparable else "limited" if realtime_month else "unavailable"
        comparison_type = (
            "same"
            if phase_agreement is True
            else "phase_changed"
            if phase_agreement is False
            else "both_unclassified"
            if realtime and final and not realtime["phase"] and not final["phase"]
            else "realtime_unclassified"
            if final and final["phase"]
            else "final_unclassified"
            if realtime and realtime["phase"]
            else "unavailable"
        )
        months.append(
            {
                "observation_period": str(period),
                "decision_as_of": cutoff_display,
                "status": status,
                "exclusion_reasons": reasons,
                "availability": {
                    "final_observations": final_observations,
                    "strict_observations": strict_observations,
                    "observation_coverage": (
                        round(strict_observations / final_observations, 4)
                        if final_observations
                        else None
                    ),
                    "required_input_codes": len(REQUIRED_CODES),
                    "available_input_codes": available_codes,
                    "input_code_coverage": round(available_codes / len(REQUIRED_CODES), 4),
                    "unknown_available_at_observations": selection_audit[
                        "unknown_available_at_observations"
                    ],
                    "not_yet_available_observations": selection_audit[
                        "not_yet_available_observations"
                    ],
                    "ambiguous_revisions_excluded": selection_audit[
                        "ambiguous_revisions_excluded"
                    ],
                    "unknown_revisions_excluded": selection_audit[
                        "unknown_revisions_excluded"
                    ],
                    "non_reconstructable_observations_excluded": selection_audit[
                        "non_reconstructable_observations_excluded"
                    ],
                    "revised_observations": revised_count,
                    "missing_final_observations": missing_count,
                },
                "realtime": realtime,
                "final": final,
                "comparable": comparable,
                "phase_agreement": phase_agreement,
                "phase_changed": (not phase_agreement) if phase_agreement is not None else None,
                "decision_comparable": decision_comparable,
                "decision_phase_agreement": decision_phase_agreement,
                "comparison_type": comparison_type,
                "coincident_revision_delta": (
                    round(realtime_coincident - final_coincident, 2)
                    if realtime_coincident is not None and final_coincident is not None
                    else None
                ),
                "leading_revision_delta": (
                    round(realtime_leading - final_leading, 2)
                    if realtime_leading is not None and final_leading is not None
                    else None
                ),
            }
        )

    display_evaluable = sum(row["comparable"] for row in months)
    decision_evaluable = sum(row["decision_comparable"] for row in months)
    stability = _stability(months)
    non_covid_rows = [
        row for row in months if not row["observation_period"].startswith("2020-")
    ]
    status = (
        "ok"
        if display_evaluable >= MIN_RATE_SAMPLE
        else "limited"
        if display_evaluable
        else "unavailable"
    )
    summary = (
        f"严格伪实时共有{display_evaluable}/{len(months)}个月可比较。"
        if display_evaluable
        else "严格伪实时样本尚不能形成可比较阶段；当前只报告发布时间覆盖，不计算准确率。"
    )
    return {
        "region": "CN",
        "country": "中国",
        "title": "中国经济周期伪实时回测",
        "mode": "pseudo_realtime",
        "data_basis": "strict_available_at_vintage",
        "status": status,
        "as_of": str(periods[-1]) if len(periods) else None,
        "start_period": str(periods[0]) if len(periods) else None,
        "end_period": str(periods[-1]) if len(periods) else None,
        "methodology_note": (
            "每个观察月固定在次月20日18:00（北京时间）截取信息，只使用发布时间明确且当时已经发布的安全历史版本，"
            "并把观察期锁在当月；当时判断与事后参考都复用同一套活动矩阵和阶段判断方法。"
        ),
        "summary": summary,
        "backtest_definition": {
            "timezone": DECISION_TIMEZONE,
            "decision_schedule": "observation_month_plus_1_day_20_18_00",
            "strict": True,
            "stored_available_at_timezone": "Asia/Shanghai (CN source-time assumption)",
            "observation_alignment": "decision_as_of=m+1月20日18:00 Asia/Shanghai; observation_end=m月末",
            "availability_policy": "available_at_required_and_not_after_decision_as_of",
            "vintage_selection": (
                "latest safe vintage by available_at; a value/formula revision without a strictly later timestamp quarantines the whole observation"
            ),
            "final_reference": "latest stored values with the same observation_end and identical model code; ex-post reference, not ground truth",
            "final_cutoff_at": final_cutoff_at.astimezone(UTC).isoformat(),
            "a1_methodology_version": A1_METHODOLOGY_VERSION,
            "a2_methodology_version": A2_METHODOLOGY_VERSION,
            "a3_methodology_version": METHODOLOGY_VERSION,
        },
        "coverage": {
            "scheduled_months": len(months),
            "display_evaluable_months": display_evaluable,
            "display_evaluable_rate": (
                round(display_evaluable / len(months), 4) if months else None
            ),
            "decision_evaluable_months": decision_evaluable,
            "decision_evaluable_rate": (
                round(decision_evaluable / len(months), 4) if months else None
            ),
            "unavailable_months": len(months) - display_evaluable,
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "stability": stability,
        "transitions": _transition_summary(months),
        "robustness": {
            "full_sample": stability,
            "exclude_covid_2020": _stability(non_covid_rows),
        },
        "input_readiness": _input_readiness(plain_vintages, plain_finals),
        "latest": months[-1] if months else None,
        "months": months,
        "warnings": [
            "事后最终值只是同模型的稳定性参照，不是GDP真值、官方衰退认定或预测准确率。",
            "发布时间未知的观测严格排除；不会用系统首次抓取时间或经验发布日期回填。",
            "改值或公式变更若没有严格递增的公开时间，该观察期全部版本均不可还原；不会继续沿用可能已失效的旧值。",
            "中国来源的存量发布时间是无时区的本地钟面时间，本页按北京时间解释；跨来源的小时级时区尚未完成独立认证。",
            f"可比月份少于{MIN_RATE_SAMPLE}时不展示一致率和翻转率；匹配转换少于{MIN_TRANSITION_SAMPLE}次时不展示确认滞后统计。",
            "当前固定使用现行A1/A2方法版本回放数据vintage，不模拟历史模型参数或旧公式。",
        ],
    }


def _latest_completed_period(now: datetime) -> pd.Period:
    local_now = now.astimezone(ZoneInfo(DECISION_TIMEZONE))
    current = pd.Period(local_now.date(), freq="M")
    previous = current - 1
    cutoff_utc, _ = _decision_as_of(previous)
    local_naive = local_now.replace(tzinfo=None)
    return previous if cutoff_utc <= local_naive else current - 2


def build_china_cycle_backtest(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    months: int = DEFAULT_BACKTEST_MONTHS,
    now: datetime | None = None,
) -> dict:
    if not 1 <= months <= MAX_BACKTEST_MONTHS:
        raise ValueError(f"months must be between 1 and {MAX_BACKTEST_MONTHS}")
    if start and end and start > end:
        raise ValueError("start must not be later than end")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    completed_end = _latest_completed_period(now)
    requested_end = pd.Period(end, freq="M") if end else completed_end
    if requested_end > completed_end:
        raise ValueError(
            f"end must not be later than {completed_end}; its fixed decision timestamp has not occurred"
        )
    requested_start = pd.Period(start, freq="M") if start else requested_end - (months - 1)
    if requested_start > requested_end:
        raise ValueError("requested monthly range is empty")
    span = _period_distance(requested_end, requested_start) + 1
    if span > MAX_BACKTEST_MONTHS:
        raise ValueError(f"requested range must not exceed {MAX_BACKTEST_MONTHS} months")
    periods = pd.period_range(requested_start, requested_end, freq="M")

    vintage_query = (
        select(DataPointVintage)
        .where(DataPointVintage.indicator_code.in_(MODEL_CODES))
        .where(DataPointVintage.date <= requested_end.end_time.date())
        .order_by(
            DataPointVintage.indicator_code,
            DataPointVintage.date,
            DataPointVintage.version,
        )
    )
    final_query = (
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(MODEL_CODES))
        .where(DataPoint.date <= requested_end.end_time.date())
        .order_by(DataPoint.indicator_code, DataPoint.date)
    )
    vintage_rows = list(db.scalars(vintage_query))
    final_rows = list(db.scalars(final_query))
    return _build_backtest_from_rows(
        vintage_rows,
        final_rows,
        periods=periods,
        final_cutoff_at=now,
    )
