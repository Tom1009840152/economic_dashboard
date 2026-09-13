from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Indicator
from app.schemas import AsOfDataPointOut, AsOfHistoryOut, AsOfSnapshotOut
from app.services.as_of_service import (
    availability_policy,
    get_indicator_history_as_of,
    get_latest_snapshot_as_of,
    normalize_as_of,
)

router = APIRouter(prefix="/api/as-of", tags=["as-of"])
MAX_SNAPSHOT_INDICATORS = 100


def _point_out(point, indicator: Indicator) -> AsOfDataPointOut:
    return AsOfDataPointOut(
        indicator_code=point.indicator_code,
        indicator_name=indicator.name,
        unit=indicator.unit,
        date=point.date,
        value=point.value,
        release_date=point.release_date,
        available_at=point.available_at,
        retrieved_at=point.retrieved_at,
        source_url=point.source_url,
        status=point.status,
        formula_version=point.formula_version,
        version=point.version,
        effective_available_at=point.effective_available_at,
        availability_basis=point.availability_basis,
    )


def _normalized_codes(codes: list[str] | None) -> list[str]:
    result: list[str] = []
    for item in codes or []:
        for code in item.split(","):
            normalized = code.strip().upper()
            if normalized and normalized not in result:
                result.append(normalized)
    return result


@router.get("/indicators/{code}/history", response_model=AsOfHistoryOut)
def indicator_history_as_of(
    code: str,
    as_of: datetime = Query(description="重建时点；无时区时按 UTC 处理"),
    strict: bool = Query(default=True, description="是否排除 available_at 未知的数据"),
    start: date | None = Query(default=None, description="观察期起点"),
    end: date | None = Query(default=None, description="观察期终点"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    normalized_code = code.upper()
    indicator = db.get(Indicator, normalized_code)
    if indicator is None:
        raise HTTPException(status_code=404, detail="indicator not found")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start must not be after end")

    points, total = get_indicator_history_as_of(
        db,
        code=normalized_code,
        as_of=as_of,
        strict=strict,
        start=start,
        end=end,
        offset=offset,
        limit=limit,
    )
    policy, note = availability_policy(strict)
    return AsOfHistoryOut(
        code=indicator.code,
        name=indicator.name,
        unit=indicator.unit,
        as_of=normalize_as_of(as_of),
        mode="strict" if strict else "fallback",
        availability_policy=policy,
        availability_note=note,
        total=total,
        offset=offset,
        limit=limit,
        points=[_point_out(point, indicator) for point in points],
    )


@router.get("/snapshot", response_model=AsOfSnapshotOut)
def snapshot_as_of(
    as_of: datetime = Query(description="重建时点；无时区时按 UTC 处理"),
    codes: list[str] | None = Query(default=None, description="可重复或逗号分隔的指标代码"),
    region: str | None = Query(default=None, description="未指定 codes 时按地区选择指标"),
    include_hidden: bool = Query(default=False, description="地区查询时包含模型底层指标"),
    strict: bool = Query(default=True, description="是否排除 available_at 未知的数据"),
    observation_end: date | None = Query(default=None, description="观察期上限，默认 as_of 日期"),
    db: Session = Depends(get_db),
):
    requested_codes = _normalized_codes(codes)
    if not requested_codes and not region:
        raise HTTPException(status_code=422, detail="provide codes or region")
    if len(requested_codes) > MAX_SNAPSHOT_INDICATORS:
        raise HTTPException(
            status_code=422,
            detail=f"at most {MAX_SNAPSHOT_INDICATORS} indicator codes are allowed",
        )

    query = select(Indicator)
    if requested_codes:
        query = query.where(Indicator.code.in_(requested_codes))
        if region:
            query = query.where(Indicator.region == region.upper())
    else:
        query = query.where(Indicator.region == region.upper())
        if not include_hidden:
            query = query.where(Indicator.is_visible.is_(True))
    indicators = db.execute(query.order_by(Indicator.sort_order, Indicator.code)).scalars().all()
    if len(indicators) > MAX_SNAPSHOT_INDICATORS:
        raise HTTPException(
            status_code=422,
            detail=f"selection contains more than {MAX_SNAPSHOT_INDICATORS} indicators; narrow it with codes",
        )

    indicator_by_code = {indicator.code: indicator for indicator in indicators}
    selected_codes = list(indicator_by_code)
    points = get_latest_snapshot_as_of(
        db,
        codes=selected_codes,
        as_of=as_of,
        strict=strict,
        observation_end=observation_end,
    )
    point_by_code = {point.indicator_code: point for point in points}
    policy, note = availability_policy(strict)
    unknown_codes = (
        [code for code in requested_codes if code not in indicator_by_code]
        if requested_codes
        else []
    )
    unavailable_codes = [
        code for code in selected_codes if code not in point_by_code
    ]
    missing_codes = [*unknown_codes, *unavailable_codes]
    return AsOfSnapshotOut(
        as_of=normalize_as_of(as_of),
        observation_end=observation_end or normalize_as_of(as_of).date(),
        mode="strict" if strict else "fallback",
        availability_policy=policy,
        availability_note=note,
        requested_count=len(requested_codes) if requested_codes else len(selected_codes),
        returned_count=len(point_by_code),
        unknown_codes=unknown_codes,
        unavailable_codes=unavailable_codes,
        missing_codes=missing_codes,
        points=[
            _point_out(point_by_code[code], indicator_by_code[code])
            for code in selected_codes
            if code in point_by_code
        ],
    )
