import unittest
from datetime import UTC, date, datetime

from app.models import RefreshResult, RefreshRun
from app.routers.refresh_status import _result_out, _run_out


class RefreshStatusTimezoneTests(unittest.TestCase):
    def test_naive_database_timestamps_are_exposed_as_utc(self) -> None:
        observed = datetime(2026, 9, 17, 8, 24)
        run = RefreshRun(
            id=1,
            trigger="manual",
            status="success",
            started_at=observed,
            finished_at=observed,
            total_indicators=1,
            success_count=1,
            no_change_count=0,
            failed_count=0,
            results=[],
        )
        result = RefreshResult(
            id=1,
            run_id=1,
            indicator_code="KR_BOK",
            status="success",
            row_count=42,
            changed_count=42,
            duration_ms=10,
            started_at=observed,
            finished_at=observed,
            last_success_at=observed,
            source_verified_through=date(2026, 9, 17),
            quality_issues=None,
        )

        result_out = _result_out(result)
        run_out = _run_out(run)

        self.assertEqual(result_out.last_success_at.tzinfo, UTC)
        self.assertEqual(result_out.source_verified_through, date(2026, 9, 17))
        self.assertEqual(result_out.started_at.tzinfo, UTC)
        self.assertEqual(run_out.finished_at.tzinfo, UTC)
        self.assertTrue(result_out.model_dump_json().count("Z") >= 3)


if __name__ == "__main__":
    unittest.main()
