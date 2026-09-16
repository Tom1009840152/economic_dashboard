"""Strict NBS release evidence for China's core CPI and PPI.

Core CPI comes from the official monthly CPI table; PPI comes from the monthly
CPI/PPI commentary.  The sources and parsers are deliberately isolated.  All
timestamps must be an exact minute rendered in the NBS title/byline area.
Legacy commentary parsing remains available for auditing existing evidence,
but the integrated collector never emits commentary-derived core CPI.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import urljoin

import pandas as pd
import requests
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.fetchers.china_cycle_data import (
    _is_nbs_https_url,
)
from app.fetchers.nbs_cycle import (
    inflation_title_observation,
    parse_core_cpi_values,
    parse_ppi_value,
)
from app.services.china_core_cpi_table_evidence import (
    CORE_CPI_ARCHIVE_SHARDS,
    CORE_CPI_REQUIRED_FROM,
    CORE_CPI_TABLE_CACHE_VERSION,
    CORE_CPI_TABLE_PARSER_VERSION,
    DEFAULT_CORE_CPI_INDEX_PAGE_COUNT,
    NbsCoreCpiFetch,
    collect_core_cpi_table_evidence,
    core_cpi_table_assertion_digest,
    cpi_release_title_observation,
    is_core_cpi_yoy_column_label,
)
from app.services.release_evidence import upsert_release_evidence


INFLATION_EVIDENCE_CODES = ("CN_CORE_CPI", "CN_PPI")
NBS_INTERPRETATION_BASE = "https://www.stats.gov.cn/sj/sjjd/"
PPI_REQUIRED_FROM = pd.Period("2016-08", freq="M")
DEFAULT_INFLATION_INDEX_PAGE_COUNT = 141
DEFAULT_RECENT_INFLATION_INDEX_PAGE_COUNT = 14
PARSER_VERSION = "nbs_inflation_release_v2"
_NBS_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": NBS_INTERPRETATION_BASE,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_NBS_INFLATION_CACHE = (
    Path(__file__).resolve().parents[2]
    / ".cache"
    / "nbs-inflation-evidence-v2"
)
_NBS_INFLATION_CACHE_VERSION = 2
_NBS_INFLATION_REQUEST_INTERVAL = 0.45
_nbs_inflation_request_lock = threading.Lock()
_last_nbs_inflation_request = 0.0

_TITLE_PATTERN = re.compile(
    r"解读20\d{2}年\d{1,2}月份CPI(?:和|、)PPI数据"
)
_OUTPUT_COLUMNS = (
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


class InflationEvidenceError(ValueError):
    """The NBS page cannot prove an eligible inflation observation."""


class InflationCoverageError(InflationEvidenceError):
    """The requested evidence chain has an unfilled month."""


@dataclass(frozen=True, slots=True)
class InflationArticleRef:
    observation_date: dt.date
    title: str
    source_url: str


@dataclass(frozen=True, slots=True)
class _NbsInflationFetch:
    request_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    source: str


def _paced_nbs_get(url: str):
    """Issue one real request, serializing starts at least 0.45s apart."""

    global _last_nbs_inflation_request
    with _nbs_inflation_request_lock:
        wait_for = _NBS_INFLATION_REQUEST_INTERVAL - (
            time.monotonic() - _last_nbs_inflation_request
        )
        if wait_for > 0:
            time.sleep(wait_for)
        try:
            return requests.get(
                url,
                headers=_NBS_HEADERS,
                timeout=30,
                allow_redirects=False,
            )
        finally:
            # A transport exception still consumed a real request attempt and
            # must not permit the retry path to burst immediately.
            _last_nbs_inflation_request = time.monotonic()


def _fetch_nbs_inflation_page(url: str) -> _NbsInflationFetch:
    current = url
    redirect_chain = [url]
    for _ in range(6):
        if not _is_nbs_https_url(current):
            raise InflationEvidenceError(
                f"refusing NBS redirect outside official HTTPS: {current}"
            )
        response = _paced_nbs_get(current)
        if response.status_code == 404:
            raise FileNotFoundError(current)
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("Location")
            if not location:
                raise InflationEvidenceError(
                    "NBS redirect is missing a Location header"
                )
            redirected = urljoin(current, location)
            if not _is_nbs_https_url(redirected):
                raise InflationEvidenceError(
                    f"refusing NBS redirect outside official HTTPS: {redirected}"
                )
            redirect_chain.append(redirected)
            current = redirected
            continue
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        source = response.text
        if "Please enable JavaScript" in source:
            raise InflationEvidenceError("NBS returned a browser challenge page")
        return _NbsInflationFetch(
            request_url=url,
            final_url=current,
            redirect_chain=tuple(redirect_chain),
            source=source,
        )
    raise InflationEvidenceError("NBS archive exceeded the redirect limit")


def _request_nbs_inflation_text(url: str) -> str:
    """Fetch NBS HTML while validating every redirect hop."""

    return _fetch_nbs_inflation_page(url).source


def _nbs_inflation_publication_metadata(
    source: str, source_url: str
) -> dict[str, object]:
    """Accept an exact minute only from the rendered title/byline area."""

    if not _is_nbs_https_url(source_url):
        raise InflationEvidenceError("NBS inflation release must use official HTTPS")
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError) as exc:
        raise InflationEvidenceError("invalid NBS inflation release HTML") from exc
    visible = " ".join(
        " ".join(node.text_content().split())
        for node in document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' detail-title-des ')]//p | //*[@id='shijian']"
        )
    )
    patterns = (
        re.compile(
            r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
            r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
        ),
        re.compile(
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日?\s+"
            r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
        ),
    )
    found: set[dt.datetime] = set()
    for pattern in patterns:
        for match in pattern.finditer(visible):
            if match.group(6) is not None and int(match.group(6)) != 0:
                raise InflationEvidenceError(
                    "NBS inflation publication time is not minute-precision"
                )
            try:
                found.add(
                    dt.datetime(
                        *(int(match.group(index)) for index in range(1, 6))
                    )
                )
            except ValueError as exc:
                raise InflationEvidenceError(
                    "invalid visible NBS publication minute"
                ) from exc
    if not found:
        return {
            "release_date": None,
            "available_at": None,
            "source_url": source_url,
            "publication_time_source": None,
        }
    if len(found) != 1:
        raise InflationEvidenceError(
            f"conflicting visible NBS publication minutes: {sorted(found)!r}"
        )
    published = next(iter(found)).replace(microsecond=0)
    return {
        "release_date": published.date(),
        "available_at": published,
        "source_url": source_url,
        "publication_time_source": "visible_nbs_detail_title",
        "source_sha256": hashlib.sha256(
            source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        ).hexdigest(),
    }


def _compact_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _valid_nbs_inflation_detail(source: str) -> bool:
    """Accept only a full, non-challenge monthly CPI/PPI commentary page."""

    if "Please enable JavaScript" in source or "<html" not in source.lower():
        return False
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError):
        return False
    observations = {
        observed
        for node in document.xpath("//h1|//title|//*[@class='detail-title']")
        if (
            observed := inflation_title_observation(
                _compact_text(node.text_content())
            )
        )
        is not None
    }
    return len(observations) == 1


def _nbs_inflation_cache_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return (
        _NBS_INFLATION_CACHE / f"{digest}.html",
        _NBS_INFLATION_CACHE / f"{digest}.json",
    )


def _read_nbs_inflation_cache(url: str) -> str | None:
    source_path, metadata_path = _nbs_inflation_cache_paths(url)
    if not source_path.is_file() or not metadata_path.is_file():
        return None
    try:
        source_bytes = source_path.read_bytes()
        source = source_bytes.decode("utf-8")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    chain = metadata.get("redirect_chain")
    final_url = metadata.get("final_url")
    if (
        metadata.get("cache_version") != _NBS_INFLATION_CACHE_VERSION
        or metadata.get("request_url") != url
        or not isinstance(chain, list)
        or not chain
        or len(chain) > 6
        or chain[0] != url
        or chain[-1] != final_url
        or not all(_is_nbs_https_url(item) for item in chain)
        or not _is_nbs_https_url(final_url)
        or metadata.get("sha256")
        != hashlib.sha256(source_bytes).hexdigest()
        or not _valid_nbs_inflation_detail(source)
    ):
        return None
    return source


def _write_nbs_inflation_cache(fetch: _NbsInflationFetch) -> None:
    if (
        not _is_nbs_https_url(fetch.request_url)
        or not _is_nbs_https_url(fetch.final_url)
        or not fetch.redirect_chain
        or fetch.redirect_chain[0] != fetch.request_url
        or fetch.redirect_chain[-1] != fetch.final_url
        or not all(_is_nbs_https_url(item) for item in fetch.redirect_chain)
        or not _valid_nbs_inflation_detail(fetch.source)
    ):
        raise InflationEvidenceError("refusing to cache unverified NBS detail")
    source_bytes = fetch.source.encode("utf-8")
    metadata = {
        "cache_version": _NBS_INFLATION_CACHE_VERSION,
        "request_url": fetch.request_url,
        "final_url": fetch.final_url,
        "redirect_chain": list(fetch.redirect_chain),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    source_path, metadata_path = _nbs_inflation_cache_paths(fetch.request_url)
    _NBS_INFLATION_CACHE.mkdir(parents=True, exist_ok=True)
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    temporary_source = source_path.with_suffix(source_path.suffix + suffix)
    temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + suffix)
    try:
        temporary_source.write_bytes(source_bytes)
        temporary_metadata.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        # Metadata is the commit marker. A crash between replacements leaves a
        # mismatched digest which the reader rejects rather than trusting.
        temporary_source.replace(source_path)
        temporary_metadata.replace(metadata_path)
    finally:
        temporary_source.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)


def _get_nbs_inflation_archive_text(url: str, *, cache: bool) -> str:
    """Fetch a mutable index or a resumably cached verified detail page."""

    if not _is_nbs_https_url(url):
        raise InflationEvidenceError("NBS archive URL must use official HTTPS")
    if cache:
        cached = _read_nbs_inflation_cache(url)
        if cached is not None:
            return cached

    attempts = 4 if cache else 2
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            fetched = _fetch_nbs_inflation_page(url)
            if cache:
                _write_nbs_inflation_cache(fetched)
            return fetched.source
        except FileNotFoundError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(min(2**attempt, 4))
    assert last_error is not None
    raise last_error


def inflation_index_url(page: int, *, archive_shard: int = 0) -> str:
    if page < 0:
        raise ValueError("NBS inflation index page cannot be negative")
    if archive_shard < 0 or archive_shard % 1000:
        raise ValueError("NBS archive shard must be zero or a multiple of 1000")
    if archive_shard:
        name = (
            f"index_{archive_shard}.html"
            if page == 0
            else f"index_{archive_shard}_{page}.html"
        )
    else:
        name = "" if page == 0 else f"index_{page}.html"
    return urljoin(NBS_INTERPRETATION_BASE, name)


def parse_inflation_index(source: str, source_url: str) -> list[InflationArticleRef]:
    """Parse one NBS commentary index and reject matching off-domain links."""

    if not _is_nbs_https_url(source_url):
        raise InflationEvidenceError("NBS inflation index must use official HTTPS")
    if "Please enable JavaScript" in source:
        raise InflationEvidenceError("NBS inflation index is a challenge page")
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError) as exc:
        raise InflationEvidenceError("invalid NBS inflation index HTML") from exc

    by_url: dict[str, InflationArticleRef] = {}
    for anchor in document.xpath("//a[@href]"):
        title = _compact_text(anchor.get("title") or anchor.text_content())
        if not _TITLE_PATTERN.search(title):
            continue
        observed = inflation_title_observation(title)
        if observed is None:
            raise InflationEvidenceError(f"invalid monthly inflation title: {title!r}")
        article_url = urljoin(source_url, str(anchor.get("href")))
        if not _is_nbs_https_url(article_url):
            raise InflationEvidenceError(
                f"matching NBS inflation link is not official HTTPS: {article_url!r}"
            )
        by_url[article_url] = InflationArticleRef(observed, title, article_url)
    return sorted(
        by_url.values(), key=lambda item: (item.observation_date, item.source_url)
    )


def _article_subject_is_present(source: str, observed: dt.date) -> bool:
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError):
        return False
    candidates = [
        _compact_text(node.text_content())
        for node in document.xpath("//h1|//title|//*[@class='detail-title']")
    ]
    return any(
        inflation_title_observation(text) == observed for text in candidates if text
    )


def _evidence_row(
    *,
    observed: dt.date,
    value: float,
    metadata: dict,
    source_url: str,
    title: str,
    status: str,
    semantics: str,
) -> dict:
    available_at = metadata.get("available_at")
    release_date = metadata.get("release_date")
    if not isinstance(available_at, dt.datetime) or release_date != available_at.date():
        raise InflationEvidenceError(
            f"NBS inflation release lacks an exact publication minute: {source_url}"
        )
    if available_at.tzinfo is not None:
        raise InflationEvidenceError("NBS source-local publication time must be naive")
    if metadata.get("publication_time_source") != "visible_nbs_detail_title":
        raise InflationEvidenceError(
            "NBS exact minute is not backed by a visible title/byline timestamp"
        )
    return {
        "date": observed,
        "value": round(float(value), 6),
        "release_date": release_date,
        "available_at": available_at.replace(microsecond=0),
        "source_url": source_url,
        "status": status,
        "formula_version": None,
        "provenance_json": {
            "article_observation": (
                inflation_title_observation(title).isoformat()
                if inflation_title_observation(title)
                else None
            ),
            "evidence_semantics": semantics,
            "parser_version": PARSER_VERSION,
            "publication_time_source": "visible_nbs_detail_title",
            "source_sha256": metadata.get("source_sha256"),
            "title": title,
        },
        "evidence_kind": "official_release",
        "chain_verified": True,
        "availability_precision": "exact_minute",
    }


def parse_inflation_release(
    source: str,
    *,
    title: str,
    source_url: str,
    include_core: bool = True,
) -> dict[str, pd.DataFrame]:
    """Parse one official monthly commentary into isolated evidence sets.

    ``include_core=False`` is the production PPI-only path.  The default keeps
    legacy core-commentary parsing available for audits and existing callers.
    """

    if not _is_nbs_https_url(source_url):
        raise InflationEvidenceError("NBS inflation release must use official HTTPS")
    if "Please enable JavaScript" in source:
        raise InflationEvidenceError("NBS inflation release is a challenge page")
    observed = inflation_title_observation(title)
    if observed is None:
        raise InflationEvidenceError("NBS inflation release title is not monthly")
    if not _article_subject_is_present(source, observed):
        raise InflationEvidenceError("NBS page title does not match its index subject")

    metadata = _nbs_inflation_publication_metadata(source, source_url)
    ppi = parse_ppi_value(source)
    core_values = parse_core_cpi_values(source, observed) if include_core else {}
    rows: dict[str, list[dict]] = {code: [] for code in INFLATION_EVIDENCE_CODES}

    if ppi is not None:
        rows["CN_PPI"].append(
            _evidence_row(
                observed=observed,
                value=ppi,
                metadata=metadata,
                source_url=source_url,
                title=title,
                status="published",
                semantics="subject_month_release",
            )
        )

    prior = (pd.Period(observed, freq="M") - 1).start_time.date()
    for core_observed, value in core_values.items():
        if core_observed not in {observed, prior}:
            raise InflationEvidenceError(
                "core CPI parser returned a month outside the subject/prior-month scope"
            )
        is_current = core_observed == observed
        rows["CN_CORE_CPI"].append(
            _evidence_row(
                observed=core_observed,
                value=value,
                metadata=metadata,
                source_url=source_url,
                title=title,
                status="published" if is_current else "published_later_reference",
                semantics=(
                    "subject_month_release"
                    if is_current
                    else "later_article_explicit_reference"
                ),
            )
        )

    return {
        code: pd.DataFrame(rows[code], columns=_OUTPUT_COLUMNS)
        for code in INFLATION_EVIDENCE_CODES
    }


def _merge_ppi_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    nonempty = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not nonempty:
        return pd.DataFrame(columns=_OUTPUT_COLUMNS)
    result = pd.concat(nonempty, ignore_index=True)
    selected: list[pd.Series] = []
    for observed, assertions in result.groupby("date", sort=True, dropna=False):
        signatures: set[tuple[object, ...]] = set()
        for _, row in assertions.iterrows():
            provenance = _provenance_value(row.get("provenance_json"))
            signatures.add(
                (
                    round(float(row.get("value")), 12),
                    row.get("available_at"),
                    provenance.get("source_sha256"),
                    provenance.get("evidence_semantics"),
                    row.get("status"),
                )
            )
        if len(signatures) != 1:
            raise InflationEvidenceError(
                "conflicting or non-identical PPI subject-month releases for "
                f"{observed}"
            )
        selected.append(
            assertions.sort_values("source_url", kind="stable").iloc[0]
        )
    deduplicated = pd.DataFrame(selected, columns=result.columns)
    return deduplicated.sort_values(
        ["date", "available_at", "source_url"], kind="stable"
    ).reset_index(drop=True)


def collect_inflation_release_evidence(
    *,
    page_count: int = DEFAULT_INFLATION_INDEX_PAGE_COUNT,
    start_page: int = 0,
    archive_shards: tuple[int, ...] = (0,),
    fetch_index: Callable[[str], str] | None = None,
    fetch_release: Callable[[str], str] | None = None,
    core_page_count: int = DEFAULT_CORE_CPI_INDEX_PAGE_COUNT,
    core_start_page: int = 0,
    core_archive_shards: tuple[int, ...] = CORE_CPI_ARCHIVE_SHARDS,
    fetch_core_index: Callable[[str], str] | None = None,
    fetch_core_release: Callable[[str], str | NbsCoreCpiFetch] | None = None,
) -> dict[str, pd.DataFrame]:
    """Collect table-backed core CPI and commentary-backed PPI evidence.

    Core CPI is intentionally ignored in commentary pages.  This prevents the
    same release minute from entering the batch twice through prose and the
    canonical monthly CPI table, while PPI retains its dedicated prose parser.
    """

    if page_count < 1 or start_page < 0:
        raise ValueError("invalid NBS inflation archive page range")
    if not archive_shards:
        raise ValueError("at least one NBS inflation archive shard is required")
    index_loader = fetch_index or (
        lambda url: _get_nbs_inflation_archive_text(url, cache=False)
    )
    release_loader = fetch_release or (
        lambda url: _get_nbs_inflation_archive_text(url, cache=True)
    )

    refs: dict[str, InflationArticleRef] = {}
    successful_indexes = 0
    for shard in archive_shards:
        for page in range(start_page, start_page + page_count):
            url = inflation_index_url(page, archive_shard=shard)
            try:
                source = index_loader(url)
            except FileNotFoundError:
                # The migrated NBS commentary archive has real numbered gaps
                # (for example index_67 is absent while index_68 and index_100
                # exist).  The caller supplies a bounded range, so one hole
                # must never terminate a deep historical scan.
                continue
            except Exception as exc:
                raise InflationEvidenceError(
                    "NBS inflation index scan failed; refusing an incomplete "
                    f"collection: {url}"
                ) from exc
            successful_indexes += 1
            for ref in parse_inflation_index(source, url):
                refs[ref.source_url] = ref
    if successful_indexes == 0:
        raise RuntimeError("all NBS inflation archive index pages failed")
    if not refs:
        raise RuntimeError("NBS inflation archive contains no monthly commentary")

    collected_ppi: list[pd.DataFrame] = []
    successful_releases = 0
    for ref in sorted(
        refs.values(), key=lambda item: (item.observation_date, item.source_url)
    ):
        try:
            parsed = parse_inflation_release(
                release_loader(ref.source_url),
                title=ref.title,
                source_url=ref.source_url,
                include_core=False,
            )
        except Exception as exc:
            raise InflationEvidenceError(
                "NBS inflation release scan failed; refusing an incomplete "
                f"collection: {ref.source_url}"
            ) from exc
        successful_releases += 1
        collected_ppi.append(parsed["CN_PPI"])
    if successful_releases == 0:
        raise RuntimeError("all NBS inflation commentary pages failed")
    try:
        core = collect_core_cpi_table_evidence(
            page_count=core_page_count,
            start_page=core_start_page,
            archive_shards=core_archive_shards,
            fetch_index=fetch_core_index,
            fetch_release=fetch_core_release,
        )
    except Exception as exc:
        raise InflationEvidenceError(
            "NBS core-CPI table scan failed; refusing an incomplete collection"
        ) from exc
    merged = {
        "CN_CORE_CPI": core,
        "CN_PPI": _merge_ppi_frames(collected_ppi),
    }
    validate_inflation_evidence_rows(merged)
    return merged


def collect_recent_ppi_release_evidence(
    *,
    page_count: int = DEFAULT_RECENT_INFLATION_INDEX_PAGE_COUNT,
    start_page: int = 0,
    fetch_index: Callable[[str], str] | None = None,
    fetch_release: Callable[[str], str] | None = None,
) -> pd.DataFrame:
    """Collect a bounded recent PPI window from official NBS commentary.

    This is the lightweight ordinary-refresh path, not the fixed historical
    coverage gate.  It scans only the current commentary shard and requires
    every discovered monthly CPI/PPI article to yield one valid headline PPI
    observation.  A malformed matching detail therefore fails the whole batch
    instead of silently leaving a hole in the current/vintage refresh.  The
    ordinary fetcher separately compares this official window with its long
    history so an aggregate-only latest month cannot pass without metadata.
    """

    if page_count < 1 or start_page < 0:
        raise ValueError("invalid recent NBS inflation archive page range")
    index_loader = fetch_index or (
        lambda url: _get_nbs_inflation_archive_text(url, cache=False)
    )
    # Recent current/vintage refreshes must revalidate mutable detail URLs.
    # The outer fetcher cache bounds this work to once per refresh window; the
    # persistent detail cache remains reserved for resumable full-history
    # backfills, where silently missing an archive page is the larger risk.
    release_loader = fetch_release or (
        lambda url: _get_nbs_inflation_archive_text(url, cache=False)
    )

    refs: dict[str, InflationArticleRef] = {}
    successful_indexes = 0
    primary_index_succeeded = False
    for page in range(start_page, start_page + page_count):
        url = inflation_index_url(page, archive_shard=0)
        try:
            source = index_loader(url)
        except FileNotFoundError:
            # NBS numbering has real holes.  Later missing pages may be skipped
            # only after the primary requested page has proved the live shard;
            # the long-history merge below supplies the freshness backstop.
            continue
        except Exception as exc:
            raise InflationEvidenceError(
                "recent NBS PPI index scan failed; refusing an incomplete "
                f"collection: {url}"
            ) from exc
        successful_indexes += 1
        if page == start_page:
            primary_index_succeeded = True
        for ref in parse_inflation_index(source, url):
            if pd.Period(ref.observation_date, freq="M") < PPI_REQUIRED_FROM:
                continue
            previous = refs.get(ref.source_url)
            if previous is not None and previous.observation_date != ref.observation_date:
                raise InflationEvidenceError(
                    f"one NBS PPI URL advertises conflicting months: {ref.source_url}"
                )
            refs[ref.source_url] = ref

    if successful_indexes == 0:
        raise RuntimeError("all recent NBS PPI index pages failed")
    if not primary_index_succeeded:
        raise RuntimeError("recent NBS PPI primary index page failed")
    if not refs:
        raise RuntimeError("recent NBS PPI indexes contain no monthly releases")

    frames: list[pd.DataFrame] = []
    for ref in sorted(
        refs.values(), key=lambda item: (item.observation_date, item.source_url)
    ):
        try:
            parsed = parse_inflation_release(
                release_loader(ref.source_url),
                title=ref.title,
                source_url=ref.source_url,
                include_core=False,
            )["CN_PPI"]
            if len(parsed) != 1 or parsed.iloc[0]["date"] != ref.observation_date:
                raise InflationEvidenceError(
                    "monthly NBS commentary did not yield exactly one subject-month PPI"
                )
        except Exception as exc:
            raise InflationEvidenceError(
                "recent NBS PPI detail scan failed; refusing an incomplete "
                f"collection: {ref.source_url}"
            ) from exc
        frames.append(parsed)

    result = _merge_ppi_frames(frames)
    if result.empty:
        raise RuntimeError("recent NBS PPI collector returned no rows")
    validate_inflation_evidence_rows({"CN_PPI": result})
    return result


def _latest_required_month(as_of: dt.date) -> pd.Period:
    return pd.Period(as_of, freq="M") - 2


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _date_value(value: object, field: str) -> dt.date:
    if _is_missing(value):
        raise InflationEvidenceError(f"inflation evidence {field} must not be empty")
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise InflationEvidenceError(
            f"invalid inflation evidence {field}: {value!r}"
        ) from exc
    if pd.isna(parsed):
        raise InflationEvidenceError(f"inflation evidence {field} must not be empty")
    return parsed.date()


def _provenance_value(value: object) -> Mapping[str, object]:
    if _is_missing(value):
        raise InflationEvidenceError("inflation evidence provenance_json is required")
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InflationEvidenceError("invalid inflation evidence provenance_json") from exc
    if not isinstance(payload, Mapping):
        raise InflationEvidenceError(
            "inflation evidence provenance_json must be an object"
        )
    return payload


def validate_inflation_evidence_rows(
    evidence: dict[str, pd.DataFrame],
) -> None:
    """Validate every source-specific assertion before coverage or storage.

    This deliberately duplicates the generic storage checks.  The generic
    layer cannot know that this collector is NBS-only, raw (not derived), and
    exact-minute-only, nor can it authenticate parser provenance.
    """

    unexpected = sorted(set(evidence) - set(INFLATION_EVIDENCE_CODES))
    if unexpected:
        raise InflationEvidenceError(
            f"unexpected inflation evidence series: {unexpected}"
        )
    for code in INFLATION_EVIDENCE_CODES:
        frame = evidence.get(code)
        if frame is None:
            continue
        if not isinstance(frame, pd.DataFrame):
            raise InflationEvidenceError(f"{code} evidence must be a DataFrame")
        missing_columns = sorted(set(_OUTPUT_COLUMNS) - set(frame.columns))
        if missing_columns and not frame.empty:
            raise InflationEvidenceError(
                f"{code} evidence is missing columns: {missing_columns}"
            )
        instants: set[tuple[dt.date, dt.datetime]] = set()
        for row in frame.itertuples(index=False):
            observed = _date_value(getattr(row, "date", None), "date")
            if observed.day != 1:
                raise InflationEvidenceError(
                    f"{code} observation is not month-normalized: {observed}"
                )
            try:
                value = float(getattr(row, "value"))
            except (TypeError, ValueError) as exc:
                raise InflationEvidenceError(
                    f"invalid {code} evidence value"
                ) from exc
            if not math.isfinite(value):
                raise InflationEvidenceError(f"invalid {code} evidence value")

            available_at = getattr(row, "available_at", None)
            if not isinstance(available_at, dt.datetime):
                raise InflationEvidenceError(
                    f"{code} available_at must be an explicit datetime"
                )
            if (
                available_at.tzinfo is not None
                or available_at.second
                or available_at.microsecond
            ):
                raise InflationEvidenceError(
                    f"{code} available_at must be a naive exact-minute time"
                )
            release_date = _date_value(
                getattr(row, "release_date", None), "release_date"
            )
            if release_date != available_at.date():
                raise InflationEvidenceError(
                    f"{code} release_date does not match available_at"
                )
            if release_date <= pd.Period(observed, freq="M").end_time.date():
                raise InflationEvidenceError(
                    f"{code} release precedes completion of its observation month"
                )
            source_url = getattr(row, "source_url", None)
            if not _is_nbs_https_url(source_url):
                raise InflationEvidenceError(
                    f"{code} source is not official NBS HTTPS: {source_url!r}"
                )
            if getattr(row, "evidence_kind", None) != "official_release":
                raise InflationEvidenceError(
                    f"{code} evidence_kind must be official_release"
                )
            if getattr(row, "chain_verified", None) is not True:
                raise InflationEvidenceError(
                    f"{code} chain_verified must be the boolean True"
                )
            if getattr(row, "availability_precision", None) != "exact_minute":
                raise InflationEvidenceError(
                    f"{code} availability_precision must be exact_minute"
                )
            if not _is_missing(getattr(row, "formula_version", None)):
                raise InflationEvidenceError(
                    f"{code} raw release evidence must not have formula_version"
                )

            provenance = _provenance_value(
                getattr(row, "provenance_json", None)
            )
            semantics = provenance.get("evidence_semantics")
            is_core_table = (
                code == "CN_CORE_CPI"
                and semantics == "subject_month_official_cpi_table_row"
            )
            expected_parser = (
                CORE_CPI_TABLE_PARSER_VERSION if is_core_table else PARSER_VERSION
            )
            if provenance.get("parser_version") != expected_parser:
                raise InflationEvidenceError(f"{code} parser provenance is invalid")
            if (
                provenance.get("publication_time_source")
                != "visible_nbs_detail_title"
            ):
                raise InflationEvidenceError(
                    f"{code} publication-time provenance is invalid"
                )
            source_sha256 = provenance.get("source_sha256")
            if not isinstance(source_sha256, str) or re.fullmatch(
                r"[0-9a-f]{64}", source_sha256
            ) is None:
                raise InflationEvidenceError(
                    f"{code} source digest provenance is invalid"
                )
            article_observation_raw = provenance.get("article_observation")
            try:
                article_observation = dt.date.fromisoformat(
                    str(article_observation_raw)
                )
            except ValueError as exc:
                raise InflationEvidenceError(
                    f"{code} article-observation provenance is invalid"
                ) from exc
            title_observation = (
                cpi_release_title_observation(
                    str(provenance.get("title") or "")
                )
                if is_core_table
                else inflation_title_observation(
                    str(provenance.get("title") or "")
                )
            )
            if title_observation != article_observation:
                raise InflationEvidenceError(
                    f"{code} title/article provenance is inconsistent"
                )

            status = getattr(row, "status", None)
            if is_core_table:
                chain = provenance.get("redirect_chain")
                request_url = provenance.get("source_request_url")
                final_url = provenance.get("source_final_url")
                assertion_digest = provenance.get("table_assertion_sha256")
                expected_assertion_digest = core_cpi_table_assertion_digest(
                    observed,
                    value,
                    provenance.get("column_label"),
                )
                if (
                    status != "published"
                    or article_observation != observed
                    or provenance.get("source_kind")
                    != "nbs_cpi_release_table"
                    or provenance.get("row_label")
                    != "其中：不包括食品和能源"
                    or not is_core_cpi_yoy_column_label(
                        provenance.get("column_label")
                    )
                    or provenance.get("cache_version")
                    != CORE_CPI_TABLE_CACHE_VERSION
                    or assertion_digest != expected_assertion_digest
                    or request_url != source_url
                    or not isinstance(chain, list)
                    or not chain
                    or chain[0] != request_url
                    or chain[-1] != final_url
                    or not all(_is_nbs_https_url(item) for item in chain)
                    or not _is_nbs_https_url(final_url)
                ):
                    raise InflationEvidenceError(
                        "CN_CORE_CPI table provenance is invalid"
                    )
            elif status == "published" and semantics == "subject_month_release":
                if article_observation != observed:
                    raise InflationEvidenceError(
                        f"{code} subject-month provenance is inconsistent"
                    )
            elif (
                code == "CN_CORE_CPI"
                and status == "published_later_reference"
                and semantics == "later_article_explicit_reference"
            ):
                if pd.Period(article_observation, freq="M") != (
                    pd.Period(observed, freq="M") + 1
                ):
                    raise InflationEvidenceError(
                        "CN_CORE_CPI later-reference provenance is inconsistent"
                    )
            else:
                raise InflationEvidenceError(
                    f"{code} status/evidence semantics are invalid"
                )

            instant = (observed, available_at)
            if instant in instants:
                raise InflationEvidenceError(
                    f"duplicate/conflicting {code} evidence for {observed} "
                    f"at {available_at.isoformat()}"
                )
            instants.add(instant)


def validate_inflation_coverage(
    evidence: dict[str, pd.DataFrame],
    *,
    as_of: dt.date | None = None,
) -> dict[str, object]:
    """Require every designed month; structural gaps are reported, never hidden."""

    validate_inflation_evidence_rows(evidence)
    latest = _latest_required_month(as_of or dt.date.today())
    starts = {
        "CN_CORE_CPI": CORE_CPI_REQUIRED_FROM,
        "CN_PPI": PPI_REQUIRED_FROM,
    }
    missing: dict[str, list[str]] = {}
    for code, start in starts.items():
        frame = evidence.get(code, pd.DataFrame())
        dates = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        present = set(pd.PeriodIndex(dates, freq="M"))
        absent = [str(period) for period in pd.period_range(start, latest, freq="M") if period not in present]
        if absent:
            missing[code] = absent
    return {
        "ready": not missing,
        "required_through": str(latest),
        "required_from": {code: str(start) for code, start in starts.items()},
        "missing": missing,
    }


def evidence_summary(evidence: dict[str, pd.DataFrame]) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for code in INFLATION_EVIDENCE_CODES:
        frame = evidence.get(code, pd.DataFrame())
        observed = pd.to_datetime(
            frame.get("date", pd.Series(dtype=object)), errors="coerce"
        ).dropna()
        summary[code] = {
            "rows": int(len(frame)),
            "observations": int(observed.dt.to_period("M").nunique()),
            "first": observed.min().date().isoformat() if not observed.empty else None,
            "last": observed.max().date().isoformat() if not observed.empty else None,
        }
    return summary


def store_inflation_evidence(
    db: Session,
    evidence: dict[str, pd.DataFrame],
) -> dict[str, int]:
    """Store both complete chains atomically; never write ``DataPoint``."""

    # Storage always uses the real production cutoff.  Replay/audit callers may
    # inspect an older cutoff through ``validate_inflation_coverage`` but can no
    # longer use it to weaken the apply gate.
    gate = validate_inflation_coverage(evidence)
    if not gate["ready"]:
        raise InflationCoverageError(
            "official NBS inflation crawl is incomplete: "
            + json.dumps(gate["missing"], ensure_ascii=False)
        )
    inserted: dict[str, int] = {}
    try:
        for code in INFLATION_EVIDENCE_CODES:
            inserted[code] = upsert_release_evidence(
                db, code, evidence.get(code, pd.DataFrame()), commit=False
            )
        db.commit()
        return inserted
    except Exception:
        db.rollback()
        raise


__all__ = [
    "CORE_CPI_ARCHIVE_SHARDS",
    "CORE_CPI_REQUIRED_FROM",
    "DEFAULT_CORE_CPI_INDEX_PAGE_COUNT",
    "DEFAULT_INFLATION_INDEX_PAGE_COUNT",
    "DEFAULT_RECENT_INFLATION_INDEX_PAGE_COUNT",
    "INFLATION_EVIDENCE_CODES",
    "InflationArticleRef",
    "InflationCoverageError",
    "InflationEvidenceError",
    "PPI_REQUIRED_FROM",
    "collect_inflation_release_evidence",
    "collect_recent_ppi_release_evidence",
    "evidence_summary",
    "inflation_index_url",
    "parse_inflation_index",
    "parse_inflation_release",
    "store_inflation_evidence",
    "validate_inflation_coverage",
    "validate_inflation_evidence_rows",
]
