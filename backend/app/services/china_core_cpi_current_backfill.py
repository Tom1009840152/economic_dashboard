"""Missing-only current backfill for official China core-CPI table rows.

The strict table collector reconstructs the publication-time evidence chain.
This module has a narrower job: fill absent ``DataPoint`` keys so the current
snapshot and A3's final-value reference cover the same months.  Existing
current rows are immutable here.  A value disagreement is an audit event, not
permission to replace a possibly revised value with its first release.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DataPoint, DataPointVintage, Indicator
from app.services.china_core_cpi_table_evidence import (
    CORE_CPI_REQUIRED_FROM,
    CORE_CPI_TABLE_CACHE_VERSION,
    CORE_CPI_TABLE_PARSER_VERSION,
    core_cpi_table_assertion_digest,
    is_core_cpi_yoy_column_label,
)
from app.services.indicator_service import upsert_points


CORE_CPI_CODE = "CN_CORE_CPI"
CORE_CPI_CURRENT_START = CORE_CPI_REQUIRED_FROM
VALUE_QUANTUM = Decimal("0.000001")
_REQUIRED_COLUMNS = frozenset(
    {
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
        "formula_version",
        "provenance_json",
        "evidence_kind",
        "chain_verified",
        "availability_precision",
    }
)


class CoreCpiCurrentBackfillError(ValueError):
    """The current backfill cannot prove a complete, non-destructive plan."""


class CoreCpiCurrentConflictError(CoreCpiCurrentBackfillError):
    """An existing current value conflicts with the official table candidate."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    date: dt.date
    value: Decimal
    release_date: dt.date
    available_at: dt.datetime
    source_url: str


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(VALUE_QUANTUM)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CoreCpiCurrentBackfillError(
            f"invalid core-CPI value: {value!r}"
        ) from exc
    if not result.is_finite():
        raise CoreCpiCurrentBackfillError(
            f"invalid core-CPI value: {value!r}"
        )
    return result


def _month_start(value: object) -> dt.date:
    if value is None or pd.isna(value):
        raise CoreCpiCurrentBackfillError(
            f"invalid core-CPI observation date: {value!r}"
        )
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise CoreCpiCurrentBackfillError(
            f"invalid core-CPI observation date: {value!r}"
        ) from exc
    result = timestamp.date()
    if result.day != 1:
        raise CoreCpiCurrentBackfillError(
            f"core-CPI observation is not a month start: {result}"
        )
    return result


def _is_nbs_https(value: object) -> bool:
    try:
        parsed = urlparse(str(value))
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        hostname == "stats.gov.cn" or hostname.endswith(".stats.gov.cn")
    )


