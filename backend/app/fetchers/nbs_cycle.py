"""Recent China industrial production, core CPI and PPI from NBS releases.

Core CPI is read from the official monthly CPI table row for prices excluding
food and energy, using its explicitly labelled year-on-year column.  Only a
rolling recent official window is requested here; the strict historical
evidence backfill owns full archive coverage.  CN_IP joins its recent official
window to an explicitly labelled transport-mirror backfill.  PPI similarly
keeps its long aggregate-source history while official commentary rows provide
exact release metadata for the rolling recent window.
"""

import datetime as dt
import html
import math
import re
import time
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from lxml import html as lxml_html

NBS_BASE = "https://www.stats.gov.cn/sj/"
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.stats.gov.cn/sj/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
_CACHE_TTL = 6 * 60 * 60
_PAGE_COUNT = 14
_cache: dict[str, dict] = {}


_INFLATION_TITLE_RE = re.compile(
    r"(?P<year>20\d{2})年(?P<month>\d{1,2})月份CPI(?:和|、)PPI数据"
)


def inflation_title_observation(title: str) -> dt.date | None:
    """Return the month named by an NBS monthly CPI/PPI commentary title."""

    match = _INFLATION_TITLE_RE.search(" ".join(str(title).split()))
    if match is None:
        return None
    year, month = int(match.group("year")), int(match.group("month"))
    if not 1 <= month <= 12:
        return None
    return dt.date(year, month, 1)


def _article_blocks(source: str) -> list[str]:
    """Return bounded visible blocks; never concatenate neighbouring nodes."""

    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid NBS inflation article HTML") from exc
    blocks: list[str] = []
    for node in document.xpath("//p|//li|//h1|//h2|//h3|//h4"):
        text = " ".join(node.text_content().split())
        if text:
            blocks.append(text)
    return blocks


def _signed(direction: str, number: str) -> float:
    value = float(number)
    return -value if direction == "下降" else value


def _previous_month(observed: dt.date) -> dt.date:
    period = pd.Period(observed, freq="M") - 1
    return dt.date(period.year, period.month, 1)


