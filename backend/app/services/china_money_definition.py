"""Audited constants for China's revised M1 definition and comparable history."""

from __future__ import annotations

import datetime as dt


CN_M1_2025_DEFINITION_VERSION = "pboc_m1_2025_definition_v1"
CN_M1_2024_COMPARABLE_SOURCE_URL = (
    "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/"
    "2025092212554572702/index.html"
)
# The original January 2025 financial-statistics release visibly preserves the
# publication minute and contains the full twelve-month comparable 2024 table.
CN_M1_2024_COMPARABLE_AVAILABLE_AT = dt.datetime(2025, 2, 14, 17, 0)
CN_M1_2024_COMPARABLE: tuple[tuple[dt.date, float, float], ...] = (
    (dt.date(2024, 1, 1), 1120120.0, 3.3),
    (dt.date(2024, 2, 1), 1093158.0, 2.6),
    (dt.date(2024, 3, 1), 1117433.0, 2.3),
    (dt.date(2024, 4, 1), 1075084.0, 0.6),
    (dt.date(2024, 5, 1), 1064391.0, -0.8),
    (dt.date(2024, 6, 1), 1089170.0, -1.7),
    (dt.date(2024, 7, 1), 1051800.0, -2.6),
    (dt.date(2024, 8, 1), 1049684.0, -3.0),
    (dt.date(2024, 9, 1), 1055410.0, -3.3),
    (dt.date(2024, 10, 1), 1054884.0, -2.3),
    (dt.date(2024, 11, 1), 1076379.0, -0.7),
    (dt.date(2024, 12, 1), 1113069.0, 1.2),
)


__all__ = [
    "CN_M1_2024_COMPARABLE",
    "CN_M1_2024_COMPARABLE_AVAILABLE_AT",
    "CN_M1_2024_COMPARABLE_SOURCE_URL",
    "CN_M1_2025_DEFINITION_VERSION",
]
