"""Strict evidence for China's core CPI from official monthly CPI tables.

The NBS commentary archive is useful for PPI, but many commentary articles do
not state core CPI.  The monthly CPI release table does: the row labelled
``其中：不包括食品和能源`` is the official core index, and the column labelled
``同比涨跌幅（%）`` (or the controlled legacy spelling ``同比涨跌（%）``) is its
year-on-year rate.  This module deliberately discovers and parses that table
independently of prose so that column order, cumulative columns, or a missing
commentary sentence cannot silently change the result.
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
from typing import Callable, Iterable
from urllib.parse import urljoin

import pandas as pd
import requests
from lxml import html as lxml_html

from app.fetchers.china_cycle_data import _is_nbs_https_url


NBS_CPI_RELEASE_BASE = "https://www.stats.gov.cn/sj/zxfb/"
CORE_CPI_ARCHIVE_SHARDS = (0, 1000, 2000)
CORE_CPI_REQUIRED_FROM = pd.Period("2017-01", freq="M")
DEFAULT_CORE_CPI_INDEX_PAGE_COUNT = 100
DEFAULT_RECENT_CORE_CPI_INDEX_PAGE_COUNT = 14
CORE_CPI_TABLE_PARSER_VERSION = "nbs_core_cpi_table_v1"
CORE_CPI_TABLE_CACHE_VERSION = 1

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
_CORE_ROW_LABEL = "其中：不包括食品和能源"
_YOY_HEADERS = frozenset({"同比涨跌幅(%)", "同比涨跌(%)"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": NBS_CPI_RELEASE_BASE,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_CACHE = (
    Path(__file__).resolve().parents[2]
    / ".cache"
    / f"nbs-core-cpi-evidence-v{CORE_CPI_TABLE_CACHE_VERSION}"
)
_REQUEST_INTERVAL = 0.45
_request_lock = threading.Lock()
_last_request = 0.0

_TITLE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>20\d{2})年(?P<month>\d{1,2})月份居民消费价格"
)
_VISIBLE_TIME_PATTERNS = (
    re.compile(
        r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+"
        r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
    ),
    re.compile(
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日?\s+"
        r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
    ),
)
_RANGE_HEADER_PATTERN = re.compile(
    r"(?:\d{1,2}|[一二三四五六七八九十]+)"
    r"(?:-|—|–|－|~|～|至)"
    r"(?:\d{1,2}|[一二三四五六七八九十]+)月"
)
_NUMBER_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?$")


class CoreCpiTableEvidenceError(ValueError):
    """An official page cannot prove one unambiguous core-CPI observation."""


@dataclass(frozen=True, slots=True)
class CoreCpiReleaseRef:
    observation_date: dt.date
    title: str
    source_url: str


@dataclass(frozen=True, slots=True)
class NbsCoreCpiFetch:
    request_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    source: str


def _compact_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _semantic_text(value: object) -> str:
    return (
        _compact_text(value)
        .replace(" ", "")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
        .replace("％", "%")
    )


def cpi_release_title_observation(value: object) -> dt.date | None:
    """Return the month asserted by a monthly CPI release title."""

    matches = {
        (int(match.group("year")), int(match.group("month")))
        for match in _TITLE_PATTERN.finditer(_compact_text(value))
    }
    if not matches:
        return None
    if len(matches) != 1:
        raise CoreCpiTableEvidenceError(
            f"conflicting CPI months in release title: {value!r}"
        )
    year, month = next(iter(matches))
    try:
        return dt.date(year, month, 1)
    except ValueError as exc:
        raise CoreCpiTableEvidenceError(
            f"invalid CPI month in release title: {value!r}"
        ) from exc


def core_cpi_index_url(page: int, *, archive_shard: int = 0) -> str:
    if page < 0:
        raise ValueError("NBS CPI index page cannot be negative")
    if archive_shard not in CORE_CPI_ARCHIVE_SHARDS:
        raise ValueError(
            "NBS CPI archive shard must be one of 0, 1000, or 2000"
        )
    if archive_shard:
        name = (
            f"index_{archive_shard}.html"
            if page == 0
            else f"index_{archive_shard}_{page}.html"
        )
    else:
        name = "" if page == 0 else f"index_{page}.html"
    return urljoin(NBS_CPI_RELEASE_BASE, name)


def parse_core_cpi_index(
    source: str, source_url: str
) -> list[CoreCpiReleaseRef]:
    """Find monthly CPI table releases without assuming ``同比`` in titles."""

    if not _is_nbs_https_url(source_url):
        raise CoreCpiTableEvidenceError("NBS CPI index must use official HTTPS")
    if "Please enable JavaScript" in source:
        raise CoreCpiTableEvidenceError("NBS CPI index is a challenge page")
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError) as exc:
        raise CoreCpiTableEvidenceError("invalid NBS CPI index HTML") from exc

    by_url: dict[str, CoreCpiReleaseRef] = {}
    for anchor in document.xpath("//a[@href]"):
        title = _compact_text(anchor.get("title") or anchor.text_content())
        observed = cpi_release_title_observation(title)
        if observed is None:
            continue
        article_url = urljoin(source_url, str(anchor.get("href")))
        if not _is_nbs_https_url(article_url):
            raise CoreCpiTableEvidenceError(
                f"matching NBS CPI link is not official HTTPS: {article_url!r}"
            )
        previous = by_url.get(article_url)
        if previous is not None and previous.observation_date != observed:
            raise CoreCpiTableEvidenceError(
                f"one NBS CPI URL advertises conflicting months: {article_url}"
            )
        by_url[article_url] = CoreCpiReleaseRef(observed, title, article_url)
    return sorted(
        by_url.values(), key=lambda item: (item.observation_date, item.source_url)
    )


def _page_observation(document) -> dt.date:
    observations: set[dt.date] = set()
    for node in document.xpath(
        "//h1|//title|//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' detail-title ')]"
    ):
        observed = cpi_release_title_observation(node.text_content())
        if observed is not None:
            observations.add(observed)
    if len(observations) != 1:
        raise CoreCpiTableEvidenceError(
            "NBS CPI page must expose exactly one subject month in its title"
        )
    return next(iter(observations))


def _publication_metadata(document, source: str, source_url: str) -> dict[str, object]:
    """Read an exact minute only from the page's visible title/byline block."""

    visible = " ".join(
        _compact_text(node.text_content())
        for node in document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' detail-title-des ')]//p | //*[@id='shijian']"
        )
    )
    found: set[dt.datetime] = set()
    for pattern in _VISIBLE_TIME_PATTERNS:
        for match in pattern.finditer(visible):
            if match.group(6) is not None and int(match.group(6)) != 0:
                raise CoreCpiTableEvidenceError(
                    "NBS CPI publication time is not minute-precision"
                )
            try:
                found.add(
                    dt.datetime(
                        *(int(match.group(index)) for index in range(1, 6))
                    )
                )
            except ValueError as exc:
                raise CoreCpiTableEvidenceError(
                    "invalid visible NBS CPI publication minute"
                ) from exc
    if not found:
        raise CoreCpiTableEvidenceError(
            "NBS CPI release lacks a visible exact publication minute"
        )
    if len(found) != 1:
        raise CoreCpiTableEvidenceError(
            f"conflicting visible NBS CPI publication minutes: {sorted(found)!r}"
        )
    published = next(iter(found)).replace(second=0, microsecond=0)
    return {
        "release_date": published.date(),
        "available_at": published,
        "source_url": source_url,
        "publication_time_source": "visible_nbs_detail_title",
        "source_sha256": hashlib.sha256(
            source.encode("utf-8")
        ).hexdigest(),
    }