def parse_core_cpi_values(source: str, observed: dt.date) -> dict[dt.date, float]:
    """Parse only explicit core-CPI statements from one monthly commentary.

    Every expression must be fully contained in one HTML paragraph/list item.
    The patterns name ``核心CPI`` again immediately before their values, so a
    section heading can never be joined to a later headline-CPI percentage.
    A small set of explicit retrospective forms is retained because NBS
    sometimes publishes a prior month's core value only in the next release.
    """

    values: dict[dt.date, set[float]] = {}

    def add(date: dt.date, value: float) -> None:
        values.setdefault(date, set()).add(round(value, 6))

    transition = re.compile(
        r"(?:扣除食品和能源价格的)?核心\s*CPI"
        r"[^。；;！？]{0,28}?同比\s*由上月\s*"
        r"(?P<previous_direction>上涨|下降)\s*(?P<previous>\d+(?:\.\d+)?)%"
        r"\s*转为\s*(?P<current_direction>上涨|下降)\s*"
        r"(?P<current>\d+(?:\.\d+)?)%"
    )
    paired_months = re.compile(
        r"(?P<first_month>\d{1,2})月份和(?P<second_month>\d{1,2})月份"
        r"(?:扣除食品和能源价格的)?核心\s*CPI\s*同比\s*分别\s*"
        r"(?P<first_direction>上涨|下降)\s*(?P<first>\d+(?:\.\d+)?)%"
        r"\s*和\s*(?P<second_direction>上涨|下降)?\s*"
        r"(?P<second>\d+(?:\.\d+)?)%"
    )
    direct = re.compile(
        r"(?:扣除食品和能源价格的)?核心\s*CPI"
        r"(?P<middle>[^。；;！？%]{0,28}?)同比\s*"
        r"(?P<tail>[^。；;！？%]{0,18}?)(?P<direction>上涨|下降)\s*"
        r"(?P<number>\d+(?:\.\d+)?)%"
    )
    reaches = re.compile(
        r"(?:扣除食品和能源价格的)?核心\s*CPI"
        r"\s*同比\s*(?P<rate_word>涨幅|降幅)[^。；;！？%]{0,20}?"
        r"(?:升至|回升至|扩大至|收窄至|为)\s*"
        r"(?P<number>\d+(?:\.\d+)?)%"
    )
    after_mom = re.compile(
        r"(?:扣除食品和能源价格的)?核心\s*CPI"
        r"[^。！？]{0,45}?环比[^。；;！？%]{0,20}?%\s*[；;]\s*"
        r"同比\s*(?P<direction>上涨|下降)\s*"
        r"(?P<number>\d+(?:\.\d+)?)%"
    )

    for block in _article_blocks(source):
        has_transition = False
        for match in paired_months.finditer(block):
            first_month = int(match.group("first_month"))
            second_month = int(match.group("second_month"))
            if not (1 <= first_month <= 12 and 1 <= second_month <= 12):
                continue
            first_year = observed.year - (first_month > observed.month)
            second_year = observed.year - (second_month > observed.month)
            add(
                dt.date(first_year, first_month, 1),
                _signed(match.group("first_direction"), match.group("first")),
            )
            second_direction = (
                match.group("second_direction") or match.group("first_direction")
            )
            add(
                dt.date(second_year, second_month, 1),
                _signed(second_direction, match.group("second")),
            )

        for match in transition.finditer(block):
            has_transition = True
            add(
                _previous_month(observed),
                _signed(
                    match.group("previous_direction"), match.group("previous")
                ),
            )
            add(
                observed,
                _signed(match.group("current_direction"), match.group("current")),
            )

        for match in after_mom.finditer(block):
            add(
                observed,
                _signed(match.group("direction"), match.group("number")),
            )

        for match in (() if has_transition else direct.finditer(block)):
            bridge = match.group("middle") + match.group("tail")
            if re.search(r"(?:CPI|环比|服务|工业消费品|食品|能源)", bridge, re.I):
                continue
            add(
                observed,
                _signed(match.group("direction"), match.group("number")),
            )
        for match in (() if has_transition else reaches.finditer(block)):
            number = float(match.group("number"))
            add(observed, -number if match.group("rate_word") == "降幅" else number)

    conflicts = {date: found for date, found in values.items() if len(found) > 1}
    if conflicts:
        rendered = ", ".join(
            f"{date:%Y-%m}={sorted(found)}"
            for date, found in sorted(conflicts.items())
        )
        raise ValueError(f"conflicting core CPI statements in one NBS article: {rendered}")
    return {date: next(iter(found)) for date, found in sorted(values.items())}


