"""A2 relative-growth regime classification built strictly on the A1 matrix."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataPoint
from app.services.china_business_cycle import (
    BLOCK_MIN_COVERAGE,
    CHINA_CYCLE_BLOCKS,
    DEFAULT_OUTPUT_MONTHS,
    INDEX_SCALE,
    MAX_OUTPUT_MONTHS,
    build_china_business_cycle_matrix,
)


METHODOLOGY_VERSION = "1.1.1"
LEVEL_NEUTRAL = 100.0
LEVEL_BUFFER = 1.5
MOMENTUM_BUFFER = 1.5
SMOOTHING_MONTHS = 3
MOMENTUM_COMPARISON_MONTHS = 3
CONFIRMATION_WITH_LEADING = 2
CONFIRMATION_WITHOUT_LEADING = 3
CURRENT_PHASE_MAX_CARRY_MONTHS = 1
INFLATION_MOMENTUM_BUFFER = 0.2
ROLE_MIN_COVERAGE = 0.65
ROLE_HIGH_COVERAGE = 0.85
BALANCED_PANEL_MONTHS = 6
MIN_SIGNALS_PER_BLOCK = 2
ROLE_REQUIRED_BLOCKS = {"coincident": 2, "leading": 2}
DECOMPOSITION_TOLERANCE = 1e-8
BLOCK_SPECS = {block.key: block for block in CHINA_CYCLE_BLOCKS}
FIXED_SURVEY_CORE_CODES = (
    "CN_PMI_PRODUCTION",
    "CN_NMI",
    "CN_PMI_EMPLOYMENT",
    "CN_NMI_EMPLOYMENT",
)
FIXED_SURVEY_CORE_SIGNATURE = "fixed_survey_core_v1[" + ",".join(
    f"{code}@0.25" for code in FIXED_SURVEY_CORE_CODES
) + "]"

Phase = Literal["recovery", "expansion", "slowdown", "contraction"]
PHASES: tuple[Phase, ...] = ("contraction", "recovery", "expansion", "slowdown")
PHASE_LABELS: dict[str, str] = {
    "recovery": "复苏",
    "expansion": "扩张",
    "slowdown": "放缓",
    "contraction": "收缩",
}
PHASE_AXES = {
    "recovery": ("below", "rising"),
    "expansion": ("above", "rising"),
    "slowdown": ("above", "falling"),
    "contraction": ("below", "falling"),
}
AXES_PHASE = {value: key for key, value in PHASE_AXES.items()}
ADJACENT_PHASES: dict[str, frozenset[Phase]] = {
    "contraction": frozenset(("recovery", "slowdown")),
    "recovery": frozenset(("contraction", "expansion")),
    "expansion": frozenset(("recovery", "slowdown")),
    "slowdown": frozenset(("expansion", "contraction")),
}


@dataclass(slots=True)
class RegimeTracker:
    confirmed_phase: Phase | None = None
    confirmed_since: str | None = None
    candidate_phase: Phase | None = None
    candidate_since: str | None = None
    candidate_streak: int = 0
    undecidable_streak: int = 0


ABSOLUTE_ANCHORS = (
    ("CN_PMI_PRODUCTION", "制造业PMI生产", 50.0),
    ("CN_NMI", "非制造业商务活动", 50.0),
    ("CN_DOMESTIC_NEW_ORDERS", "制造业与非制造业新订单", 50.0),
    ("CN_PMI_EMPLOYMENT", "制造业PMI就业", 50.0),
    ("CN_NMI_EMPLOYMENT", "非制造业PMI就业", 50.0),
)


def _month_distance(left: str, right: str) -> int:
    left_period = pd.Period(left, freq="M")
    right_period = pd.Period(right, freq="M")
    return (left_period.year - right_period.year) * 12 + left_period.month - right_period.month


def _signal_map(month: dict) -> dict[str, dict]:
    return {
        signal["code"]: signal
        for block in month.get("blocks", [])
        for signal in block.get("signals", [])
    }


def _unavailable_decomposition(
    role: str,
    reason: str,
    *,
    coverage: float = 0.0,
    codes: list[str] | None = None,
    signature: str | None = None,
) -> dict:
    """Return an explicit empty decomposition instead of implying zero impact."""

    return {
        "role": role,
        "status": "unavailable",
        "reason": reason,
        "basis_signature": signature,
        "basis_codes": codes or [],
        "basis_coverage": round(coverage, 4),
        "recent_window_start": None,
        "recent_window_end": None,
        "comparison_window_start": None,
        "comparison_window_end": None,
        "level_gap": None,
        "momentum_3m": None,
        "level_contribution_sum": None,
        "momentum_contribution_sum": None,
        "level_residual": None,
        "momentum_residual": None,
        "additivity_passed": False,
        "drivers": [],
    }


def _unique_periods(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _panel_decomposition(
    *,
    role: str,
    window: list[dict],
    components: list[dict],
    coverage: float,
    signature: str,
    panel_values: list[float],
) -> dict:
    """Exactly decompose A2's balanced level and momentum coordinates.

    The calculation deliberately reuses the panel's six observations and
    effective weights.  It must never be reconstructed from A1's one-month
    headline contributions, whose basket and time window are different.
    """

    recent_level = sum(panel_values[3:]) / 3
    comparison_level = sum(panel_values[:3]) / 3
    level_gap = recent_level - LEVEL_NEUTRAL
    momentum = recent_level - comparison_level
    drivers = []
    raw_level_sum = 0.0
    raw_momentum_sum = 0.0
    for component in components:
        scores = [float(value) for value in component["scores"]]
        recent_score = sum(scores[3:]) / 3
        comparison_score = sum(scores[:3]) / 3
        level_contribution = (
            INDEX_SCALE * component["effective_weight"] * recent_score
        )
        momentum_contribution = (
            INDEX_SCALE
            * component["effective_weight"]
            * (recent_score - comparison_score)
        )
        raw_level_sum += level_contribution
        raw_momentum_sum += momentum_contribution
        source_periods = component["source_periods"]
        drivers.append(
            {
                "code": component["code"],
                "name": component["name"],
                "block_key": component["block_key"],
                "effective_weight": round(component["effective_weight"], 8),
                "recent_score": round(recent_score, 6),
                "comparison_score": round(comparison_score, 6),
                "level_contribution": round(level_contribution, 6),
                "momentum_contribution": round(momentum_contribution, 6),
                "recent_source_periods": _unique_periods(source_periods[3:]),
                "comparison_source_periods": _unique_periods(source_periods[:3]),
            }
        )
    level_residual = level_gap - raw_level_sum
    momentum_residual = momentum - raw_momentum_sum
    return {
        "role": role,
        "status": "available",
        "reason": None,
        "basis_signature": signature,
        "basis_codes": [component["code"] for component in components],
        "basis_coverage": round(coverage, 4),
        "recent_window_start": window[3]["period"],
        "recent_window_end": window[-1]["period"],
        "comparison_window_start": window[0]["period"],
        "comparison_window_end": window[2]["period"],
        "level_gap": round(level_gap, 6),
        "momentum_3m": round(momentum, 6),
        "level_contribution_sum": round(raw_level_sum, 6),
        "momentum_contribution_sum": round(raw_momentum_sum, 6),
        "level_residual": round(level_residual, 10),
        "momentum_residual": round(momentum_residual, 10),
        "additivity_passed": bool(
            abs(level_residual) <= DECOMPOSITION_TOLERANCE
            and abs(momentum_residual) <= DECOMPOSITION_TOLERANCE
        ),
        "drivers": drivers,
    }


def _decision_reasons(
    index: int,
    *,
    level_available: bool,
    momentum_available: bool,
    role_quality: str,
    raw_phase: Phase | None,
    basis_changed: bool,
    basis_coverage: float,
) -> list[str]:
    reasons: list[str] = []
    if not level_available:
        reasons.append("同步水平至少需要连续3个月数据。")
    if role_quality == "insufficient":
        reasons.append(
            f"六个月共同同步篮子覆盖为{basis_coverage:.0%}，"
            f"低于{ROLE_MIN_COVERAGE:.0%}或有效板块不足。"
        )
    if not momentum_available:
        if index < 5:
            reasons.append("同步动能至少需要连续6个月历史。")
        else:
            reasons.append("六个月共同同步篮子不足，诊断值不参与阶段切换。")
    if basis_changed:
        reasons.append("共同同步篮子较上个可用月发生变化，本月暂停阶段判定并重置未确认候选。")
    if level_available and momentum_available and raw_phase is None:
        reasons.append("水平或动能位于判定死区，且尚无上一确认轴可继承。")
    return reasons


def _balanced_role_panel(
    months: list[dict],
    index: int,
    role: str,
    *,
    include_explanations: bool = True,
) -> dict:
    """Recompute both three-month panels on one six-month common basket."""

    empty = {
        "codes": [],
        "signature": None,
        "coverage": 0.0,
        "valid": False,
        "level": None,
        "momentum": None,
        "changed": False,
        "decomposition": (
            _unavailable_decomposition(role, "insufficient_history")
            if include_explanations
            else None
        ),
    }
    if index < BALANCED_PANEL_MONTHS - 1:
        return empty
    window = months[index - (BALANCED_PANEL_MONTHS - 1) : index + 1]
    current_blocks = {
        block["key"]: block
        for block in window[-1].get("blocks", [])
        if block.get("role") == role
    }
    if not current_blocks:
        return {
            **empty,
            "decomposition": (
                _unavailable_decomposition(role, "insufficient_common_basis")
                if include_explanations
                else None
            ),
        }

    role_specs = [block for block in CHINA_CYCLE_BLOCKS if block.role == role]
    total_role_weight = sum(block.weight for block in role_specs)
    eligible_blocks = []
    covered_role_weight = 0.0
    for block_key, current_block in current_blocks.items():
        block_rows = []
        for month in window:
            row = next(
                (
                    item
                    for item in month.get("blocks", [])
                    if item.get("role") == role and item.get("key") == block_key
                ),
                None,
            )
            if row is None:
                block_rows = []
                break
            block_rows.append(row)
        if len(block_rows) != BALANCED_PANEL_MONTHS:
            continue

        block_spec = BLOCK_SPECS[block_key]
        current_signal_weights = {
            signal.key: signal.weight for signal in block_spec.signals
        }
        total_signal_weight = sum(current_signal_weights.values())
        common_codes = set(current_signal_weights)
        signal_maps = []
        for row in block_rows:
            signal_map = {signal["code"]: signal for signal in row.get("signals", [])}
            signal_maps.append(signal_map)
            common_codes &= {
                code
                for code, signal in signal_map.items()
                if signal.get("standardized_score") is not None
            }
        stable_codes = sorted(common_codes)
        common_weight = sum(current_signal_weights[code] for code in stable_codes)
        if (
            len(stable_codes) < MIN_SIGNALS_PER_BLOCK
            or not total_signal_weight
            or common_weight / total_signal_weight < BLOCK_MIN_COVERAGE
        ):
            continue

        block_weight = block_spec.weight
        covered_role_weight += block_weight * common_weight / total_signal_weight
        eligible_blocks.append(
            {
                "key": block_key,
                "weight": block_weight,
                "signal_weights": {
                    code: current_signal_weights[code] for code in stable_codes
                },
                "signal_maps": signal_maps,
            }
        )

    coverage = covered_role_weight / total_role_weight if total_role_weight else 0.0
    required_blocks = ROLE_REQUIRED_BLOCKS[role]
    valid = len(eligible_blocks) >= required_blocks and coverage >= ROLE_MIN_COVERAGE
    if not eligible_blocks:
        return {
            **empty,
            "coverage": round(coverage, 4),
            "decomposition": (
                _unavailable_decomposition(
                    role,
                    "insufficient_common_basis",
                    coverage=coverage,
                )
                if include_explanations
                else None
            ),
        }

    active_block_weight = sum(block["weight"] for block in eligible_blocks)
    effective_signature = []
    decomposition_components = []
    panel_values = []
    for offset in range(BALANCED_PANEL_MONTHS):
        role_z = 0.0
        for block in eligible_blocks:
            signal_weight = sum(block["signal_weights"].values())
            block_z = sum(
                float(block["signal_maps"][offset][code]["standardized_score"]) * weight
                for code, weight in block["signal_weights"].items()
            ) / signal_weight
            role_z += block_z * block["weight"] / active_block_weight
        panel_values.append(LEVEL_NEUTRAL + INDEX_SCALE * role_z)
    for block in eligible_blocks:
        normalized_block_weight = block["weight"] / active_block_weight
        signal_weight = sum(block["signal_weights"].values())
        for code, weight in block["signal_weights"].items():
            effective_weight = normalized_block_weight * weight / signal_weight
            if include_explanations:
                decomposition_components.append(
                    {
                        "code": code,
                        "name": block["signal_maps"][-1][code].get("name", code),
                        "block_key": block["key"],
                        "effective_weight": effective_weight,
                        "scores": [
                            signal_map[code]["standardized_score"]
                            for signal_map in block["signal_maps"]
                        ],
                        "source_periods": [
                            signal_map[code].get("source_period")
                            for signal_map in block["signal_maps"]
                        ],
                    }
                )
        effective_signature.append(
            (
                block["key"],
                tuple(
                    (
                        code,
                        round(normalized_block_weight * weight / signal_weight, 12),
                    )
                    for code, weight in block["signal_weights"].items()
                ),
            )
        )
    codes = [
        code
        for block in eligible_blocks
        for code in block["signal_weights"]
    ]
    signature = "|".join(
        f"{block_key}[{','.join(f'{code}@{weight:.12g}' for code, weight in signals)}]"
        for block_key, signals in effective_signature
    )
    recent = sum(panel_values[3:]) / 3
    previous = sum(panel_values[:3]) / 3
    decomposition = None
    if include_explanations:
        decomposition = (
            _panel_decomposition(
                role=role,
                window=window,
                components=decomposition_components,
                coverage=coverage,
                signature=signature,
                panel_values=panel_values,
            )
            if valid
            else _unavailable_decomposition(
                role,
                "insufficient_common_basis",
                coverage=coverage,
                codes=codes,
                signature=signature,
            )
        )
    return {
        "codes": codes,
        "signature": signature,
        "coverage": round(coverage, 4),
        "valid": valid,
        "level": recent if valid else None,
        "momentum": recent - previous if valid else None,
        "changed": False,
        "decomposition": decomposition,
    }


def _balanced_role_panels(
    months: list[dict],
    role: str,
    *,
    include_explanations: bool = True,
) -> list[dict]:
    panels = []
    last_valid_signature: str | None = None
    seen_valid = False
    for index in range(len(months)):
        panel = _balanced_role_panel(
            months,
            index,
            role,
            include_explanations=include_explanations,
        )
        signature = panel["signature"] if panel["valid"] else None
        panel["changed"] = bool(
            signature is not None
            and seen_valid
            and signature != last_valid_signature
        )
        if signature is not None:
            last_valid_signature = signature
            seen_valid = True
        panels.append(panel)
    return panels


def _fixed_survey_core_panel(
    months: list[dict],
    index: int,
    *,
    include_explanations: bool = True,
) -> dict:
    """Build one strict six-month panel from four equally weighted surveys.

    This is a sensitivity diagnostic, not an alternative way to satisfy A1's
    production 65% coincident-role coverage gate.  Every one of the four
    configured survey signals must have a standardised score in every month of
    the six-month comparison window; no missing signal is reweighted.
    """

    empty = {
        "codes": [],
        "signature": None,
        "coverage": 0.0,
        "valid": False,
        "level": None,
        "momentum": None,
        "changed": False,
        "decomposition": (
            _unavailable_decomposition("coincident", "insufficient_history")
            if include_explanations
            else None
        ),
    }
    if index < BALANCED_PANEL_MONTHS - 1:
        return empty

    window = months[index - (BALANCED_PANEL_MONTHS - 1) : index + 1]
    signal_maps = [_signal_map(month) for month in window]
    common_codes = [
        code
        for code in FIXED_SURVEY_CORE_CODES
        if all(
            signal_maps[offset].get(code, {}).get("standardized_score") is not None
            for offset in range(BALANCED_PANEL_MONTHS)
        )
    ]
    coverage = len(common_codes) / len(FIXED_SURVEY_CORE_CODES)
    if len(common_codes) != len(FIXED_SURVEY_CORE_CODES):
        return {
            **empty,
            "codes": common_codes,
            "coverage": round(coverage, 4),
            "decomposition": (
                _unavailable_decomposition(
                    "coincident",
                    "insufficient_common_basis",
                    coverage=coverage,
                    codes=common_codes,
                )
                if include_explanations
                else None
            ),
        }

    panel_values = [
        LEVEL_NEUTRAL
        + INDEX_SCALE
        * sum(
            float(signal_maps[offset][code]["standardized_score"])
            for code in FIXED_SURVEY_CORE_CODES
        )
        / len(FIXED_SURVEY_CORE_CODES)
        for offset in range(BALANCED_PANEL_MONTHS)
    ]
    recent = sum(panel_values[3:]) / 3
    previous = sum(panel_values[:3]) / 3
    current_blocks = {
        signal["code"]: block["key"]
        for block in window[-1].get("blocks", [])
        for signal in block.get("signals", [])
    }
    components = (
        [
            {
                "code": code,
                "name": signal_maps[-1][code].get("name", code),
                "block_key": current_blocks.get(code, "fixed_survey_core"),
                "effective_weight": 1 / len(FIXED_SURVEY_CORE_CODES),
                "scores": [
                    signal_maps[offset][code]["standardized_score"]
                    for offset in range(BALANCED_PANEL_MONTHS)
                ],
                "source_periods": [
                    signal_maps[offset][code].get("source_period")
                    for offset in range(BALANCED_PANEL_MONTHS)
                ],
            }
            for code in FIXED_SURVEY_CORE_CODES
        ]
        if include_explanations
        else []
    )
    return {
        "codes": list(FIXED_SURVEY_CORE_CODES),
        "signature": FIXED_SURVEY_CORE_SIGNATURE,
        "coverage": 1.0,
        "valid": True,
        "level": recent,
        "momentum": recent - previous,
        "changed": False,
        "decomposition": (
            _panel_decomposition(
                role="coincident",
                window=window,
                components=components,
                coverage=1.0,
                signature=FIXED_SURVEY_CORE_SIGNATURE,
                panel_values=panel_values,
            )
            if include_explanations
            else None
        ),
    }


def _fixed_survey_core_panels(
    months: list[dict],
    *,
    include_explanations: bool = True,
) -> list[dict]:
    """Return the fixed-survey diagnostic panel for every matrix month."""

    return [
        _fixed_survey_core_panel(
            months,
            index,
            include_explanations=include_explanations,
        )
        for index in range(len(months))
    ]


def _axis(value: float | None, *, kind: str, fallback_phase: Phase | None) -> str:
    if value is None or pd.isna(value):
        return "unavailable"
    if value >= (LEVEL_BUFFER if kind == "level" else MOMENTUM_BUFFER):
        return "above" if kind == "level" else "rising"
    if value <= -(LEVEL_BUFFER if kind == "level" else MOMENTUM_BUFFER):
        return "below" if kind == "level" else "falling"
    if fallback_phase is None:
        return "neutral"
    return PHASE_AXES[fallback_phase][0 if kind == "level" else 1]


def _raw_phase(level_axis: str, momentum_axis: str) -> Phase | None:
    return AXES_PHASE.get((level_axis, momentum_axis))


def _transition_candidate(confirmed: Phase | None, raw: Phase | None) -> Phase | None:
    """Keep the observed quadrant; never invent a transition direction."""

    return raw


def _is_opposite_transition(confirmed: Phase | None, target: Phase | None) -> bool:
    return bool(
        confirmed
        and target
        and target != confirmed
        and target not in ADJACENT_PHASES[confirmed]
    )


def _leading_direction(momentum: float | None) -> str:
    if momentum is None or pd.isna(momentum):
        return "unavailable"
    if momentum >= MOMENTUM_BUFFER:
        return "up"
    if momentum <= -MOMENTUM_BUFFER:
        return "down"
    return "neutral"


def _leading_confirmation(phase: Phase | None, direction: str) -> str:
    if phase is None or direction == "unavailable":
        return "unavailable"
    if direction == "neutral":
        return "neutral"
    expected = "up" if phase in {"recovery", "expansion"} else "down"
    return "confirmed" if direction == expected else "divergent"


def _phase_basis(status: str) -> str:
    """Describe whether the displayed phase was actively assessed this month."""

    if status in {"confirmed", "transition"}:
        return "active_decision"
    if status in {"held_uncomparable", "stale"}:
        return "carried_forward"
    if status == "candidate":
        return "pending_confirmation"
    return "unclassified"


def _advance_tracker(
    tracker: RegimeTracker,
    *,
    period: str,
    raw_phase: Phase | None,
    decision_eligible: bool,
    leading_direction: str,
) -> dict:
    """Advance without back-filling the first candidate month."""

    target = _transition_candidate(tracker.confirmed_phase, raw_phase)
    required: int | None = None

    if not decision_eligible:
        tracker.undecidable_streak += 1
        # Confirmation requires uninterrupted eligible observations. Any
        # changed/invalid basket immediately invalidates an unfinished run.
        tracker.candidate_phase = None
        tracker.candidate_since = None
        tracker.candidate_streak = 0
        if tracker.confirmed_phase is not None:
            status = (
                "stale"
                if tracker.undecidable_streak >= 2
                else "held_uncomparable"
            )
        else:
            status = "insufficient"
    else:
        tracker.undecidable_streak = 0
        if target is None:
            tracker.candidate_phase = None
            tracker.candidate_since = None
            tracker.candidate_streak = 0
            status = "confirmed" if tracker.confirmed_phase else "insufficient"
        elif target == tracker.confirmed_phase:
            tracker.candidate_phase = None
            tracker.candidate_since = None
            tracker.candidate_streak = 0
            status = "confirmed"
        else:
            target_confirmation = _leading_confirmation(target, leading_direction)
            required = (
                CONFIRMATION_WITH_LEADING
                if target_confirmation == "confirmed"
                else CONFIRMATION_WITHOUT_LEADING
            )
            if _is_opposite_transition(tracker.confirmed_phase, target):
                required = max(required, CONFIRMATION_WITHOUT_LEADING)
            if target == tracker.candidate_phase:
                tracker.candidate_streak += 1
            else:
                tracker.candidate_phase = target
                tracker.candidate_since = period
                tracker.candidate_streak = 1
            if tracker.candidate_streak >= required:
                tracker.confirmed_phase = target
                tracker.confirmed_since = period
                tracker.candidate_phase = None
                tracker.candidate_since = None
                tracker.candidate_streak = 0
                status = "confirmed"
            else:
                status = "candidate" if tracker.confirmed_phase is None else "transition"

    # Bind the public interpretation to state that actually survived this
    # month's transition. In particular, a paused raw quadrant must not pose
    # as a retained candidate after that candidate has been reset.
    leading_reference = tracker.candidate_phase or tracker.confirmed_phase
    leading_confirmation = _leading_confirmation(
        leading_reference,
        leading_direction,
    )

    # A diagnostic raw quadrant is not a formal phase.  Before the first
    # confirmation, an ineligible month must remain unclassified instead of
    # leaking raw_phase into the headline.
    phase = tracker.confirmed_phase or tracker.candidate_phase
    # ``phase`` remains the historical/display state used by published
    # backtests. ``current_phase`` answers the narrower product question:
    # whether that confirmed state is still a current interpretation. One
    # missing comparable month gets a grace period; a stale run does not.
    current_phase = (
        tracker.confirmed_phase
        if tracker.confirmed_phase is not None
        and (
            decision_eligible
            or tracker.undecidable_streak <= CURRENT_PHASE_MAX_CARRY_MONTHS
        )
        else None
    )
    duration = (
        _month_distance(period, tracker.confirmed_since) + 1
        if tracker.confirmed_phase and tracker.confirmed_since
        else None
    )
    return {
        "phase": phase,
        "current_phase": current_phase,
        "phase_status": status,
        "phase_basis": _phase_basis(status),
        "confirmed": tracker.confirmed_phase is not None,
        "confirmed_phase": tracker.confirmed_phase,
        "candidate_phase": tracker.candidate_phase,
        "candidate_since": tracker.candidate_since,
        "confirmed_since": tracker.confirmed_since,
        "candidate_streak": tracker.candidate_streak,
        "required_confirmation_months": required,
        "duration_months": duration,
        "undecidable_streak": tracker.undecidable_streak,
        "carry_forward_months": (
            tracker.undecidable_streak
            if status in {"held_uncomparable", "stale"}
            else 0
        ),
        "leading_confirmation": leading_confirmation,
    }


def _state_change_context(
    *,
    previous_decision: dict | None,
    tracker_before: dict,
    state: dict,
    decision_eligible: bool,
    basis_changed: bool,
    level_gap: float | None,
    momentum: float | None,
    level_axis: str,
    momentum_axis: str,
    raw_phase: Phase | None,
) -> dict:
    """Expose the state machine's mechanical reasons without claiming causality."""

    reasons: list[str] = []
    if not decision_eligible:
        if basis_changed:
            reasons.append("basis_changed_hold")
        elif level_gap is not None and momentum is not None and raw_phase is None:
            reasons.append("dead_zone_unclassified")
        else:
            reasons.append("insufficient_hold")
        if tracker_before["candidate_phase"] is not None:
            reasons.append("candidate_reset")
        if state["phase_basis"] == "carried_forward":
            reasons.append("phase_carried_forward")
    else:
        if previous_decision is None:
            reasons.append("first_decision")
        else:
            if previous_decision["level_axis"] != level_axis:
                reasons.append("level_axis_changed")
            if previous_decision["momentum_axis"] != momentum_axis:
                reasons.append("momentum_axis_changed")
            if previous_decision["raw_phase"] != raw_phase:
                reasons.append("raw_phase_changed")

        if state["confirmed_phase"] != tracker_before["confirmed_phase"]:
            reasons.append("phase_confirmed")
        elif state["candidate_phase"] is not None:
            reasons.append(
                "candidate_progressed"
                if state["candidate_phase"] == tracker_before["candidate_phase"]
                else "candidate_started"
            )
        elif tracker_before["candidate_phase"] is not None:
            reasons.append("candidate_cleared")
        else:
            reasons.append("phase_maintained")

        if state["required_confirmation_months"] == CONFIRMATION_WITH_LEADING:
            reasons.append("leading_shortened_confirmation")

    if (
        level_gap is not None
        and abs(level_gap) < LEVEL_BUFFER
        and tracker_before["confirmed_phase"] is not None
    ):
        reasons.append("level_dead_zone_inherited")
    if (
        momentum is not None
        and abs(momentum) < MOMENTUM_BUFFER
        and tracker_before["confirmed_phase"] is not None
    ):
        reasons.append("momentum_dead_zone_inherited")

    return {
        "previous_decision_period": (
            previous_decision["period"] if previous_decision else None
        ),
        "previous_level_axis": (
            previous_decision["level_axis"] if previous_decision else None
        ),
        "previous_momentum_axis": (
            previous_decision["momentum_axis"] if previous_decision else None
        ),
        "previous_raw_phase": (
            previous_decision["raw_phase"] if previous_decision else None
        ),
        "previous_confirmed_phase": (
            previous_decision["confirmed_phase"] if previous_decision else None
        ),
        "reason_codes": list(dict.fromkeys(reasons)),
    }