def _positive_span(cell, attribute: str) -> int:
    raw = cell.get(attribute, "1")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise CoreCpiTableEvidenceError(
            f"invalid CPI table {attribute}: {raw!r}"
        ) from exc
    if value < 1 or value > 100:
        raise CoreCpiTableEvidenceError(
            f"invalid CPI table {attribute}: {raw!r}"
        )
    return value


def _table_grid(table) -> list[list[str]]:
    """Expand row/column spans into a rectangular semantic grid."""

    table_rows = table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
    coordinates: list[dict[int, str]] = []
    for row_index, row_node in enumerate(table_rows):
        while len(coordinates) <= row_index:
            coordinates.append({})
        column = 0
        for cell in row_node.xpath("./th|./td"):
            while column in coordinates[row_index]:
                column += 1
            rowspan = _positive_span(cell, "rowspan")
            colspan = _positive_span(cell, "colspan")
            text = _compact_text(cell.text_content())
            for target_row in range(row_index, row_index + rowspan):
                while len(coordinates) <= target_row:
                    coordinates.append({})
                for target_column in range(column, column + colspan):
                    if target_column in coordinates[target_row]:
                        raise CoreCpiTableEvidenceError(
                            "overlapping cells in NBS CPI table"
                        )
                    coordinates[target_row][target_column] = text
            column += colspan
    if not coordinates:
        return []
    width = max((max(row, default=-1) for row in coordinates), default=-1) + 1
    return [[row.get(column, "") for column in range(width)] for row in coordinates]


