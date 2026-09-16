"""Fixed-scope repair for two audited China core-CPI current observations."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DataPoint, DataPointVintage, Indicator
from app.services.indicator_service import upsert_points


CORE_CPI_CODE = "CN_CORE_CPI"
VALUE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class _RepairSpec:
    observed: dt.date
    value: Decimal
    available_at: dt.datetime
    evidence_status: str
    current_status: str
    allowed_existing_values: frozenset[Decimal]
    source_url: str
    source_sha256: str
    article_observation: dt.date
    evidence_semantics: str


REPAIR_SPECS = (
    _RepairSpec(
        observed=dt.date(2025, 2, 1),
        value=Decimal("-0.100000"),
        available_at=dt.datetime(2025, 4, 10, 9, 30),
        evidence_status="published_later_reference",
        current_status="historical_backfill",
        allowed_existing_values=frozenset({Decimal("-0.100000")}),
        source_url=(
            "https://www.stats.gov.cn/sj/sjjd/202504/"
            "t20250410_1959259.html"
        ),
        source_sha256="f000bb6b66cd5e58419228984fbd1fa7a6139179e8e6dc26bdd26902ab79ca48",
        article_observation=dt.date(2025, 3, 1),
        evidence_semantics="later_article_explicit_reference",
    ),
    _RepairSpec(
        observed=dt.date(2025, 6, 1),
        value=Decimal("0.700000"),
        available_at=dt.datetime(2025, 7, 9, 9, 30),
        evidence_status="published",
        current_status="published",
        allowed_existing_values=frozenset(
            {Decimal("0.100000"), Decimal("0.700000")}
        ),
        source_url=(
            "https://www.stats.gov.cn/sj/sjjd/202507/"
            "t20250709_1960365.html"
        ),
        source_sha256="8839f7a436aca208e45286061fc1e73d31a7987161d327bd41f1808a1c75d651",
        article_observation=dt.date(2025, 6, 1),
        evidence_semantics="subject_month_release",
    ),
)
TARGET_DATES = tuple(spec.observed for spec in REPAIR_SPECS)


class CoreCpiCurrentRepairError(ValueError):
    """The complete fixed-scope repair cannot be proved safe."""


class CoreCpiEvidenceError(CoreCpiCurrentRepairError):
    """Fresh NBS evidence is absent, ineligible, or conflicts with the audit."""


class CoreCpiCurrentConflictError(CoreCpiCurrentRepairError):
    """A target current value lies outside its explicitly allowed state."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    date: dt.date
    value: Decimal
    release_date: dt.date
    available_at: dt.datetime
    source_url: str
    status: str


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(VALUE_QUANTUM)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CoreCpiCurrentRepairError(f"invalid core CPI value: {value!r}") from exc
    if not result.is_finite():
        raise CoreCpiCurrentRepairError(f"invalid core CPI value: {value!r}")
    return result


def _is_nbs_https(value: object) -> bool:
    try:
        parsed = urlparse(str(value))
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        hostname == "stats.gov.cn" or hostname.endswith(".stats.gov.cn")
    )


