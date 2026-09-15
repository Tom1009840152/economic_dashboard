"""Append-only storage for independently verified official-release evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Indicator, ReleaseEvidence


_VALUE_QUANTUM = Decimal("0.000001")
_REQUIRED_COLUMNS = frozenset(
    {
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
        "evidence_kind",
        "chain_verified",
        "availability_precision",
    }
)
EVIDENCE_KINDS = frozenset(
    {
        "official_release",
        "official_distribution_mirror",
    }
)
AVAILABILITY_PRECISIONS = frozenset({"exact_minute", "date_upper_bound"})


class ReleaseEvidenceConflictError(ValueError):
    """The same release instant asserts incompatible official semantics."""


@dataclass(frozen=True, slots=True)
class _EvidenceCandidate:
    evidence_key: str
    date: date
    value: Decimal
    release_date: date | None
    available_at: datetime
    source_url: str
    status: str
    formula_version: str | None
    provenance_json: str | None
    evidence_kind: str
    chain_verified: bool
    availability_precision: str


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    # ``pandas.isna`` returns ``numpy.bool_`` for several scalar pandas/numpy
    # values and an array for container-like values.  Scalar booleans are safe
    # to coerce; arrays deliberately fall through as non-missing here.
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _normalized_value(value: object) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(_VALUE_QUANTUM)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid release evidence value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid release evidence value: {value!r}")
    return result


def _normalized_date(value: object, field: str, *, nullable: bool = False) -> date | None:
    if _is_missing(value):
        if nullable:
            return None
        raise ValueError(f"release evidence {field} must not be empty")
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid release evidence {field}: {value!r}") from exc
    if pd.isna(result):
        if nullable:
            return None
        raise ValueError(f"release evidence {field} must not be empty")
    return result.date()


def _normalized_available_at(value: object) -> datetime:
    if _is_missing(value):
        raise ValueError("release evidence available_at must not be empty")
    if not isinstance(value, datetime):
        # A release date is not a release instant.  Accepting ``date`` or a
        # date-only string would make pandas synthesize midnight and could let
        # the evidence enter an earlier as-of decision than the source proves.
        raise ValueError(
            "release evidence available_at must be a datetime with an explicit time"
        )
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid release evidence available_at: {value!r}") from exc
    if pd.isna(result):
        raise ValueError("release evidence available_at must not be empty")
    if result.tzinfo is not None:
        # A3 still stores and compares Chinese source timestamps as naive
        # Asia/Shanghai wall clocks.  Silently converting an aware +08 value
        # to UTC would make a 09:30 release appear at 01:30 and leak it into an
        # earlier as-of snapshot.  D8 will introduce an explicit UTC contract;
        # until then, reject mixed clock semantics at the storage boundary.
        raise ValueError(
            "timezone-aware release evidence is unsupported; provide the "
            "official source-local naive timestamp"
        )
    # SQLAlchemy's portable DateTime maps to second-resolution DATETIME on the
    # production MySQL database.  Canonicalize before hashing so a round trip
    # cannot turn one semantic release into a second evidence key.
    return result.to_pydatetime().replace(microsecond=0)


def _normalized_text(
    value: object,
    field: str,
    *,
    limit: int,
    nullable: bool = False,
) -> str | None:
    if _is_missing(value):
        if nullable:
            return None
        raise ValueError(f"release evidence {field} must not be empty")
    result = str(value).strip()
    if not result:
        if nullable:
            return None
        raise ValueError(f"release evidence {field} must not be empty")
    if len(result) > limit:
        raise ValueError(f"release evidence {field} exceeds {limit} characters")
    return result


def _normalized_source_url(value: object) -> str:
    result = _normalized_text(value, "source_url", limit=512)
    assert result is not None
    try:
        parsed = urlparse(result)
    except ValueError as exc:
        raise ValueError(f"invalid release evidence source_url: {value!r}") from exc
    # Historical official releases are occasionally still addressed over HTTP.
    # Source-specific collectors are responsible for the official-domain
    # allowlist; this generic storage layer only requires an absolute web URL.
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("release evidence source_url must be an absolute HTTP(S) URL")
    return result


def _normalized_provenance(value: object) -> str | None:
    if _is_missing(value):
        return None
    try:
        payload = json.loads(value) if isinstance(value, str) else value
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("release evidence provenance_json must contain valid JSON") from exc


def _normalized_chain_verified(value: object) -> bool:
    # Do not accept truthy strings/integers: verification is a deliberate
    # assertion by the source-specific collector, not a storage default.
    if isinstance(value, bool):
        return value
    if type(value).__module__ == "numpy" and type(value).__name__ == "bool_":
        return bool(value)
    raise ValueError("release evidence chain_verified must be an explicit boolean")


def _semantic_key(
    code: str,
    observed: date,
    value: Decimal,
    available_at: datetime,
    formula_version: str | None,
    status: str,
) -> str:
    payload = json.dumps(
        {
            "available_at": available_at.isoformat(timespec="seconds"),
            "code": code,
            "date": observed.isoformat(),
            "formula_version": formula_version,
            "status": status,
            "value": format(value, "f"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate(code: str, row, columns: set[str]) -> _EvidenceCandidate:
    if "indicator_code" in columns:
        row_code = _normalized_text(
            getattr(row, "indicator_code"),
            "indicator_code",
            limit=32,
            nullable=True,
        )
        if row_code is not None and row_code != code:
            raise ValueError(
                f"release evidence indicator_code {row_code!r} does not match {code!r}"
            )
    observed = _normalized_date(getattr(row, "date"), "date")
    assert observed is not None
    value = _normalized_value(getattr(row, "value"))
    release_date = _normalized_date(
        getattr(row, "release_date"), "release_date"
    )
    assert release_date is not None
    available_at = _normalized_available_at(getattr(row, "available_at"))
    evidence_kind = _normalized_text(
        getattr(row, "evidence_kind"), "evidence_kind", limit=32
    )
    assert evidence_kind is not None
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(f"unsupported release evidence kind: {evidence_kind!r}")
    chain_verified = _normalized_chain_verified(getattr(row, "chain_verified"))
    availability_precision = _normalized_text(
        getattr(row, "availability_precision"),
        "availability_precision",
        limit=32,
    )
    assert availability_precision is not None
    if availability_precision not in AVAILABILITY_PRECISIONS:
        raise ValueError(
            "unsupported release evidence availability_precision: "
            f"{availability_precision!r}"
        )
    if availability_precision == "exact_minute":
        if release_date != available_at.date():
            raise ValueError(
                "exact_minute release evidence requires release_date to equal "
                "the available_at calendar date"
            )
    else:
        conservative_upper_bound = datetime.combine(
            release_date + pd.Timedelta(days=1), datetime.min.time()
        )
        if available_at != conservative_upper_bound:
            raise ValueError(
                "date_upper_bound release evidence requires available_at to equal "
                "release_date + 1 day at 00:00"
            )
    source_url = _normalized_source_url(getattr(row, "source_url"))
    status = _normalized_text(getattr(row, "status"), "status", limit=32)
    assert status is not None
    formula_version = _normalized_text(
        getattr(row, "formula_version", None),
        "formula_version",
        limit=32,
        nullable=True,
    )
    provenance_json = _normalized_provenance(
        getattr(row, "provenance_json", None)
    )
    return _EvidenceCandidate(
        evidence_key=_semantic_key(
            code, observed, value, available_at, formula_version, status
        ),
        date=observed,
        value=value,
        release_date=release_date,
        available_at=available_at,
        source_url=source_url,
        status=status,
        formula_version=formula_version,
        provenance_json=provenance_json,
        evidence_kind=evidence_kind,
        chain_verified=chain_verified,
        availability_precision=availability_precision,
    )


def _conflicts(
    left_value: Decimal,
    left_formula: str | None,
    left_status: str,
    left_kind: str,
    left_verified: bool,
    left_precision: str,
    right_value: Decimal,
    right_formula: str | None,
    right_status: str,
    right_kind: str,
    right_verified: bool,
    right_precision: str,
) -> bool:
    return (
        left_value != right_value
        or left_formula != right_formula
        or left_status != right_status
        or left_kind != right_kind
        or left_verified != right_verified
        or left_precision != right_precision
    )


def upsert_release_evidence(
    db: Session,
    code: str,
    df: pd.DataFrame,
    commit: bool = True,
) -> int:
    """Append new official evidence without touching ``DataPoint``.

    Semantic identity is the SHA-256 of code, observation date, six-decimal
    value, availability instant, formula version, and status.  Replaying the
    same evidence is a no-op.  Two records for the same observation and exact
    availability instant may not disagree on value, formula version, or status.

    Versions are per-observation append numbers, not chronology.  Callers may
    therefore discover older releases after newer ones and still retain both;
    chronological replay must order by ``available_at``.
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError("release evidence must be provided as a pandas DataFrame")
    if df.empty:
        return 0
    normalized_code = _normalized_text(code, "indicator_code", limit=32)
    assert normalized_code is not None
    columns = set(df.columns)
    missing = sorted(_REQUIRED_COLUMNS - columns)
    if missing:
        raise ValueError(f"release evidence is missing columns: {missing}")

    candidates_by_key: dict[str, _EvidenceCandidate] = {}
    incoming_instants: dict[
        tuple[date, datetime], tuple[Decimal, str | None, str, str, bool, str]
    ] = {}
    for row in df.itertuples(index=False):
        candidate = _candidate(normalized_code, row, columns)
        instant = (candidate.date, candidate.available_at)
        prior = incoming_instants.get(instant)
        if prior is not None and _conflicts(
            prior[0],
            prior[1],
            prior[2],
            prior[3],
            prior[4],
            prior[5],
            candidate.value,
            candidate.formula_version,
            candidate.status,
            candidate.evidence_kind,
            candidate.chain_verified,
            candidate.availability_precision,
        ):
            raise ReleaseEvidenceConflictError(
                f"conflicting release evidence for {normalized_code} "
                f"{candidate.date.isoformat()} at {candidate.available_at.isoformat()}"
            )
        incoming_instants[instant] = (
            candidate.value,
            candidate.formula_version,
            candidate.status,
            candidate.evidence_kind,
            candidate.chain_verified,
            candidate.availability_precision,
        )
        prior_candidate = candidates_by_key.get(candidate.evidence_key)
        if prior_candidate is None or (
            candidate.source_url,
            candidate.provenance_json or "",
        ) < (
            prior_candidate.source_url,
            prior_candidate.provenance_json or "",
        ):
            # Retrieval metadata is not semantic identity, but choosing it must
            # still be deterministic when the same release appears twice.
            candidates_by_key[candidate.evidence_key] = candidate

    candidates = sorted(
        candidates_by_key.values(),
        key=lambda item: (item.date, item.available_at, item.evidence_key),
    )
    observed_dates = sorted({candidate.date for candidate in candidates})

    try:
        with db.no_autoflush:
            indicator_exists = db.scalar(
                select(Indicator)
                .where(Indicator.code == normalized_code)
                .with_for_update()
            )
            if indicator_exists is None:
                raise ValueError(
                    f"release evidence indicator does not exist: {normalized_code}"
                )
            existing = list(
                db.scalars(
                    select(ReleaseEvidence).where(
                        ReleaseEvidence.indicator_code == normalized_code,
                        ReleaseEvidence.date.in_(observed_dates),
                    )
                )
            )

        existing_keys = {row.evidence_key for row in existing}
        existing_instants: dict[
            tuple[date, datetime],
            list[tuple[Decimal, str | None, str, str, bool, str]],
        ] = {}
        max_versions: dict[date, int] = {}
        for row in existing:
            instant = (row.date, row.available_at.replace(microsecond=0))
            existing_instants.setdefault(instant, []).append(
                (
                    Decimal(row.value).quantize(_VALUE_QUANTUM),
                    row.formula_version,
                    row.status,
                    row.evidence_kind,
                    row.chain_verified,
                    row.availability_precision,
                )
            )
            max_versions[row.date] = max(max_versions.get(row.date, 0), row.version)

        pending: list[_EvidenceCandidate] = []
        for candidate in candidates:
            for (
                existing_value,
                existing_formula,
                existing_status,
                existing_kind,
                existing_verified,
                existing_precision,
            ) in existing_instants.get(
                (candidate.date, candidate.available_at), []
            ):
                if _conflicts(
                    existing_value,
                    existing_formula,
                    existing_status,
                    existing_kind,
                    existing_verified,
                    existing_precision,
                    candidate.value,
                    candidate.formula_version,
                    candidate.status,
                    candidate.evidence_kind,
                    candidate.chain_verified,
                    candidate.availability_precision,
                ):
                    raise ReleaseEvidenceConflictError(
                        f"conflicting release evidence for {normalized_code} "
                        f"{candidate.date.isoformat()} at "
                        f"{candidate.available_at.isoformat()}"
                    )
            if candidate.evidence_key not in existing_keys:
                pending.append(candidate)

        rows: list[ReleaseEvidence] = []
        for candidate in pending:
            version = max_versions.get(candidate.date, 0) + 1
            max_versions[candidate.date] = version
            rows.append(
                ReleaseEvidence(
                    evidence_key=candidate.evidence_key,
                    indicator_code=normalized_code,
                    date=candidate.date,
                    value=candidate.value,
                    release_date=candidate.release_date,
                    available_at=candidate.available_at,
                    source_url=candidate.source_url,
                    status=candidate.status,
                    formula_version=candidate.formula_version,
                    provenance_json=candidate.provenance_json,
                    evidence_kind=candidate.evidence_kind,
                    chain_verified=candidate.chain_verified,
                    availability_precision=candidate.availability_precision,
                    version=version,
                )
            )
        if rows:
            db.add_all(rows)
            db.flush()
        if commit:
            db.commit()
        return len(rows)
    except SQLAlchemyError:
        # A failed flush leaves a SQLAlchemy session unusable even when the
        # caller asked to manage the successful transaction itself.
        db.rollback()
        raise
    except Exception:
        if commit:
            db.rollback()
        raise


__all__ = [
    "AVAILABILITY_PRECISIONS",
    "EVIDENCE_KINDS",
    "ReleaseEvidenceConflictError",
    "upsert_release_evidence",
]
