from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay, Week
from statsmodels.tsa.arima.model import ARIMA

from app.schemas import ForecastPoint


@dataclass(frozen=True)
class ForecastCadence:
    key: str
    label: str
    default_horizon: int


def infer_forecast_cadence(dates: list) -> ForecastCadence:
    """根据真实观测间隔推断预测频率，避免把月度/季度数据伪造为日频。"""
    index = pd.DatetimeIndex(pd.to_datetime(dates, errors="coerce")).dropna().sort_values().unique()
    if len(index) < 2:
        return ForecastCadence("month", "个月", 6)

    median_days = float(pd.Series(index).diff().dropna().dt.days.median())
    if median_days <= 3:
        return ForecastCadence("business_day", "个交易日", 30)
    if median_days <= 10:
        return ForecastCadence("week", "周", 12)
    if median_days <= 45:
        return ForecastCadence("month", "个月", 6)
    if median_days <= 135:
        return ForecastCadence("quarter", "个季度", 4)
    return ForecastCadence("year", "年", 3)


def _regularize_series(dates: list, values: list[float], cadence: ForecastCadence) -> pd.Series:
    frame = pd.DataFrame({"date": pd.to_datetime(dates, errors="coerce"), "value": values})
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last")

    if cadence.key == "business_day":
        series = frame.set_index("date")["value"]
        index = pd.bdate_range(series.index.min(), series.index.max())
    elif cadence.key == "week":
        series = frame.set_index("date")["value"]
        weekday = int(series.index[-1].weekday())
        index = pd.date_range(series.index.min(), series.index.max(), freq=Week(weekday=weekday))
        series = series.reindex(series.index.union(index)).sort_index().interpolate(method="time")
    else:
        frequency = {"month": "M", "quarter": "Q-DEC", "year": "Y-DEC"}[cadence.key]
        frame["period"] = frame["date"].dt.to_period(frequency)
        series = frame.groupby("period")["value"].last()
        index = pd.period_range(series.index.min(), series.index.max(), freq=frequency)

    series = series.reindex(index).interpolate().ffill().bfill()
    return series.astype(float)


def _future_dates(last_date, cadence: ForecastCadence, horizon: int) -> list:
    if cadence.key == "business_day":
        timestamp = pd.Timestamp(last_date)
        return [item.date() for item in pd.bdate_range(timestamp + BDay(1), periods=horizon)]
    if cadence.key == "week":
        timestamp = pd.Timestamp(last_date)
        return [(timestamp + timedelta(weeks=i)).date() for i in range(1, horizon + 1)]

    frequency = {"month": "M", "quarter": "Q-DEC", "year": "Y-DEC"}[cadence.key]
    last_period = last_date if isinstance(last_date, pd.Period) else pd.Timestamp(last_date).to_period(frequency)
    return [(last_period + i).to_timestamp(how="start").date() for i in range(1, horizon + 1)]


def forecast_series(dates: list, values: list[float], horizon: int | None = None) -> list[ForecastPoint]:
    """用 ARIMA(1,1,1) 按原序列频率做多步基线预测。"""
    if len(values) < 10:
        return []

    cadence = infer_forecast_cadence(dates)
    steps = horizon or cadence.default_horizon
    series = _regularize_series(dates, values, cadence)
    if len(series) < 10:
        return []

    # 使用位置索引拟合，日期只负责确定真实频率和生成未来时点。
    model = ARIMA(pd.Series(series.to_numpy()), order=(1, 1, 1))
    fitted = model.fit()
    result = fitted.get_forecast(steps=steps)
    mean = result.predicted_mean
    conf_int = result.conf_int(alpha=0.2)
    future_dates = _future_dates(series.index[-1], cadence, steps)

    points: list[ForecastPoint] = []
    for i, step_date in enumerate(future_dates):
        lower, upper = conf_int.iloc[i]
        predicted = float(mean.iloc[i])
        lower_value = float(np.nan_to_num(lower, nan=predicted))
        upper_value = float(np.nan_to_num(upper, nan=predicted))
        points.append(
            ForecastPoint(
                date=step_date,
                value=predicted,
                lower=lower_value,
                upper=upper_value,
            )
        )
    return points
