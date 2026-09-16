import datetime as dt
import hashlib
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DataPoint, DataPointVintage, Indicator, ReleaseEvidence
from app.services.china_fiscal_current_gap import (
    DERIVED_FISCAL_CODES,
    DIRECT_FISCAL_CODES,
    FISCAL_CURRENT_CODES,
    FiscalCurrentConflictError,
    FiscalEvidenceConflictError,
    FiscalEvidenceEligibilityError,
    GDP_CODE,
    TARGET_DIRECT_DATES,
    repair_fiscal_current_gap,
)
from app.services.derived_metrics import (
    DERIVED_METRIC_SPECS,
    calculate_fiscal_broad_expenditure,
    calculate_fiscal_metrics,
)
from app.services.indicator_service import upsert_points as real_upsert_points


class ChinaFiscalCurrentGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        for code in (GDP_CODE, *FISCAL_CURRENT_CODES):
            self.db.add(
                Indicator(
                    code=code,
                    name=code,
                    category="cycle_input",
                    unit="",
                    source="official",
                    frequency="monthly",
                    is_visible=False,
                    sort_order=0,
                    region="CN",
                )
            )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _released_after(observed: dt.date) -> dt.datetime:
        next_month = pd.Period(observed, freq="M") + 1
        return dt.datetime(next_month.year, next_month.month, 20, 10)

    @staticmethod
    def _target_values(observed: dt.date) -> tuple[float, float]:
        if observed == dt.date(2024, 12, 1):
            return 2400.0, 600.0
        return float(observed.month * 100), float(observed.month * 50)

    def _fresh_fiscal(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, list[dict]] = {code: [] for code in DIRECT_FISCAL_CODES}
        for observed in TARGET_DIRECT_DATES:
            released = self._released_after(observed)
            values = self._target_values(observed)
            for code, value in zip(DIRECT_FISCAL_CODES, values, strict=True):
                frames[code].append(
                    {
                        "date": observed,
                        "value": value,
                        "release_date": released.date(),
                        "available_at": released,
                        "source_url": f"https://www.mof.gov.cn/{observed:%Y%m}.html",
                        "status": "published",
                    }
                )
        return {code: pd.DataFrame(rows) for code, rows in frames.items()}

    def _point(
        self,
        code: str,
        observed: dt.date,
        value: float,
        *,
        status: str = "published",
        version: int = 1,
        formula_version: str | None = None,
    ) -> DataPoint:
        point = DataPoint(
            indicator_code=code,
            date=observed,
            value=value,
            status=status,
            formula_version=formula_version,
            version=version,
        )
        self.db.add(point)
        return point

    def _evidence(
        self,
        code: str,
        observed: dt.date,
        value: float,
        *,
        available_at: dt.datetime | None = None,
        version: int = 1,
        chain_verified: bool = True,
        evidence_kind: str = "official_release",
        availability_precision: str = "exact_minute",
        status: str = "published",
        formula_version: str | None = None,
        host: str = "www.mof.gov.cn",
    ) -> ReleaseEvidence:
        released = available_at or self._released_after(observed)
        semantic = (
            f"{code}|{observed.isoformat()}|{value}|"
            f"{released.isoformat()}|{version}|{status}"
        )
        row = ReleaseEvidence(
            evidence_key=hashlib.sha256(semantic.encode()).hexdigest(),
            indicator_code=code,
            date=observed,
            value=value,
            release_date=released.date(),
            available_at=released,
            source_url=f"https://{host}/{observed.isoformat()}.html",
            status=status,
            formula_version=formula_version,
            provenance_json=None,
            evidence_kind=evidence_kind,
            chain_verified=chain_verified,
            availability_precision=availability_precision,
            version=version,
        )
        self.db.add(row)
        return row

    def _seed_target_evidence(self, fiscal: dict[str, pd.DataFrame]) -> None:
        for code in DIRECT_FISCAL_CODES:
            for row in fiscal[code].to_dict("records"):
                self._evidence(
                    code,
                    row["date"],
                    row["value"],
                    available_at=row["available_at"],
                )

    def _seed_full_metric_support(self) -> None:
        # Prior/current/future quarter-end current leaves that make all eight
        # authorized impulse restorations computable without historical gaps.
        support = {
            dt.date(2021, 6, 1): (500.0, 100.0, 6000.0),   # intensity 10
            dt.date(2021, 9, 1): (700.0, 200.0, 7500.0),   # intensity 12
            dt.date(2021, 12, 1): (900.0, 270.0, 9000.0),  # intensity 13
            dt.date(2023, 6, 1): (800.0, 160.0, 6000.0),   # intensity 16
            dt.date(2023, 9, 1): (1200.0, 225.0, 7500.0), # intensity 19
            dt.date(2023, 12, 1): (1600.0, 290.0, 9000.0),
            dt.date(2025, 12, 1): (2700.0, 600.0, 15000.0),
        }
        for observed, (general, fund, gdp) in support.items():
            self._point(DIRECT_FISCAL_CODES[0], observed, general)
            self._point(DIRECT_FISCAL_CODES[1], observed, fund)
            self._point(GDP_CODE, observed, gdp)

        # Current denominators for the four repaired target quarter ends.
        for observed, gdp in (
            (dt.date(2022, 6, 1), 6000.0),
            (dt.date(2022, 9, 1), 7500.0),
            (dt.date(2022, 12, 1), 9000.0),
            (dt.date(2024, 12, 1), 15000.0),
        ):
            self._point(GDP_CODE, observed, gdp)

    def test_authorized_direct_scope_is_the_exact_audited_ten_months(self) -> None:
        self.assertEqual(
            TARGET_DIRECT_DATES,
            (
                dt.date(2022, 4, 1),
                dt.date(2022, 5, 1),
                dt.date(2022, 6, 1),
                dt.date(2022, 7, 1),
                dt.date(2022, 8, 1),
                dt.date(2022, 9, 1),
                dt.date(2022, 10, 1),
                dt.date(2022, 11, 1),
                dt.date(2022, 12, 1),
                dt.date(2024, 12, 1),
            ),
        )

    def test_exact_42_row_plan_uses_current_gdp_not_derived_evidence(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()

        # These rows are tempting but forbidden inputs to the current repair.
        self._evidence(
            GDP_CODE,
            dt.date(2024, 12, 1),
            10000.0,
            host="www.stats.gov.cn",
        )
        self._evidence(
            "CN_FISCAL_BROAD_EXPENDITURE_YTD",
            dt.date(2024, 12, 1),
            777.0,
            status="derived",
            formula_version=DERIVED_METRIC_SPECS[
                "CN_FISCAL_BROAD_EXPENDITURE_YTD"
            ].version,
        )
        self._evidence(
            "CN_FISCAL_SPEND_INTENSITY",
            dt.date(2024, 12, 1),
            999.0,
            status="derived",
            formula_version=DERIVED_METRIC_SPECS[
                "CN_FISCAL_SPEND_INTENSITY"
            ].version,
        )
        self._evidence(
            "CN_FISCAL_IMPULSE_PROXY",
            dt.date(2024, 12, 1),
            888.0,
            status="derived",
            formula_version=DERIVED_METRIC_SPECS[
                "CN_FISCAL_IMPULSE_PROXY"
            ].version,
        )
        self.db.commit()

        with (
            patch(
                "app.services.china_fiscal_current_gap.calculate_fiscal_broad_expenditure",
                wraps=calculate_fiscal_broad_expenditure,
            ) as broad_formula,
            patch(
                "app.services.china_fiscal_current_gap.calculate_fiscal_metrics",
                wraps=calculate_fiscal_metrics,
            ) as metric_formula,
        ):
            dry_run = repair_fiscal_current_gap(self.db, fiscal)

        broad_formula.assert_called_once()
        metric_formula.assert_called_once()

        self.assertEqual(dry_run["mode"], "dry_run")
        self.assertEqual(
            dry_run["planned_insert_counts"],
            {
                DIRECT_FISCAL_CODES[0]: 10,
                DIRECT_FISCAL_CODES[1]: 10,
                "CN_FISCAL_BROAD_EXPENDITURE_YTD": 10,
                "CN_FISCAL_SPEND_INTENSITY": 4,
                "CN_FISCAL_IMPULSE_PROXY": 8,
            },
        )
        self.assertEqual(
            sum(dry_run["planned_insert_counts"].values()), 42
        )
        for code in DIRECT_FISCAL_CODES:
            self.assertEqual(
                dry_run["target_direct_summary"][code]["expected"], 10
            )
            self.assertEqual(
                dry_run["target_direct_summary"][code]["eligible"], 10
            )
            self.assertEqual(
                dry_run["target_direct_summary"][code]["missing"], 10
            )
        self.assertEqual(
            self.db.scalar(
                select(func.count()).select_from(DataPoint).where(
                    DataPoint.indicator_code == "CN_FISCAL_BROAD_EXPENDITURE_YTD"
                )
            ),
            0,
        )

        applied = repair_fiscal_current_gap(self.db, fiscal, apply=True)
        broad = self.db.scalar(
            select(DataPoint).where(
                DataPoint.indicator_code == "CN_FISCAL_BROAD_EXPENDITURE_YTD",
                DataPoint.date == dt.date(2024, 12, 1),
            )
        )
        intensity = self.db.scalar(
            select(DataPoint).where(
                DataPoint.indicator_code == "CN_FISCAL_SPEND_INTENSITY",
                DataPoint.date == dt.date(2024, 12, 1),
            )
        )
        impulse = self.db.scalar(
            select(DataPoint).where(
                DataPoint.indicator_code == "CN_FISCAL_IMPULSE_PROXY",
                DataPoint.date == dt.date(2024, 12, 1),
            )
        )
        self.assertEqual(float(broad.value), 3000.0)
        self.assertEqual(float(intensity.value), 20.0)
        self.assertEqual(float(impulse.value), -1.0)
        self.assertNotEqual(float(broad.value), 777.0)
        self.assertNotEqual(float(intensity.value), 999.0)
        self.assertNotEqual(float(impulse.value), 888.0)
        self.assertIsNone(intensity.release_date)
        self.assertIsNone(intensity.available_at)
        self.assertIsNone(intensity.source_url)
        self.assertEqual(
            intensity.formula_version,
            DERIVED_METRIC_SPECS["CN_FISCAL_SPEND_INTENSITY"].version,
        )
        impulse_dates = set(
            self.db.scalars(
                select(DataPoint.date).where(
                    DataPoint.indicator_code == "CN_FISCAL_IMPULSE_PROXY"
                )
            )
        )
        self.assertEqual(
            impulse_dates,
            {
                dt.date(2022, 6, 1),
                dt.date(2022, 9, 1),
                dt.date(2022, 12, 1),
                dt.date(2023, 6, 1),
                dt.date(2023, 9, 1),
                dt.date(2023, 12, 1),
                dt.date(2024, 12, 1),
                dt.date(2025, 12, 1),
            },
        )
        self.assertEqual(sum(applied["inserted_counts"].values()), 42)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)),
            42,
        )

    def test_existing_sentinels_stay_unchanged_and_second_run_writes_zero(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        observed = dt.date(2024, 12, 1)
        direct_sentinel = self._point(
            DIRECT_FISCAL_CODES[0],
            observed,
            self._target_values(observed)[0],
            status="sentinel",
            version=7,
        )
        derived_sentinel = self._point(
            "CN_FISCAL_SPEND_INTENSITY",
            observed,
            20.0,
            status="sentinel",
            version=9,
            formula_version=DERIVED_METRIC_SPECS[
                "CN_FISCAL_SPEND_INTENSITY"
            ].version,
        )
        self.db.commit()
        direct_id = direct_sentinel.id
        derived_id = derived_sentinel.id

        first = repair_fiscal_current_gap(self.db, fiscal, apply=True)
        vintage_count = self.db.scalar(
            select(func.count()).select_from(DataPointVintage)
        )
        second = repair_fiscal_current_gap(self.db, fiscal, apply=True)

        direct_after = self.db.get(DataPoint, direct_id)
        derived_after = self.db.get(DataPoint, derived_id)
        self.assertEqual(direct_after.status, "sentinel")
        self.assertEqual(direct_after.version, 7)
        self.assertEqual(float(derived_after.value), 20.0)
        self.assertEqual(derived_after.status, "sentinel")
        self.assertEqual(
            derived_after.formula_version,
            DERIVED_METRIC_SPECS["CN_FISCAL_SPEND_INTENSITY"].version,
        )
        self.assertEqual(derived_after.version, 9)
        self.assertGreater(sum(first["inserted_counts"].values()), 0)
        self.assertTrue(
            all(value == 0 for value in second["inserted_counts"].values())
        )
        self.assertEqual(second["before_counts"], second["after_counts"])
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)),
            vintage_count,
        )

    def test_missing_quarter_is_not_bridged(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        # Remove the 2023Q4 support leaves and denominator. The canonical
        # complete-quarter calendar must not bridge that gap when producing
        # the allowlisted 2024Q4 annual change.
        self.db.flush()
        for code in (*DIRECT_FISCAL_CODES, GDP_CODE):
            row = self.db.scalar(
                select(DataPoint).where(
                    DataPoint.indicator_code == code,
                    DataPoint.date == dt.date(2023, 12, 1),
                )
            )
            self.db.delete(row)
        self.db.commit()

        repair_fiscal_current_gap(self.db, fiscal, apply=True)

        impulse_dates = set(
            self.db.scalars(
                select(DataPoint.date).where(
                    DataPoint.indicator_code == "CN_FISCAL_IMPULSE_PROXY"
                )
            )
        )
        self.assertNotIn(dt.date(2024, 12, 1), impulse_dates)

    def test_ineligible_or_conflicting_evidence_fails_before_any_write(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        bad = self.db.scalar(
            select(ReleaseEvidence).where(
                ReleaseEvidence.indicator_code == DIRECT_FISCAL_CODES[1],
                ReleaseEvidence.date == TARGET_DIRECT_DATES[0],
            )
        )
        bad.chain_verified = False
        self.db.commit()
        before = self.db.scalar(select(func.count()).select_from(DataPoint))

        with self.assertRaises(FiscalEvidenceEligibilityError):
            repair_fiscal_current_gap(self.db, fiscal, apply=True)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

        bad.chain_verified = True
        bad.value = float(bad.value) + 0.000001
        self.db.commit()
        with self.assertRaises(FiscalEvidenceConflictError):
            repair_fiscal_current_gap(self.db, fiscal, apply=True)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )

    def test_triangulation_uses_exactly_six_decimal_semantics(self) -> None:
        fiscal = self._fresh_fiscal()
        target = TARGET_DIRECT_DATES[0]
        code = DIRECT_FISCAL_CODES[0]
        source_index = fiscal[code].index[fiscal[code]["date"].eq(target)][0]
        base = self._target_values(target)[0]
        fiscal[code].loc[source_index, "value"] = base + 0.0000004
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        sentinel = self._point(
            code,
            target,
            base + 0.00000049,
            status="sentinel",
            version=7,
        )
        self.db.commit()
        sentinel_id = sentinel.id

        report = repair_fiscal_current_gap(self.db, fiscal)

        self.assertEqual(report["target_direct_summary"][code]["already_present"], 1)
        self.assertEqual(report["planned_insert_counts"][code], 9)
        after = self.db.get(DataPoint, sentinel_id)
        self.assertEqual(float(after.value), base)
        self.assertEqual(after.version, 7)

    def test_target_current_conflict_fails_closed(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        target = TARGET_DIRECT_DATES[0]
        self._point(DIRECT_FISCAL_CODES[0], target, 999999.0, status="sentinel")
        self.db.commit()
        before = self.db.scalar(select(func.count()).select_from(DataPoint))

        with self.assertRaises(FiscalCurrentConflictError):
            repair_fiscal_current_gap(self.db, fiscal, apply=True)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_target_direct_formula_conflict_fails_closed(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        target = TARGET_DIRECT_DATES[0]
        self._point(
            DIRECT_FISCAL_CODES[0],
            target,
            self._target_values(target)[0],
            status="sentinel",
            formula_version="not-a-direct-leaf",
        )
        self.db.commit()
        before = self.db.scalar(select(func.count()).select_from(DataPoint))

        with self.assertRaises(FiscalCurrentConflictError):
            repair_fiscal_current_gap(self.db, fiscal, apply=True)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_target_derived_formula_conflict_fails_closed(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        sentinel = self._point(
            "CN_FISCAL_SPEND_INTENSITY",
            dt.date(2024, 12, 1),
            20.0,
            status="sentinel",
            version=9,
            formula_version="sentinel-v0",
        )
        self.db.commit()
        sentinel_id = sentinel.id
        before = self.db.scalar(select(func.count()).select_from(DataPoint))

        with self.assertRaises(FiscalCurrentConflictError):
            repair_fiscal_current_gap(self.db, fiscal, apply=True)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )
        after = self.db.get(DataPoint, sentinel_id)
        self.assertEqual(float(after.value), 20.0)
        self.assertEqual(after.status, "sentinel")
        self.assertEqual(after.version, 9)
        self.assertEqual(after.formula_version, "sentinel-v0")
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_outside_allowlist_difference_only_warns_and_is_not_promoted(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        outside = dt.date(2026, 1, 1)
        released = self._released_after(outside)
        fiscal[DIRECT_FISCAL_CODES[0]] = pd.concat(
            [
                fiscal[DIRECT_FISCAL_CODES[0]],
                pd.DataFrame(
                    [
                        {
                            "date": outside,
                            "value": 100.0,
                            "release_date": released.date(),
                            "available_at": released,
                            "source_url": "https://www.mof.gov.cn/outside.html",
                            "status": "published",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        fiscal[DIRECT_FISCAL_CODES[1]] = pd.concat(
            [
                fiscal[DIRECT_FISCAL_CODES[1]],
                pd.DataFrame(
                    [
                        {
                            "date": outside,
                            "value": 200.0,
                            "release_date": released.date(),
                            "available_at": released,
                            "source_url": "https://www.mof.gov.cn/outside.html",
                            "status": "published",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        self._evidence(DIRECT_FISCAL_CODES[0], outside, 101.0)
        self._evidence(DIRECT_FISCAL_CODES[1], outside, 200.0)
        outside_current = self._point(
            DIRECT_FISCAL_CODES[0], outside, 102.0, status="sentinel", version=5
        )
        self.db.commit()
        outside_id = outside_current.id

        report = repair_fiscal_current_gap(self.db, fiscal, apply=True)

        self.assertTrue(
            any("outside allowlist" in warning for warning in report["warnings"])
        )
        after = self.db.get(DataPoint, outside_id)
        self.assertEqual(float(after.value), 102.0)
        self.assertEqual(after.status, "sentinel")
        self.assertEqual(after.version, 5)
        self.assertIsNone(
            self.db.scalar(
                select(DataPoint).where(
                    DataPoint.indicator_code == DIRECT_FISCAL_CODES[1],
                    DataPoint.date == outside,
                )
            )
        )

    def test_apply_rolls_back_every_code_when_a_later_write_fails(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        self.db.commit()
        before = self.db.scalar(select(func.count()).select_from(DataPoint))

        def fail_after_first(
            db,
            code,
            frame,
            *,
            commit=True,
            missing_only=False,
        ):
            if code == "CN_FISCAL_IMPULSE_PROXY":
                raise RuntimeError("simulated later-code failure")
            return real_upsert_points(
                db,
                code,
                frame,
                commit=commit,
                missing_only=missing_only,
            )

        with patch(
            "app.services.china_fiscal_current_gap.upsert_points",
            side_effect=fail_after_first,
        ):
            with self.assertRaisesRegex(RuntimeError, "later-code failure"):
                repair_fiscal_current_gap(self.db, fiscal, apply=True)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_missing_only_store_cannot_turn_a_same_key_race_into_update(self) -> None:
        observed = dt.date(2022, 4, 1)
        existing = self._point(
            DIRECT_FISCAL_CODES[0],
            observed,
            123.0,
            status="sentinel",
            version=7,
        )
        self.db.commit()
        existing_id = existing.id
        incoming = pd.DataFrame(
            [
                {
                    "date": observed,
                    "value": 999.0,
                    "status": "published",
                }
            ]
        )

        changed = real_upsert_points(
            self.db,
            DIRECT_FISCAL_CODES[0],
            incoming,
            commit=False,
            missing_only=True,
        )
        self.db.commit()

        after = self.db.get(DataPoint, existing_id)
        self.assertEqual(changed, 0)
        self.assertEqual(float(after.value), 123.0)
        self.assertEqual(after.status, "sentinel")
        self.assertEqual(after.version, 7)
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )

    def test_missing_only_count_mismatch_aborts_the_transaction(self) -> None:
        fiscal = self._fresh_fiscal()
        self._seed_target_evidence(fiscal)
        self._seed_full_metric_support()
        self.db.commit()
        before = self.db.scalar(select(func.count()).select_from(DataPoint))

        def underreport_after_write(
            db,
            code,
            frame,
            *,
            commit=True,
            missing_only=False,
        ):
            changed = real_upsert_points(
                db,
                code,
                frame,
                commit=commit,
                missing_only=missing_only,
            )
            return changed - 1

        with patch(
            "app.services.china_fiscal_current_gap.upsert_points",
            side_effect=underreport_after_write,
        ):
            with self.assertRaisesRegex(
                ValueError, "missing-only write count changed concurrently"
            ):
                repair_fiscal_current_gap(self.db, fiscal, apply=True)

        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPoint)), before
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(DataPointVintage)), 0
        )


if __name__ == "__main__":
    unittest.main()
