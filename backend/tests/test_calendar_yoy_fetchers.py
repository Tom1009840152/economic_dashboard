import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd

from app.fetchers import oecd_cycle, uk_source


def _history_with_missing_february() -> pd.DataFrame:
    dates = list(pd.date_range("2023-01-01", "2024-02-01", freq="MS"))
    dates.remove(pd.Timestamp("2023-02-01"))
    values = [100.0 + index for index in range(len(dates))]
    values[dates.index(pd.Timestamp("2024-01-01"))] = 110.0
    values[dates.index(pd.Timestamp("2024-02-01"))] = 120.0
    return pd.DataFrame(
        {
            "date": [value.date() for value in dates],
            "value": values,
        }
    )


class CalendarYearOverYearFetcherTests(unittest.TestCase):
    def _assert_exact_month_matching(self, result: pd.DataFrame) -> None:
        by_date = result.set_index("date")["value"]
        self.assertAlmostEqual(by_date.loc[dt.date(2024, 1, 1)], 10.0)
        self.assertNotIn(dt.date(2024, 2, 1), by_date.index)

    def test_euro_area_ip_does_not_use_thirteen_month_old_reference(self) -> None:
        frame = _history_with_missing_february()
        with patch.object(oecd_cycle, "_eurostat_series", return_value=frame):
            result = oecd_cycle.fetch_eu_industrial_production()

        self._assert_exact_month_matching(result)

    def test_uk_ip_does_not_use_thirteen_month_old_reference(self) -> None:
        frame = _history_with_missing_february()
        with patch.object(uk_source, "_ons_series", return_value=frame):
            result = uk_source.fetch_uk_industrial_production()

        self._assert_exact_month_matching(result)


if __name__ == "__main__":
    unittest.main()