def _absolute_anchor(months: list[dict], index: int, phase: Phase | None) -> dict:
    anchors = []
    for code, name, threshold in ABSOLUTE_ANCHORS:
        values = []
        if index >= 2:
            for month in months[index - 2 : index + 1]:
                signal = _signal_map(month).get(code)
                value = signal.get("raw_value") if signal else None
                if value is None:
                    values = []
                    break
                values.append(float(value))
        average = sum(values) / len(values) if len(values) == 3 else None
        gap = average - threshold if average is not None else None
        anchors.append(
            {
                "code": code,
                "name": name,
                "three_month_average": _round(average),
                "threshold": threshold,
                "gap": _round(gap),
                "state": (
                    "unavailable"
                    if average is None
                    else "above"
                    if average >= threshold
                    else "below"
                ),
            }
        )
    valid = [item for item in anchors if item["three_month_average"] is not None]
    if len(valid) < 3:
        state = "unavailable"
        breadth = None
        gap = None
    else:
        breadth = sum(item["state"] == "above" for item in valid) / len(valid)
        gap = sum(float(item["gap"]) for item in valid) / len(valid)
        if breadth >= 0.6 and gap >= 0:
            state = "expansionary"
        elif breadth <= 0.4 and gap < 0:
            state = "contractionary"
        else:
            state = "mixed"
    conflict = bool(
        (phase == "expansion" and state == "contractionary")
        or (phase == "contraction" and state == "expansionary")
    )
    return {
        "state": state,
        "breadth": _round(breadth, 4),
        "gap": _round(gap),
        "valid_count": len(valid),
        "total_count": len(anchors),
        "conflict": conflict,
        "anchors": anchors,
    }


