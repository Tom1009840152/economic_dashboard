"""Strict pseudo-real-time backtest for the China A1/A2 cycle model.

The service deliberately contains no second scoring or regime model.  For each
fixed historical decision timestamp it reconstructs the latest *safe* vintage
visible at that timestamp, then calls the existing A1 calculation core and A2
state machine.  The ex-post reference uses the same observation-month cutoff
and the same A1/A2 code, changing only the data vintage.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import median
from threading import RLock
from time import monotonic
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.indicator_defs import INDICATOR_DEFS
from app.models import DataPoint, DataPointVintage, ReleaseEvidence
from app.services.china_business_cycle import (
    BLOCK_MIN_COVERAGE,
    CHINA_CYCLE_BLOCKS,
    FORMULA_VERSIONED_CYCLE_INPUTS,
    MAX_OUTPUT_MONTHS,
    METHODOLOGY_VERSION as A1_METHODOLOGY_VERSION,
    ROLLING_WINDOW_MONTHS,
    VALIDATION_CODES,
    _matrix_from_rows,
    cycle_indicator_codes,
    cycle_indicator_observation_lags,
)
from app.services.china_cycle_regime import (
    FIXED_SURVEY_CORE_CODES,
    METHODOLOGY_VERSION as A2_METHODOLOGY_VERSION,
    PHASES,
    _build_regime_from_matrix,
    _phase_basis,
)


METHODOLOGY_VERSION = "1.4.0"
ATTRIBUTION_METHODOLOGY_VERSION = "1.0.0"
DEFAULT_BACKTEST_MONTHS = 120
MAX_BACKTEST_MONTHS = 120
MIN_RATE_SAMPLE = 24
MIN_TRANSITION_SAMPLE = 5
DECISION_DAY = 20
DECISION_HOUR = 18
DECISION_TIMEZONE = "Asia/Shanghai"
INFLATION_CODES = ("CN_CORE_CPI", "CN_PPI")
CYCLE_CODES = cycle_indicator_codes()
CYCLE_INPUT_OBSERVATION_LAGS = cycle_indicator_observation_lags()
MODEL_CODES = tuple(dict.fromkeys((*CYCLE_CODES, *VALIDATION_CODES, *INFLATION_CODES)))
REQUIRED_CODES = tuple(dict.fromkeys((*CYCLE_CODES, *INFLATION_CODES)))
_DEFINITIONS = {row["code"]: row for row in INDICATOR_DEFS}
_BACKTEST_CACHE_MAX_SIZE = 8
_BACKTEST_CACHE_TTL_SECONDS = 15 * 60
AUTHORITATIVE_EVIDENCE_KINDS = frozenset(
    {"official_release", "official_distribution_mirror"}
)
AUTHORITATIVE_AVAILABILITY_PRECISIONS = frozenset(
    {"exact_minute", "date_upper_bound"}
)


@dataclass(frozen=True)
class _InputWatermark:
    """Cheap, content-version watermark for one backtest input table.

    The supported ingestion path increments ``DataPoint.version`` and appends a
    new ``DataPointVintage`` row for every meaningful change.  Count/max-id
    catch vintage appends and deletes, while the version sum and retrieval
    timestamp catch current-value updates that do not change the row count.
    """

    row_count: int
    max_id: int
    version_sum: int
    max_retrieved_at: datetime | None


@dataclass(frozen=True)
class _BacktestCacheKey:
    start_period: str
    end_period: str
    a1_methodology_version: str
    a2_methodology_version: str
    a3_methodology_version: str
    model_codes: tuple[str, ...]
    current_values: _InputWatermark
    vintages: _InputWatermark
    release_evidence: _InputWatermark


@dataclass(frozen=True)
class _BacktestCacheEntry:
    result: dict
    expires_at: float


@dataclass(frozen=True)
class _AttributionCacheKey:
    period: str
    a1_methodology_version: str
    a2_methodology_version: str
    a3_methodology_version: str
    attribution_methodology_version: str
    model_codes: tuple[str, ...]
    current_values: _InputWatermark
    vintages: _InputWatermark
    release_evidence: _InputWatermark


# This LRU is intentionally process-local.  Normal versioned writes change the
# watermark key immediately; the finite TTL also bounds staleness when an
# exceptional maintenance operation edits rows in place without bumping their
# version/retrieval metadata.
_backtest_cache: OrderedDict[_BacktestCacheKey, _BacktestCacheEntry] = OrderedDict()
_backtest_inflight: dict[_BacktestCacheKey, Future[dict]] = {}
_attribution_cache: OrderedDict[
    _AttributionCacheKey, _BacktestCacheEntry
] = OrderedDict()
_attribution_inflight: dict[_AttributionCacheKey, Future[dict]] = {}
_backtest_cache_lock = RLock()


def _clear_backtest_cache() -> None:
    """Clear process-local cached results (primarily for tests and operations)."""

    with _backtest_cache_lock:
        _backtest_cache.clear()
        _attribution_cache.clear()


def _table_watermark(
    db: Session,
    model,
    *,
    observation_end: date,
    extra_filters: tuple = (),
) -> _InputWatermark:
    query = (
        select(
            func.count(model.id),
            func.max(model.id),
            func.coalesce(func.sum(model.version), 0),
            func.max(model.retrieved_at),
        )
        .where(model.indicator_code.in_(MODEL_CODES))
        .where(model.date <= observation_end)
    )
    for predicate in extra_filters:
        query = query.where(predicate)
    row = db.execute(query).one()
    return _InputWatermark(
        row_count=int(row[0] or 0),
        max_id=int(row[1] or 0),
        version_sum=int(row[2] or 0),
        max_retrieved_at=row[3],
    )


def _backtest_data_watermark(
    db: Session,
    *,
    observation_end: date,
) -> tuple[_InputWatermark, _InputWatermark, _InputWatermark]:
    """Return watermarks in a short session that cannot be held by a waiter."""

    with Session(bind=db.get_bind()) as watermark_db:
        return (
            _table_watermark(watermark_db, DataPoint, observation_end=observation_end),
            _table_watermark(
                watermark_db,
                DataPointVintage,
                observation_end=observation_end,
            ),
            _table_watermark(
                watermark_db,
                ReleaseEvidence,
                observation_end=observation_end,
                extra_filters=(
                    ReleaseEvidence.chain_verified.is_(True),
                    ReleaseEvidence.evidence_kind.in_(AUTHORITATIVE_EVIDENCE_KINDS),
                    ReleaseEvidence.availability_precision.in_(
                        AUTHORITATIVE_AVAILABILITY_PRECISIONS
                    ),
                ),
            ),
        )


def _cached_backtest_result(key: _BacktestCacheKey, builder) -> dict:
    """Return one immutable-by-copy result with per-key single-flight locking."""

    with _backtest_cache_lock:
        cached = _backtest_cache.get(key)
        if cached is not None and cached.expires_at > monotonic():
            _backtest_cache.move_to_end(key)
            return deepcopy(cached.result)
        if cached is not None:
            _backtest_cache.pop(key, None)
        future = _backtest_inflight.get(key)
        owns_build = future is None
        if owns_build:
            future = Future()
            _backtest_inflight[key] = future

    if not owns_build:
        return deepcopy(future.result())

    assert future is not None
    try:
        result = builder()
    except BaseException as exc:
        with _backtest_cache_lock:
            _backtest_inflight.pop(key, None)
        future.set_exception(exc)
        raise

    with _backtest_cache_lock:
        _backtest_cache[key] = _BacktestCacheEntry(
            result=result,
            expires_at=monotonic() + _BACKTEST_CACHE_TTL_SECONDS,
        )
        _backtest_cache.move_to_end(key)
        while len(_backtest_cache) > _BACKTEST_CACHE_MAX_SIZE:
            _backtest_cache.popitem(last=False)
        _backtest_inflight.pop(key, None)
    future.set_result(result)
    return deepcopy(result)


def _cached_attribution_result(key: _AttributionCacheKey, builder) -> dict:
    """Return one cached single-month audit with the same safety as A3."""

    with _backtest_cache_lock:
        cached = _attribution_cache.get(key)
        if cached is not None and cached.expires_at > monotonic():
            _attribution_cache.move_to_end(key)
            return deepcopy(cached.result)
        if cached is not None:
            _attribution_cache.pop(key, None)
        future = _attribution_inflight.get(key)
        owns_build = future is None
        if owns_build:
            future = Future()
            _attribution_inflight[key] = future

    if not owns_build:
        return deepcopy(future.result())

    assert future is not None
    try:
        result = builder()
    except BaseException as exc:
        with _backtest_cache_lock:
            _attribution_inflight.pop(key, None)
        future.set_exception(exc)
        raise

    with _backtest_cache_lock:
        _attribution_cache[key] = _BacktestCacheEntry(
            result=result,
            expires_at=monotonic() + _BACKTEST_CACHE_TTL_SECONDS,
        )
        _attribution_cache.move_to_end(key)
        while len(_attribution_cache) > _BACKTEST_CACHE_MAX_SIZE:
            _attribution_cache.popitem(last=False)
        _attribution_inflight.pop(key, None)
    future.set_result(result)
    return deepcopy(result)


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
    offset = local.utcoffset()
    if offset is None or offset.total_seconds() % 60:
        raise ValueError(
            "period's fixed Asia/Shanghai decision timestamp falls outside "
            "the supported minute-offset timezone history"
        )
    local_naive = local.replace(tzinfo=None)
    return local_naive, local.isoformat()


def _row_value(row, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _plain_row(row) -> dict:
    provenance = _row_value(row, "vintage_provenance")
    if provenance is None:
        if isinstance(row, ReleaseEvidence):
            provenance = "release_evidence"
        elif isinstance(row, DataPoint):
            provenance = "current_data_point"
        else:
            provenance = "data_point_vintage"
    row_id = _row_value(row, "id", None) or id(row)
    # Both history tables use independent integer primary-key sequences.  A
    # namespace keeps audit identifiers collision-free after their rows are
    # combined for replay.
    if provenance == "release_evidence" and not str(row_id).startswith(
        "release_evidence:"
    ):
        row_id = f"release_evidence:{row_id}"
    return {
        "id": row_id,
        "indicator_code": _row_value(row, "indicator_code"),
        "date": _row_value(row, "date"),
        "value": float(_row_value(row, "value")),
        "release_date": _row_value(row, "release_date"),
        "available_at": _row_value(row, "available_at"),
        "retrieved_at": _row_value(row, "retrieved_at"),
        "status": _row_value(row, "status", "published"),
        "formula_version": _row_value(row, "formula_version"),
        "version": int(_row_value(row, "version", 1)),
        "source_url": _row_value(row, "source_url"),
        "provenance_json": _row_value(row, "provenance_json"),
        "evidence_kind": _row_value(row, "evidence_kind"),
        "chain_verified": _row_value(row, "chain_verified"),
        "availability_precision": _row_value(row, "availability_precision"),
        "vintage_provenance": provenance,
    }


def _is_authoritative_release_evidence(row) -> bool:
    """Return whether a row is qualified to replace the generic vintage chain.

    Keep this guard in the pure combination function as well as the database
    query.  Tests, maintenance callers, and cached/preloaded rows can bypass
    the ORM query and must not gain precedence merely by calling themselves
    release evidence.
    """

    return bool(
        _row_value(row, "chain_verified") is True
        and _row_value(row, "evidence_kind") in AUTHORITATIVE_EVIDENCE_KINDS
        and _row_value(row, "availability_precision")
        in AUTHORITATIVE_AVAILABILITY_PRECISIONS
    )


def _authoritative_vintage_rows(
    vintage_rows: Iterable,
    evidence_rows: Iterable,
) -> list[dict]:
    """Combine replay inputs with qualified release evidence taking precedence.

    Only independently verified evidence with a supported source class and
    availability precision may shadow the generic chain.  Once at least one
    qualified row exists for an indicator/observation pair, every generic
    ``DataPointVintage`` row for that pair is excluded; mixing the two chains
    could otherwise leak the revised current value into the first vintage.
    """

    evidence = [
        _plain_row(row)
        for row in evidence_rows
        if _is_authoritative_release_evidence(row)
    ]
    evidence_keys = {(row["indicator_code"], row["date"]) for row in evidence}
    vintages = [
        _plain_row(row)
        for row in vintage_rows
        if (
            _row_value(row, "indicator_code"),
            _row_value(row, "date"),
        )
        not in evidence_keys
    ]
    combined = [*vintages, *evidence]
    combined.sort(
        key=lambda row: (
            row["indicator_code"],
            row["date"],
            row["available_at"] or datetime.max,
            row["version"],
            row["retrieved_at"] or datetime.min,
            str(row["id"]),
        )
    )
    return combined


def _safe_vintage_groups(
    vintage_rows: Iterable[dict],
) -> tuple[
    dict[tuple[str, date], list[dict]],
    set[object],
    set[object],
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

    ambiguous_ids: set[object] = set()
    unknown_revision_ids: set[object] = set()
    non_reconstructable: set[tuple[str, date]] = set()
    for key, rows in grouped.items():
        if all(row["vintage_provenance"] == "release_evidence" for row in rows):
            # Evidence versions are append order because older releases may be
            # discovered later.  Publication time, not version, is chronology.
            rows.sort(
                key=lambda item: (
                    item["available_at"],
                    item["version"],
                    item["retrieved_at"] or datetime.min,
                    str(item["id"]),
                )
            )
        else:
            rows.sort(
                key=lambda item: (
                    item["version"],
                    item["retrieved_at"] or datetime.min,
                    str(item["id"]),
                )
            )
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


def _uses_current_formula_version(row) -> bool:
    """Return whether a row belongs to the formula regime used by A1.

    Formula-versioned indicators retain their legacy observations for audit
    and display.  Those rows are not model inputs, so including them in A3's
    readiness denominator would understate coverage and could let an obsolete
    version contaminate the revision audit for the current regime.
    """

    code = _row_value(row, "indicator_code")
    required = FORMULA_VERSIONED_CYCLE_INPUTS.get(code)
    return required is None or _row_value(row, "formula_version") == required


def _input_readiness(vintage_rows: list[dict], final_rows: list[dict]) -> list[dict]:
    eligible_vintages = [
        row for row in vintage_rows if _uses_current_formula_version(row)
    ]
    (
        grouped,
        ambiguous_ids,
        unknown_revision_ids,
        non_reconstructable,
    ) = _safe_vintage_groups(eligible_vintages)
    final_by_code: dict[str, set[date]] = defaultdict(set)
    for raw in final_rows:
        row = _plain_row(raw)
        if (
            row["indicator_code"] in REQUIRED_CODES
            and _uses_current_formula_version(row)
        ):
            final_by_code[row["indicator_code"]].add(row["date"])

    result = []
    for code in REQUIRED_CODES:
        observation_lag_months = CYCLE_INPUT_OBSERVATION_LAGS.get(code, 0)
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
                model_period = (
                    pd.Period(observation_date, freq="M")
                    + observation_lag_months
                )
                decision_cutoff, _ = _decision_as_of(model_period)
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
                "observation_lag_months": observation_lag_months,
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


def _regime_for_rows(
    rows: list[dict],
    period: pd.Period,
    *,
    matrix_out: dict | None = None,
) -> tuple[dict, dict | None]:
    """Run the production A1 core and production A2 state machine unchanged."""

    matrix = _matrix_from_rows(
        rows,
        end=period.end_time.date(),
        months=MAX_OUTPUT_MONTHS,
    )
    if matrix_out is not None:
        matrix_out["matrix"] = matrix
    inflation_rows = [row for row in rows if row["indicator_code"] in INFLATION_CODES]
    regime = _build_regime_from_matrix(
        matrix,
        inflation_rows,
        output_months=MAX_OUTPUT_MONTHS,
        include_explanations=False,
    )
    month = next(
        (item for item in reversed(regime.get("months", [])) if item["period"] == str(period)),
        None,
    )
    return regime, month


def _fixed_survey_matrix(matrix: dict) -> dict:
    """Replace only the displayed coincident index with the strict survey core.

    The nested A1 signal rows are reused without mutation.  A month gets a
    diagnostic index only when all four standardised survey scores exist; the
    four fixed 25% weights are never renormalised around a missing signal.
    """

    fixed_months = []
    for month in matrix.get("months", []):
        signal_map = {
            signal["code"]: signal
            for block in month.get("blocks", [])
            for signal in block.get("signals", [])
        }
        scores = [
            signal_map.get(code, {}).get("standardized_score")
            for code in FIXED_SURVEY_CORE_CODES
        ]
        complete = all(score is not None for score in scores)
        coincident_index = (
            100.0 + 10.0 * sum(float(score) for score in scores) / len(scores)
            if complete
            else None
        )
        fixed_months.append(
            {
                **month,
                "coincident_index": (
                    round(coincident_index, 2) if coincident_index is not None else None
                ),
                "coincident_coverage": round(
                    sum(score is not None for score in scores) / len(scores), 4
                ),
            }
        )
    return {
        **matrix,
        "months": fixed_months,
        "method": "diagnostic_fixed_survey_core_v1",
    }


def _fixed_survey_regime_from_matrix(
    matrix: dict,
    rows: list[dict],
    period: pd.Period,
) -> tuple[dict, dict | None]:
    """Run A2 on the diagnostic core while preserving leading/confirmation rules."""

    fixed_matrix = _fixed_survey_matrix(matrix)
    inflation_rows = [row for row in rows if row["indicator_code"] in INFLATION_CODES]
    regime = _build_regime_from_matrix(
        fixed_matrix,
        inflation_rows,
        output_months=MAX_OUTPUT_MONTHS,
        coincident_panel_mode="fixed_survey_core_v1",
        include_explanations=False,
    )
    month = next(
        (item for item in reversed(regime.get("months", [])) if item["period"] == str(period)),
        None,
    )
    return regime, month


def _phase_snapshot(month: dict | None, regime: dict) -> dict | None:
    if month is None:
        return None
    phase_status = month.get("phase_status", "insufficient")
    phase_basis = month.get("phase_basis", _phase_basis(phase_status))
    return {
        "phase": month.get("phase"),
        "phase_label": month.get("phase_label", "待判定"),
        "phase_status": phase_status,
        "phase_basis": phase_basis,
        "confirmed_phase": month.get("confirmed_phase"),
        "confirmed_since": month.get("confirmed_since"),
        "last_decision_period": month.get(
            "last_decision_period",
            regime.get("last_decision_period"),
        ),
        "carry_forward_months": int(
            month.get(
                "carry_forward_months",
                month.get("undecidable_streak", 0)
                if phase_basis == "carried_forward"
                else 0,
            )
        ),
        "decision_eligible": bool(month.get("decision_eligible")),
        "raw_phase": month.get("raw_phase"),
        "candidate_phase": month.get("candidate_phase"),
        "candidate_since": month.get("candidate_since"),
        "candidate_streak": int(month.get("candidate_streak") or 0),
        "required_confirmation_months": month.get(
            "required_confirmation_months"
        ),
        "level_axis": month.get("level_axis", "unavailable"),
        "momentum_axis": month.get("momentum_axis", "unavailable"),
        "level_3m": month.get("level_3m"),
        "level_gap": month.get("_attribution_level_gap", month.get("level_gap")),
        "momentum_3m": month.get(
            "_attribution_momentum_3m", month.get("momentum_3m")
        ),
        "leading_level_3m": month.get("leading_level_3m"),
        "leading_gap": month.get(
            "_attribution_leading_gap", month.get("leading_gap")
        ),
        "leading_momentum_3m": month.get(
            "_attribution_leading_momentum_3m",
            month.get("leading_momentum_3m"),
        ),
        "leading_direction": month.get("leading_direction", "unavailable"),
        "coincident_index": month.get("coincident_index"),
        "leading_index": month.get("leading_index"),
        "coincident_basis_signature": month.get("coincident_basis_signature"),
        "leading_basis_signature": month.get("leading_basis_signature"),
        "coincident_basis_changed": bool(
            month.get("coincident_basis_changed", False)
        ),
        "leading_basis_changed": bool(month.get("leading_basis_changed", False)),
        "confidence": month.get("confidence", "insufficient"),
    }


def _attribution_support_key(row: dict) -> tuple[str, date, str | None]:
    """Identify one model observation without weakening formula semantics."""

    return (
        str(row["indicator_code"]),
        row["date"],
        row.get("formula_version"),
    )


def _hybrid_rows_on_realtime_support(
    strict_rows: list[dict],
    final_rows: list[dict],
    *,
    support_start: date | None = None,
) -> tuple[list[dict], dict]:
    """Return H = final values on exactly R's usable input support.

    The helper deliberately copies R metadata and replaces only ``value``.
    Formula-ineligible legacy rows are retained for a faithful A1 input replay,
    but they do not enter the support audit because production A1 excludes them.
    A missing or non-unique exact peer is fail-closed by the caller.
    """

    eligible_strict = [
        row
        for row in strict_rows
        if row["indicator_code"] in CYCLE_CODES
        and _uses_current_formula_version(row)
        and (support_start is None or row["date"] >= support_start)
    ]
    eligible_final = [
        row
        for row in final_rows
        if row["indicator_code"] in CYCLE_CODES
        and _uses_current_formula_version(row)
        and (support_start is None or row["date"] >= support_start)
    ]
    final_by_exact_key: dict[tuple[str, date, str | None], list[dict]] = defaultdict(list)
    final_by_observation: dict[tuple[str, date], list[dict]] = defaultdict(list)
    for row in final_rows:
        if row["indicator_code"] in CYCLE_CODES and (
            support_start is None or row["date"] >= support_start
        ):
            final_by_observation[(row["indicator_code"], row["date"])].append(row)
    for row in eligible_final:
        final_by_exact_key[_attribution_support_key(row)].append(row)

    missing_keys: list[str] = []
    ambiguous_keys: list[str] = []
    formula_mismatch_keys: list[str] = []
    substituted = 0
    revised = 0
    hybrid_rows: list[dict] = []
    eligible_strict_ids = {id(row) for row in eligible_strict}
    for row in strict_rows:
        clone = dict(row)
        if id(row) not in eligible_strict_ids:
            hybrid_rows.append(clone)
            continue
        key = _attribution_support_key(row)
        peers = final_by_exact_key.get(key, [])
        key_label = f"{key[0]}:{key[1].isoformat()}:{key[2] or '-'}"
        if len(peers) == 1:
            final_peer = peers[0]
            final_value = float(final_peer["value"])
            revised += abs(float(row["value"]) - final_value) > 1e-9
            clone["value"] = final_value
            substituted += 1
        elif len(peers) > 1:
            ambiguous_keys.append(key_label)
        else:
            observation_peers = final_by_observation.get(
                (row["indicator_code"], row["date"]), []
            )
            if observation_peers:
                formula_mismatch_keys.append(key_label)
            else:
                missing_keys.append(key_label)
        hybrid_rows.append(clone)

    strict_keys = {_attribution_support_key(row) for row in eligible_strict}
    final_keys = {_attribution_support_key(row) for row in eligible_final}
    audit = {
        "realtime_support_count": len(strict_keys),
        "hybrid_support_count": len(strict_keys),
        "final_support_count": len(final_keys),
        "matched_final_value_count": substituted,
        "revised_input_count": revised,
        "expanded_input_count": len(final_keys - strict_keys),
        "missing_counterpart_count": len(missing_keys),
        "ambiguous_counterpart_count": len(ambiguous_keys),
        "formula_mismatch_count": len(formula_mismatch_keys),
        "input_support_preserved": True,
        "missing_counterpart_keys": missing_keys[:10],
        "ambiguous_counterpart_keys": ambiguous_keys[:10],
        "formula_mismatch_keys": formula_mismatch_keys[:10],
        "support_window_start": support_start.isoformat() if support_start else None,
    }
    return hybrid_rows, audit


def _attribution_metric(
    realtime: dict | None,
    hybrid: dict | None,
    final: dict | None,
    key: str,
) -> dict:
    values = [
        snapshot.get(key) if snapshot is not None else None
        for snapshot in (realtime, hybrid, final)
    ]
    if any(value is None for value in values):
        return {
            "status": "unavailable",
            "realtime": values[0],
            "hybrid": values[1],
            "final": values[2],
            "revision_path_delta": None,
            "support_expansion_path_delta": None,
            "total_delta": None,
            "residual": None,
            "additivity_passed": False,
        }
    realtime_value, hybrid_value, final_value = (float(value) for value in values)
    revision_delta = realtime_value - hybrid_value
    support_delta = hybrid_value - final_value
    total_delta = realtime_value - final_value
    residual = total_delta - revision_delta - support_delta
    passed = abs(residual) <= 1e-8
    return {
        "status": "available" if passed else "additivity_failed",
        "realtime": round(realtime_value, 6),
        "hybrid": round(hybrid_value, 6),
        "final": round(final_value, 6),
        "revision_path_delta": round(revision_delta, 6),
        "support_expansion_path_delta": round(support_delta, 6),
        "total_delta": round(total_delta, 6),
        "residual": round(residual, 10),
        "additivity_passed": passed,
    }


def _attribution_step(left: dict | None, right: dict | None, key: str) -> str:
    left_value = left.get(key) if left is not None else None
    right_value = right.get(key) if right is not None else None
    if left_value is None or right_value is None:
        return "not_comparable"
    return "unchanged" if left_value == right_value else "changed"


def _build_counterfactual_attribution(
    strict_rows: list[dict],
    final_rows: list[dict],
    period: pd.Period,
    *,
    decision_as_of: str,
    final_cutoff_at: datetime,
) -> dict:
    """Build one on-demand R→H→F model-output audit for an exact endpoint."""

    base = {
        "observation_period": str(period),
        "decision_as_of": decision_as_of,
        "attribution_methodology_version": ATTRIBUTION_METHODOLOGY_VERSION,
        "a1_methodology_version": A1_METHODOLOGY_VERSION,
        "a2_methodology_version": A2_METHODOLOGY_VERSION,
        "a3_methodology_version": METHODOLOGY_VERSION,
        "path_order": [
            "realtime_visible_information",
            "final_values_on_realtime_support",
            "full_final_information",
        ],
        "final_cutoff_at": final_cutoff_at.astimezone(UTC).isoformat(),
        "realtime": None,
        "hybrid": None,
        "final": None,
        "support_audit": None,
        "metrics": {},
        "phase_steps": {},
        "warnings": [
            "这是同端点伪实时重跑的模型内部反事实审计，不是历史上真实发布过的决策账本。",
            "路径拆解依赖R→H→F的替换顺序，包含重新标准化、篮子和状态路径交互，不是经济因果或原因占比。",
        ],
    }
    if not strict_rows or not _has_minimum_coincident_inputs(strict_rows):
        return {
            **base,
            "status": "not_comparable",
            "reasons": ["realtime_support_insufficient"],
        }
    if not final_rows:
        return {
            **base,
            "status": "not_comparable",
            "reasons": ["final_support_unavailable"],
        }

    # Audit exact H support before running three expensive A1/A2 paths.  A
    # missing peer cannot yield a strict attribution regardless of their
    # endpoint labels, so fail closed quickly.
    # A1 needs at most 60 prior months for each of A2's 240 replay months;
    # the extra five months cover the hidden comparison month, trailing means,
    # and the pre-registered one-month consumer-expectations alignment.
    support_start = (
        period - (MAX_OUTPUT_MONTHS + ROLLING_WINDOW_MONTHS + 5)
    ).start_time.date()
    hybrid_rows, support_audit = _hybrid_rows_on_realtime_support(
        strict_rows,
        final_rows,
        support_start=support_start,
    )
    base["support_audit"] = support_audit
    if (
        support_audit["matched_final_value_count"]
        != support_audit["realtime_support_count"]
        or support_audit["missing_counterpart_count"]
        or support_audit["ambiguous_counterpart_count"]
        or support_audit["formula_mismatch_count"]
    ):
        return {
            **base,
            "status": "hybrid_unavailable",
            "reasons": ["realtime_support_not_exactly_matchable"],
        }

    realtime_regime, realtime_month = _regime_for_rows(strict_rows, period)
    final_regime, final_month = _regime_for_rows(final_rows, period)
    realtime = _phase_snapshot(realtime_month, realtime_regime)
    final = _phase_snapshot(final_month, final_regime)
    base.update({"realtime": realtime, "final": final})
    if (
        realtime is None
        or final is None
        or realtime.get("phase") is None
        or final.get("phase") is None
    ):
        return {
            **base,
            "status": "not_comparable",
            "reasons": ["endpoint_phase_not_comparable"],
        }

    hybrid_regime, hybrid_month = _regime_for_rows(hybrid_rows, period)
    hybrid = _phase_snapshot(hybrid_month, hybrid_regime)
    base["hybrid"] = hybrid
    if hybrid is None:
        return {
            **base,
            "status": "hybrid_unavailable",
            "reasons": ["hybrid_endpoint_unavailable"],
        }

    support_audit.update(
        {
            "realtime_coincident_basis_signature": realtime.get(
                "coincident_basis_signature"
            ),
            "hybrid_coincident_basis_signature": hybrid.get(
                "coincident_basis_signature"
            ),
            "final_coincident_basis_signature": final.get(
                "coincident_basis_signature"
            ),
            "realtime_leading_basis_signature": realtime.get(
                "leading_basis_signature"
            ),
            "hybrid_leading_basis_signature": hybrid.get(
                "leading_basis_signature"
            ),
            "final_leading_basis_signature": final.get(
                "leading_basis_signature"
            ),
            "basis_changed_on_revision_path": bool(
                realtime.get("coincident_basis_signature")
                != hybrid.get("coincident_basis_signature")
                or realtime.get("leading_basis_signature")
                != hybrid.get("leading_basis_signature")
            ),
            "basis_changed_on_support_path": bool(
                hybrid.get("coincident_basis_signature")
                != final.get("coincident_basis_signature")
                or hybrid.get("leading_basis_signature")
                != final.get("leading_basis_signature")
            ),
            "decision_eligibility_changed_on_revision_path": (
                realtime.get("decision_eligible") != hybrid.get("decision_eligible")
            ),
            "decision_eligibility_changed_on_support_path": (
                hybrid.get("decision_eligible") != final.get("decision_eligible")
            ),
        }
    )
    metrics = {
        key: _attribution_metric(realtime, hybrid, final, key)
        for key in (
            "level_gap",
            "momentum_3m",
            "leading_gap",
            "leading_momentum_3m",
        )
    }
    phase_steps = {
        label: {
            "revision_step": _attribution_step(realtime, hybrid, key),
            "support_step": _attribution_step(hybrid, final, key),
        }
        for label, key in (
            ("display", "phase"),
            ("confirmed", "confirmed_phase"),
            ("raw", "raw_phase"),
        )
    }
    endpoint_label_changed = any(
        item["revision_step"] == "changed" or item["support_step"] == "changed"
        for item in phase_steps.values()
    )
    state_keys = (
        "phase_status",
        "phase_basis",
        "confirmed_since",
        "candidate_phase",
        "candidate_since",
        "candidate_streak",
        "required_confirmation_months",
        "last_decision_period",
    )
    support_audit["state_path_changed"] = bool(
        endpoint_label_changed
        or any(realtime.get(key) != hybrid.get(key) for key in state_keys)
        or any(hybrid.get(key) != final.get(key) for key in state_keys)
    )
    additivity_failed = any(
        item["status"] == "additivity_failed" for item in metrics.values()
    )
    return {
        **base,
        "status": "additivity_failed" if additivity_failed else "available",
        "reasons": ["coordinate_additivity_failed"] if additivity_failed else [],
        "hybrid": hybrid,
        "support_audit": support_audit,
        "metrics": metrics,
        "phase_steps": phase_steps,
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
    final_reference_status: str,
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
    if final_reference_status == "hidden_no_realtime_label":
        reasons.append("final_reference_hidden_noncomparable")
    elif final_month is None:
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

    def phase_basis(row: dict, side: str) -> str:
        snapshot = row.get(side) or {}
        explicit = snapshot.get("phase_basis")
        if explicit:
            return explicit
        status = snapshot.get("phase_status")
        if status in {"confirmed", "transition"}:
            return "active_decision"
        if status in {"held_uncomparable", "stale"}:
            return "carried_forward"
        if status == "candidate":
            return "pending_confirmation"
        if snapshot.get("decision_eligible") and snapshot.get("confirmed_phase"):
            return "active_decision"
        return "unclassified"

    non_decision_comparable = [
        row for row in comparable if not row["decision_comparable"]
    ]
    pending_confirmation: list[dict] = []
    carried_forward: list[dict] = []
    mixed_basis: list[dict] = []
    for row in non_decision_comparable:
        realtime_basis = phase_basis(row, "realtime")
        final_basis = phase_basis(row, "final")
        if "pending_confirmation" in {realtime_basis, final_basis}:
            pending_confirmation.append(row)
        elif realtime_basis == final_basis == "carried_forward":
            carried_forward.append(row)
        elif realtime_basis != final_basis:
            mixed_basis.append(row)
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
    carried_forward_agreement_count = sum(
        bool(row["phase_agreement"]) for row in carried_forward
    )
    carried_forward_flip_count = (
        len(carried_forward) - carried_forward_agreement_count
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
        "carried_forward_comparable_months": len(carried_forward),
        "carried_forward_agreement_count": (
            carried_forward_agreement_count if carried_forward else None
        ),
        "carried_forward_agreement_rate": safe_rate(
            carried_forward_agreement_count,
            len(carried_forward),
        ),
        "carried_forward_flip_count": (
            carried_forward_flip_count if carried_forward else None
        ),
        "carried_forward_flip_rate": safe_rate(
            carried_forward_flip_count,
            len(carried_forward),
        ),
        "mixed_basis_comparable_months": len(mixed_basis),
        "pending_confirmation_comparable_months": len(pending_confirmation),
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


def _phase_distribution(rows: list[dict]) -> dict:
    """Describe phase coverage on exactly the months used by each comparison."""

    display_rows = [row for row in rows if row["comparable"]]
    decision_rows = [row for row in rows if row["decision_comparable"]]

    def distribution(sample: list[dict], field: str) -> dict:
        realtime = Counter(row["realtime"].get(field) for row in sample)
        final = Counter(row["final"].get(field) for row in sample)
        realtime.pop(None, None)
        final.pop(None, None)
        return {
            "sample_months": len(sample),
            "realtime": {phase: int(realtime.get(phase, 0)) for phase in PHASES},
            "final": {phase: int(final.get(phase, 0)) for phase in PHASES},
        }

    return {
        "display": distribution(display_rows, "phase"),
        "decision": distribution(decision_rows, "confirmed_phase"),
    }


def _sensitivity_gates(
    stability: dict,
    transitions: dict,
    distribution: dict,
) -> dict:
    """Apply the pre-declared sample gates without turning them into a score."""

    decision_count = int(stability["decision_comparable_months"])
    decision_distribution = distribution["decision"]
    realtime_phases = [
        phase for phase in PHASES if decision_distribution["realtime"].get(phase, 0)
    ]
    final_phases = [
        phase for phase in PHASES if decision_distribution["final"].get(phase, 0)
    ]
    decision_gate = decision_count >= MIN_RATE_SAMPLE
    phase_gate = set(realtime_phases) == set(PHASES) and set(final_phases) == set(PHASES)
    transition_gate = transitions["matched_count"] >= MIN_TRANSITION_SAMPLE
    all_passed = decision_gate and phase_gate and transition_gate
    failed = []
    if not decision_gate:
        failed.append("formal_decision_sample")
    if not phase_gate:
        failed.append("four_phase_coverage")
    if not transition_gate:
        failed.append("matched_transitions")
    return {
        "formal_decision_sample": {
            "observed": decision_count,
            "minimum": MIN_RATE_SAMPLE,
            "passed": decision_gate,
        },
        "four_phase_coverage": {
            "required": list(PHASES),
            "realtime_observed": realtime_phases,
            "final_observed": final_phases,
            "passed": phase_gate,
        },
        "matched_transitions": {
            "observed": int(transitions["matched_count"]),
            "minimum": MIN_TRANSITION_SAMPLE,
            "passed": transition_gate,
        },
        "all_passed": all_passed,
        "failed_gates": failed,
        "conclusion": (
            "三个预设门槛均通过；这只说明该切片具备进一步评估条件，不等同于可预测。"
            if all_passed
            else "至少一个预设门槛未通过；不得把该敏感性结果用于转换概率训练。"
        ),
    }


def _paired_slice(
    baseline_rows: list[dict],
    diagnostic_rows: list[dict],
    *,
    decision: bool,
) -> dict:
    """Compare baseline and diagnostic accuracy on their shared calendar only."""

    diagnostic_by_period = {
        row["observation_period"]: row for row in diagnostic_rows
    }
    eligibility_key = "decision_comparable" if decision else "comparable"
    phase_key = "confirmed_phase" if decision else "phase"
    common: list[tuple[dict, dict]] = []
    for baseline in baseline_rows:
        diagnostic = diagnostic_by_period.get(baseline["observation_period"])
        if (
            diagnostic is not None
            and baseline[eligibility_key]
            and diagnostic[eligibility_key]
        ):
            common.append((baseline, diagnostic))

    baseline_agreement = sum(
        baseline["realtime"][phase_key] == baseline["final"][phase_key]
        for baseline, _ in common
    )
    diagnostic_agreement = sum(
        diagnostic["realtime"][phase_key] == diagnostic["final"][phase_key]
        for _, diagnostic in common
    )
    enough = len(common) >= MIN_RATE_SAMPLE
    baseline_rate = baseline_agreement / len(common) if enough else None
    diagnostic_rate = diagnostic_agreement / len(common) if enough else None
    changed = []
    agreement_outcome_changed = []
    for baseline, diagnostic in common:
        baseline_realtime = baseline["realtime"][phase_key]
        baseline_final = baseline["final"][phase_key]
        diagnostic_realtime = diagnostic["realtime"][phase_key]
        diagnostic_final = diagnostic["final"][phase_key]
        baseline_matches = baseline_realtime == baseline_final
        diagnostic_matches = diagnostic_realtime == diagnostic_final
        if (
            baseline_realtime != diagnostic_realtime
            or baseline_final != diagnostic_final
        ):
            changed.append(
                {
                    "observation_period": baseline["observation_period"],
                    "baseline_realtime_phase": baseline_realtime,
                    "diagnostic_realtime_phase": diagnostic_realtime,
                    "baseline_final_phase": baseline_final,
                    "diagnostic_final_phase": diagnostic_final,
                    "baseline_agreement": baseline_matches,
                    "diagnostic_agreement": diagnostic_matches,
                }
            )
        if baseline_matches != diagnostic_matches:
            agreement_outcome_changed.append(baseline["observation_period"])
    return {
        "label_basis": "confirmed_phase" if decision else "display_phase",
        "common_months": len(common),
        "minimum_rate_sample": MIN_RATE_SAMPLE,
        "baseline_agreement_count": baseline_agreement if common else None,
        "baseline_agreement_rate": (
            round(baseline_rate, 4) if baseline_rate is not None else None
        ),
        "diagnostic_agreement_count": diagnostic_agreement if common else None,
        "diagnostic_agreement_rate": (
            round(diagnostic_rate, 4) if diagnostic_rate is not None else None
        ),
        "agreement_rate_delta_percentage_points": (
            round((diagnostic_rate - baseline_rate) * 100, 2)
            if diagnostic_rate is not None and baseline_rate is not None
            else None
        ),
        "changed_judgement_months": changed,
        "agreement_outcome_changed_months": agreement_outcome_changed,
        "sample_note": (
            None
            if enough
            else f"共同可比月份少于{MIN_RATE_SAMPLE}，只报告计数，不报告率差。"
        ),
    }


def _percentage_point_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round((left - right) * 100, 2)


def _comparison_row(
    period: pd.Period,
    realtime: dict | None,
    final: dict | None,
) -> dict:
    """Return the common comparison fields consumed by stability summaries."""

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
    return {
        "observation_period": str(period),
        "realtime": realtime,
        "final": final,
        "comparable": comparable,
        "phase_agreement": phase_agreement,
        "decision_comparable": decision_comparable,
        "decision_phase_agreement": decision_phase_agreement,
    }


def _build_robustness(
    baseline_rows: list[dict],
    fixed_survey_rows: list[dict],
) -> dict:
    """Build the fixed-basket and evaluation-window sensitivity diagnostics."""

    full_stability = _stability(baseline_rows)
    non_covid_rows = [
        row
        for row in baseline_rows
        if not row["observation_period"].startswith("2020-")
    ]

    fixed_stability = _stability(fixed_survey_rows)
    fixed_transitions = _transition_summary(fixed_survey_rows)
    fixed_distribution = _phase_distribution(fixed_survey_rows)

    non_january_rows = [
        row for row in baseline_rows if not row["observation_period"].endswith("-01")
    ]
    january_stability = _stability(non_january_rows)
    january_transitions = _transition_summary(non_january_rows)
    january_distribution = _phase_distribution(non_january_rows)
    excluded_january = [
        row["observation_period"]
        for row in baseline_rows
        if row["observation_period"].endswith("-01")
    ]

    return {
        "full_sample": full_stability,
        "exclude_covid_2020": _stability(non_covid_rows),
        "fixed_survey_core": {
            "name": "fixed_survey_core_v1",
            "diagnostic_only": True,
            "signal_weights": {
                code: round(1 / len(FIXED_SURVEY_CORE_CODES), 4)
                for code in FIXED_SURVEY_CORE_CODES
            },
            "missing_policy": (
                "all_four_required_in_each_six_month_panel; no_dynamic_renormalization"
            ),
            "production_gate_policy": (
                "does_not_satisfy_or_replace_the_production_A1_65pct_role_gate"
            ),
            "leading_and_confirmation_policy": "unchanged_from_production_A2",
            "stability": fixed_stability,
            "phase_distribution": fixed_distribution,
            "transitions": fixed_transitions,
            "gates": _sensitivity_gates(
                fixed_stability,
                fixed_transitions,
                fixed_distribution,
            ),
            "paired_comparison": {
                "display": _paired_slice(
                    baseline_rows,
                    fixed_survey_rows,
                    decision=False,
                ),
                "decision": _paired_slice(
                    baseline_rows,
                    fixed_survey_rows,
                    decision=True,
                ),
            },
        },
        "exclude_january_observation": {
            "evaluation_window_only": True,
            "filter": "observation_period_month_is_not_january",
            "note": (
                "只从评价分母移除观察月为1月的结果；不补造1月数据、不重算路径，"
                "也不宣称消除了1月缺口对随后六个月共同篮子的结构影响。"
            ),
            "excluded_months": excluded_january,
            "stability": january_stability,
            "phase_distribution": january_distribution,
            "transitions": january_transitions,
            "gates": _sensitivity_gates(
                january_stability,
                january_transitions,
                january_distribution,
            ),
            "agreement_rate_delta_percentage_points": _percentage_point_delta(
                january_stability["agreement_rate"],
                full_stability["agreement_rate"],
            ),
            "decision_agreement_rate_delta_percentage_points": _percentage_point_delta(
                january_stability["decision_agreement_rate"],
                full_stability["decision_agreement_rate"],
            ),
        },
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
    fixed_survey_months = []
    reason_counts: Counter[str] = Counter()

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

        realtime_matrix_capture: dict = {}
        if strict_rows and _has_minimum_coincident_inputs(strict_rows):
            realtime_regime, realtime_month = _regime_for_rows(
                strict_rows,
                period,
                matrix_out=realtime_matrix_capture,
            )
        else:
            realtime_regime, realtime_month = {}, None
        realtime = _phase_snapshot(realtime_month, realtime_regime)

        fixed_realtime_regime: dict = {}
        fixed_realtime_month: dict | None = None
        if realtime_matrix_capture.get("matrix"):
            fixed_realtime_regime, fixed_realtime_month = (
                _fixed_survey_regime_from_matrix(
                    realtime_matrix_capture["matrix"],
                    strict_rows,
                    period,
                )
            )
        fixed_realtime = _phase_snapshot(
            fixed_realtime_month,
            fixed_realtime_regime,
        )

        final_regime: dict = {}
        final_month: dict | None = None
        fixed_final_regime: dict = {}
        fixed_final_month: dict | None = None
        baseline_needs_exact_peer = bool(realtime and realtime["phase"])
        fixed_needs_exact_peer = bool(fixed_realtime and fixed_realtime["phase"])
        exact_peer_required = baseline_needs_exact_peer or fixed_needs_exact_peer
        if exact_peer_required and target_final_rows:
            # Exact peer evaluation: same observation endpoint, model window,
            # and state-machine warmup; only the vintage basis differs.
            target_final_matrix_capture: dict = {}
            final_regime, final_month = _regime_for_rows(
                target_final_rows,
                period,
                matrix_out=target_final_matrix_capture,
            )
            if target_final_matrix_capture.get("matrix"):
                fixed_final_regime, fixed_final_month = (
                    _fixed_survey_regime_from_matrix(
                        target_final_matrix_capture["matrix"],
                        target_final_rows,
                        period,
                    )
                )
        final_reference_status = (
            "hidden_no_realtime_label"
            if not exact_peer_required
            else "same_endpoint_rerun"
            if final_month is not None
            else "same_endpoint_unavailable"
        )
        final = _phase_snapshot(final_month, final_regime)
        fixed_final = _phase_snapshot(fixed_final_month, fixed_final_regime)
        fixed_survey_months.append(
            _comparison_row(period, fixed_realtime, fixed_final)
        )
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
            final_reference_status=final_reference_status,
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
                "final_reference_status": final_reference_status,
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
            "仅有证据链已核验且证据类型、时间精度受支持的发布证据优先于通用历史版本，并把观察期锁在当月；"
            "当时判断与事后参考都复用同一套活动矩阵和阶段判断方法。"
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
                "only chain-verified release evidence with a supported evidence kind and availability precision is authoritative per indicator/date and excludes generic vintages for that key; "
                "latest safe release by available_at is selected; a value/formula revision without a strictly later timestamp quarantines the whole observation"
            ),
            "final_reference": "latest stored values are rerun with the exact same observation endpoint and identical model code whenever either baseline or fixed diagnostic has a realtime label; otherwise the ex-post label is hidden; never falls back to the full-endpoint state path; not ground truth",
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
        "robustness": _build_robustness(months, fixed_survey_months),
        "input_readiness": _input_readiness(plain_vintages, plain_finals),
        "latest": months[-1] if months else None,
        "months": months,
        "warnings": [
            "事后最终值只是同模型的稳定性参照，不是GDP真值、官方衰退认定或预测准确率。",
            "逐月事后参考只展示同观察端点精确重跑结果；当时与固定诊断都未形成标签时明确隐藏，不借用完整终点路径。",
            "发布时间未知的观测严格排除；不会用系统首次抓取时间或经验发布日期回填。",
            "改值或公式变更若没有严格递增的公开时间，该观察期全部版本均不可还原；不会继续沿用可能已失效的旧值。",
            "中国来源的存量发布时间是无时区的本地钟面时间，本页按北京时间解释；跨来源的小时级时区尚未完成独立认证。",
            f"可比月份少于{MIN_RATE_SAMPLE}时不展示一致率和翻转率；匹配转换少于{MIN_TRANSITION_SAMPLE}次时不展示确认滞后统计。",
            "当前固定使用现行A1/A2方法版本回放数据vintage，不模拟历史模型参数或旧公式。",
            "固定同步调查篮子只用于稳健性诊断，不替代A1生产篮子，也不绕过65%同步角色覆盖门槛。",
            "排除1月只改变评价窗口，不补造1月数据，也不消除缺口对随后共同篮子的影响。",
        ],
    }


def build_china_cycle_backtest_attribution(
    db: Session,
    *,
    period: str,
    now: datetime | None = None,
) -> dict:
    """Explain one exact-endpoint R→H→F difference without slowing A3."""

    try:
        target_period = pd.Period(period, freq="M")
    except (TypeError, ValueError) as exc:
        raise ValueError("period must use YYYY-MM") from exc
    if str(target_period) != period:
        raise ValueError("period must use YYYY-MM")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    completed_end = _latest_completed_period(now)
    if target_period > completed_end:
        raise ValueError(
            f"period must not be later than {completed_end}; its fixed decision timestamp has not occurred"
        )

    observation_end = target_period.end_time.date()
    cutoff_utc, cutoff_display = _decision_as_of(target_period)
    (
        current_watermark,
        vintage_watermark,
        evidence_watermark,
    ) = _backtest_data_watermark(db, observation_end=observation_end)
    cache_key = _AttributionCacheKey(
        period=str(target_period),
        a1_methodology_version=A1_METHODOLOGY_VERSION,
        a2_methodology_version=A2_METHODOLOGY_VERSION,
        a3_methodology_version=METHODOLOGY_VERSION,
        attribution_methodology_version=ATTRIBUTION_METHODOLOGY_VERSION,
        model_codes=MODEL_CODES,
        current_values=current_watermark,
        vintages=vintage_watermark,
        release_evidence=evidence_watermark,
    )

    def build() -> dict:
        vintage_query = (
            select(DataPointVintage)
            .where(DataPointVintage.indicator_code.in_(MODEL_CODES))
            .where(DataPointVintage.date <= observation_end)
            .order_by(
                DataPointVintage.indicator_code,
                DataPointVintage.date,
                DataPointVintage.version,
            )
        )
        final_query = (
            select(DataPoint)
            .where(DataPoint.indicator_code.in_(MODEL_CODES))
            .where(DataPoint.date <= observation_end)
            .order_by(DataPoint.indicator_code, DataPoint.date)
        )
        evidence_query = (
            select(ReleaseEvidence)
            .where(ReleaseEvidence.indicator_code.in_(MODEL_CODES))
            .where(ReleaseEvidence.date <= observation_end)
            .where(ReleaseEvidence.chain_verified.is_(True))
            .where(
                ReleaseEvidence.evidence_kind.in_(AUTHORITATIVE_EVIDENCE_KINDS)
            )
            .where(
                ReleaseEvidence.availability_precision.in_(
                    AUTHORITATIVE_AVAILABILITY_PRECISIONS
                )
            )
            .order_by(
                ReleaseEvidence.indicator_code,
                ReleaseEvidence.date,
                ReleaseEvidence.available_at,
                ReleaseEvidence.version,
                ReleaseEvidence.retrieved_at,
                ReleaseEvidence.id,
            )
        )
        vintages = _authoritative_vintage_rows(
            list(db.scalars(vintage_query)),
            list(db.scalars(evidence_query)),
        )
        strict_rows, _ = _select_strict_vintages(
            vintages,
            as_of=cutoff_utc,
            observation_end=observation_end,
            audit_codes=set(REQUIRED_CODES),
        )
        strict_rows = [
            row for row in strict_rows if row["indicator_code"] in MODEL_CODES
        ]
        final_rows = [
            _plain_row(row)
            for row in db.scalars(final_query)
            if row.indicator_code in MODEL_CODES
        ]
        return _build_counterfactual_attribution(
            strict_rows,
            final_rows,
            target_period,
            decision_as_of=cutoff_display,
            final_cutoff_at=now,
        )

    result = _cached_attribution_result(cache_key, build)
    result["final_cutoff_at"] = now.astimezone(UTC).isoformat()
    return result


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
    observation_end = requested_end.end_time.date()
    (
        current_watermark,
        vintage_watermark,
        evidence_watermark,
    ) = _backtest_data_watermark(db, observation_end=observation_end)
    cache_key = _BacktestCacheKey(
        start_period=str(requested_start),
        end_period=str(requested_end),
        a1_methodology_version=A1_METHODOLOGY_VERSION,
        a2_methodology_version=A2_METHODOLOGY_VERSION,
        a3_methodology_version=METHODOLOGY_VERSION,
        model_codes=MODEL_CODES,
        current_values=current_watermark,
        vintages=vintage_watermark,
        release_evidence=evidence_watermark,
    )

    def build() -> dict:
        vintage_query = (
            select(DataPointVintage)
            .where(DataPointVintage.indicator_code.in_(MODEL_CODES))
            .where(DataPointVintage.date <= observation_end)
            .order_by(
                DataPointVintage.indicator_code,
                DataPointVintage.date,
                DataPointVintage.version,
            )
        )
        final_query = (
            select(DataPoint)
            .where(DataPoint.indicator_code.in_(MODEL_CODES))
            .where(DataPoint.date <= observation_end)
            .order_by(DataPoint.indicator_code, DataPoint.date)
        )
        evidence_query = (
            select(ReleaseEvidence)
            .where(ReleaseEvidence.indicator_code.in_(MODEL_CODES))
            .where(ReleaseEvidence.date <= observation_end)
            .where(ReleaseEvidence.chain_verified.is_(True))
            .where(
                ReleaseEvidence.evidence_kind.in_(AUTHORITATIVE_EVIDENCE_KINDS)
            )
            .where(
                ReleaseEvidence.availability_precision.in_(
                    AUTHORITATIVE_AVAILABILITY_PRECISIONS
                )
            )
            .order_by(
                ReleaseEvidence.indicator_code,
                ReleaseEvidence.date,
                ReleaseEvidence.available_at,
                ReleaseEvidence.version,
                ReleaseEvidence.retrieved_at,
                ReleaseEvidence.id,
            )
        )
        vintage_rows = list(db.scalars(vintage_query))
        final_rows = list(db.scalars(final_query))
        evidence_rows = list(db.scalars(evidence_query))
        return _build_backtest_from_rows(
            _authoritative_vintage_rows(vintage_rows, evidence_rows),
            final_rows,
            periods=periods,
            final_cutoff_at=now,
        )

    result = _cached_backtest_result(cache_key, build)
    # ``final_cutoff_at`` describes this response's request time, not the cache
    # generation time.  The copied result can be safely refreshed on every hit.
    result["backtest_definition"]["final_cutoff_at"] = now.astimezone(UTC).isoformat()
    return result
