"""Verified CEI distribution evidence for China's consumer survey indices.

CEI pages identify the National Bureau of Statistics as the data source but
publish only a calendar date, not a release time.  This collector therefore
uses the first instant after that date (next-day 00:00, Asia/Shanghai wall
clock) as a conservative availability upper bound.  The title month remains
the page's release subject. Formula-valid rolling rows may only prove that an
older value was known by this later page date; they are never backdated. The
collector keeps the earliest proof of an identical three-index snapshot and
retains genuinely changed snapshots as later revisions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import pandas as pd
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html
from lxml.etree import ParserError


logger = logging.getLogger(__name__)

CEI_CONSUMER_COLUMN_ID = "4028c7ca-37115425-0137-115646c5-00ec"
CEI_CONSUMER_FIRST_INDEX_YEAR = 2016
CEI_CONSUMER_EVIDENCE_CODES = (
    "CN_CONSUMER_EXPECTATIONS",
    "CN_CONSUMER_SATISFACTION",
    "CN_CONSUMER_CONFIDENCE",
)
_CEI_BASE_URL = "https://www.cei.cn"
_CEI_APPROVED_HOSTS = frozenset({"cei.cn", "www.cei.cn", "ibe.cei.cn"})
_CEI_INDEX_HOSTS = ("www.cei.cn", "ibe.cei.cn")
_CEI_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "cei-consumer"
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_TITLE_RE = re.compile(r"消费者信心指数（(\d{4})年(\d{1,2})月）")
_INDEX_TITLE_RE = re.compile(r"^消费者信心指数（(\d{4})年(\d{1,2})月）$")
_RELEASE_DATE_RE = re.compile(r"时间\s*[：:]\s*(\d{4}-\d{2}-\d{2})")
_SOURCE_RE = re.compile(r"来源\s*[：:]\s*国家统计局(?![\u3400-\u9fff])")
_TABLE_COLUMNS = (
    "日期",
    "消费者预期指数",
    "消费者满意指数",
    "消费者信心指数",
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
_session = curl_requests.Session(impersonate="chrome")


@dataclass(frozen=True, slots=True)
class ConsumerArticleRef:
    observation_date: dt.date
    release_date: dt.date
    title: str
    source_url: str


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _element_text(element) -> str:
    return " ".join("".join(element.itertext()).split())


def _is_cei_https_url(value: object) -> bool:
    try:
        parsed = urlsplit(str(value))
        port = parsed.port
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme.lower() == "https"
        and port in {None, 443}
        and hostname in _CEI_APPROVED_HOSTS
    )


def _canonical_cei_url(
    base_url: str,
    href: object,
    *,
    upgrade_index_http: bool = False,
) -> str:
    absolute = urljoin(base_url, str(href or "").strip())
    parsed = urlsplit(absolute)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        upgrade_index_http
        and parsed.scheme.lower() == "http"
        and hostname in _CEI_APPROVED_HOSTS
        and parsed.port in {None, 80, 443}
    ):
        absolute = urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    if not _is_cei_https_url(absolute):
        raise ValueError(f"CEI evidence URL is not an approved HTTPS host: {absolute!r}")
    parsed = urlsplit(absolute)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path = re.sub(r"/{2,}", "/", parsed.path)
    # A default :443 and query ordering have no source semantics.  Canonical
    # URLs keep evidence metadata stable across CEI's old/new templates.
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit(("https", hostname, path, query, ""))


def consumer_index_url(year: int, *, host: str = "www.cei.cn") -> str:
    if year < CEI_CONSUMER_FIRST_INDEX_YEAR:
        raise ValueError(
            f"CEI consumer evidence starts from index year "
            f"{CEI_CONSUMER_FIRST_INDEX_YEAR}"
        )
    if host not in _CEI_INDEX_HOSTS:
        raise ValueError(f"unsupported CEI index host: {host!r}")
    return (
        f"https://{host}/defaultsite/s/column/"
        f"{CEI_CONSUMER_COLUMN_ID}_{year}.html?"
        "articleListType=1&coluOpenType=1"
    )


def _month_from_title(title: str) -> dt.date:
    match = _INDEX_TITLE_RE.fullmatch(_compact_text(title))
    if match is None:
        raise ValueError(f"invalid CEI consumer article title: {title!r}")
    year, month = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        raise ValueError(f"invalid CEI consumer article month: {title!r}")
    return dt.date(year, month, 1)


def parse_consumer_index(source: str, source_url: str) -> list[ConsumerArticleRef]:
    """Parse one CEI year index without trusting unrelated navigation links."""

    if not _is_cei_https_url(source_url):
        raise ValueError("CEI consumer index must use an approved HTTPS cei.cn URL")
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError, ParserError) as exc:
        raise ValueError("invalid CEI consumer index HTML") from exc

    by_identity: dict[tuple[dt.date, dt.date, str], ConsumerArticleRef] = {}
    for item in document.xpath("//li[.//a]"):
        anchors = item.xpath(".//a[@href]")
        for anchor in anchors:
            visible_title = _compact_text(_element_text(anchor))
            attribute_title = _compact_text(anchor.get("title"))
            title = (
                attribute_title
                if _INDEX_TITLE_RE.fullmatch(attribute_title)
                else visible_title
            )
            if not _INDEX_TITLE_RE.fullmatch(title):
                continue
            observation = _month_from_title(title)
            dates = {
                value
                for value in re.findall(
                    r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)",
                    _element_text(item),
                )
            }
            if len(dates) != 1:
                raise ValueError(
                    f"CEI index entry {title!r} must contain one release date"
                )
            try:
                release_date = dt.date.fromisoformat(next(iter(dates)))
            except ValueError as exc:
                raise ValueError(f"invalid CEI index release date for {title!r}") from exc
            article_url = _canonical_cei_url(
                source_url, anchor.get("href"), upgrade_index_http=True
            )
            ref = ConsumerArticleRef(
                observation_date=observation,
                release_date=release_date,
                title=title,
                source_url=article_url,
            )
            by_identity[(observation, release_date, article_url)] = ref

    if not by_identity:
        raise ValueError("CEI consumer index contains no matching article entries")
    return sorted(
        by_identity.values(),
        key=lambda item: (
            item.observation_date,
            item.release_date,
            item.source_url,
        ),
    )


def _article_header(document) -> tuple[str, dt.date, str]:
    containers = document.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' xx_con_tile ')]"
    )
    if len(containers) != 1:
        raise ValueError("CEI consumer article must contain one xx_con_tile header")
    header = containers[0]
    header_text = _element_text(header)
    title_strings = _TITLE_RE.finditer(_compact_text(header_text))
    matches = list(title_strings)
    if len(matches) != 1:
        raise ValueError("CEI consumer article must contain one title month")
    title = matches[0].group(0)
    observation = _month_from_title(title)

    dates = _RELEASE_DATE_RE.findall(header_text)
    if len(set(dates)) != 1:
        raise ValueError("CEI consumer article must contain one page release date")
    try:
        release_date = dt.date.fromisoformat(dates[0])
    except ValueError as exc:
        raise ValueError("invalid CEI consumer page release date") from exc
    if len(_SOURCE_RE.findall(header_text)) != 1:
        raise ValueError("CEI consumer article source must be 国家统计局")
    return title, release_date, observation


def _matching_table(document) -> list[list[str]]:
    candidates: list[list[list[str]]] = []
    roots = document.xpath("//*[@id='content']")
    if len(roots) != 1:
        raise ValueError("CEI consumer article must contain one content container")
    # Word-exported CEI pages can wrap the data table in layout tables.  Only
    # leaf tables are considered, preventing the same cells from being counted
    # once through an outer wrapper and again through the real table.
    for table in roots[0].xpath(".//table[not(.//table)]"):
        rows: list[list[str]] = []
        for tr in table.xpath(".//tr"):
            cells = [
                _compact_text(_element_text(cell))
                for cell in tr.xpath("./th|./td")
            ]
            if cells and any(cells):
                rows.append(cells)
        if not rows:
            continue
        if tuple(rows[0]) == _TABLE_COLUMNS:
            if any(len(row) != 4 for row in rows):
                raise ValueError("CEI consumer data table must have exactly four columns")
            candidates.append(rows)
    if len(candidates) != 1:
        raise ValueError("CEI consumer article must contain one four-column data table")
    return candidates[0]


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid CEI {field} value: {value!r}") from exc
    if not result.is_finite() or result < 0 or result > 200:
        raise ValueError(f"CEI {field} value must be between 0 and 200")
    return result


def _parse_consumer_release_history(
    source: str,
    source_url: str,
    *,
    expected_observation: dt.date | None = None,
    expected_release_date: dt.date | None = None,
    strict_title: bool = False,
) -> tuple[dt.date, dict[str, pd.DataFrame]]:
    """Parse valid snapshots and retain the page date that proves each value."""

    if not _is_cei_https_url(source_url):
        raise ValueError("CEI consumer release must use an approved HTTPS cei.cn URL")
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError, ParserError) as exc:
        raise ValueError("invalid CEI consumer article HTML") from exc
    title, release_date, observation = _article_header(document)
    if expected_observation is not None and observation != expected_observation:
        raise ValueError("CEI article title month does not match its index entry")
    if expected_release_date is not None and release_date != expected_release_date:
        raise ValueError("CEI page release date does not match its index entry")

    raw_rows: dict[dt.date, list[str]] = {}
    for cells in _matching_table(document)[1:]:
        date_text = cells[0]
        match = re.fullmatch(r"(\d{4})\.(\d{1,2})", date_text)
        if match is None:
            continue
        year, month = (int(part) for part in match.groups())
        if not 1 <= month <= 12:
            continue
        row_observation = dt.date(year, month, 1)
        prior = raw_rows.get(row_observation)
        if prior is not None and prior != cells:
            raise ValueError(
                "CEI table contains conflicting rows for one observation month"
            )
        raw_rows[row_observation] = cells
    if observation not in raw_rows:
        raise ValueError("CEI table must contain exactly one row for the title month")
    if observation != max(raw_rows):
        raise ValueError("CEI article title month must equal the table's latest month")

    available_at = dt.datetime.combine(
        release_date + dt.timedelta(days=1), dt.time.min
    )
    rows_by_code: dict[str, list[dict]] = {
        code: [] for code in CEI_CONSUMER_EVIDENCE_CODES
    }
    for row_observation, cells in sorted(raw_rows.items()):
        _, expectations_text, satisfaction_text, confidence_text = cells
        try:
            expectations = _decimal(expectations_text, "expectations")
            satisfaction = _decimal(satisfaction_text, "satisfaction")
            confidence = _decimal(confidence_text, "confidence")
        except ValueError:
            if strict_title and row_observation == observation:
                raise
            continue
        expected_heavier = (
            Decimal("0.6") * expectations + Decimal("0.4") * satisfaction
        )
        satisfaction_heavier = (
            Decimal("0.4") * expectations + Decimal("0.6") * satisfaction
        )
        # NBS describes confidence as a weighted combination of satisfaction
        # and expectations, but does not publish one invariant pair of weights
        # for this historical series.  Both relationships have appeared in
        # official-source tables, and component aggregation/seasonal treatment
        # can also prevent an exact identity.  Keep the residuals as an audit
        # diagnostic; never reject a source-authenticated row on an invented
        # arithmetic constraint.
        expected_heavier_residual = abs(confidence - expected_heavier)
        satisfaction_heavier_residual = abs(confidence - satisfaction_heavier)
        provenance = json.dumps(
            {
                "availability_policy": "start_of_day_after_release_date",
                "collector": "cei_consumer_release_v1",
                "direct_official": False,
                "distribution_mirror": "中国经济信息网",
                "weighted_relationship_diagnostic": {
                    "blocking": False,
                    "nbs_published_fixed_weights": False,
                    "reported_confidence": format(confidence, "f"),
                    "residual_60pct_expectations_40pct_satisfaction": format(
                        expected_heavier_residual, "f"
                    ),
                    "residual_40pct_expectations_60pct_satisfaction": format(
                        satisfaction_heavier_residual, "f"
                    ),
                },
                "page_release_date_precision": "date",
                "publisher_source_label": "国家统计局",
                "row_policy": (
                    "article_title_month_only"
                    if row_observation == observation
                    else "rolling_table_later_confirmation"
                ),
                "title": title,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        values = {
            "CN_CONSUMER_EXPECTATIONS": expectations,
            "CN_CONSUMER_SATISFACTION": satisfaction,
            "CN_CONSUMER_CONFIDENCE": confidence,
        }
        for code, value in values.items():
            rows_by_code[code].append(
                {
                    "date": row_observation,
                    "value": float(value),
                    "release_date": release_date,
                    "available_at": available_at,
                    "source_url": _canonical_cei_url(_CEI_BASE_URL, source_url),
                    "status": "published",
                    "formula_version": None,
                    "provenance_json": provenance,
                    "evidence_kind": "official_distribution_mirror",
                    "chain_verified": True,
                    "availability_precision": "date_upper_bound",
                }
            )
    if not rows_by_code["CN_CONSUMER_CONFIDENCE"]:
        raise ValueError("CEI consumer page contains no valid observations")
    return observation, {
        code: pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
        for code, rows in rows_by_code.items()
    }


def parse_consumer_release_history(
    source: str,
    source_url: str,
    *,
    expected_observation: dt.date | None = None,
    expected_release_date: dt.date | None = None,
) -> dict[str, pd.DataFrame]:
    """Return valid rows as later-known snapshots at this page's date."""

    _, result = _parse_consumer_release_history(
        source,
        source_url,
        expected_observation=expected_observation,
        expected_release_date=expected_release_date,
    )
    return result


