"""Safely move legacy CN_GDP observations from quarter-start to quarter-end.

The old collector stored Q1/Q2/Q3/Q4 at January/April/July/October.  The source
values are cumulative quarterly observations and belong at March/June/September/
December.  Run without ``--apply`` for a read-only plan.  Applying requires an
explicit backup path and updates current rows plus every linked vintage in one
transaction.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import DataPoint, DataPointVintage


SOURCE_MONTHS = {1, 4, 7, 10}
TARGET_MONTHS = {3, 6, 9, 12}


def _target_date(value: date) -> date:
    if value.month not in SOURCE_MONTHS or value.day != 1:
        raise ValueError(f"unexpected legacy CN_GDP date: {value}")
    return date(value.year, value.month + 2, 1)


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if value is not None else None


def _point_payload(point: DataPoint) -> dict:
    fields = (
        "id", "indicator_code", "date", "value", "release_date", "available_at",
        "retrieved_at", "source_url", "status", "formula_version", "version",
    )
    return {field: _json_value(getattr(point, field)) for field in fields}


def _vintage_payload(vintage: DataPointVintage) -> dict:
    fields = (
        "id", "data_point_id", "indicator_code", "date", "value", "release_date",
        "available_at", "retrieved_at", "source_url", "status", "formula_version",
        "version",
    )
    return {field: _json_value(getattr(vintage, field)) for field in fields}


def realign(*, apply: bool, backup_path: Path | None) -> dict:
    with SessionLocal() as db:
        points = list(
            db.execute(
                select(DataPoint)
                .where(DataPoint.indicator_code == "CN_GDP")
                .order_by(DataPoint.date)
                .with_for_update()
            ).scalars()
        )
        candidates = [point for point in points if point.date.month in SOURCE_MONTHS]
        unexpected = [
            point.date
            for point in points
            if point.date.month not in SOURCE_MONTHS | TARGET_MONTHS or point.date.day != 1
        ]
        if unexpected:
            raise RuntimeError(f"unexpected CN_GDP dates; refusing update: {unexpected[:5]}")
        if not candidates:
            db.rollback()
            return {"status": "no_change", "current_rows": len(points), "moved_rows": 0}

        target_dates = {_target_date(point.date) for point in candidates}
        candidate_ids = {point.id for point in candidates}
        conflicts = [
            point.date
            for point in points
            if point.id not in candidate_ids and point.date in target_dates
        ]
        if conflicts:
            raise RuntimeError(
                f"quarter-end rows already exist; refusing ambiguous merge: {conflicts[:5]}"
            )

        vintages = list(
            db.execute(
                select(DataPointVintage)
                .where(DataPointVintage.data_point_id.in_(candidate_ids))
                .order_by(DataPointVintage.data_point_id, DataPointVintage.version)
                .with_for_update()
            ).scalars()
        )
        plan = {
            "status": "ready" if not apply else "applied",
            "indicator_code": "CN_GDP",
            "moved_rows": len(candidates),
            "moved_vintages": len(vintages),
            "first_before": candidates[0].date.isoformat(),
            "first_after": _target_date(candidates[0].date).isoformat(),
            "last_before": candidates[-1].date.isoformat(),
            "last_after": _target_date(candidates[-1].date).isoformat(),
        }
        if not apply:
            db.rollback()
            return plan
        if backup_path is None:
            raise RuntimeError("--backup is required together with --apply")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now().isoformat(),
                    "current": [_point_payload(point) for point in candidates],
                    "vintages": [_vintage_payload(vintage) for vintage in vintages],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        legacy_dates = [point.date for point in candidates]
        target_by_id = {point.id: _target_date(point.date) for point in candidates}
        for point in candidates:
            point.date = target_by_id[point.id]
        for vintage in vintages:
            vintage.date = target_by_id[vintage.data_point_id]
        db.flush()

        remaining_legacy = db.scalar(
            select(DataPoint.id)
            .where(DataPoint.indicator_code == "CN_GDP")
            .where(DataPoint.date.in_(legacy_dates))
            .limit(1)
        )
        if remaining_legacy is not None:
            raise RuntimeError("post-update verification found a legacy date")
        db.commit()
        plan["backup"] = str(backup_path.resolve())
        return plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(json.dumps(realign(apply=args.apply, backup_path=args.backup), ensure_ascii=False))


if __name__ == "__main__":
    main()