def _inflation_series(rows: list[dict], code: str) -> pd.Series:
    selected = [row for row in rows if row["indicator_code"] == code]
    if not selected:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(selected)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"]).sort_values("date")
    frame["period"] = frame["date"].dt.to_period("M")
    frame = frame.drop_duplicates("period", keep="last")
    return pd.Series(frame["value"].to_numpy(float), index=frame["period"])


def _inflation_by_month(rows: list[dict], calendar: pd.PeriodIndex) -> dict[str, dict]:
    core = _inflation_series(rows, "CN_CORE_CPI").reindex(calendar)
    ppi = _inflation_series(rows, "CN_PPI").reindex(calendar)
    core_3m = core.rolling(3, min_periods=3).mean()
    ppi_3m = ppi.rolling(3, min_periods=3).mean()
    momentum = core_3m - core_3m.shift(3)
    result = {}
    for period in calendar:
        average = _number(core_3m.get(period))
        ppi_average = _number(ppi_3m.get(period))
        change = _number(momentum.get(period))
        if average is None:
            level = "unavailable"
            state = "unavailable"
        else:
            level = "very_low" if average < 0.5 else "mild" if average <= 1.5 else "elevated"
            if average < 0 or (average < 0.5 and ppi_average is not None and ppi_average < -1):
                state = "deflation_pressure"
            elif average < 0.5 or (average < 1 and ppi_average is not None and ppi_average < 0):
                state = "low_inflation"
            elif average > 1.5 and change is not None and change >= INFLATION_MOMENTUM_BUFFER:
                state = "heating"
            else:
                state = "moderate"
        if change is None:
            direction = "unavailable"
        elif change >= INFLATION_MOMENTUM_BUFFER:
            direction = "reflation"
        elif change <= -INFLATION_MOMENTUM_BUFFER:
            direction = "disinflation"
        else:
            direction = "stable"
        result[str(period)] = {
            "period": str(period),
            "value": _round(_number(core.get(period))),
            "level": level,
            "three_month_average": _round(average),
            "momentum": _round(change),
            "state": state,
            "direction": direction,
            "ppi_value": _round(_number(ppi.get(period))),
            "ppi_three_month_average": _round(ppi_average),
            "rationale": _inflation_rationale(state, direction, average, ppi_average),
        }
    return result


