import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event, Lock
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator, ReleaseEvidence
from app.services.china_cycle_backtest import (
    _BACKTEST_CACHE_TTL_SECONDS,
    _BacktestCacheKey,
    _InputWatermark,
    _cached_backtest_result,
    _clear_backtest_cache,
    build_china_cycle_backtest,
)


class ChinaCycleBacktestCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_backtest_cache()
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(
            Indicator(
                code="CN_NMI",
                name="中国非制造业PMI",
                category="cycle_input",
                unit="点",
                source="test",
                frequency="monthly",
                is_visible=False,
                sort_order=0,
                region="CN",
            )
        )
        point = DataPoint(
            indicator_code="CN_NMI",
            date=date(2025, 1, 31),
            value=50,
            retrieved_at=datetime(2025, 2, 1, 10),
            version=1,
        )
        self.db.add(point)
        self.db.flush()
        self.db.add(
            DataPointVintage(
                data_point_id=point.id,
                indicator_code=point.indicator_code,
                date=point.date,
                value=point.value,
                retrieved_at=point.retrieved_at,
                version=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        _clear_backtest_cache()
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _fake_payload(vintage_count: int, cutoff: datetime) -> dict:
        return {
            "title": f"build-with-{vintage_count}-vintages",
            "backtest_definition": {"final_cutoff_at": cutoff.isoformat()},
        }

    def test_identical_request_hits_cache_and_refreshes_request_cutoff(self) -> None:
        def fake_build(vintages, _finals, *, periods, final_cutoff_at):
            self.assertEqual(str(periods[0]), "2025-01")
            return self._fake_payload(len(vintages), final_cutoff_at)

        first_now = datetime(2026, 1, 1, tzinfo=UTC)
        second_now = datetime(2026, 1, 2, tzinfo=UTC)
        with patch(
            "app.services.china_cycle_backtest._build_backtest_from_rows",
            side_effect=fake_build,
        ) as builder:
            first = build_china_cycle_backtest(
                self.db,
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
                months=1,
                now=first_now,
            )
            first["title"] = "caller mutation"
            second = build_china_cycle_backtest(
                self.db,
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
                months=1,
                now=second_now,
            )

        self.assertEqual(builder.call_count, 1)
        self.assertEqual(second["title"], "build-with-1-vintages")
        self.assertEqual(
            second["backtest_definition"]["final_cutoff_at"],
            second_now.isoformat(),
        )

    def test_supported_data_update_automatically_invalidates_cache(self) -> None:
        def fake_build(vintages, _finals, *, periods, final_cutoff_at):
            return self._fake_payload(len(vintages), final_cutoff_at)

        request = {
            "start": date(2025, 1, 1),
            "end": date(2025, 1, 31),
            "months": 1,
            "now": datetime(2026, 1, 1, tzinfo=UTC),
        }
        with patch(
            "app.services.china_cycle_backtest._build_backtest_from_rows",
            side_effect=fake_build,
        ) as builder:
            first = build_china_cycle_backtest(self.db, **request)

            point = self.db.query(DataPoint).filter_by(indicator_code="CN_NMI").one()
            point.value = 51
            point.version = 2
            point.retrieved_at = datetime(2025, 3, 1, 10)
            self.db.add(
                DataPointVintage(
                    data_point_id=point.id,
                    indicator_code=point.indicator_code,
                    date=point.date,
                    value=point.value,
                    retrieved_at=point.retrieved_at,
                    version=2,
                )
            )
            self.db.commit()

            second = build_china_cycle_backtest(self.db, **request)

        self.assertEqual(builder.call_count, 2)
        self.assertEqual(first["title"], "build-with-1-vintages")
        self.assertEqual(second["title"], "build-with-2-vintages")

    def test_release_evidence_append_automatically_invalidates_cache(self) -> None:
        observed_provenance: list[list[str]] = []
        observed_values: list[tuple[list[float], list[float]]] = []

        def fake_build(vintages, finals, *, periods, final_cutoff_at):
            observed_provenance.append(
                [row["vintage_provenance"] for row in vintages]
            )
            observed_values.append(
                (
                    [float(row["value"]) for row in vintages],
                    [float(row.value) for row in finals],
                )
            )
            return self._fake_payload(len(vintages), final_cutoff_at)

        request = {
            "start": date(2025, 1, 1),
            "end": date(2025, 1, 31),
            "months": 1,
            "now": datetime(2026, 1, 1, tzinfo=UTC),
        }
        with patch(
            "app.services.china_cycle_backtest._build_backtest_from_rows",
            side_effect=fake_build,
        ) as builder:
            build_china_cycle_backtest(self.db, **request)
            self.db.add(
                ReleaseEvidence(
                    evidence_key="a" * 64,
                    indicator_code="CN_NMI",
                    date=date(2025, 1, 31),
                    value=49,
                    release_date=date(2025, 2, 1),
                    available_at=datetime(2025, 2, 1, 9),
                    retrieved_at=datetime(2026, 1, 2, 12),
                    source_url="https://www.stats.gov.cn/official-release.html",
                    status="published",
                    evidence_kind="official_release",
                    chain_verified=True,
                    availability_precision="exact_minute",
                    version=1,
                )
            )
            self.db.commit()
            build_china_cycle_backtest(self.db, **request)

        self.assertEqual(builder.call_count, 2)
        self.assertEqual(observed_provenance[0], ["data_point_vintage"])
        self.assertEqual(observed_provenance[1], ["release_evidence"])
        self.assertEqual(observed_values[1], ([49.0], [50.0]))

    def test_unverified_evidence_neither_invalidates_nor_enters_replay(self) -> None:
        observed_provenance: list[list[str]] = []

        def fake_build(vintages, _finals, *, periods, final_cutoff_at):
            observed_provenance.append(
                [row["vintage_provenance"] for row in vintages]
            )
            return self._fake_payload(len(vintages), final_cutoff_at)

        request = {
            "start": date(2025, 1, 1),
            "end": date(2025, 1, 31),
            "months": 1,
            "now": datetime(2026, 1, 1, tzinfo=UTC),
        }
        with patch(
            "app.services.china_cycle_backtest._build_backtest_from_rows",
            side_effect=fake_build,
        ) as builder:
            build_china_cycle_backtest(self.db, **request)
            self.db.add(
                ReleaseEvidence(
                    evidence_key="b" * 64,
                    indicator_code="CN_NMI",
                    date=date(2025, 1, 31),
                    value=49,
                    release_date=date(2025, 2, 1),
                    available_at=datetime(2025, 2, 1, 9),
                    retrieved_at=datetime(2026, 1, 2, 12),
                    source_url="https://example.invalid/unverified",
                    status="published",
                    evidence_kind="official_release",
                    chain_verified=False,
                    availability_precision="exact_minute",
                    version=1,
                )
            )
            self.db.commit()

            # Disqualified evidence is outside the cache watermark, so it does
            # not trigger a pointless rebuild.
            build_china_cycle_backtest(self.db, **request)
            self.assertEqual(builder.call_count, 1)

            # A cold rebuild also filters it at the query boundary.
            _clear_backtest_cache()
            build_china_cycle_backtest(self.db, **request)

        self.assertEqual(builder.call_count, 2)
        self.assertEqual(
            observed_provenance,
            [["data_point_vintage"], ["data_point_vintage"]],
        )

    def test_resolved_range_is_reused_and_method_versions_invalidate(self) -> None:
        watermark = _InputWatermark(1, 1, 1, datetime(2025, 2, 1, 10))
        fake_db = Mock()
        fake_db.scalars.return_value = []

        with (
            patch(
                "app.services.china_cycle_backtest._backtest_data_watermark",
                return_value=(watermark, watermark, watermark),
            ),
            patch(
                "app.services.china_cycle_backtest._build_backtest_from_rows",
                side_effect=lambda *_args, final_cutoff_at, **_kwargs: self._fake_payload(
                    0, final_cutoff_at
                ),
            ) as builder,
        ):
            base = {
                "start": date(2025, 1, 1),
                "end": date(2025, 1, 31),
                "now": datetime(2026, 1, 1, tzinfo=UTC),
            }
            build_china_cycle_backtest(fake_db, months=1, **base)
            build_china_cycle_backtest(fake_db, months=2, **base)
            with patch(
                "app.services.china_cycle_backtest.A1_METHODOLOGY_VERSION",
                "test-new-a1-version",
            ):
                build_china_cycle_backtest(fake_db, months=2, **base)
            with patch(
                "app.services.china_cycle_backtest.METHODOLOGY_VERSION",
                "test-new-a3-version",
            ):
                build_china_cycle_backtest(fake_db, months=2, **base)

        self.assertEqual(builder.call_count, 3)

    def test_expired_entry_is_rebuilt(self) -> None:
        watermark = _InputWatermark(1, 1, 1, datetime(2025, 2, 1, 10))
        key = _BacktestCacheKey(
            start_period="2025-01",
            end_period="2025-01",
            a1_methodology_version="a1",
            a2_methodology_version="a2",
            a3_methodology_version="a3",
            model_codes=("CN_NMI",),
            current_values=watermark,
            vintages=watermark,
            release_evidence=watermark,
        )
        builder = Mock(side_effect=[{"build": 1}, {"build": 2}])

        with patch("app.services.china_cycle_backtest.monotonic") as clock:
            clock.return_value = 0
            self.assertEqual(_cached_backtest_result(key, builder), {"build": 1})
            clock.return_value = _BACKTEST_CACHE_TTL_SECONDS - 1
            self.assertEqual(_cached_backtest_result(key, builder), {"build": 1})
            clock.return_value = _BACKTEST_CACHE_TTL_SECONDS
            self.assertEqual(_cached_backtest_result(key, builder), {"build": 2})

        self.assertEqual(builder.call_count, 2)

    def test_same_key_is_built_once_under_concurrency(self) -> None:
        watermark = _InputWatermark(1, 1, 1, datetime(2025, 2, 1, 10))
        key = _BacktestCacheKey(
            start_period="2025-01",
            end_period="2025-01",
            a1_methodology_version="a1",
            a2_methodology_version="a2",
            a3_methodology_version="a3",
            model_codes=("CN_NMI",),
            current_values=watermark,
            vintages=watermark,
            release_evidence=watermark,
        )
        started = Event()
        release = Event()
        count_lock = Lock()
        build_count = 0

        def builder() -> dict:
            nonlocal build_count
            with count_lock:
                build_count += 1
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return {"value": [1]}

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_cached_backtest_result, key, builder)
            self.assertTrue(started.wait(timeout=2))
            second = pool.submit(_cached_backtest_result, key, builder)
            release.set()
            first_result = first.result(timeout=2)
            second_result = second.result(timeout=2)

        self.assertEqual(build_count, 1)
        self.assertEqual(first_result, second_result)
        self.assertIsNot(first_result, second_result)
        self.assertIsNot(first_result["value"], second_result["value"])


if __name__ == "__main__":
    unittest.main()
