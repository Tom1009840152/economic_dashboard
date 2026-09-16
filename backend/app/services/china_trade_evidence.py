"""Auditable first-release evidence for China's monthly export growth.

The GACC preliminary archive is independent from the current-value transport.
This module accepts only official HTTPS pages, preserves initial values even
when they differ from today's snapshot, and assigns a conservative next-day
availability bound when a page publishes only a calendar date.  January-
February aggregate tables are deliberately not converted into monthly data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

import pandas as pd

from app.fetchers.china_cycle_data import (
    _GACC_RELEASE_BASE,
    _gacc_catalog_entries,
    _gacc_combined_period,
    _gacc_export_yoy,
    _gacc_period_date,
    _gacc_publication_metadata,
    _get_gacc_archive_text,
    _is_gacc_https_url,
    _valid_gacc_index_source,
    _valid_gacc_release_source,
)


TRADE_EVIDENCE_CODES = ("CN_EXPORTS",)
TRADE_REQUIRED_START = pd.Period("2020-03", freq="M")
_EVIDENCE_COLUMNS = (
    "date",
    "value",
    "release_date",
    "available_at",
    "source_url",
    "status",
    "formula_version",
    "provenance_json",
    "evidence_kind",
    "chain_verified",
    "availability_precision",
)


class TradeEvidenceCollectionError(RuntimeError):
    """The official archive could not be proven safe and complete enough."""


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_tls_verification_error(exc: BaseException) -> bool:
    markers = (
        "certificate verify failed",
        "certificateverifyfailed",
        "sslerror",
        "unable to get local issuer certificate",
        "certificate has expired",
    )
    return any(
        any(marker in f"{type(item).__name__}: {item}".lower() for marker in markers)
        for item in _exception_chain(exc)
    )


def _collection_failure(context: str, exc: BaseException) -> TradeEvidenceCollectionError:
    if _is_tls_verification_error(exc):
        return TradeEvidenceCollectionError(
            f"GACC TLS certificate verification failed while reading {context}; "
            "refusing trade evidence with TLS verification enabled"
        )
    return TradeEvidenceCollectionError(
        f"GACC official archive failed while reading {context}; refusing partial "
        f"trade evidence: {type(exc).__name__}: {exc}"
    )


def _index_catalog(*, page_count: int, start_page: int) -> tuple[tuple[str, str], ...]:
    found: dict[str, str] = {}
    for page in range(start_page, start_page + page_count):
        index_url = f"{_GACC_RELEASE_BASE}?ColumnId=1&page={page}"
        try:
            source = _get_gacc_archive_text(index_url, cache=False)
        except Exception as exc:
            raise _collection_failure(f"index page {page}", exc) from exc
        if not _valid_gacc_index_source(source):
            raise TradeEvidenceCollectionError(
                f"GACC index page {page} returned a soft-error or unrelated body; "
                "refusing partial trade evidence"
            )
        for source_url, title in _gacc_catalog_entries(source, index_url):
            prior = found.get(source_url)
            if prior is not None and prior != title:
                raise TradeEvidenceCollectionError(
                    f"GACC catalog has conflicting titles for {source_url}"
                )
            found[source_url] = title
    if not found:
        raise TradeEvidenceCollectionError(
            "GACC archive contained no official nationwide USD release pages"
        )
    return tuple(sorted(found.items()))


def _provenance(
    *,
    source: str,
    title: str,
    precision: str,
) -> str:
    return json.dumps(
        {
            "availability_policy": (
                "page_confirmed_exact_minute"
                if precision == "exact_minute"
                else "start_of_day_after_release_date"
            ),
            "collector": "gacc_trade_release_v1",
            "direct_official": True,
            "page_release_date_precision": (
                "minute" if precision == "exact_minute" else "date"
            ),
            "preliminary": True,
            "reported_scope": "single_month_yoy_usd",
            "reported_unit": "%",
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_title": title,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def collect_gacc_trade_evidence(
    *,
    page_count: int = 30,
    start_page: int = 1,
) -> dict[str, pd.DataFrame]:
    """Collect strict GACC preliminary releases without consulting current data."""

    if page_count < 1:
        raise ValueError("page_count must be at least 1")
    if start_page < 1:
        raise ValueError("start_page must be at least 1")

    rows: list[dict] = []
    for source_url, title in _index_catalog(
        page_count=page_count, start_page=start_page
    ):
        if not _is_gacc_https_url(source_url):
            raise TradeEvidenceCollectionError(
                f"GACC catalog emitted a non-official or non-HTTPS URL: {source_url}"
            )
        try:
            source = _get_gacc_archive_text(source_url)
        except Exception as exc:
            raise _collection_failure(f"detail {source_url}", exc) from exc
        if not _valid_gacc_release_source(source):
            raise TradeEvidenceCollectionError(
                f"GACC detail returned a soft-error or unrelated body: {source_url}"
            )

        combined = _gacc_combined_period(f"{title} {source}")
        observed = _gacc_period_date(title, source)
        if observed is None:
            if combined:
                continue
            raise TradeEvidenceCollectionError(
                f"GACC detail has no unambiguous observation month: {source_url}"
            )
        value = _gacc_export_yoy(source, title)
        if value is None:
            # Some pages label the release as February while the only value is
            # January-February cumulative.  It is a real publication, but not
            # a monthly CN_EXPORTS observation.
            if combined or observed.month in {1, 2}:
                continue
            raise TradeEvidenceCollectionError(
                f"GACC detail has no unique single-month export YoY: {source_url}"
            )
        if not math.isfinite(float(value)):
            raise TradeEvidenceCollectionError(
                f"GACC detail has a non-finite export YoY: {source_url}"
            )

        metadata = _gacc_publication_metadata(source, source_url)
        release_date = metadata.get("release_date")
        exact_available_at = metadata.get("available_at")
        if not isinstance(release_date, dt.date):
            raise TradeEvidenceCollectionError(
                f"GACC detail has no verifiable publication date: {source_url}"
            )
        if release_date < observed:
            raise TradeEvidenceCollectionError(
                f"GACC publication date precedes its observation month: {source_url}"
            )
        if exact_available_at is None:
            precision = "date_upper_bound"
            available_at = dt.datetime.combine(
                release_date + dt.timedelta(days=1), dt.time.min
            )
        else:
            if not isinstance(exact_available_at, dt.datetime):
                raise TradeEvidenceCollectionError(
                    f"GACC detail has invalid publication time: {source_url}"
                )
            precision = "exact_minute"
            available_at = exact_available_at.replace(second=0, microsecond=0)
            if available_at.date() != release_date:
                raise TradeEvidenceCollectionError(
                    f"GACC detail publication date/time disagree: {source_url}"
                )

        rows.append(
            {
                "date": observed,
                "value": round(float(value), 6),
                "release_date": release_date,
                "available_at": available_at,
                "source_url": source_url,
                "status": "published",
                "formula_version": None,
                "provenance_json": _provenance(
                    source=source, title=title, precision=precision
                ),
                "evidence_kind": "official_release",
                "chain_verified": True,
                "availability_precision": precision,
            }
        )

    by_semantic_key: dict[tuple, dict] = {}
    by_instant: dict[tuple[dt.date, dt.datetime], float] = {}
    for row in rows:
        instant = (row["date"], row["available_at"])
        prior_value = by_instant.get(instant)
        if prior_value is not None and round(prior_value, 6) != row["value"]:
            raise TradeEvidenceCollectionError(
                "GACC archive has conflicting export values for "
                f"{row['date']} at {row['available_at'].isoformat()}"
            )
        by_instant[instant] = row["value"]
        key = (
            row["date"],
            row["value"],
            row["available_at"],
            row["status"],
        )
        prior = by_semantic_key.get(key)
        if prior is None or row["source_url"] < prior["source_url"]:
            by_semantic_key[key] = row

    frame = pd.DataFrame(
        sorted(
            by_semantic_key.values(),
            key=lambda row: (row["date"], row["available_at"], row["source_url"]),
        ),
        columns=_EVIDENCE_COLUMNS,
    )
    return {"CN_EXPORTS": frame}


def expected_trade_observation_through(
    as_of: dt.date | None = None,
) -> pd.Period:
    """Return the latest single-month release conservatively expected by now."""

    effective = as_of or dt.date.today()
    current = pd.Period(effective, freq="M")
    expected = current - (1 if effective.day >= 15 else 2)
    return _latest_required_single_month(expected)


def _latest_required_single_month(period: pd.Period) -> pd.Period:
    if period.month in {1, 2}:
        return pd.Period(year=period.year - 1, month=12, freq="M")
    return period


def _required_periods(
    required_from: pd.Period, required_through: pd.Period
) -> set[pd.Period]:
    if required_through < required_from:
        return set()
    return {
        period
        for period in pd.period_range(required_from, required_through, freq="M")
        if period.month not in {1, 2}
    }


def validate_trade_evidence(
    evidence: dict[str, pd.DataFrame],
    *,
    expected_through: str | dt.date | pd.Period,
    required_from: str | dt.date | pd.Period = TRADE_REQUIRED_START,
) -> dict:
    """Validate provenance, continuity and freshness before any database write."""

    requested_expected = pd.Period(expected_through, freq="M")
    expected = _latest_required_single_month(requested_expected)
    start = pd.Period(required_from, freq="M")
    frame = evidence.get("CN_EXPORTS", pd.DataFrame())
    invalid: list[str] = []
    periods: set[pd.Period] = set()
    instants: dict[tuple[dt.date, dt.datetime], float] = {}

    for index, raw in enumerate(frame.to_dict("records")):
        try:
            observed = pd.Timestamp(raw.get("date")).date()
            value = float(raw.get("value"))
            release_date = pd.Timestamp(raw.get("release_date")).date()
            available_at = pd.Timestamp(raw.get("available_at")).to_pydatetime()
            source_url = str(raw.get("source_url") or "")
            precision = raw.get("availability_precision")
            if observed.day != 1 or not math.isfinite(value):
                raise ValueError("invalid observation/value")
            if release_date < observed:
                raise ValueError("release date precedes observation month")
            if available_at.tzinfo is not None:
                raise ValueError("timezone-aware publication time is unsupported")
            if not _is_gacc_https_url(source_url):
                raise ValueError("source is not official GACC HTTPS")
            if raw.get("status") != "published" or raw.get("formula_version") is not None:
                raise ValueError("direct release classification is invalid")
            if (
                raw.get("evidence_kind") != "official_release"
                or raw.get("chain_verified") is not True
            ):
                raise ValueError("evidence is not verified official release")
            if precision == "exact_minute":
                if available_at.date() != release_date or (
                    available_at.second or available_at.microsecond
                ):
                    raise ValueError("exact minute does not match release date")
            elif precision == "date_upper_bound":
                expected_bound = dt.datetime.combine(
                    release_date + dt.timedelta(days=1), dt.time.min
                )
                if available_at != expected_bound:
                    raise ValueError("date upper bound is not next-day midnight")
            else:
                raise ValueError("unsupported availability precision")
            provenance = json.loads(str(raw.get("provenance_json") or ""))
            expected_policy = (
                "page_confirmed_exact_minute"
                if precision == "exact_minute"
                else "start_of_day_after_release_date"
            )
            source_sha256 = (
                provenance.get("source_sha256")
                if isinstance(provenance, dict)
                else None
            )
            if (
                not isinstance(provenance, dict)
                or provenance.get("collector") != "gacc_trade_release_v1"
                or provenance.get("direct_official") is not True
                or provenance.get("preliminary") is not True
                or provenance.get("reported_scope") != "single_month_yoy_usd"
                or provenance.get("reported_unit") != "%"
                or provenance.get("availability_policy") != expected_policy
                or not isinstance(source_sha256, str)
                or len(source_sha256) != 64
                or any(character not in "0123456789abcdef" for character in source_sha256)
            ):
                raise ValueError("missing GACC provenance")
            instant = (observed, available_at)
            prior = instants.get(instant)
            if prior is not None and round(prior, 6) != round(value, 6):
                raise ValueError("conflicting value at one release instant")
            instants[instant] = value
            periods.add(pd.Period(observed, freq="M"))
        except Exception as exc:
            invalid.append(f"row {index}: {exc}")

    required = _required_periods(start, expected)
    missing = [str(period) for period in sorted(required - periods)]
    latest = max(periods) if periods else None
    fresh = latest is not None and latest >= expected
    return {
        "ready": bool(not invalid and not missing and fresh),
        "required_from": str(start),
        "required_through": str(expected),
        "expected_through": str(expected),
        "requested_expected_through": str(requested_expected),
        "january_february_policy": "not_required_without_single_month_release",
        "latest_observation": str(latest) if latest is not None else None,
        "fresh_enough": fresh,
        "rows": int(len(frame)),
        "missing": missing,
        "invalid": invalid,
    }


def validate_production_trade_evidence(
    evidence: dict[str, pd.DataFrame],
) -> dict:
    """Apply the non-overridable production continuity and freshness gate.

    ``validate_trade_evidence`` remains parameterized so collectors and unit
    tests can explain a particular archive slice.  Any database writer must
    use this wrapper: callers cannot move the start date forward or move the
    freshness boundary backward.
    """

    return validate_trade_evidence(
        evidence,
        expected_through=expected_trade_observation_through(),
        required_from=TRADE_REQUIRED_START,
    )


__all__ = [
    "TRADE_EVIDENCE_CODES",
    "TRADE_REQUIRED_START",
    "TradeEvidenceCollectionError",
    "collect_gacc_trade_evidence",
    "expected_trade_observation_through",
    "validate_production_trade_evidence",
    "validate_trade_evidence",
]