def parse_consumer_release(
    source: str,
    source_url: str,
    *,
    expected_observation: dt.date | None = None,
    expected_release_date: dt.date | None = None,
) -> dict[str, pd.DataFrame]:
    """Return only the title-month row; rolling history is never backdated."""

    title_month, history = _parse_consumer_release_history(
        source,
        source_url,
        expected_observation=expected_observation,
        expected_release_date=expected_release_date,
        strict_title=True,
    )
    result = {
        code: frame.loc[frame["date"] == title_month].reset_index(drop=True)
        for code, frame in history.items()
    }
    if result["CN_CONSUMER_CONFIDENCE"].empty:
        raise ValueError("CEI consumer title month contains no valid observation")
    return result


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _CEI_CACHE / f"{digest}.html"


def _cached_source(url: str, validator: Callable[[str], object]) -> str | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    try:
        source = path.read_text(encoding="utf-8")
        validator(source)
        return source
    except (OSError, ValueError, ParserError):
        return None


def _save_source(url: str, source: str) -> None:
    _CEI_CACHE.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(source, encoding="utf-8")
    temporary.replace(path)


def _load_source(
    url: str,
    validator: Callable[[str], object],
    *,
    fetch_text: Callable[[str], str] | None = None,
    attempts: int = 3,
) -> str:
    if not _is_cei_https_url(url):
        raise ValueError("CEI source must use an approved HTTPS cei.cn URL")
    if fetch_text is not None:
        source = fetch_text(url)
        validator(source)
        return source

    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = _session.get(url, headers=_HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = "utf-8"
            source = response.text
            validator(source)
            _save_source(url, source)
            return source
        except Exception as exc:  # transport and strict source validation
            error = exc
            if attempt + 1 < attempts:
                time.sleep(0.25 * (attempt + 1))

    cached = _cached_source(url, validator)
    if cached is not None:
        logger.warning("using last-good CEI cache for %s after %s", url, error)
        return cached
    raise RuntimeError(f"failed to load verified CEI source {url}: {error}") from error


def collect_consumer_release_evidence(
    *,
    start_year: int = CEI_CONSUMER_FIRST_INDEX_YEAR,
    end_year: int | None = None,
    fetch_text: Callable[[str], str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Crawl CEI year indexes and return strict title-month release evidence."""

    final_year = end_year or dt.date.today().year
    if start_year < CEI_CONSUMER_FIRST_INDEX_YEAR or final_year < start_year:
        raise ValueError("invalid CEI consumer index year range")

    refs_by_identity: dict[
        tuple[dt.date, dt.date, str], ConsumerArticleRef
    ] = {}
    # CEI's public and legacy front ends occasionally publish complementary
    # year indexes, and can assign different detail UUIDs to the same title and
    # page date.  Do not collapse those candidates before fetching: one UUID
    # can be public while its peer redirects to login or returns an empty shell.
    # Every distinct URL must first pass the same source/page/table checks;
    # semantic snapshot deduplication happens only after validation below.
    for year in range(start_year, final_year + 1):
        for host in _CEI_INDEX_HOSTS:
            index_url = consumer_index_url(year, host=host)
            try:
                source = _load_source(
                    index_url,
                    lambda body, url=index_url: parse_consumer_index(body, url),
                    fetch_text=fetch_text,
                )
                for ref in parse_consumer_index(source, index_url):
                    refs_by_identity[
                        (ref.observation_date, ref.release_date, ref.source_url)
                    ] = ref
            except (RuntimeError, ValueError) as exc:
                logger.error(
                    "rejecting CEI consumer index %s on %s: %s", year, host, exc
                )
    semantic_snapshots: dict[
        tuple[dt.date, float, float, float], dict[str, dict]
    ] = {}
    candidates_by_release: dict[
        tuple[dt.date, dt.date], list[ConsumerArticleRef]
    ] = {}
    for ref in refs_by_identity.values():
        candidates_by_release.setdefault(
            (ref.observation_date, ref.release_date), []
        ).append(ref)

    for release_identity, candidates in sorted(candidates_by_release.items()):
        parsed: dict[str, pd.DataFrame] | None = None
        selected_ref: ConsumerArticleRef | None = None
        # Prefer the public front end, but do not trust that preference until
        # the detail page itself passes every validation.  Try alternate UUIDs
        # and the legacy host only as fallbacks for this same release.
        ordered_candidates = sorted(
            candidates,
            key=lambda item: (
                0
                if urlsplit(item.source_url).hostname == "www.cei.cn"
                else 1,
                item.source_url,
            ),
        )
        for ref in ordered_candidates:
            validator = lambda body, item=ref: parse_consumer_release_history(
                body,
                item.source_url,
                expected_observation=item.observation_date,
                expected_release_date=item.release_date,
            )
            try:
                source = _load_source(
                    ref.source_url, validator, fetch_text=fetch_text
                )
                parsed = validator(source)
                selected_ref = ref
                break
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "rejecting CEI consumer release candidate %s from %s: %s",
                    ref.title,
                    ref.source_url,
                    exc,
                )
        if parsed is None or selected_ref is None:
            logger.error(
                "no verified CEI detail page for consumer release %s dated %s",
                release_identity[0],
                release_identity[1],
            )
            continue
        rows_by_code = {
            code: {
                row["date"]: row for row in parsed[code].to_dict("records")
            }
            for code in CEI_CONSUMER_EVIDENCE_CODES
        }
        common_dates = set.intersection(
            *(set(rows) for rows in rows_by_code.values())
        )
        for observed in common_dates:
            if observed < dt.date(2015, 12, 1):
                continue
            triple = (
                observed,
                rows_by_code["CN_CONSUMER_EXPECTATIONS"][observed]["value"],
                rows_by_code["CN_CONSUMER_SATISFACTION"][observed]["value"],
                rows_by_code["CN_CONSUMER_CONFIDENCE"][observed]["value"],
            )
            snapshot = {
                code: rows_by_code[code][observed]
                for code in CEI_CONSUMER_EVIDENCE_CODES
            }
            prior = semantic_snapshots.get(triple)
            if prior is None or (
                snapshot["CN_CONSUMER_CONFIDENCE"]["available_at"],
                snapshot["CN_CONSUMER_CONFIDENCE"]["source_url"],
            ) < (
                prior["CN_CONSUMER_CONFIDENCE"]["available_at"],
                prior["CN_CONSUMER_CONFIDENCE"]["source_url"],
            ):
                semantic_snapshots[triple] = snapshot

    records: dict[str, list[dict]] = {
        code: [] for code in CEI_CONSUMER_EVIDENCE_CODES
    }
    for snapshot in semantic_snapshots.values():
        for code in CEI_CONSUMER_EVIDENCE_CODES:
            records[code].append(snapshot[code])

    return {
        code: pd.DataFrame(rows, columns=_OUTPUT_COLUMNS).sort_values(
            ["date", "available_at", "source_url"], ignore_index=True
        )
        if rows
        else pd.DataFrame(columns=_OUTPUT_COLUMNS)
        for code, rows in records.items()
    }


__all__ = [
    "CEI_CONSUMER_COLUMN_ID",
    "CEI_CONSUMER_EVIDENCE_CODES",
    "CEI_CONSUMER_FIRST_INDEX_YEAR",
    "ConsumerArticleRef",
    "collect_consumer_release_evidence",
    "consumer_index_url",
    "parse_consumer_index",
    "parse_consumer_release",
    "parse_consumer_release_history",
]