def _header_label(grid: list[list[str]], row_index: int, column: int) -> str:
    parts: list[str] = []
    for row in grid[:row_index]:
        part = _compact_text(row[column]) if column < len(row) else ""
        if part and (not parts or parts[-1] != part):
            parts.append(part)
    return " ".join(parts)


def is_core_cpi_yoy_column_label(value: object) -> bool:
    """Accept only the two exact official monthly YoY header spellings."""

    semantic = _semantic_text(value)
    if not any(semantic.endswith(header) for header in _YOY_HEADERS):
        return False
    if (
        any(
            marker in semantic
            for marker in ("累计", "平均", "季度", "半年", "全年")
        )
        or _RANGE_HEADER_PATTERN.search(semantic)
        # A parent header such as ``2018年`` plus an otherwise valid child
        # ``同比涨跌（%）`` is an annual column, not the subject-month column.
        or re.search(
            r"(?<!\d)20\d{2}年度?同比涨跌(?:幅)?\(%\)$",
            semantic,
        )
    ):
        return False
    return True


def _yoy_column(
    grid: list[list[str]], row_index: int, observed: dt.date
) -> tuple[int, str]:
    candidates: list[tuple[int, int, int, str]] = []
    width = max((len(row) for row in grid), default=0)
    # Locate the semantic header cell itself.  Joining every row before the
    # target would accidentally treat earlier data values as header text on
    # NBS tables that place all rows inside ``thead``.
    for header_row in range(row_index):
        for column in range(width):
            cell = grid[header_row][column] if column < len(grid[header_row]) else ""
            if _semantic_text(cell) not in _YOY_HEADERS:
                continue
            label = _header_label(grid, header_row + 1, column)
            semantic = _semantic_text(label)
            if not is_core_cpi_yoy_column_label(label):
                continue
            mentioned_months = {
                int(value)
                for value in re.findall(r"(?<!\d)(\d{1,2})月", semantic)
            }
            if mentioned_months and mentioned_months != {observed.month}:
                continue
            # Prefer a column explicitly nested under the subject month, then
            # the closest eligible header row.  The latter handles duplicated
            # styling rows without relying on a fixed table position.
            priority = 0 if mentioned_months == {observed.month} else 1
            candidates.append((priority, -header_row, column, label))
    if not candidates:
        raise CoreCpiTableEvidenceError(
            "CPI table has no controlled subject-month YoY rate column"
        )
    best_priority = min(item[0] for item in candidates)
    best = [item for item in candidates if item[0] == best_priority]
    closest_header = min(item[1] for item in best)
    best = [item for item in best if item[1] == closest_header]
    unique_columns = {(item[2], item[3]) for item in best}
    if len(unique_columns) != 1:
        raise CoreCpiTableEvidenceError(
            "CPI table has multiple controlled subject-month YoY rate columns"
        )
    column, label = next(iter(unique_columns))
    return column, label


def _numeric_cell(value: object) -> float:
    semantic = _semantic_text(value)
    if not _NUMBER_PATTERN.fullmatch(semantic):
        raise CoreCpiTableEvidenceError(
            f"core-CPI YoY table cell is not a plain numeric rate: {value!r}"
        )
    parsed = float(semantic.rstrip("%"))
    if not math.isfinite(parsed):
        raise CoreCpiTableEvidenceError("core-CPI YoY table value is not finite")
    return parsed


