"""China business-cycle monthly activity matrix (A1).

The service is read-only and uses the latest/final database snapshot. Every
score is calibrated with observations strictly earlier than the scored month,
so adding a later-dated observation cannot change an earlier score. Historical
revisions or backfills can still change it; A3 will reuse this calculation over
vintage/as-of inputs for genuine real-time tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil
from typing import Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indicator_catalog import get_indicator_catalog
from app.indicator_defs import INDICATOR_DEFS
from app.models import DataPoint
from app.services.derived_metrics import DERIVED_METRIC_SPECS


METHODOLOGY_VERSION = "1.0.0"
DEFAULT_OUTPUT_MONTHS = 120
MAX_OUTPUT_MONTHS = 240
ROLLING_WINDOW_MONTHS = 60
MONTHLY_MIN_HISTORY = 24
QUARTERLY_MIN_HISTORY = 8
ZSCORE_CLIP = 3.0
INDEX_NEUTRAL = 100.0
INDEX_SCALE = 10.0
BLOCK_MIN_COVERAGE = 0.5
OVERALL_MIN_COVERAGE = 0.65
MIN_ACTIVE_BLOCKS = 4
QUARTERLY_FORWARD_FILL_MONTHS = 2  # observation month plus two later months

# A formula change creates a different economic series, even when the indicator
# code stays the same.  A1 therefore admits only the currently documented
# formula version and reports any legacy rows instead of silently splicing them
# into the history used for standardisation.
FORMULA_VERSIONED_CYCLE_INPUTS = {
    code: DERIVED_METRIC_SPECS[code].version
    for code in ("CN_CREDIT_IMPULSE", "CN_M1M2", "CN_FISCAL_IMPULSE_PROXY")
}

CycleRole = Literal["coincident", "leading"]
SignalOperation = Literal["level", "mean", "difference", "trailing_mean_3"]


@dataclass(frozen=True, slots=True)
class CycleSignalSpec:
    key: str
    name: str
    input_codes: tuple[str, ...]
    weight: float
    operation: SignalOperation = "level"
    direction: int = 1
    calibration_start: str | None = None


@dataclass(frozen=True, slots=True)
class CycleBlockSpec:
    key: str
    name: str
    role: CycleRole
    signals: tuple[CycleSignalSpec, ...]
    weight: float = 0.2


def _direct(code: str, weight: float, *, direction: int = 1) -> CycleSignalSpec:
    return CycleSignalSpec(
        key=code,
        name=code,
        input_codes=(code,),
        weight=weight,
        direction=direction,
    )


# Five blocks have fixed 20% base weights. Missing signals are re-normalized
# only after a block passes its coverage gate; missing values are never zero.
CHINA_CYCLE_BLOCKS: tuple[CycleBlockSpec, ...] = (
    CycleBlockSpec(
        key="activity",
        name="同步活动",
        role="coincident",
        signals=(
            _direct("CN_PMI_PRODUCTION", 0.30),
            _direct("CN_NMI", 0.30),
            _direct("CN_RETAIL", 0.25),
            _direct("CN_IP", 0.15),
        ),
    ),
    CycleBlockSpec(
        key="demand_expectations",
        name="领先需求与预期",
        role="leading",
        signals=(
            CycleSignalSpec(
                key="CN_DOMESTIC_NEW_ORDERS",
                name="制造业与非制造业新订单均值",
                input_codes=("CN_PMI_NEW_ORDERS", "CN_NMI_NEW_ORDERS"),
                operation="mean",
                weight=0.30,
            ),
            _direct("CN_PMI_NEW_EXPORT_ORDERS", 0.20),
            CycleSignalSpec(
                key="CN_FINISHED_GOODS_INVENTORY_PRESSURE",
                name="产成品库存积压代理（反向）",
                input_codes=("CN_PMI_FINISHED_GOODS_INVENTORY",),
                weight=0.15,
                direction=-1,
            ),
            CycleSignalSpec(
                key="CN_BUSINESS_EXPECTATIONS",
                name="制造业与非制造业经营预期均值",
                input_codes=("CN_PMI_EXPECTATIONS", "CN_NMI_EXPECTATIONS"),
                operation="mean",
                weight=0.20,
            ),
            _direct("CN_CONSUMER_EXPECTATIONS", 0.15),
        ),
    ),
    CycleBlockSpec(
        key="credit_policy",
        name="信用与货币政策",
        role="leading",
        signals=(
            _direct("CN_CREDIT_IMPULSE", 0.60),
            CycleSignalSpec(
                key="CN_M1M2",
                name="CN_M1M2",
                input_codes=("CN_M1M2",),
                weight=0.40,
                calibration_start="2024-01",
            ),
        ),
    ),
    CycleBlockSpec(
        key="property_fiscal",
        name="房地产与财政",
        role="leading",
        signals=(
            _direct("CN_RE_SALES_AREA_YTD_YOY", 0.25),
            _direct("CN_RE_STARTS_YTD_YOY", 0.20),
            _direct("CN_RE_INVEST_YTD_YOY", 0.20),
            _direct("CN_RE_PRICE_RISING_SHARE", 0.20),
            _direct("CN_FISCAL_IMPULSE_PROXY", 0.15),
        ),
    ),
    CycleBlockSpec(
        key="employment_external",
        name="就业与外需",
        role="coincident",
        signals=(
            _direct("CN_PMI_EMPLOYMENT", 0.30),
            _direct("CN_NMI_EMPLOYMENT", 0.30),
            CycleSignalSpec(
                key="CN_EXPORTS_3M_AVG",
                name="出口同比三月均值",
                input_codes=("CN_EXPORTS",),
                operation="trailing_mean_3",
                weight=0.40,
            ),
        ),
    ),
)

# These series never enter the main score. They are external comparison series,
# not all statistically independent: CLI and the real-estate climate index can
# overlap with inputs, while GDP is the cleaner outcome variable for validation.
VALIDATION_CODES = ("CN_CLI", "CN_GDP", "CN_REALESTATE")
_DEFINITIONS = {definition["code"]: definition for definition in INDICATOR_DEFS}


@dataclass(slots=True)
class PreparedSignal:
    score: pd.Series
    raw: pd.Series
    source_period: pd.Series
    realtime_known: pd.Series


def _validate_block_specs(blocks: tuple[CycleBlockSpec, ...]) -> None:
    keys = [block.key for block in blocks]
    signal_keys = [signal.key for block in blocks for signal in block.signals]
    input_codes = {
        code for block in blocks for signal in block.signals for code in signal.input_codes
    }
    if len(keys) != len(set(keys)) or len(signal_keys) != len(set(signal_keys)):
        raise ValueError("cycle block and signal keys must be unique")
    unknown = input_codes - set(_DEFINITIONS)
    if unknown:
        raise ValueError(f"unknown cycle indicator codes: {sorted(unknown)}")
    if {block.role for block in blocks} != {"coincident", "leading"}:
        raise ValueError("cycle matrix requires coincident and leading blocks")
    if abs(sum(block.weight for block in blocks) - 1.0) > 1e-9:
        raise ValueError("cycle block weights must sum to one")
    for block in blocks:
        if abs(sum(signal.weight for signal in block.signals) - 1.0) > 1e-9:
            raise ValueError(f"signal weights in {block.key} must sum to one")
        if any(signal.direction not in {-1, 1} for signal in block.signals):
            raise ValueError("cycle signal direction must be +1 or -1")


_validate_block_specs(CHINA_CYCLE_BLOCKS)


def cycle_indicator_codes(
    blocks: tuple[CycleBlockSpec, ...] = CHINA_CYCLE_BLOCKS,
) -> tuple[str, ...]:
    """Unique stored inputs in deterministic first-use order."""

    return tuple(
        dict.fromkeys(
            code
            for block in blocks
            for signal in block.signals
            for code in signal.input_codes
        )
    )


def _one_sided_robust_zscore(
    observations: pd.Series,
    *,
    min_history: int,
    window_months: int = ROLLING_WINDOW_MONTHS,
    clip: float = ZSCORE_CLIP,
) -> pd.Series:
    """Robust z-score using only the preceding, at-most-60-month window.

    Before the window fills this behaves as a one-sided expanding window. MAD
    is the robust scale; sample standard deviation is a fallback for discrete
    series whose historical MAD is temporarily zero.
    """

    values = pd.to_numeric(observations, errors="coerce").dropna().astype(float).sort_index()
    result = pd.Series(index=values.index, dtype=float)
    for period, value in values.items():
        history = values.loc[
            (values.index < period) & (values.index >= period - window_months)
        ]
        if len(history) < min_history:
            continue
        center = float(history.median())
        scale = float((history - center).abs().median()) * 1.4826
        if scale <= 1e-12:
            scale = float(history.std(ddof=1))
        if pd.isna(scale) or scale <= 1e-12:
            continue
        result.loc[period] = max(-clip, min(clip, (float(value) - center) / scale))
    return result


def _month_distance(left: pd.Period, right: pd.Period) -> int:
    return (left.year - right.year) * 12 + left.month - right.month


def _raw_series_from_rows(
    rows: list[dict], code: str
) -> tuple[pd.Series, pd.Series, dict]:
    selected = [row for row in rows if row["indicator_code"] == code]
    required_version = FORMULA_VERSIONED_CYCLE_INPUTS.get(code)
    empty_audit = {
        "required_formula_version": required_version,
        "latest_formula_version": None,
        "latest_status": None,
        "latest_stored_observation": None,
        "latest_stored_formula_version": None,
        "latest_stored_status": None,
        "excluded_version_observations": 0,
        "excluded_formula_versions": [],
    }
    if not selected:
        return pd.Series(dtype=float), pd.Series(dtype=bool), empty_audit
    frame = pd.DataFrame(selected)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    if "available_at" not in frame:
        frame["available_at"] = None
    if "formula_version" not in frame:
        frame["formula_version"] = None
    if "status" not in frame:
        frame["status"] = None
    frame = frame.dropna(subset=["date", "value"]).sort_values("date")
    if frame.empty:
        return pd.Series(dtype=float), pd.Series(dtype=bool), empty_audit

    latest_stored = frame.iloc[-1]
    audit = {
        **empty_audit,
        "latest_stored_observation": str(latest_stored["date"].to_period("M")),
        "latest_stored_formula_version": (
            None
            if pd.isna(latest_stored["formula_version"])
            else str(latest_stored["formula_version"])
        ),
        "latest_stored_status": (
            None if pd.isna(latest_stored["status"]) else str(latest_stored["status"])
        ),
    }
    if required_version is not None:
        version_text = frame["formula_version"].astype("string")
        eligible = version_text.eq(required_version).fillna(False)
        excluded = frame.loc[~eligible, "formula_version"]
        audit["excluded_version_observations"] = int((~eligible).sum())
        audit["excluded_formula_versions"] = sorted(
            {
                "missing" if pd.isna(value) else str(value)
                for value in excluded.tolist()
            }
        )
        frame = frame.loc[eligible]
        if frame.empty:
            return pd.Series(dtype=float), pd.Series(dtype=bool), audit

    latest_eligible = frame.iloc[-1]
    audit["latest_formula_version"] = (
        None
        if pd.isna(latest_eligible["formula_version"])
        else str(latest_eligible["formula_version"])
    )
    audit["latest_status"] = (
        None if pd.isna(latest_eligible["status"]) else str(latest_eligible["status"])
    )
    frame["period"] = frame["date"].dt.to_period("M")
    frame = frame.drop_duplicates("period", keep="last")
    values = pd.Series(frame["value"].to_numpy(dtype=float), index=frame["period"])
    known = pd.Series(
        frame["available_at"].notna().to_numpy(dtype=bool),
        index=frame["period"],
    )
    return values, known, audit


def _signal_observations(
    spec: CycleSignalSpec,
    raw_values: dict[str, pd.Series],
    raw_realtime: dict[str, pd.Series],
) -> tuple[pd.Series, pd.Series]:
    dependencies = [raw_values[code] for code in spec.input_codes]
    populated = [series for series in dependencies if not series.empty]
    if len(populated) != len(dependencies):
        return pd.Series(dtype=float), pd.Series(dtype=bool)
    start = min(series.index.min() for series in populated)
    end = max(series.index.max() for series in populated)
    calendar = pd.period_range(start, end, freq="M")
    aligned = [series.reindex(calendar) for series in dependencies]
    aligned_known = [raw_realtime[code].reindex(calendar) for code in spec.input_codes]

    if spec.operation == "level":
        values = aligned[0]
        known = aligned_known[0]
    elif spec.operation == "mean":
        values = pd.concat(aligned, axis=1).mean(axis=1, skipna=False)
        known = pd.concat(aligned_known, axis=1).all(axis=1)
    elif spec.operation == "difference":
        values = aligned[0] - aligned[1]
        known = pd.concat(aligned_known, axis=1).all(axis=1)
    elif spec.operation == "trailing_mean_3":
        values = aligned[0].rolling(3, min_periods=3).mean()
        known = (
            aligned_known[0].fillna(False).astype(int).rolling(3, min_periods=3).sum()
            == 3
        )
    else:  # pragma: no cover - Literal and static config make this defensive
        raise ValueError(f"unsupported signal operation: {spec.operation}")
    valid_values = values.dropna()
    known = known.reindex(valid_values.index).eq(True).astype(bool)
    if spec.calibration_start is not None:
        calibration_start = pd.Period(spec.calibration_start, freq="M")
        valid_values = valid_values.loc[valid_values.index >= calibration_start]
        known = known.reindex(valid_values.index)
    return valid_values, known


def _signal_frequency(spec: CycleSignalSpec) -> str:
    if spec.operation != "level" or len(spec.input_codes) != 1:
        return "monthly"
    return get_indicator_catalog()[spec.input_codes[0]].frequency


def _prepare_signal(
    observations: pd.Series,
    realtime_known: pd.Series,
    *,
    frequency: str,
    direction: int,
    calendar: pd.PeriodIndex,
) -> PreparedSignal:
    minimum = QUARTERLY_MIN_HISTORY if frequency == "quarterly" else MONTHLY_MIN_HISTORY
    score = _one_sided_robust_zscore(observations, min_history=minimum) * direction
    source_period = pd.Series(
        [str(period) for period in observations.index], index=observations.index, dtype=object
    )
    aligned_score = score.reindex(calendar)
    aligned_raw = observations.reindex(calendar)
    aligned_source = source_period.reindex(calendar)
    aligned_realtime = realtime_known.reindex(calendar)
    if frequency == "quarterly":
        # Standardize at source frequency first; otherwise carrying a quarter
        # into three months would distort its own historical center and scale.
        aligned_score = aligned_score.ffill(limit=QUARTERLY_FORWARD_FILL_MONTHS)
        aligned_raw = aligned_raw.ffill(limit=QUARTERLY_FORWARD_FILL_MONTHS)
        aligned_source = aligned_source.ffill(limit=QUARTERLY_FORWARD_FILL_MONTHS)
        aligned_realtime = aligned_realtime.ffill(limit=QUARTERLY_FORWARD_FILL_MONTHS)
    return PreparedSignal(
        score=aligned_score,
        raw=aligned_raw,
        source_period=aligned_source,
        realtime_known=aligned_realtime,
    )


def _round_optional(value: float | None, digits: int = 2) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _name_for_signal(signal: CycleSignalSpec) -> str:
    if signal.name != signal.key:
        return signal.name
    return str(_DEFINITIONS[signal.input_codes[0]]["name"])


def _compose_month(
    period: pd.Period,
    *,
    blocks: tuple[CycleBlockSpec, ...],
    prepared: dict[str, PreparedSignal],
    validations: dict[str, PreparedSignal],
) -> dict:
    block_rows: list[dict] = []
    signal_specs = {
        signal.key: signal for block in blocks for signal in block.signals
    }
    for block in blocks:
        signals = []
        weighted_score = 0.0
        available_weight = 0.0
        realtime_weight = 0.0
        available_count = 0
        for spec in block.signals:
            item = prepared[spec.key]
            score = item.score.get(period)
            raw_value = item.raw.get(period)
            source_period = item.source_period.get(period)
            realtime_known = item.realtime_known.get(period)
            usable = score is not None and not pd.isna(score)
            if usable:
                score = float(score)
                weighted_score += score * spec.weight
                available_weight += spec.weight
                available_count += 1
                if (
                    realtime_known is not None
                    and not pd.isna(realtime_known)
                    and bool(realtime_known)
                ):
                    realtime_weight += spec.weight
            signals.append(
                {
                    "code": spec.key,
                    "name": _name_for_signal(spec),
                    "input_codes": list(spec.input_codes),
                    "weight": spec.weight,
                    "raw_value": _round_optional(raw_value, 4),
                    "source_period": None if pd.isna(source_period) else str(source_period),
                    "standardized_score": _round_optional(score, 4),
                    "realtime_ready": (
                        bool(realtime_known)
                        if usable and realtime_known is not None and not pd.isna(realtime_known)
                        else False
                    ),
                    "contribution": None,
                }
            )
        coverage = available_weight
        eligible = available_count >= 2 and coverage >= BLOCK_MIN_COVERAGE
        block_z = weighted_score / available_weight if eligible else None
        block_rows.append(
            {
                "key": block.key,
                "name": block.name,
                "role": block.role,
                "score": _round_optional(
                    INDEX_NEUTRAL + INDEX_SCALE * block_z if block_z is not None else None
                ),
                "coverage": round(coverage, 4),
                "realtime_coverage": round(realtime_weight, 4),
                "available_count": available_count,
                "total_count": len(block.signals),
                "minimum_coverage": BLOCK_MIN_COVERAGE,
                "contribution": None,
                "signals": signals,
                "_z": block_z,
                "_weight": block.weight,
                "_available_weight": available_weight,
            }
        )

    role_indexes: dict[str, float | None] = {}
    role_coverages: dict[str, float] = {}
    for role in ("coincident", "leading"):
        role_blocks = [row for row in block_rows if row["role"] == role]
        eligible_blocks = [row for row in role_blocks if row["_z"] is not None]
        minimum_blocks = len(role_blocks) if role == "coincident" else ceil(len(role_blocks) * 0.5)
        role_weight = sum(row["_weight"] for row in role_blocks)
        role_coverages[role] = sum(
            row["coverage"] * row["_weight"] for row in role_blocks
        ) / role_weight
        if len(eligible_blocks) < minimum_blocks:
            role_indexes[role] = None
            continue
        weight_sum = sum(row["_weight"] for row in eligible_blocks)
        role_z = sum(row["_z"] * row["_weight"] for row in eligible_blocks) / weight_sum
        role_indexes[role] = INDEX_NEUTRAL + INDEX_SCALE * role_z

    # ``input_coverage`` answers how much configured signal weight has a value.
    # It deliberately includes isolated observations in a block that fails its
    # two-signal gate. ``overall_coverage`` instead measures only fixed block
    # weights that genuinely enter the composite score.
    input_coverage = sum(row["coverage"] * row["_weight"] for row in block_rows)
    realtime_coverage = sum(
        row["realtime_coverage"] * row["_weight"] for row in block_rows
    )
    eligible_blocks = [row for row in block_rows if row["_z"] is not None]
    overall_coverage = sum(row["_weight"] for row in eligible_blocks)
    composite_index = None
    if len(eligible_blocks) >= MIN_ACTIVE_BLOCKS and overall_coverage >= OVERALL_MIN_COVERAGE:
        weight_sum = sum(row["_weight"] for row in eligible_blocks)
        composite_z = 0.0
        for row in eligible_blocks:
            normalized_block_weight = row["_weight"] / weight_sum
            composite_z += row["_z"] * normalized_block_weight
            row["contribution"] = round(
                INDEX_SCALE * row["_z"] * normalized_block_weight, 4
            )
            for signal in row["signals"]:
                score = signal["standardized_score"]
                if score is not None:
                    spec = signal_specs[signal["code"]]
                    normalized_signal_weight = spec.weight / row["_available_weight"]
                    signal["contribution"] = round(
                        INDEX_SCALE
                        * score
                        * normalized_signal_weight
                        * normalized_block_weight,
                        4,
                    )
        composite_index = INDEX_NEUTRAL + INDEX_SCALE * composite_z

    validation_rows = []
    for code, item in validations.items():
        source_period = item.source_period.get(period)
        validation_rows.append(
            {
                "code": code,
                "name": _DEFINITIONS[code]["name"],
                "raw_value": _round_optional(item.raw.get(period), 4),
                "source_period": None if pd.isna(source_period) else str(source_period),
                "standardized_score": _round_optional(item.score.get(period), 4),
            }
        )

    for row in block_rows:
        row.pop("_z")
        row.pop("_weight")
        row.pop("_available_weight")
    coincident_index = role_indexes["coincident"]
    leading_index = role_indexes["leading"]
    confidence = _confidence(
        overall_coverage=overall_coverage,
        input_coverage=input_coverage,
        active_blocks=len(eligible_blocks),
        total_blocks=len(blocks),
        composite_index=composite_index,
        coincident_index=coincident_index,
        leading_index=leading_index,
    )
    return {
        "period": str(period),
        "composite_index": _round_optional(composite_index),
        "coincident_index": _round_optional(coincident_index),
        "leading_index": _round_optional(leading_index),
        "coincident_coverage": round(role_coverages["coincident"], 4),
        "leading_coverage": round(role_coverages["leading"], 4),
        "overall_coverage": round(overall_coverage, 4),
        "input_coverage": round(input_coverage, 4),
        "realtime_coverage": round(realtime_coverage, 4),
        "active_blocks": len(eligible_blocks),
        "confidence": confidence,
        "blocks": block_rows,
        "validation": validation_rows,
    }


def _confidence(
    *,
    overall_coverage: float,
    input_coverage: float,
    active_blocks: int,
    total_blocks: int,
    composite_index: float | None,
    coincident_index: float | None,
    leading_index: float | None,
) -> str:
    if (
        composite_index is None
        or coincident_index is None
        or leading_index is None
        or active_blocks < MIN_ACTIVE_BLOCKS
        or overall_coverage < OVERALL_MIN_COVERAGE
    ):
        return "insufficient"
    if active_blocks == total_blocks and input_coverage >= 0.85:
        return "high"
    if overall_coverage >= 0.75:
        return "medium"
    return "low"


def _latest_summary(
    latest_month: dict | None,
    *,
    blocks: tuple[CycleBlockSpec, ...],
    prepared: dict[str, PreparedSignal],
    input_audits: dict[str, dict] | None = None,
) -> dict | None:
    if latest_month is None:
        return None
    period = pd.Period(latest_month["period"], freq="M")
    contributions = []
    for block in latest_month["blocks"]:
        for signal in block["signals"]:
            contribution = signal["contribution"]
            if contribution is None:
                continue
            contributions.append(
                {
                    "code": signal["code"],
                    "name": signal["name"],
                    "role": block["role"],
                    "block_key": block["key"],
                    "source_period": signal["source_period"],
                    "standardized_score": signal["standardized_score"],
                    "contribution": contribution,
                }
            )
    positive = sorted(
        (item for item in contributions if item["contribution"] > 0),
        key=lambda item: item["contribution"],
        reverse=True,
    )
    negative = sorted(
        (item for item in contributions if item["contribution"] < 0),
        key=lambda item: item["contribution"],
    )

    input_latest = []
    input_audits = input_audits or {}
    for block in blocks:
        for spec in block.signals:
            item = prepared[spec.key]
            actual_periods = item.source_period.dropna()
            latest_observation = (
                max(pd.Period(value, freq="M") for value in actual_periods.unique())
                if not actual_periods.empty
                else None
            )
            latest_value = None
            if latest_observation is not None:
                same_observation = item.raw.loc[
                    item.source_period == str(latest_observation)
                ].dropna()
                if not same_observation.empty:
                    latest_value = same_observation.iloc[0]
            used_source = item.source_period.get(period)
            used_score = item.score.get(period)
            lag = (
                max(_month_distance(period, latest_observation), 0)
                if latest_observation
                else None
            )
            frequency = _signal_frequency(spec)
            stale_after = 4 if frequency == "quarterly" else 2
            direct_input_audit = (
                input_audits.get(spec.input_codes[0], {})
                if len(spec.input_codes) == 1
                else {}
            )
            input_latest.append(
                {
                    "code": spec.key,
                    "name": _name_for_signal(spec),
                    "input_codes": list(spec.input_codes),
                    "frequency": frequency,
                    "latest_observation": str(latest_observation) if latest_observation else None,
                    "latest_value": _round_optional(latest_value, 4),
                    "lag_months": lag,
                    "is_stale": lag is None or lag > stale_after,
                    "used_in_latest": used_score is not None and not pd.isna(used_score),
                    "used_observation": None if pd.isna(used_source) else str(used_source),
                    "required_formula_version": direct_input_audit.get(
                        "required_formula_version"
                    ),
                    "latest_formula_version": direct_input_audit.get(
                        "latest_formula_version"
                    ),
                    "latest_status": direct_input_audit.get("latest_status"),
                    "latest_stored_observation": direct_input_audit.get(
                        "latest_stored_observation"
                    ),
                    "latest_stored_formula_version": direct_input_audit.get(
                        "latest_stored_formula_version"
                    ),
                    "latest_stored_status": direct_input_audit.get(
                        "latest_stored_status"
                    ),
                    "excluded_version_observations": direct_input_audit.get(
                        "excluded_version_observations", 0
                    ),
                }
            )
    return {
        "period": latest_month["period"],
        "composite_index": latest_month["composite_index"],
        "coincident_index": latest_month["coincident_index"],
        "leading_index": latest_month["leading_index"],
        "overall_coverage": latest_month["overall_coverage"],
        "input_coverage": latest_month["input_coverage"],
        "realtime_coverage": latest_month["realtime_coverage"],
        "confidence": latest_month["confidence"],
        "positive_contributions": positive,
        "negative_contributions": negative,
        "input_latest_periods": input_latest,
    }


def _composition_signature(
    month: dict,
    *,
    blocks: tuple[CycleBlockSpec, ...],
) -> tuple | None:
    """Describe exactly which signals and normalized weights formed a score.

    A quarterly observation naturally ages while it is forward-filled; that
    does not by itself change the score's composition.  A changed source age is
    reported through ``source_period`` but does not invalidate comparability.
    """

    if month["composite_index"] is None:
        return None
    block_specs = {block.key: block for block in blocks}
    active_rows = [row for row in month["blocks"] if row["contribution"] is not None]
    active_weight = sum(block_specs[row["key"]].weight for row in active_rows)
    signature = []
    for row in active_rows:
        block_spec = block_specs[row["key"]]
        signal_specs = {signal.key: signal for signal in block_spec.signals}
        used_signals = [
            signal for signal in row["signals"] if signal["contribution"] is not None
        ]
        available_weight = sum(signal_specs[item["code"]].weight for item in used_signals)
        block_weight = block_spec.weight / active_weight
        signal_signature = []
        for item in used_signals:
            effective_weight = (
                block_weight * signal_specs[item["code"]].weight / available_weight
            )
            signal_signature.append(
                (
                    item["code"],
                    round(effective_weight, 12),
                )
            )
        signature.append(
            (row["key"], round(block_weight, 12), tuple(signal_signature))
        )
    return tuple(signature)


def _comparison_reasons(
    previous: dict,
    current: dict,
    *,
    blocks: tuple[CycleBlockSpec, ...],
) -> list[str]:
    """Explain why a raw month-over-month index change is not like-for-like."""

    reasons: list[str] = []
    if previous["composite_index"] is None:
        reasons.append("previous_month_score_insufficient")
    if current["composite_index"] is None:
        reasons.append("current_month_score_insufficient")

    previous_blocks = {
        row["key"] for row in previous["blocks"] if row["contribution"] is not None
    }
    current_blocks = {
        row["key"] for row in current["blocks"] if row["contribution"] is not None
    }
    if previous_blocks != current_blocks:
        reasons.append("available_block_set_changed")

    previous_rows = {row["key"]: row for row in previous["blocks"]}
    current_rows = {row["key"]: row for row in current["blocks"]}
    signal_set_changed = False
    for block_key in previous_blocks | current_blocks:
        previous_signals = {
            signal["code"]: signal
            for signal in previous_rows[block_key]["signals"]
            if signal["contribution"] is not None
        }
        current_signals = {
            signal["code"]: signal
            for signal in current_rows[block_key]["signals"]
            if signal["contribution"] is not None
        }
        if previous_signals.keys() != current_signals.keys():
            signal_set_changed = True
    if signal_set_changed:
        reasons.append("available_signal_set_changed")

    old_signature = _composition_signature(previous, blocks=blocks)
    new_signature = _composition_signature(current, blocks=blocks)
    if (
        old_signature != new_signature
        and previous_blocks == current_blocks
        and not signal_set_changed
    ):
        reasons.append("effective_weight_composition_changed")

    previous_period = pd.Period(previous["period"], freq="M")
    current_period = pd.Period(current["period"], freq="M")
    if reasons and (previous_period.month == 1 or current_period.month == 1):
        reasons.append("regular_january_data_gap")
    return reasons


def _annotate_comparability(
    months: list[dict],
    *,
    blocks: tuple[CycleBlockSpec, ...],
    previous_month: dict | None = None,
) -> None:
    previous = previous_month
    for month in months:
        if previous is None:
            month["comparable_to_previous"] = False
            month["composition_changed"] = False
            month["comparison_reasons"] = ["no_previous_month"]
        else:
            reasons = _comparison_reasons(previous, month, blocks=blocks)
            month["comparable_to_previous"] = not reasons
            month["composition_changed"] = any(
                reason
                in {
                    "available_block_set_changed",
                    "available_signal_set_changed",
                    "effective_weight_composition_changed",
                    "regular_january_data_gap",
                }
                for reason in reasons
            )
            month["comparison_reasons"] = reasons
        previous = month


def _block_metadata(blocks: tuple[CycleBlockSpec, ...]) -> list[dict]:
    catalog = get_indicator_catalog()
    return [
        {
            "key": block.key,
            "name": block.name,
            "role": block.role,
            "weight": block.weight,
            "minimum_coverage": BLOCK_MIN_COVERAGE,
            "indicator_codes": list(
                dict.fromkeys(code for signal in block.signals for code in signal.input_codes)
            ),
            "signals": [
                {
                    "code": signal.key,
                    "name": _name_for_signal(signal),
                    "input_codes": list(signal.input_codes),
                    "weight": signal.weight,
                    "operation": signal.operation,
                    "direction": "positive" if signal.direction > 0 else "negative",
                    "frequency": _signal_frequency(signal),
                    "calibration_start": signal.calibration_start,
                    "sources": list(
                        dict.fromkeys(catalog[code].source for code in signal.input_codes)
                    ),
                }
                for signal in block.signals
            ],
        }
        for block in blocks
    ]


def _validation_metadata() -> list[dict]:
    catalog = get_indicator_catalog()
    return [
        {
            "code": code,
            "name": _DEFINITIONS[code]["name"],
            "unit": _DEFINITIONS[code]["unit"],
            "source": catalog[code].source,
            "frequency": catalog[code].frequency,
            "purpose": "external_validation_only",
        }
        for code in VALIDATION_CODES
    ]


def _methodology() -> dict:
    return {
        "standardization": "prior_only_robust_zscore_expanding_to_60m",
        "rolling_window_months": ROLLING_WINDOW_MONTHS,
        "monthly_min_history": MONTHLY_MIN_HISTORY,
        "quarterly_min_history": QUARTERLY_MIN_HISTORY,
        "zscore_clip": ZSCORE_CLIP,
        "quarterly_forward_fill_months": QUARTERLY_FORWARD_FILL_MONTHS,
        "block_min_coverage": BLOCK_MIN_COVERAGE,
        "minimum_active_blocks": MIN_ACTIVE_BLOCKS,
        "overall_min_coverage": OVERALL_MIN_COVERAGE,
        "neutral_level": INDEX_NEUTRAL,
        "index_scale": INDEX_SCALE,
        "missing_value_policy": "exclude_then_dynamic_renormalize; never_zero_fill",
        "weighting": "fixed_signal_weights_then_equal_20pct_block_weights",
        "input_coverage_definition": (
            "全部已获得可计算值的信号配置权重占比；即使所在板块未通过最少2信号门槛，"
            "仍用于反映原始数据到齐程度。"
        ),
        "overall_coverage_definition": (
            "真正通过板块门槛并进入总分的固定板块权重占比；四个有效板块为80%。"
        ),
        "realtime_coverage_definition": (
            "配置权重中，同时有可计算分数且观测available_at已知的比例；"
            "它衡量伪实时回测准备度，不改变当前快照得分。"
        ),
    }


def _empty_matrix(blocks: tuple[CycleBlockSpec, ...], warning: str) -> dict:
    return {
        "region": "CN",
        "country": "中国",
        "title": "中国经济周期月度活动矩阵",
        "mode": "current",
        "data_basis": "final",
        "as_of": None,
        "realtime_coverage": None,
        "method": "prior_only_robust_zscore_equal_block_weight",
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": "数据库暂无足够输入，尚不能计算活动矩阵。",
        "methodology": _methodology(),
        "blocks": _block_metadata(blocks),
        "validation_indicators": _validation_metadata(),
        "months": [],
        "latest": None,
        "warnings": [warning],
    }


def _matrix_from_rows(
    rows: list[dict],
    *,
    start: date | None = None,
    end: date | None = None,
    months: int = DEFAULT_OUTPUT_MONTHS,
    blocks: tuple[CycleBlockSpec, ...] = CHINA_CYCLE_BLOCKS,
) -> dict:
    """Pure calculation core, separated from SQL for deterministic tests."""

    if not 1 <= months <= MAX_OUTPUT_MONTHS:
        raise ValueError(f"months must be between 1 and {MAX_OUTPUT_MONTHS}")
    if start and end and start > end:
        raise ValueError("start must not be later than end")

    query_codes = tuple(dict.fromkeys((*cycle_indicator_codes(blocks), *VALIDATION_CODES)))
    raw_values: dict[str, pd.Series] = {}
    raw_realtime: dict[str, pd.Series] = {}
    input_audits: dict[str, dict] = {}
    for code in query_codes:
        (
            raw_values[code],
            raw_realtime[code],
            input_audits[code],
        ) = _raw_series_from_rows(rows, code)
    # The output calendar is owned only by inputs that can enter the score.
    # A malformed or future-dated validation series must never pull the matrix
    # into empty tail months (or make an otherwise valid request return 400).
    score_inputs = [
        raw_values[code]
        for code in cycle_indicator_codes(blocks)
        if not raw_values[code].empty
    ]
    populated = [series for series in raw_values.values() if not series.empty]
    latest_observation = max((series.index.max() for series in score_inputs), default=None)
    earliest_observation = min((series.index.min() for series in score_inputs), default=None)
    requested_end = pd.Period(end, freq="M") if end else latest_observation
    if requested_end is None:
        return _empty_matrix(blocks, "数据库中没有周期矩阵所需的指标。")
    if requested_end > pd.Period(date.today(), freq="M"):
        raise ValueError("end must not be later than the current month")
    if start:
        requested_start = pd.Period(start, freq="M")
        if _month_distance(requested_end, requested_start) + 1 > MAX_OUTPUT_MONTHS:
            raise ValueError(f"requested range must not exceed {MAX_OUTPUT_MONTHS} months")
    else:
        requested_start = requested_end - (months - 1)
    if requested_start > requested_end:
        raise ValueError("requested monthly range is empty")

    history_start = min(
        (series.index.min() for series in populated),
        default=requested_start,
    )
    # Compute on all pre-start history first; slicing earlier would make scores
    # depend on the caller's requested output window.
    full_calendar = pd.period_range(history_start, requested_end, freq="M")
    prepared: dict[str, PreparedSignal] = {}
    for block in blocks:
        for spec in block.signals:
            observations, known = _signal_observations(spec, raw_values, raw_realtime)
            prepared[spec.key] = _prepare_signal(
                observations,
                known,
                frequency=_signal_frequency(spec),
                direction=spec.direction,
                calendar=full_calendar,
            )
    validations: dict[str, PreparedSignal] = {}
    catalog = get_indicator_catalog()
    for code in VALIDATION_CODES:
        validations[code] = _prepare_signal(
            raw_values[code],
            raw_realtime[code],
            frequency=catalog[code].frequency,
            direction=1,
            calendar=full_calendar,
        )

    # Compose one hidden prior month as well, so the first returned month can
    # still state whether its month-on-month movement is like-for-like.
    comparison_calendar = pd.period_range(requested_start - 1, requested_end, freq="M")
    comparison_months = [
        _compose_month(
            period,
            blocks=blocks,
            prepared=prepared,
            validations=validations,
        )
        for period in comparison_calendar
    ]
    previous_month, matrix_months = comparison_months[0], comparison_months[1:]
    _annotate_comparability(
        matrix_months,
        blocks=blocks,
        previous_month=previous_month,
    )
    eligible = [item for item in matrix_months if item["confidence"] != "insufficient"]
    latest_month = eligible[-1] if eligible else None
    latest = _latest_summary(
        latest_month,
        blocks=blocks,
        prepared=prepared,
        input_audits=input_audits,
    )

    warnings = [
        "当前结果使用最新/最终数据库快照，不是历史时点可见数据；伪实时结论必须等待A3使用vintage/as_of重算。",
        "指数100是各输入相对自身既往历史的中性值，不是PMI 50点荣枯线，也不是经济增速。",
        "季度指标在原频率标准化后仅向后延用2个月；缺失值不按0计入。",
        "房地产销售面积、新开工和投资采用年内累计同比；其月度变化含累计窗口机械平滑，不等同于单月增速或单月动能。",
        "CLI、GDP和国房景气仅作外部对照、不参与评分；CLI与国房景气存在成分重叠，不能冒充独立样本外验证，GDP才是更接近结果变量的主验证目标。",
    ]
    calibrated_signals = [
        signal
        for block in blocks
        for signal in block.signals
        if signal.calibration_start is not None
    ]
    for signal in calibrated_signals:
        warnings.append(
            f"{signal.key} 因统计口径可比性仅使用 {signal.calibration_start} 起的观测校准；"
            "更早值不参与历史中心和波动尺度计算。"
        )
    for code, required_version in FORMULA_VERSIONED_CYCLE_INPUTS.items():
        audit = input_audits.get(code, {})
        excluded = int(audit.get("excluded_version_observations", 0))
        if excluded:
            versions = "、".join(audit.get("excluded_formula_versions", []))
            warnings.append(
                f"{code} 已排除 {excluded} 条公式版本为 {versions} 的观测；"
                f"主指数仅使用公式 v{required_version}。"
            )
        if raw_values.get(code, pd.Series(dtype=float)).empty:
            warnings.append(
                f"{code} 没有公式 v{required_version} 的可用观测，当前不参与主指数。"
            )
    if latest_month is None:
        warnings.append("所选区间没有同时满足至少4个板块和65%总体覆盖的合格月份。")
    elif matrix_months and latest_month["period"] != matrix_months[-1]["period"]:
        warnings.append(
            f"最新请求月份覆盖不足；latest回退到最近合格月份 {latest_month['period']}。"
        )
    if earliest_observation and requested_start < earliest_observation:
        warnings.append("请求区间早于现有输入历史，前段月份会显示覆盖不足。")

    return {
        "region": "CN",
        "country": "中国",
        "title": "中国经济周期月度活动矩阵",
        "mode": "current",
        "data_basis": "final",
        "as_of": latest_month["period"] if latest_month else None,
        "realtime_coverage": latest_month["realtime_coverage"] if latest_month else None,
        "method": "prior_only_robust_zscore_equal_block_weight",
        "methodology_version": METHODOLOGY_VERSION,
        "methodology_note": (
            "各信号只使用当期之前最多60个月历史，以中位数和MAD计算稳健z分数并截尾至±3。"
            "五个板块固定等权20%；缺项时仅在板块覆盖达50%后动态归一。"
            "总指数至少需要4个有效板块且有效板块覆盖不低于65%；"
            "只有5个板块均有效且输入覆盖不低于85%时才标为高置信度。"
        ),
        "methodology": _methodology(),
        "blocks": _block_metadata(blocks),
        "validation_indicators": _validation_metadata(),
        "months": matrix_months,
        "latest": latest,
        "warnings": warnings,
    }


def build_china_business_cycle_matrix(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    months: int = DEFAULT_OUTPUT_MONTHS,
) -> dict:
    """Read current values and build the A1 matrix without writing to the DB."""

    codes = list(dict.fromkeys((*cycle_indicator_codes(), *VALIDATION_CODES)))
    query = (
        select(
            DataPoint.indicator_code,
            DataPoint.date,
            DataPoint.value,
            DataPoint.available_at,
            DataPoint.formula_version,
            DataPoint.status,
        )
        .where(DataPoint.indicator_code.in_(codes))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    )
    if end is not None:
        end_of_month = pd.Period(end, freq="M").end_time.date()
        query = query.where(DataPoint.date <= end_of_month)
    rows = [
        {
            "indicator_code": row.indicator_code,
            "date": row.date,
            "value": float(row.value),
            "available_at": row.available_at,
            "formula_version": row.formula_version,
            "status": row.status,
        }
        for row in db.execute(query).all()
    ]
    return _matrix_from_rows(rows, start=start, end=end, months=months)