def parse_ppi_value(source: str) -> float | None:
    """Parse the headline PPI year-on-year rate, never purchase prices."""

    marker = (
        r"(?:全国\s*)?(?:工业生产者出厂价格指数\s*[（(]?PPI[）)]?|"
        r"工业生产者出厂价格|(?<!购进价格)PPI)"
    )
    direct = re.compile(
        marker + r"(?P<middle>[^。！？]{0,100}?)同比\s*"
        r"(?P<direction>上涨|下降|持平)\s*(?P<number>\d+(?:\.\d+)?)%",
        re.I,
    )
    reaches = re.compile(
        marker + r"(?P<middle>[^。！？]{0,100}?)同比\s*"
        r"(?P<rate_word>涨幅|降幅)[^。；;！？%]{0,20}?"
        r"(?:升至|回升至|扩大至|收窄至|为)\s*"
        r"(?P<number>\d+(?:\.\d+)?)%",
        re.I,
    )
    transition = re.compile(
        marker + r"(?P<middle>[^。！？]{0,100}?)同比\s*由上月\s*"
        r"(?P<previous_direction>上涨|下降)\s*\d+(?:\.\d+)?%\s*转为\s*"
        r"(?P<direction>上涨|下降)\s*(?P<number>\d+(?:\.\d+)?)%",
        re.I,
    )
    from_yoy = re.compile(
        r"从同比看[，,]\s*(?P<context>[^。！？%]{0,48}?)"
        r"(?:全国\s*)?PPI\s*(?P<direction>上涨|下降|持平)"
        r"(?:\s*(?P<number>\d+(?:\.\d+)?)%)?",
        re.I,
    )
    from_yoy_transition = re.compile(
        r"从同比看[，,]\s*(?P<context>[^。！？%]{0,48}?)"
        r"(?:全国\s*)?PPI\s*(?:同比\s*)?"
        r"由(?:上月\s*)?(?:(?:上涨|下降)\s*\d+(?:\.\d+)?%|持平)"
        r"\s*转为\s*(?P<direction>上涨|下降|持平)"
        r"(?:\s*(?P<number>\d+(?:\.\d+)?)%)?",
        re.I,
    )
    mom_then_yoy_transition = re.compile(
        marker
        + r"\s*环比[^。！？]{0,72}?[，,；;]\s*同比\s*"
        r"由(?:上月\s*)?(?:(?:上涨|下降)\s*\d+(?:\.\d+)?%|持平)"
        r"\s*转为\s*(?P<direction>上涨|下降|持平)"
        r"(?:\s*(?P<number>\d+(?:\.\d+)?)%)?",
        re.I,
    )
    found: set[float] = set()
    month_range = re.compile(
        r"(?:\d{1,2}|[一二三四五六七八九十]+)\s*"
        r"(?:-|—|–|－|~|～|至)\s*"
        r"(?:\d{1,2}|[一二三四五六七八九十]+)\s*月"
    )

    def add_directional(match: re.Match[str]) -> None:
        direction = match.group("direction")
        number = match.groupdict().get("number")
        if direction == "持平":
            if number is None or float(number) == 0.0:
                found.add(0.0)
            return
        if number is not None:
            found.add(round(_signed(direction, number), 6))

    def headline_context(match: re.Match[str], block: str) -> bool:
        context = match.groupdict().get("context", "") or ""
        middle = match.groupdict().get("middle", "") or ""
        local = context + middle
        if re.search(
            r"(?:CPI|购进价格|生产资料|生活资料|采掘工业|原材料工业|"
            r"加工工业|行业|合计影响)",
            local,
            re.I,
        ):
            return False
        prefix = block[max(0, match.start() - 120) : match.start()]
        # Qualifiers from a completed sentence can describe CPI or another
        # assertion in the same paragraph.  Only the current clause may scope
        # this PPI match.
        prefix_clause = re.split(r"[。！？；;]", prefix)[-1]
        cumulative_context = prefix_clause + local
        return not (
            re.search(r"(?:全年|季度|平均|累计)", cumulative_context)
            or month_range.search(cumulative_context)
        )

    for block in _article_blocks(source):
        for pattern in (from_yoy_transition, mom_then_yoy_transition):
            for match in pattern.finditer(block):
                if headline_context(match, block):
                    add_directional(match)

        transition_matches = list(transition.finditer(block))
        patterns = (transition,) if transition_matches else (direct, reaches, from_yoy)
        for pattern in patterns:
            for match in pattern.finditer(block):
                middle = match.groupdict().get("middle", "") or ""
                context = match.groupdict().get("context", "") or ""
                # The long official phrase can be followed by headline CPI
                # before the page reaches PPI. Never cross that second token.
                if not headline_context(match, block) or "购进价格" in middle or re.search(
                    r"(?:CPI|PPI)", middle + context, re.I
                ):
                    continue
                if pattern in (direct, transition, from_yoy):
                    add_directional(match)
                    continue
                else:
                    number = float(match.group("number"))
                    value = (
                        -number if match.group("rate_word") == "降幅" else number
                    )
                found.add(round(value, 6))
    if len(found) > 1:
        raise ValueError(f"conflicting PPI statements in one NBS article: {sorted(found)}")
    return next(iter(found)) if found else None


def _page_url(section: str, page: int) -> str:
    suffix = "" if page == 0 else f"index_{page}.html"
    return urljoin(NBS_BASE, f"{section}/{suffix}")


