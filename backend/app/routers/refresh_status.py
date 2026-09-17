"""Read-only visibility into indicator refresh health."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import RefreshResult, RefreshRun


router = APIRouter(prefix="/api/refresh", tags=["refresh"])


def _as_utc(value: datetime | None) -> datetime | None:
    """The database stores refresh timestamps as naive UTC; expose that fact."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class RefreshResultOut(BaseModel):
    indicator_code: str
    status: str
    row_count: int
    changed_count: int
    duration_ms: int
    started_at: datetime
    finished_at: datetime
    last_success_at: datetime | None = None
    source_verified_through: date | None = None
    error: str | None = None
    quality_issues: list[dict]


class RefreshRunOut(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    total_indicators: int
    success_count: int
    no_change_count: int
    failed_count: int
    error: str | None = None
    results: list[RefreshResultOut]


def _result_out(result: RefreshResult) -> RefreshResultOut:
    issues: list[dict] = []
    if result.quality_issues:
        try:
            parsed = json.loads(result.quality_issues)
            if isinstance(parsed, list):
                issues = [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            issues = [
                {
                    "code": "unparseable_quality_record",
                    "message": result.quality_issues,
                    "severity": "warning",
                }
            ]
    return RefreshResultOut(
        indicator_code=result.indicator_code,
        status=result.status,
        row_count=result.row_count,
        changed_count=result.changed_count,
        duration_ms=result.duration_ms,
        started_at=_as_utc(result.started_at),
        finished_at=_as_utc(result.finished_at),
        last_success_at=_as_utc(result.last_success_at),
        source_verified_through=result.source_verified_through,
        error=result.error,
        quality_issues=issues,
    )


def _run_out(run: RefreshRun) -> RefreshRunOut:
    return RefreshRunOut(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        started_at=_as_utc(run.started_at),
        finished_at=_as_utc(run.finished_at),
        total_indicators=run.total_indicators,
        success_count=run.success_count,
        no_change_count=run.no_change_count,
        failed_count=run.failed_count,
        error=run.error,
        results=[_result_out(result) for result in run.results],
    )


def _run_query():
    return select(RefreshRun).options(selectinload(RefreshRun.results))


@router.get("/status", response_model=RefreshRunOut | None)
def latest_refresh_status(db: Session = Depends(get_db)):
    run = db.scalar(
        _run_query().order_by(RefreshRun.started_at.desc(), RefreshRun.id.desc()).limit(1)
    )
    return _run_out(run) if run else None


@router.get("/runs", response_model=list[RefreshRunOut])
def list_refresh_runs(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    runs = db.execute(
        _run_query().order_by(RefreshRun.started_at.desc(), RefreshRun.id.desc()).limit(limit)
    ).scalars()
    return [_run_out(run) for run in runs]


@router.get("/runs/{run_id}", response_model=RefreshRunOut)
def get_refresh_run(run_id: int, db: Session = Depends(get_db)):
    run = db.scalar(_run_query().where(RefreshRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="refresh run not found")
    return _run_out(run)