def _normalize_evidence(frame: pd.DataFrame) -> dict[dt.date, _Candidate]:
    required = {
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
        "formula_version",
        "evidence_kind",
        "chain_verified",
        "availability_precision",
        "provenance_json",
    }
    if not isinstance(frame, pd.DataFrame):
        raise CoreCpiEvidenceError("core CPI evidence must be a data frame")
    missing = sorted(required - set(frame.columns))
    if missing:
        raise CoreCpiEvidenceError(f"core CPI evidence is missing columns: {missing}")

    specs = {spec.observed: spec for spec in REPAIR_SPECS}
    by_date: dict[dt.date, list[_Candidate]] = {date: [] for date in TARGET_DATES}
    for raw in frame.to_dict("records"):
        try:
            observed = pd.Timestamp(raw.get("date")).date()
        except (TypeError, ValueError):
            continue
        spec = specs.get(observed)
        if spec is None:
            continue
        available = raw.get("available_at")
        if not isinstance(available, dt.datetime) or available.tzinfo is not None:
            raise CoreCpiEvidenceError(
                f"{observed:%Y-%m} evidence lacks a naive exact timestamp"
            )
        available = available.replace(microsecond=0)
        try:
            release_date = pd.Timestamp(raw.get("release_date")).date()
        except (TypeError, ValueError) as exc:
            raise CoreCpiEvidenceError(
                f"{observed:%Y-%m} evidence lacks a release date"
            ) from exc
        source_url = str(raw.get("source_url") or "").strip()
        provenance_raw = raw.get("provenance_json")
        try:
            provenance = (
                json.loads(provenance_raw)
                if isinstance(provenance_raw, str)
                else provenance_raw
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CoreCpiEvidenceError(
                f"{observed:%Y-%m} evidence provenance is invalid"
            ) from exc
        if not isinstance(provenance, dict):
            raise CoreCpiEvidenceError(
                f"{observed:%Y-%m} evidence provenance is required"
            )
        source_sha256 = provenance.get("source_sha256")
        if (
            _decimal(raw.get("value")) != spec.value
            or available != spec.available_at
            or release_date != available.date()
            or raw.get("status") != spec.evidence_status
            or raw.get("evidence_kind") != "official_release"
            or raw.get("chain_verified") is not True
            or raw.get("availability_precision") != "exact_minute"
            or not _is_nbs_https(source_url)
            or source_url != spec.source_url
            or pd.notna(raw.get("formula_version"))
            or provenance.get("parser_version") != "nbs_inflation_release_v1"
            or provenance.get("article_observation")
            != spec.article_observation.isoformat()
            or provenance.get("evidence_semantics") != spec.evidence_semantics
            or provenance.get("publication_time_source")
            != "visible_nbs_detail_title"
            or not isinstance(source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
            or source_sha256 != spec.source_sha256
        ):
            raise CoreCpiEvidenceError(
                f"{observed:%Y-%m} evidence does not match the fixed audited proof"
            )
        by_date[observed].append(
            _Candidate(
                date=observed,
                value=spec.value,
                release_date=release_date,
                available_at=available,
                source_url=source_url,
                status=spec.current_status,
            )
        )

    result: dict[dt.date, _Candidate] = {}
    for observed, candidates in by_date.items():
        if not candidates:
            raise CoreCpiEvidenceError(
                f"fixed repair lacks eligible evidence for {observed:%Y-%m}"
            )
        if len(candidates) != 1:
            raise CoreCpiEvidenceError(
                f"fixed repair has duplicate/conflicting evidence for {observed:%Y-%m}"
            )
        result[observed] = candidates[0]
    return result


def _same_content(point: DataPoint, candidate: _Candidate) -> bool:
    return (
        _decimal(point.value) == candidate.value
        and point.release_date == candidate.release_date
        and point.available_at == candidate.available_at
        and point.source_url == candidate.source_url
        and point.status == candidate.status
        and point.formula_version is None
    )


def _build_plan(
    db: Session,
    candidates: dict[dt.date, _Candidate],
    *,
    lock: bool,
) -> tuple[list[_Candidate], dict[str, object]]:
    indicator_stmt = select(Indicator).where(Indicator.code == CORE_CPI_CODE)
    if lock:
        indicator_stmt = indicator_stmt.with_for_update()
    if db.scalar(indicator_stmt) is None:
        raise CoreCpiCurrentRepairError("CN_CORE_CPI indicator is not seeded")

    point_stmt = select(DataPoint).where(
        DataPoint.indicator_code == CORE_CPI_CODE,
        DataPoint.date.in_(TARGET_DATES),
    )
    if lock:
        point_stmt = point_stmt.with_for_update()
    current = {row.date: row for row in db.scalars(point_stmt)}
    specs = {spec.observed: spec for spec in REPAIR_SPECS}
    plan: list[_Candidate] = []
    summary: dict[str, object] = {}
    for observed in TARGET_DATES:
        spec = specs[observed]
        candidate = candidates[observed]
        point = current.get(observed)
        if point is None:
            plan.append(candidate)
            summary[observed.strftime("%Y-%m")] = "insert"
            continue
        stored = _decimal(point.value)
        if stored not in spec.allowed_existing_values:
            raise CoreCpiCurrentConflictError(
                f"unexpected current value for {observed:%Y-%m}: {stored}"
            )
        if point.formula_version is not None:
            raise CoreCpiCurrentConflictError(
                f"direct core CPI has a formula version for {observed:%Y-%m}"
            )
        if _same_content(point, candidate):
            summary[observed.strftime("%Y-%m")] = "already_repaired"
        else:
            prior_vintage = db.scalar(
                select(DataPointVintage).where(
                    DataPointVintage.data_point_id == point.id,
                    DataPointVintage.version == point.version,
                )
            )
            if (
                prior_vintage is None
                or _decimal(prior_vintage.value) != stored
            ):
                raise CoreCpiCurrentConflictError(
                    "cannot revise current core CPI without a matching prior "
                    f"vintage for {observed:%Y-%m} version {point.version}"
                )
            plan.append(candidate)
            summary[observed.strftime("%Y-%m")] = (
                "correct_value_metadata_update" if stored == spec.value else "value_revision"
            )
    return plan, summary


def _frame(plan: list[_Candidate]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": item.date,
                "value": float(item.value),
                "release_date": item.release_date,
                "available_at": item.available_at,
                "source_url": item.source_url,
                "status": item.status,
                "formula_version": None,
            }
            for item in plan
        ]
    )


