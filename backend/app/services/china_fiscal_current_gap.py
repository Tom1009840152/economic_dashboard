"""One-time, missing-only repair for the current China fiscal chain.

The repair has an explicit direct-leaf allowlist. A value is eligible only
when a fresh deep MOF crawl and qualified direct ``ReleaseEvidence`` agree to
six decimal places. Evidence is therefore a cross-check, never the source
copied into the current table. Derived values are recomputed from the
post-repair current fiscal leaves and current GDP with the canonical formulas.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DataPoint, Indicator, ReleaseEvidence
from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    calculate_fiscal_broad_expenditure,
    calculate_fiscal_metrics,
)
from app.services.indicator_service import upsert_points


GDP_CODE = "CN_GDP_NOMINAL_YTD"
DIRECT_FISCAL_CODES = (
    "CN_FISCAL_GENERAL_SPEND_YTD",
    "CN_FISCAL_FUND_EXPENDITURE_YTD",
)
DERIVED_FISCAL_CODES = (
    "CN_FISCAL_BROAD_EXPENDITURE_YTD",
    "CN_FISCAL_SPEND_INTENSITY",
    "CN_FISCAL_IMPULSE_PROXY",
)
FISCAL_CURRENT_CODES = (*DIRECT_FISCAL_CODES, *DERIVED_FISCAL_CODES)

# This is deliberately not a dynamic "all missing evidence" repair. These are
# the ten observations missed by the former shallow/too-narrow current loader.
TARGET_DIRECT_DATES = (
    *(dt.date(2022, month, 1) for month in range(4, 13)),
    dt.date(2024, 12, 1),
)
TARGET_BROAD_DATES = frozenset(TARGET_DIRECT_DATES)
TARGET_INTENSITY_DATES = frozenset(
    observed for observed in TARGET_DIRECT_DATES if observed.month in {3, 6, 9, 12}
)
TARGET_IMPULSE_DATES = frozenset(
    {
        *TARGET_INTENSITY_DATES,
        *(
            dt.date(observed.year + 1, observed.month, 1)
            for observed in TARGET_INTENSITY_DATES
        ),
    }
)
EXPECTED_MAX_INSERT_COUNTS = {
    DIRECT_FISCAL_CODES[0]: 10,
    DIRECT_FISCAL_CODES[1]: 10,
    DERIVED_FISCAL_CODES[0]: 10,
    DERIVED_FISCAL_CODES[1]: 4,
    DERIVED_FISCAL_CODES[2]: 8,
}

_LOCK_ORDER = (GDP_CODE, *FISCAL_CURRENT_CODES)
_VALUE_QUANTUM = Decimal("0.000001")
_TARGET_DIRECT_SET = frozenset(TARGET_DIRECT_DATES)


class FiscalCurrentGapError(ValueError):
    """The repair cannot prove that its complete plan is safe."""


class FiscalEvidenceEligibilityError(FiscalCurrentGapError):
    """A target direct key lacks a qualified, matching source/evidence pair."""


class FiscalEvidenceConflictError(FiscalCurrentGapError):
    """Qualified evidence disagrees with the fresh official source."""


class FiscalCurrentConflictError(FiscalCurrentGapError):
    """A target current leaf disagrees with the validated official value."""


@dataclass(frozen=True, slots=True)
class _PlannedPoint:
    code: str
    date: dt.date
    value: Decimal
    release_date: dt.date | None
    available_at: dt.datetime | None
    source_url: str | None
    status: str
    formula_version: str | None


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _decimal(value: object, *, context: str) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(_VALUE_QUANTUM)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FiscalCurrentGapError(f"invalid value for {context}: {value!r}") from exc
    if not result.is_finite():
        raise FiscalCurrentGapError(f"invalid value for {context}: {value!r}")
    return result


def _date(value: object, *, context: str) -> dt.date:
    if _is_missing(value):
        raise FiscalEvidenceEligibilityError(f"{context} must not be empty")
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise FiscalEvidenceEligibilityError(f"invalid {context}: {value!r}") from exc
    if pd.isna(result):
        raise FiscalEvidenceEligibilityError(f"{context} must not be empty")
    return result.date()


def _available_at(value: object, *, context: str) -> dt.datetime:
    if _is_missing(value) or not isinstance(value, dt.datetime):
        raise FiscalEvidenceEligibilityError(
            f"{context} must be a datetime with an explicit time"
        )
    result = pd.Timestamp(value).to_pydatetime()
    if result.tzinfo is not None:
        raise FiscalEvidenceEligibilityError(
            f"{context} must be a naive official source-local timestamp"
        )
    return result.replace(microsecond=0)


def _is_mof_https_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value))
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        hostname == "mof.gov.cn" or hostname.endswith(".mof.gov.cn")
    )


def _period_date(period: pd.Period) -> dt.date:
    return dt.date(period.year, period.month, 1)


def _lock_and_validate_indicators(db: Session, *, lock: bool) -> None:
    if lock:
        # Match the evidence writer's order so the two jobs cannot acquire the
        # same indicator locks in opposite orders.
        found: set[str] = set()
        for code in _LOCK_ORDER:
            value = db.scalar(
                select(Indicator.code)
                .where(Indicator.code == code)
                .with_for_update()
            )
            if value is not None:
                found.add(str(value))
    else:
        found = set(
            db.scalars(
                select(Indicator.code).where(Indicator.code.in_(_LOCK_ORDER))
            )
        )
    missing = [code for code in _LOCK_ORDER if code not in found]
    if missing:
        raise FiscalCurrentGapError(
            "required indicators are not seeded: " + ", ".join(missing)
        )


def _load_current_rows(db: Session, *, lock: bool) -> list[DataPoint]:
    statement = (
        select(DataPoint)
        .where(DataPoint.indicator_code.in_(_LOCK_ORDER))
        .order_by(DataPoint.indicator_code, DataPoint.date)
    )
    if lock:
        statement = statement.with_for_update()
    return list(db.scalars(statement))


def _counts(db: Session) -> dict[str, int]:
    result = {code: 0 for code in FISCAL_CURRENT_CODES}
    for code, count in db.execute(
        select(DataPoint.indicator_code, func.count())
        .where(DataPoint.indicator_code.in_(FISCAL_CURRENT_CODES))
        .group_by(DataPoint.indicator_code)
    ):
        result[str(code)] = int(count)
    return result


def _normalize_target_source(
    fiscal: Mapping[str, pd.DataFrame],
) -> dict[tuple[str, dt.date], _PlannedPoint]:
    normalized: dict[tuple[str, dt.date], _PlannedPoint] = {}
    required_columns = {
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
    }
    for code in DIRECT_FISCAL_CODES:
        frame = fiscal.get(code)
        if not isinstance(frame, pd.DataFrame):
            raise FiscalEvidenceEligibilityError(
                f"fresh MOF crawl did not return a data frame for {code}"
            )
        missing_columns = sorted(required_columns - set(frame.columns))
        if missing_columns:
            raise FiscalEvidenceEligibilityError(
                f"fresh MOF crawl for {code} is missing columns: {missing_columns}"
            )
        for raw in frame.to_dict("records"):
            observed = _date(raw.get("date"), context=f"{code} observation date")
            if observed not in _TARGET_DIRECT_SET:
                continue
            key = (code, observed)
            if key in normalized:
                raise FiscalEvidenceConflictError(
                    f"fresh MOF crawl contains duplicate target {code} {observed}"
                )
            available_at = _available_at(
                raw.get("available_at"), context=f"{code} {observed} available_at"
            )
            release_date = _date(
                raw.get("release_date"),
                context=f"{code} {observed} release_date",
            )
            source_url = str(raw.get("source_url") or "").strip()
            status = str(raw.get("status") or "").strip()
            formula = raw.get("formula_version")
            if not _is_mof_https_url(source_url):
                raise FiscalEvidenceEligibilityError(
                    f"fresh MOF target {code} {observed} has a non-official source"
                )
            if status != "published":
                raise FiscalEvidenceEligibilityError(
                    f"fresh MOF target {code} {observed} is not published direct data"
                )
            if not _is_missing(formula):
                raise FiscalEvidenceEligibilityError(
                    f"fresh MOF target {code} {observed} unexpectedly has a formula"
                )
            if release_date != available_at.date():
                raise FiscalEvidenceEligibilityError(
                    f"fresh MOF target {code} {observed} lacks exact release timing"
                )
            normalized[key] = _PlannedPoint(
                code=code,
                date=observed,
                value=_decimal(raw.get("value"), context=f"fresh MOF {code} {observed}"),
                release_date=release_date,
                available_at=available_at,
                source_url=source_url,
                status="published",
                formula_version=None,
            )

    expected_keys = {
        (code, observed)
        for code in DIRECT_FISCAL_CODES
        for observed in TARGET_DIRECT_DATES
    }
    missing = sorted(expected_keys - set(normalized))
    if missing:
        rendered = ", ".join(f"{code}:{observed:%Y-%m}" for code, observed in missing)
        raise FiscalEvidenceEligibilityError(
            "fresh MOF deep crawl is incomplete for the repair allowlist: " + rendered
        )
    return normalized


def _evidence_disqualifications(row: ReleaseEvidence) -> list[str]:
    reasons: list[str] = []
    if row.evidence_kind != "official_release":
        reasons.append("evidence_kind is not official_release")
    if row.chain_verified is not True:
        reasons.append("chain_verified is not true")
    if row.availability_precision != "exact_minute":
        reasons.append("availability_precision is not exact_minute")
    if row.status != "published":
        reasons.append("status is not published")
    if row.formula_version is not None:
        reasons.append("direct evidence has a formula version")
    if not _is_mof_https_url(row.source_url):
        reasons.append("source is not an official MOF HTTPS page")
    if row.date.day != 1:
        reasons.append("observation date is not normalized to month start")
    if row.available_at.tzinfo is not None:
        reasons.append("available_at is not a naive source-local timestamp")
    if row.release_date != row.available_at.date():
        reasons.append("release_date does not match exact available_at")
    try:
        _decimal(row.value, context=f"release evidence {row.indicator_code} {row.date}")
    except FiscalCurrentGapError as exc:
        reasons.append(str(exc))
    return reasons


def _validate_target_triangulation(
    source: dict[tuple[str, dt.date], _PlannedPoint],
    evidence_rows: list[ReleaseEvidence],
    current_rows: list[DataPoint],
) -> tuple[list[_PlannedPoint], dict[str, dict[str, object]]]:
    evidence_by_key: dict[tuple[str, dt.date], list[ReleaseEvidence]] = defaultdict(list)
    for row in evidence_rows:
        evidence_by_key[(row.indicator_code, row.date)].append(row)
    current_by_key = {(row.indicator_code, row.date): row for row in current_rows}
    planned: list[_PlannedPoint] = []
    summary: dict[str, dict[str, object]] = {}

    for code in DIRECT_FISCAL_CODES:
        missing_periods: list[str] = []
        present_periods: list[str] = []
        for observed in TARGET_DIRECT_DATES:
            key = (code, observed)
            expected = source[key]
            rows = evidence_by_key.get(key, [])
            if not rows:
                raise FiscalEvidenceEligibilityError(
                    f"no release evidence exists for target {code} {observed}"
                )
            rejected = [(row, _evidence_disqualifications(row)) for row in rows]
            rejected = [(row, reasons) for row, reasons in rejected if reasons]
            if rejected:
                details = "; ".join(
                    f"id={row.id}: {', '.join(reasons)}" for row, reasons in rejected
                )
                raise FiscalEvidenceEligibilityError(
                    f"ineligible evidence for target {code} {observed}: {details}"
                )
            evidence_values = {
                _decimal(row.value, context=f"release evidence {code} {observed}")
                for row in rows
            }
            if evidence_values != {expected.value}:
                raise FiscalEvidenceConflictError(
                    f"fresh MOF and release evidence conflict for {code} {observed}"
                )

            current = current_by_key.get(key)
            if current is None:
                planned.append(expected)
                missing_periods.append(observed.strftime("%Y-%m"))
            else:
                current_value = _decimal(
                    current.value, context=f"current {code} {observed}"
                )
                if current_value != expected.value:
                    raise FiscalCurrentConflictError(
                        f"fresh MOF/evidence and current conflict for {code} {observed}"
                    )
                if current.formula_version is not None:
                    raise FiscalCurrentConflictError(
                        f"direct current formula version is ineligible for target "
                        f"{code} {observed}: {current.formula_version!r}"
                    )
                present_periods.append(observed.strftime("%Y-%m"))

        summary[code] = {
            "expected": len(TARGET_DIRECT_DATES),
            "eligible": len(TARGET_DIRECT_DATES),
            "missing": len(missing_periods),
            "already_present": len(present_periods),
            "missing_periods": missing_periods,
            "already_present_periods": present_periods,
        }
    return planned, summary


def _raw_value_map(
    fiscal: Mapping[str, pd.DataFrame],
) -> dict[tuple[str, dt.date], set[Decimal]]:
    result: dict[tuple[str, dt.date], set[Decimal]] = defaultdict(set)
    for code in DIRECT_FISCAL_CODES:
        frame = fiscal.get(code)
        if not isinstance(frame, pd.DataFrame) or not {"date", "value"}.issubset(frame):
            continue
        for raw_date, raw_value in frame[["date", "value"]].itertuples(
            index=False, name=None
        ):
            try:
                observed = pd.Timestamp(raw_date).date()
                value = _decimal(raw_value, context=f"fresh MOF {code} {observed}")
            except (FiscalCurrentGapError, TypeError, ValueError):
                continue
            result[(code, observed)].add(value)
    return result


def _outside_scope_warnings(
    fiscal: Mapping[str, pd.DataFrame],
    evidence_rows: list[ReleaseEvidence],
    current_rows: list[DataPoint],
) -> list[str]:
    source = _raw_value_map(fiscal)
    evidence: dict[tuple[str, dt.date], set[Decimal]] = defaultdict(set)
    for row in evidence_rows:
        try:
            evidence[(row.indicator_code, row.date)].add(
                _decimal(row.value, context="outside-scope evidence")
            )
        except FiscalCurrentGapError:
            continue
    current = {
        (row.indicator_code, row.date): _decimal(
            row.value, context="outside-scope current"
        )
        for row in current_rows
        if row.indicator_code in DIRECT_FISCAL_CODES
    }
    warnings: list[str] = []
    for code, observed in sorted(set(source) | set(evidence) | set(current)):
        if observed in _TARGET_DIRECT_SET:
            continue
        source_values = source.get((code, observed), set())
        evidence_values = evidence.get((code, observed), set())
        current_value = current.get((code, observed))
        if len(source_values) > 1 or len(evidence_values) > 1:
            warnings.append(
                f"outside allowlist {code} {observed:%Y-%m} has conflicting proofs; ignored"
            )
            continue
        comparable = set(source_values) | set(evidence_values)
        if current_value is not None:
            comparable.add(current_value)
        if len(comparable) > 1:
            warnings.append(
                f"outside allowlist {code} {observed:%Y-%m} differs across "
                "MOF/evidence/current; ignored"
            )
    return warnings


def _series(
    current_rows: list[DataPoint],
    code: str,
    planned: list[_PlannedPoint] = (),
) -> pd.Series:
    values: dict[pd.Period, float] = {}
    for row in current_rows:
        if row.indicator_code != code:
            continue
        if row.date.day != 1:
            raise FiscalCurrentGapError(
                f"current {code} date is not normalized to month start: {row.date}"
            )
        period = pd.Period(row.date, freq="M")
        if period in values:
            raise FiscalCurrentGapError(f"current {code} has duplicate month {period}")
        values[period] = float(
            _decimal(row.value, context=f"current {code} {row.date}")
        )
    for item in planned:
        if item.code != code:
            continue
        period = pd.Period(item.date, freq="M")
        if period in values:
            raise FiscalCurrentGapError(
                f"repair attempted to replace existing {code} {period}"
            )
        values[period] = float(item.value)
    return pd.Series(values, dtype="float64").sort_index()


def _derived_point(code: str, period: pd.Period, value: object) -> _PlannedPoint:
    return _PlannedPoint(
        code=code,
        date=_period_date(period),
        value=_decimal(value, context=f"derived {code} {period}"),
        release_date=None,
        available_at=None,
        source_url=None,
        status="derived_backfill",
        formula_version=DERIVED_METRIC_SPECS[code].version,
    )


def _append_missing_derived(
    planned: list[_PlannedPoint],
    current_rows: list[DataPoint],
    code: str,
    values: pd.Series,
    allowed_dates: frozenset[dt.date],
) -> None:
    existing = {row.date: row for row in current_rows if row.indicator_code == code}
    for period, value in values.items():
        observed = _period_date(period)
        if observed not in allowed_dates:
            continue
        candidate = _derived_point(code, period, value)
        stored = existing.get(observed)
        if stored is None:
            planned.append(candidate)
            continue
        stored_value = _decimal(stored.value, context=f"current {code} {observed}")
        if stored_value != candidate.value:
            raise FiscalCurrentConflictError(
                f"canonical and current conflict for target {code} "
                f"{observed:%Y-%m}"
            )
        if stored.formula_version != candidate.formula_version:
            raise FiscalCurrentConflictError(
                f"current formula version is ineligible for target {code} "
                f"{observed:%Y-%m}: stored={stored.formula_version!r}, "
                f"required={candidate.formula_version!r}"
            )


def _target_period_summary(
    planned: list[_PlannedPoint],
) -> dict[str, dict[str, object]]:
    grouped = {code: [] for code in FISCAL_CURRENT_CODES}
    for item in planned:
        grouped[item.code].append(item.date)
    result: dict[str, dict[str, object]] = {}
    for code in FISCAL_CURRENT_CODES:
        dates = sorted(grouped[code])
        result[code] = {
            "count": len(dates),
            "first": dates[0].strftime("%Y-%m") if dates else None,
            "last": dates[-1].strftime("%Y-%m") if dates else None,
            "periods": [observed.strftime("%Y-%m") for observed in dates],
        }
    return result


def _build_plan(
    db: Session,
    fiscal: Mapping[str, pd.DataFrame],
    *,
    lock: bool,
) -> tuple[
    list[_PlannedPoint],
    dict[str, int],
    dict[str, dict[str, object]],
    list[str],
    int,
]:
    source = _normalize_target_source(fiscal)
    with db.no_autoflush:
        _lock_and_validate_indicators(db, lock=lock)
        current_rows = _load_current_rows(db, lock=lock)
        before_counts = _counts(db)
        evidence_rows = list(
            db.scalars(
                select(ReleaseEvidence)
                .where(ReleaseEvidence.indicator_code.in_(DIRECT_FISCAL_CODES))
                .order_by(
                    ReleaseEvidence.indicator_code,
                    ReleaseEvidence.date,
                    ReleaseEvidence.available_at,
                    ReleaseEvidence.version,
                )
            )
        )

    planned, direct_summary = _validate_target_triangulation(
        source, evidence_rows, current_rows
    )
    warnings = _outside_scope_warnings(fiscal, evidence_rows, current_rows)

    general = _series(current_rows, DIRECT_FISCAL_CODES[0], planned)
    fund = _series(current_rows, DIRECT_FISCAL_CODES[1], planned)
    broad = calculate_fiscal_broad_expenditure(general, fund)
    _append_missing_derived(
        planned,
        current_rows,
        DERIVED_FISCAL_CODES[0],
        broad,
        TARGET_BROAD_DATES,
    )

    # Current GDP is the only denominator. GDP release evidence and every
    # derived evidence row are deliberately outside this query and plan.
    current_gdp = _series(current_rows, GDP_CODE)
    intensity, impulse = calculate_fiscal_metrics(broad, current_gdp)
    _append_missing_derived(
        planned,
        current_rows,
        DERIVED_FISCAL_CODES[1],
        intensity,
        TARGET_INTENSITY_DATES,
    )
    _append_missing_derived(
        planned,
        current_rows,
        DERIVED_FISCAL_CODES[2],
        impulse,
        TARGET_IMPULSE_DATES,
    )

    planned.sort(key=lambda item: (item.code, item.date))
    return planned, before_counts, direct_summary, warnings, len(evidence_rows)


def _frame(items: list[_PlannedPoint]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": item.date,
                "value": float(item.value),
                "release_date": item.release_date,
                "available_at": item.available_at,
                "source_url": item.source_url,
                "status": item.status,
                "formula_version": item.formula_version,
            }
            for item in items
        ]
    )


def _store_plan(db: Session, planned: list[_PlannedPoint]) -> dict[str, int]:
    inserted = {code: 0 for code in FISCAL_CURRENT_CODES}
    for code in FISCAL_CURRENT_CODES:
        items = [item for item in planned if item.code == code]
        if not items:
            continue
        changed = upsert_points(
            db,
            code,
            _frame(items),
            commit=False,
            missing_only=True,
        )
        if changed != len(items):
            raise FiscalCurrentGapError(
                f"missing-only write count changed concurrently for {code}: "
                f"planned={len(items)}, stored={changed}"
            )
        inserted[code] = changed
    return inserted


def repair_fiscal_current_gap(
    db: Session,
    fiscal: Mapping[str, pd.DataFrame],
    *,
    apply: bool = False,
) -> dict[str, object]:
    """Plan or atomically apply the allowlisted current fiscal repair.

    ``fiscal`` must be the fresh result of the deep MOF loader. Dry-run is the
    default. Apply validates the full plan before any write, uses the normal
    quality/current-vintage path with deferred commits, and commits once.
    """

    try:
        (
            planned,
            before_counts,
            direct_summary,
            warnings,
            evidence_rows_scanned,
        ) = _build_plan(db, fiscal, lock=apply)
        planned_counts = {code: 0 for code in FISCAL_CURRENT_CODES}
        for item in planned:
            planned_counts[item.code] += 1
        oversized = {
            code: count
            for code, count in planned_counts.items()
            if count > EXPECTED_MAX_INSERT_COUNTS[code]
        }
        if oversized:
            raise FiscalCurrentGapError(
                f"repair plan exceeds its audited maximum scope: {oversized}"
            )
        projected_after = {
            code: before_counts[code] + planned_counts[code]
            for code in FISCAL_CURRENT_CODES
        }
        report: dict[str, object] = {
            "mode": "apply" if apply else "dry_run",
            "ready": True,
            "authorized_direct_periods": [
                observed.strftime("%Y-%m") for observed in TARGET_DIRECT_DATES
            ],
            "expected_max_insert_counts": EXPECTED_MAX_INSERT_COUNTS.copy(),
            "target_direct_summary": direct_summary,
            "evidence_rows_scanned": evidence_rows_scanned,
            "warnings": warnings,
            "before_counts": before_counts,
            "before_total": sum(before_counts.values()),
            "planned_insert_counts": planned_counts,
            "planned_insert_total": sum(planned_counts.values()),
            "target_periods": _target_period_summary(planned),
            "projected_after_counts": projected_after,
            "inserted_counts": {code: 0 for code in FISCAL_CURRENT_CODES},
            "inserted_total": 0,
        }
        if not apply:
            report["after_counts"] = before_counts.copy()
            report["after_total"] = sum(before_counts.values())
            return report

        inserted = _store_plan(db, planned)
        after_counts = _counts(db)
        if after_counts != projected_after or inserted != planned_counts:
            raise FiscalCurrentGapError(
                "post-write counts do not match the validated repair plan"
            )
        report["inserted_counts"] = inserted
        report["inserted_total"] = sum(inserted.values())
        report["after_counts"] = after_counts
        report["after_total"] = sum(after_counts.values())
        db.commit()
        return report
    except Exception:
        if apply:
            db.rollback()
        raise


__all__ = [
    "DERIVED_FISCAL_CODES",
    "DIRECT_FISCAL_CODES",
    "FISCAL_CURRENT_CODES",
    "EXPECTED_MAX_INSERT_COUNTS",
    "FiscalCurrentConflictError",
    "FiscalCurrentGapError",
    "FiscalEvidenceConflictError",
    "FiscalEvidenceEligibilityError",
    "GDP_CODE",
    "TARGET_DIRECT_DATES",
    "TARGET_IMPULSE_DATES",
    "TARGET_INTENSITY_DATES",
    "repair_fiscal_current_gap",
]