def core_cpi_table_assertion_digest(
    observed: dt.date,
    value: object,
    column_label: object,
) -> str:
    """Hash the normalized assertion, independent of unrelated page chrome."""

    return hashlib.sha256(
        json.dumps(
            {
                "column_label": _semantic_text(column_label),
                "date": observed.isoformat(),
                "row_label": _semantic_text(_CORE_ROW_LABEL),
                "value": format(round(float(value), 12), ".12g"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _core_values_from_tables(
    document, observed: dt.date
) -> tuple[float, str]:
    assertions: list[tuple[float, str]] = []
    target = _semantic_text(_CORE_ROW_LABEL)
    for table in document.xpath("//table"):
        grid = _table_grid(table)
        for row_index, row in enumerate(grid):
            if target not in {_semantic_text(cell) for cell in row}:
                continue
            column, header = _yoy_column(grid, row_index, observed)
            if column >= len(row):
                raise CoreCpiTableEvidenceError(
                    "core-CPI table row is shorter than its YoY header"
                )
            assertions.append((_numeric_cell(row[column]), header))
    if not assertions:
        raise CoreCpiTableEvidenceError(
            "NBS CPI release has no core row under an explicit YoY header"
        )
    values = {round(value, 12) for value, _ in assertions}
    if len(values) != 1:
        raise CoreCpiTableEvidenceError(
            f"conflicting core-CPI YoY values inside one release: {sorted(values)!r}"
        )
    value = next(iter(values))
    header = sorted({header for _, header in assertions}, key=lambda item: (len(item), item))[0]
    return value, header


def parse_core_cpi_release(
    source: str,
    *,
    title: str,
    source_url: str,
    fetch: NbsCoreCpiFetch | None = None,
) -> dict:
    """Parse one official CPI table release into one strict evidence row."""

    if not _is_nbs_https_url(source_url):
        raise CoreCpiTableEvidenceError(
            "NBS CPI release must use official HTTPS"
        )
    if "Please enable JavaScript" in source or "<html" not in source.lower():
        raise CoreCpiTableEvidenceError("NBS CPI release is not full HTML")
    indexed_observation = cpi_release_title_observation(title)
    if indexed_observation is None:
        raise CoreCpiTableEvidenceError("NBS CPI index title is not monthly")
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError) as exc:
        raise CoreCpiTableEvidenceError("invalid NBS CPI release HTML") from exc
    observed = _page_observation(document)
    if observed != indexed_observation:
        raise CoreCpiTableEvidenceError(
            "NBS CPI page title does not match its index subject"
        )
    metadata = _publication_metadata(document, source, source_url)
    value, column_label = _core_values_from_tables(document, observed)
    table_assertion_sha256 = core_cpi_table_assertion_digest(
        observed, value, column_label
    )

    if fetch is None:
        fetch = NbsCoreCpiFetch(source_url, source_url, (source_url,), source)
    if fetch.source != source or fetch.request_url != source_url:
        raise CoreCpiTableEvidenceError("CPI fetch provenance does not match source")
    if (
        not fetch.redirect_chain
        or fetch.redirect_chain[0] != fetch.request_url
        or fetch.redirect_chain[-1] != fetch.final_url
        or not all(_is_nbs_https_url(item) for item in fetch.redirect_chain)
        or not _is_nbs_https_url(fetch.final_url)
    ):
        raise CoreCpiTableEvidenceError(
            "CPI fetch provenance contains an untrusted redirect"
        )

    available_at = metadata["available_at"]
    assert isinstance(available_at, dt.datetime)
    observation_month_end = pd.Period(observed, freq="M").end_time.date()
    if available_at.date() <= observation_month_end:
        raise CoreCpiTableEvidenceError(
            "NBS CPI publication time does not follow its observation month"
        )
    return {
        "date": observed,
        "value": round(float(value), 6),
        "release_date": available_at.date(),
        "available_at": available_at,
        "source_url": source_url,
        "status": "published",
        "formula_version": None,
        "provenance_json": {
            "article_observation": observed.isoformat(),
            "cache_version": CORE_CPI_TABLE_CACHE_VERSION,
            "column_label": column_label,
            "evidence_semantics": "subject_month_official_cpi_table_row",
            "parser_version": CORE_CPI_TABLE_PARSER_VERSION,
            "publication_time_source": "visible_nbs_detail_title",
            "redirect_chain": list(fetch.redirect_chain),
            "row_label": _CORE_ROW_LABEL,
            "source_final_url": fetch.final_url,
            "source_kind": "nbs_cpi_release_table",
            "source_request_url": fetch.request_url,
            "source_sha256": metadata["source_sha256"],
            "table_assertion_sha256": table_assertion_sha256,
            "title": title,
        },
        "evidence_kind": "official_release",
        "chain_verified": True,
        "availability_precision": "exact_minute",
    }


def _valid_detail(source: str, source_url: str) -> bool:
    try:
        document = lxml_html.fromstring(source)
        observed = _page_observation(document)
        _publication_metadata(document, source, source_url)
        _core_values_from_tables(document, observed)
    except (CoreCpiTableEvidenceError, TypeError, ValueError):
        return False
    return True


def _paced_get(url: str):
    global _last_request
    with _request_lock:
        wait_for = _REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if wait_for > 0:
            time.sleep(wait_for)
        try:
            return requests.get(
                url,
                headers=_HEADERS,
                timeout=30,
                allow_redirects=False,
            )
        finally:
            _last_request = time.monotonic()


def _fetch_page(url: str) -> NbsCoreCpiFetch:
    current = url
    chain = [url]
    for _ in range(6):
        if not _is_nbs_https_url(current):
            raise CoreCpiTableEvidenceError(
                f"refusing NBS CPI redirect outside official HTTPS: {current}"
            )
        response = _paced_get(current)
        if response.status_code == 404:
            raise FileNotFoundError(current)
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("Location")
            if not location:
                raise CoreCpiTableEvidenceError(
                    "NBS CPI redirect is missing a Location header"
                )
            redirected = urljoin(current, location)
            if not _is_nbs_https_url(redirected):
                raise CoreCpiTableEvidenceError(
                    f"refusing NBS CPI redirect outside official HTTPS: {redirected}"
                )
            chain.append(redirected)
            current = redirected
            continue
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        source = response.text
        if "Please enable JavaScript" in source:
            raise CoreCpiTableEvidenceError(
                "NBS CPI archive returned a browser challenge page"
            )
        return NbsCoreCpiFetch(url, current, tuple(chain), source)
    raise CoreCpiTableEvidenceError("NBS CPI archive exceeded the redirect limit")


def _cache_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _CACHE / f"{digest}.html", _CACHE / f"{digest}.json"


def _read_cache(url: str) -> NbsCoreCpiFetch | None:
    source_path, metadata_path = _cache_paths(url)
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
        metadata.get("cache_version") != CORE_CPI_TABLE_CACHE_VERSION
        or metadata.get("request_url") != url
        or not isinstance(chain, list)
        or not chain
        or len(chain) > 6
        or chain[0] != url
        or chain[-1] != final_url
        or not all(_is_nbs_https_url(item) for item in chain)
        or not _is_nbs_https_url(final_url)
        or metadata.get("sha256") != hashlib.sha256(source_bytes).hexdigest()
        or not _valid_detail(source, str(final_url))
    ):
        return None
    return NbsCoreCpiFetch(url, str(final_url), tuple(chain), source)


def _write_cache(fetch: NbsCoreCpiFetch) -> None:
    if (
        not _is_nbs_https_url(fetch.request_url)
        or not _is_nbs_https_url(fetch.final_url)
        or not fetch.redirect_chain
        or fetch.redirect_chain[0] != fetch.request_url
        or fetch.redirect_chain[-1] != fetch.final_url
        or not all(_is_nbs_https_url(item) for item in fetch.redirect_chain)
        or not _valid_detail(fetch.source, fetch.final_url)
    ):
        raise CoreCpiTableEvidenceError(
            "refusing to cache unverified NBS CPI detail"
        )
    source_bytes = fetch.source.encode("utf-8")
    metadata = {
        "cache_version": CORE_CPI_TABLE_CACHE_VERSION,
        "request_url": fetch.request_url,
        "final_url": fetch.final_url,
        "redirect_chain": list(fetch.redirect_chain),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    source_path, metadata_path = _cache_paths(fetch.request_url)
    _CACHE.mkdir(parents=True, exist_ok=True)
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    temporary_source = source_path.with_suffix(source_path.suffix + suffix)
    temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + suffix)
    try:
        temporary_source.write_bytes(source_bytes)
        temporary_metadata.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_source.replace(source_path)
        temporary_metadata.replace(metadata_path)
    finally:
        temporary_source.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)


def _get_index_text(url: str) -> str:
    if not _is_nbs_https_url(url):
        raise CoreCpiTableEvidenceError("NBS CPI index must use official HTTPS")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return _fetch_page(url).source
        except FileNotFoundError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    assert last_error is not None
    raise last_error


def _get_detail(url: str, *, refresh: bool = False) -> NbsCoreCpiFetch:
    if not _is_nbs_https_url(url):
        raise CoreCpiTableEvidenceError("NBS CPI detail must use official HTTPS")
    if not refresh:
        cached = _read_cache(url)
        if cached is not None:
            return cached
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            fetched = _fetch_page(url)
            _write_cache(fetched)
            return fetched
        except FileNotFoundError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(min(2**attempt, 4))
    assert last_error is not None
    raise last_error


def _get_recent_detail(url: str) -> NbsCoreCpiFetch:
    """Revalidate rolling-window pages so same-URL corrections are visible."""

    return _get_detail(url, refresh=True)


def _deduplicate_rows(rows: Iterable[dict]) -> pd.DataFrame:
    by_month: dict[dt.date, list[dict]] = {}
    for row in rows:
        by_month.setdefault(row["date"], []).append(row)
    selected: list[dict] = []
    for observed, assertions in sorted(by_month.items()):
        signatures = {
            (
                round(float(row["value"]), 12),
                row["available_at"],
                row["provenance_json"].get("table_assertion_sha256"),
            )
            for row in assertions
        }
        if len(signatures) != 1:
            raise CoreCpiTableEvidenceError(
                "conflicting or non-identical core-CPI table releases for "
                f"{observed}"
            )
        # Archive mirrors may differ in unrelated page chrome.  Deduplicate only
        # when the normalized table assertion and exact publication instant are
        # identical; the full-page digest deliberately does not define equality.
        selected.append(
            min(assertions, key=lambda row: row["source_url"])
        )
    if not selected:
        return pd.DataFrame(columns=_OUTPUT_COLUMNS)
    return pd.DataFrame(selected, columns=_OUTPUT_COLUMNS).sort_values(
        ["date", "available_at", "source_url"], kind="stable"
    ).reset_index(drop=True)


def _collect_release_refs(
    refs: Iterable[CoreCpiReleaseRef],
    release_loader: Callable[[str], str | NbsCoreCpiFetch],
) -> pd.DataFrame:
    rows: list[dict] = []
    for ref in sorted(
        refs, key=lambda item: (item.observation_date, item.source_url)
    ):
        try:
            loaded = release_loader(ref.source_url)
            if isinstance(loaded, NbsCoreCpiFetch):
                fetched = loaded
            elif isinstance(loaded, str):
                fetched = NbsCoreCpiFetch(
                    ref.source_url,
                    ref.source_url,
                    (ref.source_url,),
                    loaded,
                )
            else:
                raise TypeError("CPI detail loader returned an unsupported value")
            rows.append(
                parse_core_cpi_release(
                    fetched.source,
                    title=ref.title,
                    source_url=ref.source_url,
                    fetch=fetched,
                )
            )
        except Exception as exc:
            raise CoreCpiTableEvidenceError(
                "NBS CPI detail scan failed; refusing an incomplete "
                f"collection: {ref.source_url}"
            ) from exc
    return _deduplicate_rows(rows)


def collect_recent_core_cpi_table_evidence(
    *,
    page_count: int = DEFAULT_RECENT_CORE_CPI_INDEX_PAGE_COUNT,
    start_page: int = 0,
    fetch_index: Callable[[str], str] | None = None,
    fetch_release: Callable[[str], str | NbsCoreCpiFetch] | None = None,
) -> pd.DataFrame:
    """Collect a rolling core-CPI window from the current ``zxfb`` shard.

    This is the lightweight daily-refresh path, not a historical coverage
    gate.  It scans only shard 0 and does not require a continuous time series,
    but every discovered in-contract detail page is parsed fail-closed.
    """

    if page_count < 1 or start_page < 0:
        raise ValueError("invalid recent NBS CPI archive page range")
    index_loader = fetch_index or _get_index_text
    release_loader = fetch_release or _get_recent_detail

    refs: dict[str, CoreCpiReleaseRef] = {}
    successful_indexes = 0
    for page in range(start_page, start_page + page_count):
        url = core_cpi_index_url(page, archive_shard=0)
        try:
            index_source = index_loader(url)
        except FileNotFoundError:
            continue
        except Exception as exc:
            raise CoreCpiTableEvidenceError(
                "recent NBS CPI index scan failed; refusing an incomplete "
                f"collection: {url}"
            ) from exc
        successful_indexes += 1
        for ref in parse_core_cpi_index(index_source, url):
            if pd.Period(ref.observation_date, freq="M") < CORE_CPI_REQUIRED_FROM:
                continue
            previous = refs.get(ref.source_url)
            if previous is not None and previous.observation_date != ref.observation_date:
                raise CoreCpiTableEvidenceError(
                    f"one NBS CPI URL advertises conflicting months: {ref.source_url}"
                )
            refs[ref.source_url] = ref
    if successful_indexes == 0:
        raise RuntimeError("all recent NBS CPI index pages failed")
    if not refs:
        raise RuntimeError("recent NBS CPI indexes contain no monthly CPI releases")
    result = _collect_release_refs(refs.values(), release_loader)
    if result.empty:
        raise RuntimeError("recent NBS CPI table collector returned no rows")
    return result


def collect_core_cpi_table_evidence(
    *,
    page_count: int = DEFAULT_CORE_CPI_INDEX_PAGE_COUNT,
    start_page: int = 0,
    archive_shards: tuple[int, ...] = CORE_CPI_ARCHIVE_SHARDS,
    fetch_index: Callable[[str], str] | None = None,
    fetch_release: Callable[[str], str | NbsCoreCpiFetch] | None = None,
) -> pd.DataFrame:
    """Collect all table-backed core CPI rows, failing closed on any conflict."""

    if page_count < 1 or start_page < 0:
        raise ValueError("invalid NBS CPI archive page range")
    if tuple(archive_shards) != CORE_CPI_ARCHIVE_SHARDS:
        raise ValueError("core CPI discovery must scan archive shards 0/1000/2000")
    index_loader = fetch_index or _get_index_text
    release_loader = fetch_release or _get_detail

    refs: dict[str, CoreCpiReleaseRef] = {}
    successful_indexes = 0
    for shard in archive_shards:
        for page in range(start_page, start_page + page_count):
            url = core_cpi_index_url(page, archive_shard=shard)
            try:
                index_source = index_loader(url)
            except FileNotFoundError:
                # NBS archive numbering has real holes; a bounded scan must
                # continue so a later page or shard is never silently skipped.
                continue
            except Exception as exc:
                raise CoreCpiTableEvidenceError(
                    "NBS CPI index scan failed; refusing an incomplete "
                    f"collection: {url}"
                ) from exc
            successful_indexes += 1
            for ref in parse_core_cpi_index(index_source, url):
                previous = refs.get(ref.source_url)
                if previous is not None and previous.observation_date != ref.observation_date:
                    raise CoreCpiTableEvidenceError(
                        f"one NBS CPI URL advertises conflicting months: {ref.source_url}"
                    )
                refs[ref.source_url] = ref
    if successful_indexes == 0:
        raise RuntimeError("all NBS CPI archive index pages failed")
    if not refs:
        raise RuntimeError("NBS CPI archive contains no monthly CPI releases")

    # Early NBS CPI tables legitimately predate the core row.  The fixed
    # evidence contract begins in 2017-01, so filter by the already-validated
    # index title before requesting details.  Once inside the contract window,
    # every missing/malformed table remains a batch-fatal error below.
    eligible_refs = (
        ref
        for ref in refs.values()
        if pd.Period(ref.observation_date, freq="M") >= CORE_CPI_REQUIRED_FROM
    )
    return _collect_release_refs(eligible_refs, release_loader)


__all__ = [
    "CORE_CPI_ARCHIVE_SHARDS",
    "CORE_CPI_REQUIRED_FROM",
    "CORE_CPI_TABLE_CACHE_VERSION",
    "CORE_CPI_TABLE_PARSER_VERSION",
    "CoreCpiReleaseRef",
    "CoreCpiTableEvidenceError",
    "DEFAULT_CORE_CPI_INDEX_PAGE_COUNT",
    "DEFAULT_RECENT_CORE_CPI_INDEX_PAGE_COUNT",
    "NbsCoreCpiFetch",
    "collect_core_cpi_table_evidence",
    "collect_recent_core_cpi_table_evidence",
    "core_cpi_table_assertion_digest",
    "core_cpi_index_url",
    "cpi_release_title_observation",
    "is_core_cpi_yoy_column_label",
    "parse_core_cpi_index",
    "parse_core_cpi_release",
]