def _get_text(url: str) -> str:
    current = url
    for _ in range(6):
        parsed = urlparse(current)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() != "https" or not (
            hostname == "stats.gov.cn" or hostname.endswith(".stats.gov.cn")
        ):
            raise ValueError(f"refusing NBS redirect outside official HTTPS: {current}")
        response = requests.get(
            current,
            headers=_HEADERS,
            timeout=30,
            allow_redirects=False,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                raise RuntimeError("NBS redirect is missing a Location header")
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        if "Please enable JavaScript and refresh the page" in response.text:
            raise RuntimeError("NBS website returned a JavaScript verification page")
        return response.text
    raise RuntimeError("NBS request exceeded the redirect limit")


def _plain_text(source: str) -> str:
    source = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", source, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", source))).strip()


def _links(section: str, title_pattern: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    title_re = re.compile(title_pattern)
    link_re = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
    for page in range(_PAGE_COUNT):
        page_url = _page_url(section, page)
        for href, body in link_re.findall(_get_text(page_url)):
            title = _plain_text(body)
            if title_re.search(title):
                found[urljoin(page_url, href)] = title
    return [(url, title) for url, title in found.items()]


def _cached(key: str, loader) -> pd.DataFrame:
    now = time.time()
    entry = _cache.get(key)
    if entry is None or now - entry["ts"] > _CACHE_TTL:
        frame = loader()
        if frame.empty:
            raise RuntimeError(f"NBS release parser returned no rows for {key}")
        entry = {"df": frame, "ts": now}
        _cache[key] = entry
    return entry["df"].copy()


def _load_industrial() -> pd.DataFrame:
    rows: list[tuple[dt.date, float]] = []
    monthly_pattern = re.compile(
        r"(20\d{2})年(\d{1,2})月份规模以上工业增加值(?:同比)?(增长|下降)(\d+(?:\.\d+)?)%"
    )
    range_pattern = re.compile(
        r"(20\d{2})年1[—–-](\d{1,2})月份规模以上工业增加值(?:同比)?(增长|下降)(\d+(?:\.\d+)?)%"
    )
    article_pattern = re.compile(
        r"(\d{1,2})\s*月份[，,]\s*规模以上工业增加值同比\s*(?:实际\s*)?(增长|下降)\s*(\d+(?:\.\d+)?)%"
    )
    for url, title in _links("zxfb", r"规模以上工业增加值.*(?:增长|下降)"):
        match = monthly_pattern.search(title)
        if match:
            sign = -1 if match.group(3) == "下降" else 1
            rows.append((dt.date(int(match.group(1)), int(match.group(2)), 1), sign * float(match.group(4))))
            continue

        range_match = range_pattern.search(title)
        if not range_match:
            continue
        year, end_month = int(range_match.group(1)), int(range_match.group(2))
        # NBS publishes January-February as one combined observation.  For later
        # cumulative-title releases, the body contains the current month's rate.
        if end_month == 2:
            sign = -1 if range_match.group(3) == "下降" else 1
            rows.append((dt.date(year, 2, 1), sign * float(range_match.group(4))))
            continue
        article_matches = article_pattern.findall(_plain_text(_get_text(url)))
        if article_matches:
            cumulative_value = float(range_match.group(4))
            article_match = next(
                (item for item in article_matches if float(item[2]) != cumulative_value),
                article_matches[1] if len(article_matches) > 1 else article_matches[0],
            )
            sign = -1 if article_match[1] == "下降" else 1
            rows.append((dt.date(year, int(article_match[0]), 1), sign * float(article_match[2])))
    return pd.DataFrame(rows, columns=["date", "value"]).drop_duplicates("date").sort_values("date")


def _load_core_cpi() -> pd.DataFrame:
    from app.services.china_core_cpi_table_evidence import (
        collect_recent_core_cpi_table_evidence,
    )

    evidence = collect_recent_core_cpi_table_evidence()
    columns = [
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
        "formula_version",
    ]
    return evidence.loc[:, columns].sort_values("date", kind="stable").reset_index(
        drop=True
    )


def _load_ppi() -> pd.DataFrame:
    # Imported lazily because the strict evidence module reuses the parsers in
    # this module.  By the time a fetcher is called this module is fully loaded,
    # so the dependency cannot form an import-time cycle.
    from app.services.china_inflation_evidence import (
        collect_recent_ppi_release_evidence,
    )

    evidence = collect_recent_ppi_release_evidence()
    columns = [
        "date",
        "value",
        "release_date",
        "available_at",
        "source_url",
        "status",
        "formula_version",
    ]
    return evidence.loc[:, columns].sort_values("date", kind="stable").reset_index(
        drop=True
    )


def _load_ppi_history() -> pd.DataFrame:
    """Load the existing long PPI history without inventing release times."""

    # Import AkShare itself rather than macro_source: macro_source participates
    # in the aggregate fetcher registry and importing it from a directly loaded
    # nbs_cycle module would recreate the registry's historical import cycle.
    import akshare as ak

    raw = ak.macro_china_ppi()
    required = {"月份", "当月同比增长"}
    missing = required.difference(raw.columns)
    if missing:
        raise KeyError(f"PPI history source missing columns: {sorted(missing)}")
    history = raw[["月份", "当月同比增长"]].rename(
        columns={"月份": "date", "当月同比增长": "value"}
    )
    history["date"] = pd.to_datetime(
        history["date"], format="%Y年%m月份", errors="coerce"
    ).dt.date
    history["value"] = pd.to_numeric(history["value"], errors="coerce")
    history = history.dropna(subset=["date", "value"])
    history = history.loc[history["date"] >= dt.date(2000, 1, 1)].copy()
    history["release_date"] = None
    history["available_at"] = None
    history["source_url"] = None
    history["status"] = "historical_backfill"
    history["formula_version"] = None
    return history.sort_values("date", kind="stable").reset_index(drop=True)


def _merge_ppi_history(
    official: pd.DataFrame, history: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Retain long history while letting exact official releases win overlaps."""

    from app.fetchers.china_cycle_data import _frame

    if history is None:
        history = _cached("ppi_long_history", _load_ppi_history)
    def valid_dates(frame: pd.DataFrame) -> pd.Series:
        if not {"date", "value"}.issubset(frame.columns):
            raise ValueError("PPI history merge requires date/value columns")
        dates = pd.to_datetime(frame["date"], errors="coerce")
        values = pd.to_numeric(frame["value"], errors="coerce")
        finite = values.map(
            lambda value: math.isfinite(float(value)) if pd.notna(value) else False
        )
        return dates.loc[dates.notna() & finite]

    official_dates = valid_dates(official)
    history_dates = valid_dates(history)
    if official_dates.empty:
        raise RuntimeError("official recent PPI window has no valid month")
    if not history_dates.empty and history_dates.max() > official_dates.max():
        raise RuntimeError(
            "PPI long history is newer than the official recent window; "
            "refusing an aggregate-only latest month without release metadata"
        )
    history_rows = [
        {**row, "status": row.get("status") or "historical_backfill"}
        for row in history.to_dict("records")
    ]
    official_rows = [
        {**row, "status": row.get("status") or "published"}
        for row in official.to_dict("records")
    ]
    return _frame([*history_rows, *official_rows])


def fetch_cn_industrial_production() -> pd.DataFrame:
    # The NBS archive is deliberately a rolling official window. Join it to
    # the long transport-mirror history while retaining official precedence on
    # overlaps and leaving mirror publication time unknown.
    from app.fetchers.china_cycle_data import _merge_cn_ip_history

    official = _cached("industrial", _load_industrial).reset_index(drop=True)
    return _merge_cn_ip_history(official)


def fetch_cn_core_cpi() -> pd.DataFrame:
    return _cached("core_cpi_table_v1", _load_core_cpi).reset_index(drop=True)


def fetch_cn_ppi() -> pd.DataFrame:
    official = _cached("ppi_commentary_v2", _load_ppi).reset_index(drop=True)
    return _merge_ppi_history(official)


NBS_CYCLE_FETCHERS = {
    "CN_IP": fetch_cn_industrial_production,
    "CN_CORE_CPI": fetch_cn_core_cpi,
    "CN_PPI": fetch_cn_ppi,
}
