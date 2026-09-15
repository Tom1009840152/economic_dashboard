"""Canonical formulas and maintained inputs for derived macro indicators.

All callers should use this module instead of reproducing formulas in a page or
collector.  The formula registry provides an auditable version identifier, and
the policy-rate schedule is explicitly marked as manually maintained.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

import pandas as pd


@dataclass(frozen=True, slots=True)
class DerivedMetricSpec:
    code: str
    formula: str
    input_codes: tuple[str, ...]
    frequency: str
    unit: str
    version: str
    effective_date: date
    notes: str


DERIVED_METRIC_SPECS: Mapping[str, DerivedMetricSpec] = {
    "CN_FISCAL_BROAD_EXPENDITURE_YTD": DerivedMetricSpec(
        code="CN_FISCAL_BROAD_EXPENDITURE_YTD",
        formula="一般公共预算累计支出 + 政府性基金预算累计支出",
        input_codes=(
            "CN_FISCAL_GENERAL_SPEND_YTD",
            "CN_FISCAL_FUND_EXPENDITURE_YTD",
        ),
        frequency="monthly",
        unit="亿元",
        version="1.0.0",
        effective_date=date(2026, 9, 15),
        notes=(
            "A transparent two-budget spending proxy; it is not consolidated "
            "for transfers between the two budgets."
        ),
    ),
    "CN_M1M2": DerivedMetricSpec(
        code="CN_M1M2",
        formula="M1同比增速 − M2同比增速",
        input_codes=("CN_M1_YOY", "CN_M2_YOY"),
        frequency="monthly",
        unit="pp",
        version="1.1.0",
        effective_date=date(2026, 9, 13),
        notes=(
            "Uses the PBOC's revised-M1 comparable 2024 backcast and the revised "
            "series from 2025 onward. Pre-2024 old-definition M1 is excluded."
        ),
    ),
    "CN_CREDIT_INTENSITY": DerivedMetricSpec(
        code="CN_CREDIT_INTENSITY",
        formula="滚动12个月社融增量 ÷ 最近可得四季度滚动名义GDP × 100",
        input_codes=("CN_TSF", "CN_GDP_NOMINAL_YTD"),
        frequency="monthly",
        unit="% GDP",
        version="1.0.0",
        effective_date=date(2026, 9, 13),
        notes="GDP denominator uses the latest completed quarter available for each month.",
    ),
    "CN_CREDIT_IMPULSE": DerivedMetricSpec(
        code="CN_CREDIT_IMPULSE",
        formula="信用强度[t] − 信用强度[t−12个月]",
        input_codes=("CN_CREDIT_INTENSITY",),
        frequency="monthly",
        unit="pp",
        version="1.0.0",
        effective_date=date(2026, 9, 13),
        notes="Year-over-year change in credit intensity; not the raw change in TSF flow.",
    ),
    "CN_FISCAL_SPEND_INTENSITY": DerivedMetricSpec(
        code="CN_FISCAL_SPEND_INTENSITY",
        formula="广义财政累计支出 ÷ 同期累计名义GDP × 100",
        input_codes=("CN_FISCAL_BROAD_EXPENDITURE_YTD", "CN_GDP_NOMINAL_YTD"),
        frequency="quarterly",
        unit="% GDP",
        version="1.0.0",
        effective_date=date(2026, 9, 13),
        notes="Calculated only at quarter ends using like-for-like cumulative values.",
    ),
    "CN_FISCAL_IMPULSE_PROXY": DerivedMetricSpec(
        code="CN_FISCAL_IMPULSE_PROXY",
        formula="财政支出强度[t] − 财政支出强度[t−4季度]",
        input_codes=("CN_FISCAL_SPEND_INTENSITY",),
        frequency="quarterly",
        unit="pp",
        version="1.0.0",
        effective_date=date(2026, 9, 13),
        notes="Spending-intensity proxy, not a structural-primary-balance fiscal impulse.",
    ),
}


def calculate_spread(left: pd.Series, right: pd.Series) -> pd.Series:
    """Subtract two explicitly aligned series; unmatched periods stay missing."""

    aligned = pd.concat(
        [
            pd.to_numeric(left, errors="coerce").rename("left"),
            pd.to_numeric(right, errors="coerce").rename("right"),
        ],
        axis=1,
    ).dropna()
    return (aligned["left"] - aligned["right"]).sort_index()


def calculate_fiscal_broad_expenditure(
    general_expenditure_ytd: pd.Series,
    fund_expenditure_ytd: pd.Series,
) -> pd.Series:
    """Add the two published YTD expenditure budgets on an exact calendar join."""

    general = _monthly_series(general_expenditure_ytd)
    fund = _monthly_series(fund_expenditure_ytd)
    aligned = pd.concat(
        [general.rename("general"), fund.rename("fund")], axis=1
    ).dropna()
    return (aligned["general"] + aligned["fund"]).sort_index()


@dataclass(frozen=True, slots=True)
class PolicyRateChange:
    effective_date: date
    rate: float
    source_url: str


@dataclass(frozen=True, slots=True)
class PolicyRateSchedule:
    code: str
    name: str
    maintenance: str
    source_name: str
    source_url: str
    verified_through: date
    catalog_updated_at: date
    changes: tuple[PolicyRateChange, ...]


PBOC_7D_REVERSE_REPO = PolicyRateSchedule(
    code="PBOC_7D_REVERSE_REPO",
    name="中国人民银行公开市场7天期逆回购操作利率",
    maintenance="manual",
    source_name="中国人民银行公开市场业务交易公告",
    source_url=(
        "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/"
        "2025122608571540479/index.html"
    ),
    verified_through=date(2025, 12, 26),
    catalog_updated_at=date(2026, 9, 13),
    changes=(
        PolicyRateChange(
            effective_date=date(2024, 1, 1),
            rate=1.80,
            source_url=(
                "https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/5347949/"
                "afbfa5df25ee45889d916a2819b60a43/2024110815410752868.pdf"
            ),
        ),
        PolicyRateChange(
            effective_date=date(2024, 7, 22),
            rate=1.70,
            source_url=(
                "https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/5347949/"
                "afbfa5df25ee45889d916a2819b60a43/2024110815410752868.pdf"
            ),
        ),
        PolicyRateChange(
            effective_date=date(2024, 9, 27),
            rate=1.50,
            source_url=(
                "https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/5347949/"
                "afbfa5df25ee45889d916a2819b60a43/2024110815410752868.pdf"
            ),
        ),
        PolicyRateChange(
            effective_date=date(2025, 5, 8),
            rate=1.40,
            source_url="https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/5896228/index.html",
        ),
    ),
)


def policy_rate_for_periods(
    periods: pd.PeriodIndex,
    schedule: PolicyRateSchedule = PBOC_7D_REVERSE_REPO,
) -> pd.Series:
    """Return the rate in force at each period end from a maintained schedule."""

    changes = tuple(sorted(schedule.changes, key=lambda item: item.effective_date))
    values: list[float] = []
    for period in periods:
        period_end = period.to_timestamp(how="end").date()
        if period_end > schedule.verified_through:
            values.append(float("nan"))
            continue
        applicable = [item.rate for item in changes if item.effective_date <= period_end]
        values.append(applicable[-1] if applicable else float("nan"))
    return pd.Series(values, index=periods, dtype="float64")


def _monthly_series(series: pd.Series) -> pd.Series:
    result = pd.Series(series, dtype="float64").dropna().copy()
    if not isinstance(result.index, pd.PeriodIndex):
        result.index = pd.to_datetime(result.index).to_period("M")
    else:
        result.index = result.index.asfreq("M")
    return result.groupby(level=0).last().sort_index()


def trailing_nominal_gdp_from_ytd(nominal_gdp_ytd: pd.Series) -> pd.Series:
    """Turn cumulative quarterly nominal GDP into a trailing-four-quarter level."""

    ytd = _monthly_series(nominal_gdp_ytd)
    values = {period: float(value) for period, value in ytd.items()}
    trailing: dict[pd.Period, float] = {}
    for period, current_ytd in values.items():
        previous_annual = values.get(pd.Period(year=period.year - 1, month=12, freq="M"))
        previous_same_period = values.get(period - 12)
        if previous_annual is None or previous_same_period is None:
            continue
        trailing[period] = current_ytd + previous_annual - previous_same_period
    return pd.Series(trailing, dtype="float64").sort_index()


def calculate_credit_metrics(
    tsf_monthly_flow: pd.Series,
    nominal_gdp_ytd: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """Calculate canonical monthly credit intensity and credit impulse."""

    tsf = _monthly_series(tsf_monthly_flow)
    if tsf.empty:
        return pd.Series(dtype="float64"), pd.Series(dtype="float64")
    complete_months = pd.period_range(tsf.index.min(), tsf.index.max(), freq="M")
    rolling_credit = tsf.reindex(complete_months).rolling(12, min_periods=12).sum()
    trailing_gdp = trailing_nominal_gdp_from_ytd(nominal_gdp_ytd)

    intensity: dict[pd.Period, float] = {}
    for period, credit in rolling_credit.dropna().items():
        completed = trailing_gdp[trailing_gdp.index <= period]
        if completed.empty or completed.iloc[-1] == 0:
            continue
        intensity[period] = float(credit / completed.iloc[-1] * 100)
    intensity_series = pd.Series(intensity, dtype="float64").sort_index()
    if intensity_series.empty:
        return intensity_series, pd.Series(dtype="float64")
    # ``Series.shift(12)`` means twelve *rows*, not twelve calendar months.
    # Reindex first so a missing input month cannot silently compare t with
    # the wrong historical period.
    calendar_months = pd.period_range(
        intensity_series.index.min(), intensity_series.index.max(), freq="M"
    )
    calendar_intensity = intensity_series.reindex(calendar_months)
    impulse = calendar_intensity - calendar_intensity.shift(12)
    return intensity_series.dropna(), impulse.dropna()


def calculate_fiscal_metrics(
    broad_expenditure_ytd: pd.Series,
    nominal_gdp_ytd: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """Calculate canonical quarterly fiscal spending intensity and its proxy impulse."""

    spending = _monthly_series(broad_expenditure_ytd)
    gdp = _monthly_series(nominal_gdp_ytd)
    quarter_ends = spending[spending.index.month.isin({3, 6, 9, 12})]
    aligned = pd.concat([quarter_ends.rename("spending"), gdp.rename("gdp")], axis=1).dropna()
    aligned = aligned[aligned["gdp"] != 0]
    intensity = (aligned["spending"] / aligned["gdp"] * 100).sort_index()
    if intensity.empty:
        return intensity, pd.Series(dtype="float64")
    # Keep a complete quarter-end calendar before applying the annual lag.
    # Otherwise one missing quarter turns ``shift(4)`` into a five-quarter
    # or three-quarter comparison.
    quarter_ends_index = pd.period_range(
        intensity.index.min().asfreq("Q-DEC"),
        intensity.index.max().asfreq("Q-DEC"),
        freq="Q-DEC",
    ).asfreq("M", how="end")
    calendar_intensity = intensity.reindex(quarter_ends_index)
    impulse = calendar_intensity - calendar_intensity.shift(4)
    return intensity.dropna(), impulse.dropna()


def derived_metric_versions(codes: Iterable[str] | None = None) -> dict[str, str]:
    selected = DERIVED_METRIC_SPECS if codes is None else {
        code: DERIVED_METRIC_SPECS[code] for code in codes
    }
    return {code: spec.version for code, spec in selected.items()}
