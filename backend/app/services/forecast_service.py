from datetime import timedelta

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA

from app.schemas import ForecastPoint


def forecast_series(dates: list, values: list[float], horizon: int = 30) -> list[ForecastPoint]:
    """用 ARIMA(1,1,1) 对序列做 horizon 步预测，返回预测值+置信区间。

    这是 v1 基线模型：小样本、噪声较大的经济/金融序列上，
    简单固定阶数的 ARIMA 比调参复杂的模型更稳定、更容易维护。
    """
    if len(values) < 10:
        return []

    series = pd.Series(values, index=pd.to_datetime(dates))
    series = series.asfreq("D").interpolate()

    model = ARIMA(series, order=(1, 1, 1))
    fitted = model.fit()
    result = fitted.get_forecast(steps=horizon)
    mean = result.predicted_mean
    conf_int = result.conf_int(alpha=0.2)

    last_date = series.index[-1]
    points: list[ForecastPoint] = []
    for i in range(horizon):
        step_date = (last_date + timedelta(days=i + 1)).date()
        lower, upper = conf_int.iloc[i]
        points.append(
            ForecastPoint(
                date=step_date,
                value=float(mean.iloc[i]),
                lower=float(lower),
                upper=float(np.nan_to_num(upper, nan=lower)),
            )
        )
    return points