def _counts(db: Session) -> tuple[int, int]:
    points = int(
        db.scalar(
            select(func.count()).select_from(DataPoint).where(
                DataPoint.indicator_code == CORE_CPI_CODE
            )
        )
        or 0
    )
    vintages = int(
        db.scalar(
            select(func.count()).select_from(DataPointVintage).where(
                DataPointVintage.indicator_code == CORE_CPI_CODE
            )
        )
        or 0
    )
    return points, vintages


def repair_core_cpi_current(
    db: Session,
    evidence: pd.DataFrame,
    *,
    apply: bool = False,
) -> dict[str, object]:
    """Dry-run or atomically apply the two-key allowlisted current repair."""

    candidates = _normalize_evidence(evidence)
    try:
        before_points, before_vintages = _counts(db)
        plan, target_summary = _build_plan(db, candidates, lock=apply)
        report: dict[str, object] = {
            "mode": "apply" if apply else "dry_run",
            "ready": True,
            "authorized_periods": [date.strftime("%Y-%m") for date in TARGET_DATES],
            "planned_changes": len(plan),
            "target_summary": target_summary,
            "before": {
                "current_rows": before_points,
                "vintage_rows": before_vintages,
            },
        }
        if not apply:
            report["projected_after"] = {
                "current_rows": before_points
                + sum(1 for item in plan if item.date not in {
                    row.date
                    for row in db.scalars(
                        select(DataPoint).where(
                            DataPoint.indicator_code == CORE_CPI_CODE,
                            DataPoint.date.in_(TARGET_DATES),
                        )
                    )
                }),
                "vintage_rows": before_vintages + len(plan),
            }
            return report

        changed = 0
        if plan:
            changed = upsert_points(
                db, CORE_CPI_CODE, _frame(plan), commit=False
            )
            if changed != len(plan):
                raise CoreCpiCurrentRepairError(
                    f"concurrent repair changed the plan: planned={len(plan)}, stored={changed}"
                )
        db.commit()
        after_points, after_vintages = _counts(db)
        report["changed"] = changed
        report["after"] = {
            "current_rows": after_points,
            "vintage_rows": after_vintages,
        }
        return report
    except Exception:
        db.rollback()
        raise


__all__ = [
    "CORE_CPI_CODE",
    "CoreCpiCurrentConflictError",
    "CoreCpiCurrentRepairError",
    "CoreCpiEvidenceError",
    "REPAIR_SPECS",
    "TARGET_DATES",
    "repair_core_cpi_current",
]
