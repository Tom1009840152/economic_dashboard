"""Rules for the currently comparable semantic regime of stored series."""

from __future__ import annotations

from datetime import date
from typing import Iterable, TypeVar

from sqlalchemy.sql import Select

from app.models import DataPoint
from app.services.derived_metrics import DERIVED_METRIC_SPECS


# The PBOC supplied a revised-M1 comparable backcast only for 2024.  Older M1
# rows remain useful as explicitly legacy history, but must not be connected to
# the revised series by default. January 2024 M1 MOM has no comparable Dec-2023
# denominator and therefore starts one month later.
CURRENT_REGIME_STARTS = {
    "CN_M1_ABS": date(2024, 1, 1),
    "CN_M1_YOY": date(2024, 1, 1),
    "CN_M1_MOM": date(2024, 2, 1),
    "CN_M1M2": date(2024, 1, 1),
}

PointT = TypeVar("PointT")


def is_current_series_point(code: str, point) -> bool:
    formula = DERIVED_METRIC_SPECS.get(code)
    if formula is not None and point.formula_version != formula.version:
        return False
    regime_start = CURRENT_REGIME_STARTS.get(code)
    return regime_start is None or point.date >= regime_start


def current_series_points(code: str, points: Iterable[PointT]) -> list[PointT]:
    return [point for point in points if is_current_series_point(code, point)]


def constrain_current_series(query: Select, code: str) -> Select:
    formula = DERIVED_METRIC_SPECS.get(code)
    if formula is not None:
        query = query.where(DataPoint.formula_version == formula.version)
    regime_start = CURRENT_REGIME_STARTS.get(code)
    if regime_start is not None:
        query = query.where(DataPoint.date >= regime_start)
    return query
