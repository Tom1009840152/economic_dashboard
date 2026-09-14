import datetime as dt
import unittest
from types import SimpleNamespace

import pandas as pd

from scripts.backfill_china_cycle import _verified_release_evidence


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _query):
        return self.rows


class ChinaCycleEvidenceBackfillTests(unittest.TestCase):
    def test_only_exact_matches_or_missing_current_rows_are_accepted(self) -> None:
        frame = pd.DataFrame(
            [
                self._row(dt.date(2024, 1, 1), 5.0),
                self._row(dt.date(2024, 2, 1), 6.0),
                self._row(dt.date(2024, 3, 1), 7.0),
            ]
        )
        db = _FakeSession(
            [
                SimpleNamespace(date=dt.date(2024, 1, 1), value=5.0),
                SimpleNamespace(date=dt.date(2024, 2, 1), value=6.5),
            ]
        )

        accepted, mismatches = _verified_release_evidence(db, "CN_IP", frame)

        self.assertEqual(
            accepted["date"].tolist(),
            [dt.date(2024, 1, 1), dt.date(2024, 3, 1)],
        )
        self.assertEqual(mismatches, ["2024-02-01"])

    def test_non_nbs_page_is_not_accepted_as_nbs_release_evidence(self) -> None:
        frame = pd.DataFrame([self._row(dt.date(2024, 1, 1), 5.0)])
        frame.loc[0, "source_url"] = "https://example.test/not-official"

        accepted, mismatches = _verified_release_evidence(
            _FakeSession([]), "CN_IP", frame
        )

        self.assertTrue(accepted.empty)
        self.assertEqual(mismatches, [])

    @staticmethod
    def _row(observed: dt.date, value: float) -> dict:
        released = dt.datetime(2024, 4, 16, 10)
        return {
            "date": observed,
            "value": value,
            "release_date": released.date(),
            "available_at": released,
            "source_url": "https://www.stats.gov.cn/sj/zxfb/example.html",
            "status": "published",
        }


if __name__ == "__main__":
    unittest.main()
