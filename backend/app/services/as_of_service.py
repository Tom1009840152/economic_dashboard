from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.models import DataPointVintage


@dataclass(frozen=True)
class AsOfPoint:
    """One observation reconstructed from the vintages visible at a point in time."""

    indicator_code: str
    date: date
    value: float
    release_date: date | None
    available_at: datetime | None
    retrieved_at: datetime
    source_url: str | None
    status: str
    formula_version: str | None
    version: int
    effective_available_at: datetime
    availability_basis: str


def normalize_as_of(value: datetime) -> datetime:
    """Store and compare publication timestamps as naive UTC datetimes."""

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def availability_policy(strict: bool) -> tuple[str, str]:
    if strict:
        return (
            "published_only",
            "仅纳入 available_at 已知且不晚于 as_of 的版本；发布时间未知的数据会被排除。",
        )
    return (
        "published_or_first_seen",
        "优先使用 available_at；发布时间未知时以 retrieved_at（系统首次见到时间）作为保守替代。",
    )


def _ranked_vintages(
    *,
    as_of: datetime,
    strict: bool,
    codes: list[str],
    start: date | None = None,
    end: date | None = None,
):
    point = DataPointVintage
    effective_available_at = (
        point.available_at
        if strict
        else func.coalesce(point.available_at, point.retrieved_at)
    )
    availability_basis = case(
        (point.available_at.is_not(None), "available_at"),
        else_="retrieved_at_fallback",
    )

    filters = [point.indicator_code.in_(codes)]
    if strict:
        filters.extend(
            [
                point.available_at.is_not(None),
                point.available_at <= as_of,
            ]
        )
    else:
        filters.append(effective_available_at <= as_of)
    if start is not None:
        filters.append(point.date >= start)
    if end is not None:
        filters.append(point.date <= end)

    return (
        select(
            point.id.label("vintage_id"),
            point.indicator_code,
            point.date,
            point.value,
            point.release_date,
            point.available_at,
            point.retrieved_at,
            point.source_url,
            point.status,
            point.formula_version,
            point.version,
            effective_available_at.label("effective_available_at"),
            availability_basis.label("availability_basis"),
            func.row_number()
            .over(
                partition_by=(point.indicator_code, point.date),
                order_by=(
                    effective_available_at.desc(),
                    point.version.desc(),
                    point.retrieved_at.desc(),
                    point.id.desc(),
                ),
            )
            .label("vintage_rank"),
        )
        .where(*filters)
        .subquery("ranked_vintages")
    )


def _selected_vintages(ranked) -> Select:
    return select(
        ranked.c.indicator_code,
        ranked.c.date,
        ranked.c.value,
        ranked.c.release_date,
        ranked.c.available_at,
        ranked.c.retrieved_at,
        ranked.c.source_url,
        ranked.c.status,
        ranked.c.formula_version,
        ranked.c.version,
        ranked.c.effective_available_at,
        ranked.c.availability_basis,
    ).where(ranked.c.vintage_rank == 1)


def _to_point(row) -> AsOfPoint:
    return AsOfPoint(
        indicator_code=row.indicator_code,
        date=row.date,
        value=float(row.value),
        release_date=row.release_date,
        available_at=row.available_at,
        retrieved_at=row.retrieved_at,
        source_url=row.source_url,
        status=row.status,
        formula_version=row.formula_version,
        version=row.version,
        effective_available_at=row.effective_available_at,
        availability_basis=row.availability_basis,
    )


def get_indicator_history_as_of(
    db: Session,
    *,
    code: str,
    as_of: datetime,
    strict: bool = True,
    start: date | None = None,
    end: date | None = None,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[AsOfPoint], int]:
    """Reconstruct one indicator without using vintages published after ``as_of``."""

    normalized_as_of = normalize_as_of(as_of)
    ranked = _ranked_vintages(
        as_of=normalized_as_of,
        strict=strict,
        codes=[code],
        start=start,
        end=end,
    )
    selected = _selected_vintages(ranked).subquery("selected_vintages")
    total = db.scalar(select(func.count()).select_from(selected)) or 0
    rows = db.execute(
        select(selected)
        .order_by(selected.c.date)
        .offset(offset)
        .limit(limit)
    ).all()
    return [_to_point(row) for row in rows], total


def get_latest_snapshot_as_of(
    db: Session,
    *,
    codes: list[str],
    as_of: datetime,
    strict: bool = True,
    observation_end: date | None = None,
) -> list[AsOfPoint]:
    """Return the latest visible observation for every requested indicator."""

    if not codes:
        return []
    normalized_as_of = normalize_as_of(as_of)
    ranked = _ranked_vintages(
        as_of=normalized_as_of,
        strict=strict,
        codes=codes,
        end=observation_end or normalized_as_of.date(),
    )
    selected = _selected_vintages(ranked).subquery("selected_vintages")
    latest = select(
        *selected.c,
        func.row_number()
        .over(
            partition_by=selected.c.indicator_code,
            order_by=(
                selected.c.date.desc(),
                selected.c.effective_available_at.desc(),
                selected.c.version.desc(),
            ),
        )
        .label("observation_rank"),
    ).subquery("latest_observations")
    rows = db.execute(
        select(latest)
        .where(latest.c.observation_rank == 1)
        .order_by(latest.c.indicator_code)
    ).all()
    return [_to_point(row) for row in rows]
