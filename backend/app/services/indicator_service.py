import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fetchers.akshare_source import FETCHERS
from app.indicator_defs import INDICATOR_DEFS
from app.models import DataPoint, DataPointVintage, Indicator, RefreshResult, RefreshRun
from app.services.data_quality import (
    DataQualityError,
    QualityReport,
    consumer_component_issues,
    enforce_quality,
    validate_indicator_frame,
)

logger = logging.getLogger(__name__)
_STATUS_PRIORITY = {
    "revision_metadata_unknown": 0,
    "backfilled": 0,
    "mirror_backfill": 0,
    "historical_backfill": 1,
    "derived_backfill": 1,
    "published": 2,
    "derived": 2,
}


def ensure_indicators_seeded(db: Session) -> None:
    existing = {row.code: row for row in db.execute(select(Indicator)).scalars()}
    for meta in INDICATOR_DEFS:
        row = existing.get(meta["code"])
        if row is None:
            db.add(Indicator(**meta))
        else:
            for key, value in meta.items():
                setattr(row, key, value)
    db.commit()


def _optional_date(value) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.to_datetime(value).date()


def _optional_datetime(value) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    result = pd.to_datetime(value).to_pydatetime()
    if result.tzinfo is not None:
        result = result.astimezone(UTC).replace(tzinfo=None)
    return result


def _optional_text(value, limit: int) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text[:limit] or None


