from datetime import date
import unittest
from unittest.mock import patch

import pandas as pd

from app.fetchers import fred_source


def _frame(periods: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date.fromisoformat(period) for period in periods],
            "value": values,
        }
    )


class FredCalendarDerivativeTests(unittest.TestCase):
    def test_mom_skips_month_without_exact_previous_calendar_month(self) -> None:
        frame = _frame(
            ["2025-01-01", "2025-03-01", "2025-04-01"],
            [100.0, 110.0, 121.0],
        )

        with patch.object(fred_source, "_cached_fred_raw", return_value=frame):
            result = fred_source._fred_mom("TEST")

        self.assertEqual(result["date"].tolist(), [date(2025, 4, 1)])
        self.assertAlmostEqual(result["value"].iloc[0], 10.0)

    def test_yoy_skips_month_without_exact_prior_year_month(self) -> None:
        periods = ["2024-01-01"] + [
            f"2024-{month:02d}-01" for month in range(3, 13)
        ] + ["2025-01-01", "2025-02-01"]
        values = [100.0 + index for index in range(len(periods))]

        with patch.object(
            fred_source,
            "_cached_fred_raw",
            return_value=_frame(periods, values),
        ):
            result = fred_source._fred_yoy("TEST")

        self.assertEqual(result["date"].tolist(), [date(2025, 1, 1)])
        self.assertAlmostEqual(result["value"].iloc[0], 11.0)

    def test_nfp_change_skips_month_after_a_gap(self) -> None:
        frame = _frame(
            ["2025-01-01", "2025-03-01", "2025-04-01"],
            [150_000.0, 150_250.0, 150_400.0],
        )

        with patch.object(fred_source, "_cached_fred_raw", return_value=frame):
            result = fred_source._fetch_us_nfp_change()

        self.assertEqual(result["date"].tolist(), [date(2025, 4, 1)])
        self.assertEqual(result["value"].tolist(), [15.0])


if __name__ == "__main__":
    unittest.main()