def _provenance(value: object, observed: dt.date, parsed_value: Decimal) -> dict:
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CoreCpiCurrentBackfillError(
            f"invalid provenance for {observed:%Y-%m}"
        ) from exc
    if not isinstance(result, dict):
        raise CoreCpiCurrentBackfillError(
            f"missing provenance for {observed:%Y-%m}"
        )
    source_sha256 = result.get("source_sha256")
    table_assertion_sha256 = result.get("table_assertion_sha256")
    request_url = result.get("source_request_url")
    final_url = result.get("source_final_url")
    redirect_chain = result.get("redirect_chain")
    if (
        result.get("parser_version") != CORE_CPI_TABLE_PARSER_VERSION
        or result.get("cache_version") != CORE_CPI_TABLE_CACHE_VERSION
        or result.get("evidence_semantics")
        != "subject_month_official_cpi_table_row"
        or result.get("article_observation") != observed.isoformat()
        or result.get("publication_time_source")
        != "visible_nbs_detail_title"
        or result.get("source_kind") != "nbs_cpi_release_table"
        or result.get("row_label") != "其中：不包括食品和能源"
        or not is_core_cpi_yoy_column_label(result.get("column_label"))
        or not _is_nbs_https(request_url)
        or not _is_nbs_https(final_url)
        or not isinstance(redirect_chain, list)
        or not 1 <= len(redirect_chain) <= 6
        or redirect_chain[0] != request_url
        or redirect_chain[-1] != final_url
        or not all(_is_nbs_https(item) for item in redirect_chain)
        or not isinstance(source_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or not isinstance(table_assertion_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", table_assertion_sha256) is None
        or table_assertion_sha256
        != core_cpi_table_assertion_digest(
            observed,
            parsed_value,
            result.get("column_label"),
        )
    ):
        raise CoreCpiCurrentBackfillError(
            f"ineligible official-table provenance for {observed:%Y-%m}"
        )
    return result


def _current_period(today: dt.date | None) -> pd.Period:
    return pd.Period(today or dt.date.today(), freq="M")


def _normalize_candidates(
    frame: pd.DataFrame,
    *,
    today: dt.date | None = None,
) -> tuple[list[_Candidate], dict[str, object]]:
    if not isinstance(frame, pd.DataFrame):
        raise CoreCpiCurrentBackfillError(
            "core-CPI table evidence must be a data frame"
        )
    missing_columns = sorted(_REQUIRED_COLUMNS - set(frame.columns))
    if missing_columns:
        raise CoreCpiCurrentBackfillError(
            f"core-CPI table evidence is missing columns: {missing_columns}"
        )

    current = _current_period(today)
    required_through = current - 2
    latest_allowed = current - 1
    by_date: dict[dt.date, _Candidate] = {}
    ignored_before_start = 0
    for raw in frame.to_dict("records"):
        observed = _month_start(raw.get("date"))
        period = pd.Period(observed, freq="M")
        if period < CORE_CPI_CURRENT_START:
            ignored_before_start += 1
            continue
        if period > latest_allowed:
            raise CoreCpiCurrentBackfillError(
                f"core-CPI candidate is newer than the latest eligible month: {period}"
            )
        if observed in by_date:
            raise CoreCpiCurrentBackfillError(
                f"duplicate core-CPI candidate month: {period}"
            )

        available_at = raw.get("available_at")
        if not isinstance(available_at, dt.datetime) or available_at.tzinfo is not None:
            raise CoreCpiCurrentBackfillError(
                f"{period} lacks a naive exact publication minute"
            )
        if available_at.second != 0 or available_at.microsecond != 0:
            raise CoreCpiCurrentBackfillError(
                f"{period} publication time is not exact to the minute"
            )
        release_value = raw.get("release_date")
        if release_value is None or pd.isna(release_value):
            raise CoreCpiCurrentBackfillError(
                f"{period} lacks a valid release date"
            )
        try:
            release_date = pd.Timestamp(release_value).date()
        except (TypeError, ValueError) as exc:
            raise CoreCpiCurrentBackfillError(
                f"{period} lacks a valid release date"
            ) from exc
        source_url = str(raw.get("source_url") or "").strip()
        parsed_value = _decimal(raw.get("value"))
        provenance = _provenance(
            raw.get("provenance_json"), observed, parsed_value
        )
        if (
            release_date != available_at.date()
            or available_at.date() <= period.end_time.date()
            or raw.get("status") != "published"
            or pd.notna(raw.get("formula_version"))
            or raw.get("evidence_kind") != "official_release"
            or raw.get("chain_verified") is not True
            or raw.get("availability_precision") != "exact_minute"
            or not _is_nbs_https(source_url)
            or provenance.get("source_request_url") != source_url
        ):
            raise CoreCpiCurrentBackfillError(
                f"ineligible official-table candidate for {period}"
            )
        by_date[observed] = _Candidate(
            date=observed,
            value=parsed_value,
            release_date=release_date,
            available_at=available_at,
            source_url=source_url,
        )

    if not by_date:
        raise CoreCpiCurrentBackfillError(
            "core-CPI table evidence contains no in-scope months"
        )
    latest_collected = pd.Period(max(by_date), freq="M")
    if latest_collected < required_through:
        raise CoreCpiCurrentBackfillError(
            "core-CPI table evidence stops before the production cutoff: "
            f"latest={latest_collected}, required={required_through}"
        )
    expected = pd.period_range(
        CORE_CPI_CURRENT_START, latest_collected, freq="M"
    )
    actual = {pd.Period(value, freq="M") for value in by_date}
    missing_months = [str(period) for period in expected if period not in actual]
    if missing_months:
        raise CoreCpiCurrentBackfillError(
            "core-CPI table evidence is incomplete: " + ", ".join(missing_months)
        )

    candidates = [by_date[period.start_time.date()] for period in expected]
    report = {
        "candidate_count": len(candidates),
        "ignored_before_start_count": ignored_before_start,
        "coverage_start": str(CORE_CPI_CURRENT_START),
        "required_through": str(required_through),
        "collected_through": str(latest_collected),
        "required_month_count": len(
            pd.period_range(CORE_CPI_CURRENT_START, required_through, freq="M")
        ),
        "candidate_coverage_rate": 1.0,
    }
    return candidates, report


def inspect_core_cpi_current_candidates(
    frame: pd.DataFrame,
    *,
    today: dt.date | None = None,
) -> dict[str, object]:
    """Validate source completeness without opening or requiring a database."""

    _, source_report = _normalize_candidates(frame, today=today)
    return {
        "mode": "source_dry_run",
        "ready": True,
        "database_checked": False,
        **source_report,
        "existing_count": None,
        "consistent_overlap_count": None,
        "conflict_count": None,
        "conflicts": [],
        "planned_insert_count": None,
        # Current coverage cannot be projected honestly until the optional
        # database plan checks existing values and missing keys.
        "projected_current_count": None,
        "projected_coverage_rate": None,
    }


def _counts(db: Session) -> tuple[int, int]:
    points = int(
        db.scalar(
            select(func.count()).select_from(DataPoint).where(
                DataPoint.indicator_code == CORE_CPI_CODE
            )
        )
        or 0
    )
    vintages = int(
        db.scalar(
            select(func.count()).select_from(DataPointVintage).where(
                DataPointVintage.indicator_code == CORE_CPI_CODE
            )
        )
        or 0
    )
    return points, vintages


def _build_plan(
    db: Session,
    candidates: list[_Candidate],
    source_report: dict[str, object],
    *,
    lock: bool,
) -> tuple[list[_Candidate], dict[str, object]]:
    indicator_stmt = select(Indicator).where(Indicator.code == CORE_CPI_CODE)
    if lock:
        indicator_stmt = indicator_stmt.with_for_update()
    if db.scalar(indicator_stmt) is None:
        raise CoreCpiCurrentBackfillError("CN_CORE_CPI indicator is not seeded")

    dates = [item.date for item in candidates]
    point_stmt = select(DataPoint).where(
        DataPoint.indicator_code == CORE_CPI_CODE,
        DataPoint.date.in_(dates),
    )
    if lock:
        point_stmt = point_stmt.with_for_update()
    existing = {point.date: point for point in db.scalars(point_stmt)}
    consistent = 0
    conflicts: list[dict[str, object]] = []
    plan: list[_Candidate] = []
    for candidate in candidates:
        point = existing.get(candidate.date)
        if point is None:
            plan.append(candidate)
            continue
        stored = _decimal(point.value)
        if stored != candidate.value or point.formula_version is not None:
            conflicts.append(
                {
                    "period": candidate.date.strftime("%Y-%m"),
                    "current_value": str(stored),
                    "official_table_value": str(candidate.value),
                    "current_formula_version": point.formula_version,
                }
            )
        else:
            consistent += 1

    report = {
        **source_report,
        "database_checked": True,
        "existing_count": len(existing),
        "consistent_overlap_count": consistent,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "planned_insert_count": len(plan),
        "projected_current_count": consistent + len(plan),
        "projected_coverage_rate": round(
            (consistent + len(plan)) / len(candidates), 6
        ),
    }
    return plan, report


def _plan_frame(plan: list[_Candidate]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": item.date,
                "value": float(item.value),
                "release_date": item.release_date,
                "available_at": item.available_at,
                "source_url": item.source_url,
                "status": "historical_backfill",
                "formula_version": None,
            }
            for item in plan
        ],
        columns=(
            "date",
            "value",
            "release_date",
            "available_at",
            "source_url",
            "status",
            "formula_version",
        ),
    )


