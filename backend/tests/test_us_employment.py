from datetime import date
import unittest

import pandas as pd

from app.fetchers import us_employment


def _frame(periods: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date.fromisoformat(period) for period in periods],
            "value": values,
        }
    )


class USEmploymentCalendarDerivativeTests(unittest.TestCase):
    def test_derived_series_require_exact_calendar_references(self) -> None:
        frames = {
            "nonfarm_payrolls": _frame(
                ["2025-01-01", "2025-03-01", "2025-04-01"],
                [150_000.0, 150_250.0, 150_400.0],
            ),
            "hourly_earnings": _frame(
                ["2024-01-01", "2025-01-01", "2025-02-01"],
                [30.0, 33.0, 34.0],
            ),
            "job_openings": _frame(["2025-04-01"], [8_000.0]),
            "unemployed_people": _frame(["2025-04-01"], [7_500.0]),
        }

        derived = {
            item["key"]: item
            for item in us_employment._derived_series(frames)
        }

        self.assertEqual(
            derived["payroll_change"]["points"],
            [{"period": "2025-04", "value": 150.0}],
        )
        earnings_points = derived["hourly_earnings_yoy"]["points"]
        self.assertEqual([point["period"] for point in earnings_points], ["2025-01"])
        self.assertAlmostEqual(earnings_points[0]["value"], 10.0)
        self.assertNotIn(
            "2025-03",
            {point["period"] for point in derived["payroll_change"]["points"]},
        )
        self.assertNotIn(
            "2025-02",
            {
                point["period"]
                for point in derived["hourly_earnings_yoy"]["points"]
            },
        )


if __name__ == "__main__":
    unittest.main()