def _decimal_value(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid indicator value: {value!r}") from exc


def _incoming_metadata(row, columns: set[str]) -> dict:
    metadata = {}
    if "release_date" in columns:
        metadata["release_date"] = _optional_date(row.release_date)
    if "available_at" in columns:
        metadata["available_at"] = _optional_datetime(row.available_at)
    if "source_url" in columns:
        metadata["source_url"] = _optional_text(row.source_url, 512)
    if "status" in columns:
        metadata["status"] = _optional_text(row.status, 32) or "published"
    if "formula_version" in columns:
        metadata["formula_version"] = _optional_text(row.formula_version, 32)
    return metadata


def _content_changed(point: DataPoint, value: Decimal, metadata: dict) -> bool:
    if Decimal(point.value).quantize(Decimal("0.000001")) != value:
        return True
    return any(getattr(point, key) != incoming for key, incoming in metadata.items())


def _avoid_metadata_downgrade(point: DataPoint, value: Decimal, metadata: dict) -> dict:
    """Keep richer release metadata when a final-value backfill is identical.

    If the numeric value changed, the lower-quality final-value record is still
    stored as a new vintage; the earlier published vintage remains queryable.
    """
    current_value = Decimal(point.value).quantize(Decimal("0.000001"))
    incoming_status = metadata.get("status")
    if current_value != value:
        # Publication metadata belongs to a specific numeric vintage.  A sparse
        # revision must never inherit the previous vintage's timestamp: doing so
        # would make a value learned later appear visible in strict as-of views.
        # Formula/source provenance belongs to that vintage as well.  Keep an
        # explicitly supplied field, otherwise clear the stale value and label
        # the revision honestly instead of silently calling it published.
        return {
            **metadata,
            "release_date": metadata.get("release_date"),
            "available_at": metadata.get("available_at"),
            "source_url": metadata.get("source_url"),
            "formula_version": metadata.get("formula_version"),
            "status": metadata.get("status", "revision_metadata_unknown"),
        }
    if incoming_status is not None and _STATUS_PRIORITY.get(
        incoming_status, 0
    ) < _STATUS_PRIORITY.get(point.status, 0):
        return {}
    # Empty cells in a metadata-bearing frame mean "not supplied", not
    # "erase what a richer release already told us".  This rule is limited to
    # unchanged numeric values: a changed historical backfill must not inherit
    # an older value's publication timestamp and leak into strict as-of views.
    return {
        key: incoming
        for key, incoming in metadata.items()
        if key == "status" or incoming is not None or getattr(point, key) is None
    }


def _append_vintage(db: Session, point: DataPoint) -> None:
    db.add(
        DataPointVintage(
            data_point_id=point.id,
            indicator_code=point.indicator_code,
            date=point.date,
            value=point.value,
            release_date=point.release_date,
            available_at=point.available_at,
            retrieved_at=point.retrieved_at,
            source_url=point.source_url,
            status=point.status,
            formula_version=point.formula_version,
            version=point.version,
        )
    )


def _store_points(
    db: Session,
    code: str,
    df: pd.DataFrame,
    *,
    commit: bool = True,
) -> int:
    """Update current observations and append only meaningful vintage changes.

    Fetchers may return the original ``date, value`` pair or additionally provide
    ``release_date``, ``available_at``, ``source_url`` and ``status``. Missing
    metadata only preserves metadata captured by an earlier, richer source when
    the numeric value is unchanged.  A sparse numeric revision clears the old
    release timestamp so it cannot leak into strict vintage queries.
    """
    if df is None or df.empty:
        return 0

    required = {"date", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{code} fetcher missing columns: {sorted(missing)}")

    clean = df.dropna(subset=["date", "value"]).drop_duplicates("date", keep="last")
    dates = [_optional_date(value) for value in clean["date"]]
    existing = {
        point.date: point
        for point in db.execute(
            select(DataPoint).where(
                DataPoint.indicator_code == code,
                DataPoint.date.in_([value for value in dates if value is not None]),
            )
        ).scalars()
    }
    now = datetime.now(UTC).replace(tzinfo=None)
    changed = 0
    columns = set(clean.columns)
    vintage_points: list[DataPoint] = []

    for row in clean.itertuples(index=False):
        observation_date = _optional_date(row.date)
        if observation_date is None:
            continue
        value = _decimal_value(row.value)
        metadata = _incoming_metadata(row, columns)
        point = existing.get(observation_date)

        if point is None:
            payload = {
                "indicator_code": code,
                "date": observation_date,
                "value": value,
                "retrieved_at": now,
                "status": "published",
                "version": 1,
                **metadata,
            }
            point = DataPoint(
                **payload,
            )
            db.add(point)
            existing[observation_date] = point
            vintage_points.append(point)
            changed += 1
            continue

        metadata = _avoid_metadata_downgrade(point, value, metadata)
        if not _content_changed(point, value, metadata):
            continue

        point.value = value
        for key, incoming in metadata.items():
            setattr(point, key, incoming)
        point.retrieved_at = now
        point.version += 1
        vintage_points.append(point)
        changed += 1

    # One flush assigns ids to every new current-value row in a batch. This is
    # materially faster than one cloud-database round trip per observation.
    db.flush()
    for point in vintage_points:
        _append_vintage(db, point)
    if commit:
        db.commit()
    logger.info("stored %s: %d changed vintages from %d observations", code, changed, len(clean))
    return changed


def upsert_points(db: Session, code: str, df: pd.DataFrame) -> int:
    """Quality-check and store one indicator outside a refresh run."""

    report = validate_indicator_frame(db, code, df)
    enforce_quality(report)
    return _store_points(db, code, df)


def _last_success(db: Session, code: str) -> RefreshResult | None:
    return db.scalar(
        select(RefreshResult)
        .where(
            RefreshResult.indicator_code == code,
            RefreshResult.status.in_(("success", "no_change")),
        )
        .order_by(RefreshResult.finished_at.desc(), RefreshResult.id.desc())
        .limit(1)
    )


def _error_text(exc: BaseException, limit: int = 4000) -> str:
    return f"{type(exc).__name__}: {exc}"[:limit]


def _record_refresh_result(
    db: Session,
    *,
    run_id: int,
    code: str,
    status: str,
    row_count: int,
    changed_count: int,
    duration_ms: int,
    started_at: datetime,
    finished_at: datetime,
    previous_success_at: datetime | None,
    error: str | None = None,
    quality_report: QualityReport | None = None,
) -> None:
    last_success_at = finished_at if status in {"success", "no_change"} else previous_success_at
    db.add(
        RefreshResult(
            run_id=run_id,
            indicator_code=code,
            status=status,
            row_count=row_count,
            changed_count=changed_count,
            duration_ms=max(duration_ms, 0),
            started_at=started_at,
            finished_at=finished_at,
            last_success_at=last_success_at,
            error=error,
            quality_issues=quality_report.issues_json() if quality_report else None,
        )
    )


def refresh_all_indicators(
    db: Session,
    trigger: str = "manual",
    *,
    fetchers: Mapping[str, Callable[[], pd.DataFrame]] | None = None,
) -> dict[str, int]:
    """Refresh every configured series and persist an auditable run ledger.

    Fetching and validation happen before any values are written.  A failed
    indicator is recorded and its last known-good current snapshot is left
    untouched; independent indicators continue refreshing.
    """

    ensure_indicators_seeded(db)
    selected_fetchers = dict(FETCHERS if fetchers is None else fetchers)
    run_started = datetime.now(UTC).replace(tzinfo=None)
    run = RefreshRun(
        trigger=trigger[:16] or "manual",
        status="running",
        started_at=run_started,
        total_indicators=len(selected_fetchers),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    results: dict[str, int] = {}
    attempts: dict[str, dict] = {}

    for code, fetcher in selected_fetchers.items():
        started_at = datetime.now(UTC).replace(tzinfo=None)
        started_clock = time.perf_counter()
        previous = _last_success(db, code)
        try:
            frame = fetcher()
            report = validate_indicator_frame(
                db,
                code,
                frame,
                previous_row_count=previous.row_count if previous else None,
            )
            error = None
        except Exception as exc:
            frame = None
            report = None
            error = exc
        fetch_duration_ms = round((time.perf_counter() - started_clock) * 1000)
        attempts[code] = {
            "frame": frame,
            "report": report,
            "error": error,
            "started_at": started_at,
            "fetch_duration_ms": fetch_duration_ms,
            "previous_success_at": previous.last_success_at if previous else None,
        }

    # This check must see all three frames together; it catches a known upstream
    # field-mapping failure where expectations silently duplicated satisfaction.
    consumer_issues = consumer_component_issues(
        {code: attempt["frame"] for code, attempt in attempts.items()}
    )
    for code, issues in consumer_issues.items():
        attempt = attempts[code]
        report = attempt["report"]
        if report is not None:
            attempt["report"] = QualityReport(report.row_count, report.issues + issues)

    failures: list[str] = []
    counts = {"success": 0, "no_change": 0, "failed": 0}
    for code, attempt in attempts.items():
        frame = attempt["frame"]
        report = attempt["report"]
        error = attempt["error"]
        changed = 0
        status = "failed"
        error_message = None
        write_started = time.perf_counter()
        try:
            if error is not None:
                raise error
            assert report is not None
            enforce_quality(report)
            # Keep current values, appended vintages and the per-indicator
            # ledger row in one transaction.
            changed = _store_points(db, code, frame, commit=False)
            status = "success" if changed else "no_change"
        except Exception as exc:
            db.rollback()
            error_message = _error_text(exc)
            failures.append(f"{code}: {error_message}")
            if isinstance(exc, DataQualityError):
                logger.warning("quality gate rejected %s: %s", code, exc)
            else:
                logger.exception("refresh failed for %s", code)

        finished_at = datetime.now(UTC).replace(tzinfo=None)
        duration_ms = attempt["fetch_duration_ms"] + round(
            (time.perf_counter() - write_started) * 1000
        )
        try:
            _record_refresh_result(
                db,
                run_id=run.id,
                code=code,
                status=status,
                row_count=(
                    report.row_count
                    if report
                    else (len(frame) if frame is not None else 0)
                ),
                changed_count=changed,
                duration_ms=duration_ms,
                started_at=attempt["started_at"],
                finished_at=finished_at,
                previous_success_at=attempt["previous_success_at"],
                error=error_message,
                quality_report=report,
            )
            db.commit()
        except Exception as ledger_error:
            # The data/vintage flush above is in the same transaction, so a
            # ledger failure cannot leave un-audited indicator changes behind.
            db.rollback()
            persisted_run = db.get(RefreshRun, run.id)
            if persisted_run is not None:
                persisted_run.status = "failed"
                persisted_run.finished_at = datetime.now(UTC).replace(tzinfo=None)
                persisted_run.failed_count += 1
                persisted_run.error = _error_text(ledger_error)
                db.commit()
            raise
        results[code] = changed if status != "failed" else 0
        counts[status] += 1

    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    run.success_count = counts["success"]
    run.no_change_count = counts["no_change"]
    run.failed_count = counts["failed"]
    if not failures:
        run.status = "success"
    elif len(failures) == len(selected_fetchers):
        run.status = "failed"
    else:
        run.status = "partial_failure"
    run.error = "\n".join(failures)[:8000] or None
    db.commit()

    return results