def _inflation_rationale(
    state: str,
    direction: str,
    core_average: float | None,
    ppi_average: float | None,
) -> str:
    if state == "unavailable":
        return "核心CPI不足3个月，通胀状态不可判定；不会以整体CPI替代。"
    labels = {
        "deflation_pressure": "存在通缩压力",
        "low_inflation": "处于低通胀区间",
        "moderate": "通胀温和",
        "heating": "通胀有所升温",
    }
    directions = {
        "reflation": "近三个月方向回升",
        "disinflation": "近三个月方向回落",
        "stable": "近三个月大致稳定",
        "unavailable": "方向所需6个月历史不足",
    }
    ppi_text = "PPI三月均值缺失" if ppi_average is None else f"PPI三月均值{ppi_average:.2f}%"
    return f"核心CPI三月均值{core_average:.2f}%，{labels[state]}；{directions[direction]}，{ppi_text}。"


def _number(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def _confidence(
    *,
    coincident_confidence: str,
    phase_status: str,
    confirmed: bool,
    leading_confirmation: str,
    absolute_conflict: bool,
    recent_two_comparable: bool,
    diagnostics_available: bool,
    undecidable_streak: int,
) -> tuple[str, list[str]]:
    reasons = []
    if phase_status == "insufficient":
        reasons.append("尚未形成可确认的相对周期象限")
    if coincident_confidence == "insufficient":
        reasons.append("同步活动角色覆盖不足")
    if undecidable_streak >= 2:
        reasons.append("已连续两个月无法进行同成分判断")
    if not diagnostics_available and not (
        confirmed and phase_status == "held_uncomparable"
    ):
        reasons.append("三个月水平或可比动能不足")
    if reasons:
        return "insufficient", reasons
    if phase_status in {"candidate", "transition", "held_uncomparable", "stale"}:
        reasons.append("阶段尚未完成确认或当前成分不可比")
        return "low", reasons
    if leading_confirmation == "divergent":
        reasons.append("领先信号与阶段方向背离")
        return "low", reasons
    if (
        confirmed
        and coincident_confidence == "high"
        and recent_two_comparable
        and leading_confirmation == "confirmed"
        and not absolute_conflict
    ):
        return "high", ["阶段、领先方向与绝对荣枯校验一致"]
    if absolute_conflict:
        reasons.append("相对周期与绝对荣枯锚冲突")
    if leading_confirmation in {"neutral", "unavailable"}:
        reasons.append("领先信号未提供同向确认")
    if coincident_confidence != "high":
        reasons.append(f"同步活动角色置信度为{coincident_confidence}")
    if not recent_two_comparable:
        reasons.append("最近两个月并非全部同成分可比")
    return (
        "medium"
        if confirmed and coincident_confidence in {"high", "medium"}
        else "low"
    ), reasons


def _basis_quality(basis: dict) -> str:
    coverage = _number(basis.get("coverage")) or 0.0
    if not basis.get("valid") or coverage < ROLE_MIN_COVERAGE:
        return "insufficient"
    return "high" if coverage >= ROLE_HIGH_COVERAGE else "medium"


def _drivers(month: dict, positive: bool, limit: int = 20) -> list[dict]:
    """Recompute current-activity contributions independently of A1 overall."""

    rows = []
    blocks = [
        block
        for block in month.get("blocks", [])
        if block.get("role") == "coincident" and block.get("score") is not None
    ]
    active_block_weight = sum(BLOCK_SPECS[block["key"]].weight for block in blocks)
    if not active_block_weight:
        return []
    for block in blocks:
        signals = [
            signal
            for signal in block.get("signals", [])
            if signal.get("standardized_score") is not None
        ]
        signal_specs = {
            signal.key: signal for signal in BLOCK_SPECS[block["key"]].signals
        }
        active_signal_weight = sum(signal_specs[signal["code"]].weight for signal in signals)
        if not active_signal_weight:
            continue
        normalized_block_weight = BLOCK_SPECS[block["key"]].weight / active_block_weight
        for signal in signals:
            contribution = (
                INDEX_SCALE
                * float(signal["standardized_score"])
                * normalized_block_weight
                * signal_specs[signal["code"]].weight
                / active_signal_weight
            )
            if (positive and contribution <= 0) or (not positive and contribution >= 0):
                continue
            rows.append(
                {
                    "code": signal["code"],
                    "name": signal["name"],
                    "block_key": block["key"],
                    "source_period": signal.get("source_period"),
                    "contribution": round(contribution, 4),
                }
            )
    return sorted(rows, key=lambda item: item["contribution"], reverse=positive)[:limit]


def _summary(
    current_phase: Phase | None,
    status: str,
    candidate: Phase | None,
    raw_phase: Phase | None,
    anchor: dict,
    *,
    confirmed_phase: Phase | None,
) -> str:
    if status == "stale":
        base = (
            f"当前相对增长阶段不可判断。最近一次确认阶段为{PHASE_LABELS[confirmed_phase]}，仅作历史参考。"
            if confirmed_phase
            else "当前相对增长阶段不可判断，且尚无历史确认阶段。"
        )
        if raw_phase is not None and raw_phase != confirmed_phase:
            base += (
                f"当前诊断坐标落在{PHASE_LABELS[raw_phase]}，"
                "仅作方向提示，不构成正式切换证据。"
            )
    elif status == "held_uncomparable" and current_phase:
        base = (
            f"本月口径不可比，相对增长阶段暂时沿用{PHASE_LABELS[current_phase]}一次；"
            "本月不形成新判断。"
        )
        if raw_phase is not None and raw_phase != current_phase:
            base += (
                f"当前诊断坐标落在{PHASE_LABELS[raw_phase]}，"
                "仅作方向提示，不构成正式切换证据。"
            )
    elif status == "candidate" and candidate:
        base = f"{PHASE_LABELS[candidate]}是尚待连续月份确认的相对增长周期候选。"
    elif current_phase is None:
        base = "相对增长周期所需的同成分水平与动能不足，暂不判定阶段。"
    elif status == "transition" and candidate:
        phase_text = PHASE_LABELS[current_phase]
        base = f"当前确认的相对增长周期仍为{phase_text}，正在观察向{PHASE_LABELS[candidate]}切换。"
    else:
        base = f"当前确认的相对增长周期处于{PHASE_LABELS[current_phase]}。"
    anchor_text = {
        "expansionary": "绝对荣枯锚整体位于扩张侧。",
        "contractionary": "绝对荣枯锚整体位于收缩侧。",
        "mixed": "绝对荣枯锚分化。",
        "unavailable": "绝对荣枯锚覆盖不足。",
    }[anchor["state"]]
    if anchor["conflict"]:
        anchor_text += "这与相对周期标签冲突，不能把正历史标准分直接理解为绝对扩张。"
    return base + anchor_text


def _outlook(
    direction: str,
    confirmation: str,
    *,
    candidate_phase: Phase | None,
    confirmed_phase: Phase | None,
) -> str:
    reference = candidate_phase or confirmed_phase
    reference_label = PHASE_LABELS[reference] if reference else None
    if direction == "up":
        prefix = "领先三月信号向上"
    elif direction == "down":
        prefix = "领先三月信号向下"
    elif direction == "neutral":
        if candidate_phase:
            return f"领先信号处于缓冲区，暂未确认{reference_label}候选。"
        if confirmed_phase:
            return f"领先信号处于缓冲区，暂未对已确认的{reference_label}阶段给出明确支持或背离。"
        return "领先信号处于缓冲区，当前尚无可判定阶段或候选。"
    else:
        if candidate_phase:
            return f"领先信号历史或同成分可比性不足，暂不能确认{reference_label}候选。"
        if confirmed_phase:
            return f"领先信号历史或同成分可比性不足，暂不能校验已确认的{reference_label}阶段。"
        return "领先信号历史或同成分可比性不足，当前尚无可判定阶段或候选。"
    if candidate_phase:
        if confirmation == "confirmed":
            return prefix + f"，与{reference_label}候选方向一致，可缩短为连续2个月确认。"
        if confirmation == "divergent":
            return prefix + f"，但与{reference_label}候选方向背离，该候选需至少连续3个月确认。"
        return prefix + f"，但尚未形成对{reference_label}候选的可用确认。"
    if confirmed_phase:
        if confirmation == "confirmed":
            return prefix + f"，与已确认的{reference_label}阶段方向一致，当前阶段获得领先信号支持。"
        if confirmation == "divergent":
            return prefix + f"，与已确认的{reference_label}阶段方向背离，提示转向风险，但尚无候选切换。"
        return prefix + f"，但尚未形成对已确认{reference_label}阶段的可用校验。"
    return prefix + "，但当前尚无可判定阶段或候选。"


def _triggers(
    *,
    phase: Phase | None,
    candidate: Phase | None,
    candidate_streak: int,
    required: int | None,
    level_gap: float | None,
    momentum: float | None,
) -> list[str]:
    rows = []
    if level_gap is not None:
        rows.append(
            f"三月同步水平缺口为{level_gap:+.2f}点；高低位确认边界为±{LEVEL_BUFFER:.1f}点。"
        )
    if momentum is not None:
        rows.append(
            f"三月动能为{momentum:+.2f}点；升降方向确认边界为±{MOMENTUM_BUFFER:.1f}点。"
        )
    if candidate and required:
        remaining = max(required - candidate_streak, 0)
        rows.append(
            f"{PHASE_LABELS[candidate]}候选还需{remaining}个连续可决策月确认；不会回填到首个候选月。"
        )
    if phase:
        neighbours = "或".join(PHASE_LABELS[item] for item in sorted(ADJACENT_PHASES[phase]))
        rows.append(
            f"{PHASE_LABELS[phase]}的相邻阶段为{neighbours}；对角候选需更长确认，不预测确定日期。"
        )
    return rows


def _phase_label(phase: Phase | None) -> str:
    return PHASE_LABELS.get(phase, "待判定")


def _build_regime_from_matrix(
    matrix: dict,
    inflation_rows: list[dict],
    *,
    output_start: pd.Period | None = None,
    output_months: int = DEFAULT_OUTPUT_MONTHS,
    coincident_panel_mode: Literal["production", "fixed_survey_core_v1"] = "production",
    include_explanations: bool = True,
) -> dict:
    # A3 may request an internal projection without payload-only explanations.
    # That projection is not intended for ChinaCycleRegimeOut validation.
    source_months = matrix.get("months", [])
    if not source_months:
        return _empty_response(matrix, "A1活动矩阵为空，无法识别相对增长周期。")
    calendar = pd.PeriodIndex([month["period"] for month in source_months], freq="M")
    coincident = pd.Series(
        [month.get("coincident_index") for month in source_months], index=calendar, dtype=float
    )
    leading = pd.Series(
        [month.get("leading_index") for month in source_months], index=calendar, dtype=float
    )
    diagnostic_level = coincident.rolling(
        SMOOTHING_MONTHS, min_periods=SMOOTHING_MONTHS
    ).mean()
    leading_diagnostic_level = leading.rolling(
        SMOOTHING_MONTHS, min_periods=SMOOTHING_MONTHS
    ).mean()
    diagnostic_momentum = diagnostic_level - diagnostic_level.shift(
        MOMENTUM_COMPARISON_MONTHS
    )
    leading_diagnostic = leading_diagnostic_level - leading_diagnostic_level.shift(
        MOMENTUM_COMPARISON_MONTHS
    )
    coincident_panels = (
        _fixed_survey_core_panels(
            source_months,
            include_explanations=include_explanations,
        )
        if coincident_panel_mode == "fixed_survey_core_v1"
        else _balanced_role_panels(
            source_months,
            "coincident",
            include_explanations=include_explanations,
        )
    )
    leading_panels = _balanced_role_panels(
        source_months,
        "leading",
        include_explanations=include_explanations,
    )
    inflation = _inflation_by_month(inflation_rows, calendar)
    tracker = RegimeTracker()
    computed = []
    last_decision_period: str | None = None
    previous_decision: dict | None = None

    for index, (period, a1_month) in enumerate(zip(calendar, source_months)):
        coincident_basis = coincident_panels[index]
        leading_basis = leading_panels[index]
        level_value = _number(coincident_basis["level"])
        level_gap = level_value - LEVEL_NEUTRAL if level_value is not None else None
        diagnostic = _number(diagnostic_momentum.get(period))
        diagnostic_level_value = _number(diagnostic_level.get(period))
        safe_momentum = _number(coincident_basis["momentum"])
        leading_value = _number(leading_basis["level"])
        leading_gap = leading_value - LEVEL_NEUTRAL if leading_value is not None else None
        leading_diagnostic_value = _number(leading_diagnostic.get(period))
        leading_diagnostic_level_value = _number(leading_diagnostic_level.get(period))
        leading_safe = _number(leading_basis["momentum"])
        level_axis = _axis(level_gap, kind="level", fallback_phase=tracker.confirmed_phase)
        momentum_axis = _axis(
            safe_momentum,
            kind="momentum",
            fallback_phase=tracker.confirmed_phase,
        )
        raw = _raw_phase(level_axis, momentum_axis)
        lead_direction = _leading_direction(
            None if leading_basis["changed"] else leading_safe
        )
        decision_eligible = bool(
            level_value is not None
            and safe_momentum is not None
            and raw is not None
            and coincident_basis["valid"]
            and not coincident_basis["changed"]
        )
        decision_reasons = _decision_reasons(
            index,
            level_available=level_value is not None,
            momentum_available=safe_momentum is not None,
            role_quality=_basis_quality(coincident_basis),
            raw_phase=raw,
            basis_changed=coincident_basis["changed"],
            basis_coverage=coincident_basis["coverage"],
        )
        if decision_eligible:
            last_decision_period = str(period)
        tracker_before = {
            "confirmed_phase": tracker.confirmed_phase,
            "candidate_phase": tracker.candidate_phase,
        }
        state = _advance_tracker(
            tracker,
            period=str(period),
            raw_phase=raw,
            decision_eligible=decision_eligible,
            leading_direction=lead_direction,
        )
        state_change = (
            _state_change_context(
                previous_decision=previous_decision,
                tracker_before=tracker_before,
                state=state,
                decision_eligible=decision_eligible,
                basis_changed=coincident_basis["changed"],
                level_gap=level_gap,
                momentum=safe_momentum,
                level_axis=level_axis,
                momentum_axis=momentum_axis,
                raw_phase=raw,
            )
            if include_explanations
            else None
        )
        anchor = _absolute_anchor(source_months, index, state["current_phase"])
        recent_two = bool(
            index >= 2
            and all(
                item["valid"] and not item["changed"]
                for item in coincident_panels[index - 1 : index + 1]
            )
            and all(
                item["valid"] and not item["changed"]
                for item in leading_panels[index - 1 : index + 1]
            )
        )
        confidence, reasons = _confidence(
            coincident_confidence=_basis_quality(coincident_basis),
            phase_status=state["phase_status"],
            confirmed=state["confirmed"],
            leading_confirmation=state["leading_confirmation"],
            absolute_conflict=anchor["conflict"],
            recent_two_comparable=recent_two,
            diagnostics_available=level_value is not None and safe_momentum is not None,
            undecidable_streak=state["undecidable_streak"],
        )
        summary = _summary(
            state["current_phase"],
            state["phase_status"],
            state["candidate_phase"],
            raw,
            anchor,
            confirmed_phase=state["confirmed_phase"],
        )
        outlook = _outlook(
            lead_direction,
            state["leading_confirmation"],
            candidate_phase=state["candidate_phase"],
            confirmed_phase=state["current_phase"],
        )
        computed.append(
            {
                "period": str(period),
                "phase": state["phase"],
                "phase_label": _phase_label(state["phase"]),
                "raw_phase": raw,
                **{key: value for key, value in state.items() if key != "phase"},
                "decision_eligible": decision_eligible,
                "last_decision_period": last_decision_period,
                "decision_reasons": decision_reasons,
                "coincident_comparable": bool(
                    coincident_basis["valid"] and not coincident_basis["changed"]
                ),
                "leading_comparable": bool(
                    leading_basis["valid"] and not leading_basis["changed"]
                ),
                "coincident_coverage": _round(a1_month.get("coincident_coverage"), 4),
                "leading_coverage": _round(a1_month.get("leading_coverage"), 4),
                "coincident_basis": coincident_basis["codes"],
                "leading_basis": leading_basis["codes"],
                "coincident_basis_signature": coincident_basis["signature"],
                "leading_basis_signature": leading_basis["signature"],
                "coincident_basis_coverage": _round(coincident_basis["coverage"], 4),
                "leading_basis_coverage": _round(leading_basis["coverage"], 4),
                "coincident_basis_changed": coincident_basis["changed"],
                "leading_basis_changed": leading_basis["changed"],
                **(
                    {
                        "coincident_decomposition": coincident_basis["decomposition"],
                        "leading_decomposition": leading_basis["decomposition"],
                    }
                    if include_explanations
                    else {}
                ),
                "coincident_index": _round(_number(a1_month.get("coincident_index"))),
                "leading_index": _round(_number(a1_month.get("leading_index"))),
                "level_3m": _round(level_value),
                "level_gap": _round(level_gap),
                "momentum_3m": _round(safe_momentum),
                # A3/A5c consumes these private audit coordinates before the
                # public response model strips them.  Phase rules use the
                # unrounded values, so a two-decimal counterfactual could look
                # unchanged at a ±1.5 boundary even when the label differs.
                "_attribution_level_gap": _round(level_gap, 6),
                "_attribution_momentum_3m": _round(safe_momentum, 6),
                "diagnostic_level_3m": _round(diagnostic_level_value),
                "diagnostic_momentum_3m": _round(diagnostic),
                "level_axis": level_axis,
                "momentum_axis": momentum_axis,
                "leading_level_3m": _round(leading_value),
                "leading_gap": _round(leading_gap),
                "leading_momentum_3m": _round(leading_safe),
                "_attribution_leading_gap": _round(leading_gap, 6),
                "_attribution_leading_momentum_3m": _round(leading_safe, 6),
                "leading_diagnostic_level_3m": _round(
                    leading_diagnostic_level_value
                ),
                "leading_diagnostic_momentum_3m": _round(leading_diagnostic_value),
                "leading_direction": lead_direction,
                "confidence": confidence,
                "confidence_reasons": reasons,
                "data_basis": matrix.get("data_basis", "final"),
                "realtime_coverage": a1_month.get("realtime_coverage", 0.0),
                "absolute_anchor": anchor,
                "inflation": inflation[str(period)],
                "summary": summary,
                "outlook": outlook,
                "triggers": _triggers(
                    phase=state["current_phase"],
                    candidate=state["candidate_phase"],
                    candidate_streak=state["candidate_streak"],
                    required=state["required_confirmation_months"],
                    level_gap=level_gap,
                    momentum=safe_momentum,
                ),
                **({"state_change": state_change} if include_explanations else {}),
                "positive_contributions": _drivers(a1_month, True),
                "negative_contributions": _drivers(a1_month, False),
            }
        )
        if decision_eligible:
            previous_decision = computed[-1]

    if output_start is not None:
        output = [row for row in computed if pd.Period(row["period"], freq="M") >= output_start]
    else:
        output = computed[-output_months:]
    # A1's headline as_of requires both coincident and leading indexes. A2 is
    # deliberately allowed to hold or advance on coincident data when leading
    # inputs lag, so its as_of is the latest requested A1 calendar month.
    as_of = computed[-1]["period"] if computed else None
    latest = computed[-1] if computed else None
    last_decision_period = next(
        (row["period"] for row in reversed(computed) if row["decision_eligible"]),
        None,
    )
    trajectory_source = [row for row in output if row["level_gap"] is not None][-12:]
    trajectory = [
        {
            "period": row["period"],
            "level_gap": row["level_gap"],
            "momentum_3m": row["momentum_3m"],
            "diagnostic_momentum_3m": row["diagnostic_momentum_3m"],
            "phase": row["current_phase"],
            "phase_status": row["phase_status"],
            "comparable": row["coincident_comparable"],
        }
        for row in trajectory_source
    ]
    warnings = [
        "阶段标签描述相对增长周期，不等同于官方衰退认定或GDP绝对负增长。",
        "A2沿用A1最终值/current快照；历史实时可见性必须在A3用vintage/as_of检验。",
        "同步与领先均在滚动六个月共同成分、同一归一权重下重算两个三月面板；篮子变化月立即重置未确认候选。",
        "diagnostic字段保留原A1指数直接计算值，只作对照，不参与阶段切换。",
        "A5b水平与动能驱动严格复用六个月共同篮子和有效权重；贡献加总分别还原三月水平相对100的缺口与三月动能。",
        "positive/negative_contributions为兼容保留的A1单月构成；解释A2坐标应使用coincident_decomposition或leading_decomposition。",
        "通胀状态独立于四阶段，不参与相对增长周期分类。",
    ]
    if latest and latest["absolute_anchor"]["conflict"]:
        warnings.append("最新相对周期阶段与绝对荣枯锚冲突，置信度已下调。")
    if latest and latest["phase_status"] == "held_uncomparable":
        warnings.append("最新月份成分不可比，当前阶段仅暂时沿用上次确认结果一次。")
    elif latest and latest["phase_status"] == "stale":
        warnings.append("最新月份已连续至少两个月不可判，当前阶段判断已过期；旧阶段仅作历史参考。")
    return {
        "region": "CN",
        "country": "中国",
        "title": "中国相对增长周期识别",
        "mode": matrix.get("mode", "current"),
        "data_basis": matrix.get("data_basis", "final"),
        "as_of": as_of,
        "last_decision_period": last_decision_period,
        "realtime_coverage": latest["realtime_coverage"] if latest else None,
        "a1_methodology_version": matrix.get("methodology_version"),
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": (
            "阶段由六个月共同同步成分重算的两个三月面板确定；水平和动能均设±1.5点缓冲。"
            "相邻象限可双向切换，领先同向需连续2个可决策月；对角跨越或领先未确认需3个月，且不回填。"
            "领先方向只按共同面板LM3判断；共同篮子变化会立即清空未确认候选。"
            "A5b按同一有效权重逐项分解水平缺口和动能，并执行严格加总校验。"
            "current_phase最多跨一个不可判月；连续至少两个月不可判时置空，旧phase只作历史兼容。"
        ),
        "methodology": _methodology(),
        "change_conditions": _change_conditions(),
        "latest": latest,
        "months": output,
        "trajectory": trajectory,
        "timeline": _timeline(computed, as_of),
        "a1_warnings": matrix.get("warnings", []),
        "warnings": warnings,
    }


