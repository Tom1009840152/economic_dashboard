"""Validation gates for indicator refreshes.

The checks in this module are deliberately conservative: deterministic parser
or unit failures block a write, while a statistically unusual but still
plausible economic observation is retained and surfaced as a warning.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from statistics import median

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indicator_catalog import get_indicator_catalog
from app.models import DataPoint
from app.services.derived_metrics import DERIVED_METRIC_SPECS


_VALID_STATUSES = frozenset(
    {
        "published",
        "derived",
        "revision_metadata_unknown",
        "historical_backfill",
        "derived_backfill",
        "mirror_backfill",
        "backfilled",
    }
)


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"

    @property
    def blocking(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True)
class QualityReport:
    row_count: int
    issues: tuple[QualityIssue, ...] = ()

    @property
    def blocking_issues(self) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    def issues_json(self) -> str | None:
        if not self.issues:
            return None
        return json.dumps([asdict(issue) for issue in self.issues], ensure_ascii=False)


class DataQualityError(ValueError):
    def __init__(self, report: QualityReport):
        self.report = report
        message = "; ".join(issue.message for issue in report.blocking_issues)
        super().__init__(message or "indicator data failed quality validation")


def _issue(code: str, message: str, severity: str = "error") -> QualityIssue:
    return QualityIssue(code=code, message=message, severity=severity)


def _is_pmi(code: str) -> bool:
    return code in {"CN_PMI", "CN_NMI"} or code.startswith(("CN_PMI_", "CN_NMI_"))


def _robust_jump_warning(db: Session, code: str, incoming: pd.DataFrame) -> QualityIssue | None:
    latest_existing = db.execute(
        select(DataPoint.date, DataPoint.value)
        .where(DataPoint.indicator_code == code)
        .order_by(DataPoint.date.desc())
        .limit(61)
    ).all()
    if len(latest_existing) < 12:
        return None

    history = sorted(
        ((row.date, float(row.value)) for row in latest_existing), key=lambda item: item[0]
    )
    latest_date, latest_value = history[-1]
    future_values = incoming.loc[incoming["_date"] > latest_date, ["_date", "_value"]]
    if future_values.empty:
        return None

    differences = [right[1] - left[1] for left, right in zip(history, history[1:])]
    centre = median(differences)
    mad = median(abs(value - centre) for value in differences)
    # A zero-MAD series is common for policy rates.  Keep a small scale floor so
    # a real policy change is warned about, not rejected.
    scale = max(1.4826 * mad, max(abs(value) for value in differences) * 0.05, 1e-6)
    first_new = future_values.sort_values("_date").iloc[0]
    jump = float(first_new["_value"]) - latest_value
    robust_z = abs(jump - centre) / scale
    if robust_z <= 20:
        return None
    return _issue(
        "abnormal_jump",
        (
            f"latest step for {code} is statistically unusual "
            f"(change={jump:.6g}, robust_z={robust_z:.1f}); retained for review"
        ),
        severity="warning",
    )


def _scale_break_issue(db: Session, code: str, incoming: pd.DataFrame) -> QualityIssue | None:
    sample = incoming.sort_values("_date").tail(24)
    dates = sample["_date"].tolist()
    existing = {
        row.date: float(row.value)
        for row in db.execute(
            select(DataPoint.date, DataPoint.value).where(
                DataPoint.indicator_code == code, DataPoint.date.in_(dates)
            )
        )
    }
    for observed, raw_value in sample[["_date", "_value"]].itertuples(index=False, name=None):
        old = existing.get(observed)
        new = float(raw_value)
        if old is None or abs(old) < 1e-9 or abs(new) < 1e-9:
            continue
        ratio = abs(new / old)
        absolute_change = abs(new - old)
        scale_threshold = max(1.0, min(abs(old), abs(new)) * 10)
        if (ratio >= 50 or ratio <= 0.02) and absolute_change >= scale_threshold:
            return _issue(
                "scale_break",
                (
                    f"{code} changed by a likely unit factor on {observed}: "
                    f"stored={old:.6g}, incoming={new:.6g}"
                ),
            )
    return None


def _is_formula_regime_upgrade(
    db: Session,
    code: str,
    incoming: pd.DataFrame,
) -> bool:
    """Allow one explicit history reset when a registered formula version changes.

    A shorter series is expected when a definition change establishes a new
    comparability start. This exception is deliberately narrow: every incoming
    row must declare the registry's current version, legacy rows must exist, and
    no current-version row may already be stored. A later same-version collapse
    remains blocking.
    """

    spec = DERIVED_METRIC_SPECS.get(code)
    if spec is None or "formula_version" not in incoming.columns:
        return False
    versions = {
        str(value).strip()
        for value in incoming["formula_version"].dropna()
        if str(value).strip()
    }
    if versions != {spec.version}:
        return False
    stored_versions = set(
        db.scalars(
            select(DataPoint.formula_version)
            .where(DataPoint.indicator_code == code)
            .distinct()
        )
    )
    return bool(stored_versions) and spec.version not in stored_versions


def validate_indicator_frame(
    db: Session,
    code: str,
    frame: pd.DataFrame | None,
    *,
    previous_row_count: int | None = None,
    today: date | None = None,
) -> QualityReport:
    """Return all quality findings; callers must reject blocking findings."""

    if frame is None or not isinstance(frame, pd.DataFrame):
        return QualityReport(0, (_issue("empty_data", f"{code} returned no data frame"),))

    row_count = len(frame)
    if row_count == 0:
        return QualityReport(0, (_issue("empty_data", f"{code} returned an empty data set"),))

    missing_columns = {"date", "value"} - set(frame.columns)
    if missing_columns:
        return QualityReport(
            row_count,
            (
                _issue(
                    "missing_columns",
                    f"{code} is missing required columns: {sorted(missing_columns)}",
                ),
            ),
        )

    checked = frame.copy()
    parsed_dates = pd.to_datetime(checked["date"], errors="coerce")
    parsed_values = pd.to_numeric(checked["value"], errors="coerce")
    issues: list[QualityIssue] = []

    invalid_dates = int(parsed_dates.isna().sum())
    if invalid_dates:
        issues.append(
            _issue("invalid_date", f"{code} contains {invalid_dates} invalid or missing dates")
        )
    invalid_values = int(parsed_values.isna().sum())
    finite_values = parsed_values.map(
        lambda value: math.isfinite(float(value)) if pd.notna(value) else False
    )
    non_finite = int((parsed_values.notna() & ~finite_values).sum())
    if invalid_values or non_finite:
        issues.append(
            _issue(
                "non_numeric_value",
                f"{code} contains {invalid_values + non_finite} missing or non-finite values",
            )
        )

    valid_mask = parsed_dates.notna() & parsed_values.notna() & finite_values
    checked = checked.loc[valid_mask].copy()
    checked["_date"] = parsed_dates.loc[valid_mask].dt.date
    checked["_value"] = parsed_values.loc[valid_mask].astype(float)

    duplicate_count = int(checked["_date"].duplicated(keep=False).sum())
    if duplicate_count:
        issues.append(
            _issue(
                "duplicate_date",
                f"{code} contains {duplicate_count} rows whose observation dates are duplicated",
            )
        )

    cutoff = today or date.today()
    future_count = int((checked["_date"] > cutoff).sum())
    if future_count:
        issues.append(
            _issue(
                "future_date",
                f"{code} contains {future_count} observations later than {cutoff.isoformat()}",
            )
        )

    if "release_date" in checked.columns:
        raw_release_dates = checked["release_date"]
        release_dates = pd.to_datetime(raw_release_dates, errors="coerce")
        invalid_release_dates = int(
            (raw_release_dates.notna() & release_dates.isna()).sum()
        )
        if invalid_release_dates:
            issues.append(
                _issue(
                    "invalid_release_date",
                    f"{code} contains {invalid_release_dates} invalid release dates",
                )
            )
        future_releases = int((release_dates.dropna().dt.date > cutoff).sum())
        if future_releases:
            issues.append(
                _issue(
                    "future_release_date",
                    f"{code} contains {future_releases} release dates later than {cutoff.isoformat()}",
                )
            )

    if "available_at" in checked.columns:
        raw_available = checked["available_at"]
        available = pd.to_datetime(raw_available, errors="coerce", utc=True)
        invalid_available = int((raw_available.notna() & available.isna()).sum())
        if invalid_available:
            issues.append(
                _issue(
                    "invalid_available_at",
                    f"{code} contains {invalid_available} invalid availability timestamps",
                )
            )
        future_available = int((available.dropna().dt.date > cutoff).sum())
        if future_available:
            issues.append(
                _issue(
                    "future_available_at",
                    f"{code} contains {future_available} availability timestamps later than {cutoff.isoformat()}",
                )
            )

    if "status" in checked.columns:
        statuses = {
            str(value).strip()
            for value in checked["status"].dropna()
            if str(value).strip()
        }
        unknown_statuses = statuses - _VALID_STATUSES
        if unknown_statuses:
            issues.append(
                _issue(
                    "invalid_status",
                    f"{code} contains unsupported statuses: {sorted(unknown_statuses)}",
                )
            )

    if previous_row_count is not None and previous_row_count >= 10:
        collapse_limit = max(3, math.floor(previous_row_count * 0.5))
        if row_count < collapse_limit:
            if _is_formula_regime_upgrade(db, code, checked):
                issues.append(
                    _issue(
                        "formula_regime_history_reset",
                        (
                            f"{code} row count fell from {previous_row_count} to {row_count} "
                            "during an explicit current-formula regime upgrade; retained for audit"
                        ),
                        severity="warning",
                    )
                )
            else:
                issues.append(
                    _issue(
                        "row_count_collapse",
                        (
                            f"{code} row count fell from {previous_row_count} to {row_count} "
                            f"(blocking threshold {collapse_limit})"
                        ),
                    )
                )

    catalog_entry = get_indicator_catalog().get(code)
    if catalog_entry is not None and not checked.empty:
        outside_mask = pd.Series(False, index=checked.index)
        if catalog_entry.valid_min is not None:
            outside_mask |= checked["_value"] < catalog_entry.valid_min
        if catalog_entry.valid_max is not None:
            outside_mask |= checked["_value"] > catalog_entry.valid_max
        outside = int(outside_mask.sum())
        if outside:
            issues.append(
                _issue(
                    "hard_range",
                    (
                        f"{code} has {outside} values outside its hard range "
                        f"[{catalog_entry.valid_min}, {catalog_entry.valid_max}]"
                    ),
                )
            )
    elif _is_pmi(code) and not checked.empty:
        outside = int(((checked["_value"] < 0) | (checked["_value"] > 100)).sum())
        if outside:
            issues.append(
                _issue("hard_range", f"{code} has {outside} values outside [0, 100]")
            )

    if not checked.empty:
        scale_issue = _scale_break_issue(db, code, checked)
        if scale_issue:
            issues.append(scale_issue)
        jump_issue = _robust_jump_warning(db, code, checked)
        if jump_issue:
            issues.append(jump_issue)

    return QualityReport(row_count=row_count, issues=tuple(issues))


def consumer_component_issues(
    frames: dict[str, pd.DataFrame | None],
) -> dict[str, tuple[QualityIssue, ...]]:
    """Detect the known source-mapping failure that duplicates confidence components."""

    codes = (
        "CN_CONSUMER_CONFIDENCE",
        "CN_CONSUMER_SATISFACTION",
        "CN_CONSUMER_EXPECTATIONS",
    )
    normalized: dict[str, pd.Series] = {}
    for code in codes:
        frame = frames.get(code)
        if frame is None or frame.empty or not {"date", "value"}.issubset(frame.columns):
            continue
        dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
        values = pd.to_numeric(frame["value"], errors="coerce")
        valid = dates.notna() & values.notna()
        normalized[code] = pd.Series(
            values.loc[valid].to_numpy(dtype=float), index=dates.loc[valid]
        ).groupby(level=0).last()

    findings: dict[str, list[QualityIssue]] = {}
    for left_index, left_code in enumerate(codes):
        for right_code in codes[left_index + 1 :]:
            left = normalized.get(left_code)
            right = normalized.get(right_code)
            if left is None or right is None:
                continue
            common = sorted(set(left.index) & set(right.index))[-12:]
            if len(common) < 6:
                continue
            if all(math.isclose(float(left.at[item]), float(right.at[item]), abs_tol=1e-9) for item in common):
                issue = _issue(
                    "duplicate_consumer_series",
                    (
                        f"{left_code} and {right_code} are identical for the latest "
                        f"{len(common)} common observations"
                    ),
                )
                findings.setdefault(left_code, []).append(issue)
                findings.setdefault(right_code, []).append(issue)
    return {code: tuple(issues) for code, issues in findings.items()}


def enforce_quality(report: QualityReport) -> None:
    if report.blocking_issues:
        raise DataQualityError(report)