def backfill_core_cpi_current(
    db: Session,
    evidence: pd.DataFrame,
    *,
    apply: bool = False,
    today: dt.date | None = None,
) -> dict[str, object]:
    """Build or atomically apply a complete missing-only current plan."""

    candidates, source_report = _normalize_candidates(evidence, today=today)
    try:
        before_points, before_vintages = _counts(db)
        initial_plan, initial_report = _build_plan(
            db, candidates, source_report, lock=False
        )
        if not apply:
            return {
                "mode": "database_dry_run",
                "ready": initial_report["conflict_count"] == 0,
                **initial_report,
                "before": {
                    "current_rows": before_points,
                    "vintage_rows": before_vintages,
                },
                "projected_after": {
                    "current_rows": before_points + len(initial_plan),
                    "vintage_rows": before_vintages + len(initial_plan),
                },
            }

        if initial_report["conflict_count"]:
            raise CoreCpiCurrentConflictError(
                "existing CN_CORE_CPI current values conflict with the official "
                f"monthly table: {initial_report['conflicts']}"
            )

        locked_plan, locked_report = _build_plan(
            db, candidates, source_report, lock=True
        )
        if locked_report["conflict_count"]:
            raise CoreCpiCurrentConflictError(
                "CN_CORE_CPI current values conflicted while acquiring locks: "
                f"{locked_report['conflicts']}"
            )
        if [item.date for item in locked_plan] != [
            item.date for item in initial_plan
        ]:
            raise CoreCpiCurrentBackfillError(
                "CN_CORE_CPI current plan changed while acquiring locks"
            )

        changed = 0
        if locked_plan:
            changed = upsert_points(
                db,
                CORE_CPI_CODE,
                _plan_frame(locked_plan),
                commit=False,
                missing_only=True,
            )
        if changed != len(locked_plan):
            raise CoreCpiCurrentBackfillError(
                "concurrent CN_CORE_CPI write changed the missing-only plan: "
                f"planned={len(locked_plan)}, stored={changed}"
            )
        # Production SessionLocal uses autoflush=False.  ``upsert_points``
        # flushes new current rows to obtain their ids and then stages the
        # matching vintages, so make those pending vintage INSERTs visible to
        # the count-delta safety gate explicitly.
        db.flush()
        after_points, after_vintages = _counts(db)
        if (
            after_points != before_points + changed
            or after_vintages != before_vintages + changed
        ):
            raise CoreCpiCurrentBackfillError(
                "CN_CORE_CPI current/vintage count delta is inconsistent"
            )
        db.commit()
        return {
            "mode": "apply",
            "ready": True,
            **locked_report,
            "changed": changed,
            "before": {
                "current_rows": before_points,
                "vintage_rows": before_vintages,
            },
            "after": {
                "current_rows": after_points,
                "vintage_rows": after_vintages,
            },
        }
    except Exception:
        db.rollback()
        raise


__all__ = [
    "CORE_CPI_CODE",
    "CORE_CPI_CURRENT_START",
    "CoreCpiCurrentBackfillError",
    "CoreCpiCurrentConflictError",
    "backfill_core_cpi_current",
    "inspect_core_cpi_current_candidates",
]