def _timeline(months: list[dict], as_of: str | None) -> list[dict]:
    spans = []
    active_span: int | None = None
    for month in months:
        if as_of is not None and month["period"] > as_of:
            continue
        phase = month.get("current_phase")
        if phase is None:
            active_span = None
            continue
        if active_span is None or spans[active_span]["phase"] != phase:
            spans.append(
                {
                    "phase": phase,
                    "phase_label": PHASE_LABELS[phase],
                    "start_period": month["period"],
                    "end_period": month["period"],
                    "duration_months": 1,
                    "ongoing": False,
                }
            )
            active_span = len(spans) - 1
        else:
            spans[active_span]["end_period"] = month["period"]
            spans[active_span]["duration_months"] = _month_distance(
                month["period"], spans[active_span]["start_period"]
            ) + 1
    latest_current_phase = next(
        (
            month.get("current_phase")
            for month in reversed(months)
            if as_of is None or month["period"] <= as_of
        ),
        None,
    )
    if spans and as_of and latest_current_phase is not None and spans[-1]["end_period"] >= as_of:
        spans[-1]["end_period"] = as_of
        spans[-1]["duration_months"] = _month_distance(as_of, spans[-1]["start_period"]) + 1
        spans[-1]["ongoing"] = True
    return spans


def _methodology() -> dict:
    return {
        "classification_scope": "relative_growth_cycle_not_recession_call",
        "level_source": "A1 standardized signals recomposed on a six-month common basket",
        "level_smoothing_months": SMOOTHING_MONTHS,
        "momentum_definition": "balanced C3(t)-balanced C3(t-3) on identical signals and weights",
        "momentum_comparison_months": MOMENTUM_COMPARISON_MONTHS,
        "level_neutral": LEVEL_NEUTRAL,
        "level_buffer": LEVEL_BUFFER,
        "momentum_buffer": MOMENTUM_BUFFER,
        "confirmation_months_with_leading": CONFIRMATION_WITH_LEADING,
        "confirmation_months_without_leading": CONFIRMATION_WITHOUT_LEADING,
        "current_phase_max_carry_months": CURRENT_PHASE_MAX_CARRY_MONTHS,
        "phase_order": list(PHASES),
        "role_comparability": "rolling six-month common-signal panel with fixed normalized weights",
        "balanced_panel_months": BALANCED_PANEL_MONTHS,
        "basis_change_policy": "any ineligible or changed-basket month immediately resets every unconfirmed candidate; the next eligible month restarts at one",
        "coincident_min_coverage": ROLE_MIN_COVERAGE,
        "coincident_high_coverage": ROLE_HIGH_COVERAGE,
        "absolute_anchor_min_valid": 3,
        "absolute_breadth_expansionary": 0.6,
        "absolute_breadth_contractionary": 0.4,
        "inflation_direction_buffer_pp": INFLATION_MOMENTUM_BUFFER,
        "driver_decomposition": (
            "level_gap=sum(10*effective_weight*recent_3m_avg_z); "
            "momentum=sum(10*effective_weight*(recent_3m_avg_z-prior_3m_avg_z))"
        ),
        "decomposition_tolerance": DECOMPOSITION_TOLERANCE,
    }


