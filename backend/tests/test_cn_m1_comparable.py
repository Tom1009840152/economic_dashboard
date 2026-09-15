import datetime as dt
import unittest
from unittest.mock import patch

import pandas as pd

# Production imports this registry first; doing the same here avoids the legacy
# fetcher registry's intentional late-import cycle.
from app.fetchers import akshare_source  # noqa: F401
from app.fetchers import macro_source
from app.services.derived_metrics import DERIVED_METRIC_SPECS


class ChinaM1ComparableTests(unittest.TestCase):
    @staticmethod
    def _source_frame() -> pd.DataFrame:
        expected_spread = [-5.4, -6.1, -6.0, -6.6, -7.8, -7.9, -8.9, -9.3, -10.1, -9.8, -7.8, -6.1]
        comparable_yoy = [row[2] for row in macro_source._CN_M1_2024_COMPARABLE]
        m2_yoy = [left - spread for left, spread in zip(comparable_yoy, expected_spread)]
        rows = [
            {
                "月份": "2023年12月份",
                "货币(M1)-数量(亿元)": 680000.0,
                "货币(M1)-同比增长": 1.0,
                "货币(M1)-环比增长": 0.5,
                "货币和准货币(M2)-同比增长": 9.0,
            }
        ]
        for month, m2 in enumerate(m2_yoy, start=1):
            rows.append(
                {
                    "月份": f"2024年{month:02d}月份",
                    # Deliberately wrong upstream cells: the fetchers must
                    # replace them with the official comparable backcast.
                    "货币(M1)-数量(亿元)": 1.0,
                    "货币(M1)-同比增长": -99.0,
                    "货币(M1)-环比增长": 99.0,
                    "货币和准货币(M2)-同比增长": m2,
                }
            )
        return pd.DataFrame(rows)

    def test_official_2024_balance_and_yoy_replace_mixed_upstream_columns(self) -> None:
        with patch.object(macro_source, "_raw_money_supply_df", return_value=self._source_frame()):
            balances = macro_source.fetch_cn_m1_abs()
            yoy = macro_source.fetch_cn_m1_yoy()

        expected_balances = [row[1] for row in macro_source._CN_M1_2024_COMPARABLE]
        expected_yoy = [row[2] for row in macro_source._CN_M1_2024_COMPARABLE]
        actual_balances = balances[pd.to_datetime(balances["date"]).dt.year.eq(2024)]
        actual_yoy = yoy[pd.to_datetime(yoy["date"]).dt.year.eq(2024)]
        self.assertEqual(actual_balances["value"].tolist(), expected_balances)
        self.assertEqual(actual_yoy["value"].tolist(), expected_yoy)
        self.assertTrue(
            actual_yoy["available_at"].eq(dt.datetime(2025, 2, 14, 17, 0)).all()
        )

    def test_january_2024_mom_is_excluded_without_a_comparable_prior_balance(self) -> None:
        with patch.object(macro_source, "_raw_money_supply_df", return_value=self._source_frame()):
            result = macro_source.fetch_cn_m1_mom()

        dates_2024 = result.loc[
            pd.to_datetime(result["date"]).dt.year.eq(2024), "date"
        ].tolist()
        self.assertNotIn(dt.date(2024, 1, 1), dates_2024)
        self.assertEqual(dates_2024[0], dt.date(2024, 2, 1))
        jan_balance = macro_source._CN_M1_2024_COMPARABLE[0][1]
        feb_balance = macro_source._CN_M1_2024_COMPARABLE[1][1]
        feb_value = result.loc[result["date"].eq(dt.date(2024, 2, 1)), "value"].item()
        self.assertAlmostEqual(feb_value, (feb_balance / jan_balance - 1) * 100)

    def test_spread_starts_at_comparable_regime_and_has_versioned_formula(self) -> None:
        expected = [-5.4, -6.1, -6.0, -6.6, -7.8, -7.9, -8.9, -9.3, -10.1, -9.8, -7.8, -6.1]
        with patch.object(macro_source, "_raw_money_supply_df", return_value=self._source_frame()):
            result = macro_source.fetch_cn_m1_m2_spread()

        self.assertEqual(result["date"].min(), dt.date(2024, 1, 1))
        self.assertEqual(result["value"].round(1).tolist(), expected)
        self.assertEqual(
            result["formula_version"].unique().tolist(),
            [DERIVED_METRIC_SPECS["CN_M1M2"].version],
        )
        self.assertEqual(DERIVED_METRIC_SPECS["CN_M1M2"].version, "1.1.0")


if __name__ == "__main__":
    unittest.main()
