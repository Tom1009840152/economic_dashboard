import datetime as dt
import unittest
from types import SimpleNamespace

from app.services.indicator_series import current_series_points


class CurrentIndicatorSeriesTests(unittest.TestCase):
    def test_derived_series_defaults_to_current_formula_version(self) -> None:
        points = [
            SimpleNamespace(
                date=dt.date(2024, 1, 1), formula_version="1.0.0", value=-8.0
            ),
            SimpleNamespace(
                date=dt.date(2024, 1, 1), formula_version="1.1.0", value=-5.4
            ),
        ]

        current = current_series_points("CN_M1M2", points)

        self.assertEqual([point.value for point in current], [-5.4])

    def test_revised_m1_regime_does_not_connect_to_old_definition(self) -> None:
        points = [
            SimpleNamespace(
                date=dt.date(2023, 12, 1), formula_version=None, value=-1.4
            ),
            SimpleNamespace(
                date=dt.date(2024, 1, 1), formula_version=None, value=3.3
            ),
        ]

        current = current_series_points("CN_M1_YOY", points)

        self.assertEqual([point.date for point in current], [dt.date(2024, 1, 1)])


if __name__ == "__main__":
    unittest.main()