def _change_conditions() -> list[str]:
    return [
        "相对水平高于+1.5或低于-1.5，才确认高位或低位；缓冲区内继承已确认轴。",
        "三月均值较前三月均值上升+1.5或下降-1.5，才确认动能方向。",
        "领先方向与候选阶段一致时需连续2个可决策月，否则需连续3个月。",
        "相邻象限允许双向切换；若两个轴同时翻转到对角阶段，保留真实候选但至少连续3个月确认。",
        "成分不可比或换篮子月立即清空未确认候选；下一可决策月从第1个月重新累计，也不会给出确定切换日期。",
        "首个连续不可判月最多暂时沿用一次当前阶段；从第2个月起 current_phase 置空，旧 phase 仅保留用于历史展示与回测兼容。",
    ]


def _empty_response(matrix: dict, warning: str) -> dict:
    return {
        "region": "CN",
        "country": "中国",
        "title": "中国相对增长周期识别",
        "mode": matrix.get("mode", "current"),
        "data_basis": matrix.get("data_basis", "final"),
        "as_of": None,
        "last_decision_period": None,
        "realtime_coverage": None,
        "a1_methodology_version": matrix.get("methodology_version"),
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": "A1矩阵不足，暂不能识别阶段。",
        "methodology": _methodology(),
        "change_conditions": _change_conditions(),
        "latest": None,
        "months": [],
        "trajectory": [],
        "timeline": [],
        "a1_warnings": matrix.get("warnings", []),
        "warnings": [warning],
    }


