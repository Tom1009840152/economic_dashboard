import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fetchers.akshare_source import fetch_all
from app.indicator_defs import INDICATOR_DEFS
from app.models import DataPoint, DataPointVintage, Indicator

logger = logging.getLogger(__name__)
_STATUS_PRIORITY = {
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
    if current_value != value or incoming_status is None:
        return metadata
    if _STATUS_PRIORITY.get(incoming_status, 0) < _STATUS_PRIORITY.get(point.status, 0):
        return {}
    return metadata


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
            version=point.version,
        )
    )


def upsert_points(db: Session, code: str, df: pd.DataFrame) -> int:
    """Update current observations and append only meaningful vintage changes.

    Fetchers may return the original ``date, value`` pair or additionally provide
    ``release_date``, ``available_at``, ``source_url`` and ``status``. Missing
    metadata never erases metadata captured by an earlier, richer source.
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
    db.commit()
    logger.info("stored %s: %d changed vintages from %d observations", code, changed, len(clean))
    return changed


def refresh_all_indicators(db: Session) -> dict[str, int]:
    ensure_indicators_seeded(db)
    results: dict[str, int] = {}
    data_by_code = fetch_all()

    for code, df in data_by_code.items():
        results[code] = upsert_points(db, code, df)

    return results