def _buffered_matrix(
    db: Session,
    *,
    start: date | None,
    end: date | None,
    months: int,
) -> tuple[dict, pd.Period | None]:
    if start and end and start > end:
        raise ValueError("start must not be later than end")
    output_start = pd.Period(start, freq="M") if start else None
    if output_start is not None and end is not None:
        output_end = pd.Period(end, freq="M")
        requested = _month_distance(str(output_end), str(output_start)) + 1
        if requested > MAX_OUTPUT_MONTHS:
            raise ValueError(f"requested range must not exceed {MAX_OUTPUT_MONTHS} months")

    # State must not cold-start at the caller's display boundary. Always load
    # the same maximum A1 history for a given end month, then slice only after
    # the complete regime state machine has run. This also makes months=24 and
    # months=120 return the same latest phase, confirmation date and duration.
    matrix = build_china_business_cycle_matrix(
        db,
        end=end,
        months=MAX_OUTPUT_MONTHS,
    )
    matrix_months = matrix.get("months", [])
    if output_start is not None and matrix_months:
        available_end = pd.Period(matrix_months[-1]["period"], freq="M")
        if output_start > available_end:
            raise ValueError("requested monthly range is empty")
        requested = _month_distance(str(available_end), str(output_start)) + 1
        if requested > MAX_OUTPUT_MONTHS:
            raise ValueError(f"requested range must not exceed {MAX_OUTPUT_MONTHS} months")
    return matrix, output_start


def build_china_cycle_regime(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    months: int = DEFAULT_OUTPUT_MONTHS,
) -> dict:
    """Build A2 from A1 and query only the two independent inflation series."""

    if not 1 <= months <= MAX_OUTPUT_MONTHS:
        raise ValueError(f"months must be between 1 and {MAX_OUTPUT_MONTHS}")
    matrix, output_start = _buffered_matrix(
        db,
        start=start,
        end=end,
        months=months,
    )
    query = (
        select(DataPoint.indicator_code, DataPoint.date, DataPoint.value)
        .where(DataPoint.indicator_code.in_(("CN_CORE_CPI", "CN_PPI")))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    )
    if end is not None:
        query = query.where(DataPoint.date <= pd.Period(end, freq="M").end_time.date())
    inflation_rows = [
        {
            "indicator_code": row.indicator_code,
            "date": row.date,
            "value": float(row.value),
        }
        for row in db.execute(query).all()
    ]
    return _build_regime_from_matrix(
        matrix,
        inflation_rows,
        output_start=output_start,
        output_months=months,
    )
