"""Bank of Korea policy-rate history from the official BOK website.

The English Bank of Korea Base Rate page embeds the chart data in an inline
``chartObj2_s`` JavaScript array.  The page currently appends one unlabeled,
unchanged observation to that array to show when the chart was last verified.
That final observation is *not* a policy decision, so the parser keeps its date
as metadata and removes it from the event history.

No API key is required.  Structural validation is deliberately strict: if the
Bank of Korea changes the page, refresh must fail visibly instead of silently
persisting a partial or misread policy-rate series.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from numbers import Real

import pandas as pd
import requests
from lxml import html as lxml_html
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BOK_BASE_RATE_URL = (
    "https://www.bok.or.kr/eng/singl/baseRate/"
    "progress.do?dataSeCd=01&menuNo=400016"
)
_HEADERS = {
    "User-Agent": "economic-dashboard/1.0 (+official macro data refresh)",
    "Accept-Language": "en-US,en;q=0.9",
}
_MIN_EVENT_ROWS = 20
_MIN_RATE = -5.0
_MAX_RATE = 25.0
_FETCH_ATTEMPTS = 3

_SESSION = requests.Session()
_SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
    ),
)


def _without_javascript_comments(source: str) -> str:
    """Remove JS comments while preserving comment markers inside strings."""

    out: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    state = "code"

    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "line_comment":
            if char in "\r\n":
                out.append(char)
                state = "code"
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 2
            else:
                # Retain newlines so declaration anchors still reflect source lines.
                if char in "\r\n":
                    out.append(char)
                index += 1
            continue

        if quote is not None:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char in {'"', "'", "`"}:
            quote = char
            out.append(char)
            index += 1
        elif char == "/" and following == "/":
            state = "line_comment"
            index += 2
        elif char == "/" and following == "*":
            state = "block_comment"
            index += 2
        else:
            out.append(char)
            index += 1

    if state == "block_comment":
        raise ValueError("BOK Base Rate page contains an unterminated block comment")
    return "".join(out)


def _active_scripts(source: str) -> list[str]:
    try:
        document = lxml_html.fromstring(source)
    except (TypeError, ValueError) as exc:
        raise ValueError("BOK Base Rate page is not valid HTML") from exc

    scripts = document.xpath("//script[contains(., 'chartObj2_s')]/text()")
    if not scripts:
        raise ValueError("BOK Base Rate chart script was not found")
    return [_without_javascript_comments(str(script)) for script in scripts]


def _decode_assignment(scripts: list[str], variable: str) -> object:
    declaration = re.compile(
        rf"(?m)^[ \t]*var[ \t]+{re.escape(variable)}[ \t]*=[ \t]*"
    )
    matches: list[tuple[str, re.Match[str]]] = []
    for script in scripts:
        matches.extend((script, match) for match in declaration.finditer(script))

    if len(matches) != 1:
        raise ValueError(
            f"BOK Base Rate page must contain exactly one active {variable} "
            f"declaration; found {len(matches)}"
        )

    script, match = matches[0]
    try:
        value, _ = json.JSONDecoder().raw_decode(script[match.end() :].lstrip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"BOK Base Rate {variable} is not valid JSON data") from exc
    return value


def _parse_rows(raw_rows: object) -> list[tuple[dt.date, float]]:
    if not isinstance(raw_rows, list):
        raise ValueError("BOK Base Rate chart data is not an array")

    rows: list[tuple[dt.date, float]] = []
    for position, row in enumerate(raw_rows, start=1):
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError(f"BOK Base Rate row {position} is not a date/rate pair")
        raw_date, raw_rate = row
        if not isinstance(raw_date, str):
            raise ValueError(f"BOK Base Rate row {position} has a non-text date")
        try:
            event_date = dt.datetime.strptime(raw_date.strip(), "%Y/%m/%d").date()
        except ValueError as exc:
            raise ValueError(
                f"BOK Base Rate row {position} has an invalid date"
            ) from exc

        if isinstance(raw_rate, bool) or not isinstance(raw_rate, Real):
            raise ValueError(f"BOK Base Rate row {position} has a non-numeric rate")
        rate = float(raw_rate)
        if not math.isfinite(rate) or not _MIN_RATE <= rate <= _MAX_RATE:
            raise ValueError(
                f"BOK Base Rate row {position} is outside the valid rate range"
            )
        rows.append((event_date, rate))

    if any(current[0] <= previous[0] for previous, current in zip(rows, rows[1:])):
        raise ValueError("BOK Base Rate dates are not strictly ascending")
    return rows


def _parse_label_months(raw_labels: object) -> list[str]:
    if not isinstance(raw_labels, list):
        raise ValueError("BOK Base Rate chart labels are not an array")

    months: list[str] = []
    for position, label in enumerate(raw_labels, start=1):
        if (
            not isinstance(label, list)
            or len(label) != 1
            or not isinstance(label[0], str)
        ):
            raise ValueError(f"BOK Base Rate label {position} has an invalid shape")
        matched = re.fullmatch(r"\[(\d{4}\.\d{2})\]", label[0].strip())
        if matched is None:
            raise ValueError(f"BOK Base Rate label {position} has an invalid month")
        months.append(matched.group(1))
    return months


def parse_bok_base_rate_html(source: str) -> pd.DataFrame:
    """Parse official BOK HTML into policy-change events.

    Returned columns are ``date``, ``value`` and ``source_url``.
    ``DataFrame.attrs`` contains:

    - ``verified_through``: last date through which the page verified the rate;
    - ``latest_event_date``: effective date of the latest actual rate change;
    - ``verification_extension_removed``: whether an unlabeled same-rate row was
      removed from the event history;
    - ``source_url``: official page URL.
    """

    if not isinstance(source, str) or not source.strip():
        raise ValueError("BOK Base Rate page is empty")

    scripts = _active_scripts(source)
    rows = _parse_rows(_decode_assignment(scripts, "chartObj2_s"))
    label_months = _parse_label_months(
        _decode_assignment(scripts, "chartObj2Labels")
    )

    if len(label_months) not in {len(rows), len(rows) - 1}:
        raise ValueError(
            "BOK Base Rate label count must equal the observation count or be "
            "shorter by exactly one verification row"
        )

    labeled_rows = rows[: len(label_months)]
    for position, ((event_date, _), label_month) in enumerate(
        zip(labeled_rows, label_months), start=1
    ):
        if event_date.strftime("%Y.%m") != label_month:
            raise ValueError(
                f"BOK Base Rate label {position} does not match its event month"
            )

    verified_through = rows[-1][0]
    extension_removed = len(label_months) == len(rows) - 1
    if extension_removed:
        if len(rows) < 2 or rows[-1][1] != rows[-2][1]:
            raise ValueError(
                "BOK Base Rate unlabeled final row changes the rate and cannot be "
                "treated as a verification-only observation"
            )
        rows = rows[:-1]

    if len(rows) < _MIN_EVENT_ROWS:
        raise ValueError(
            f"BOK Base Rate history has fewer than {_MIN_EVENT_ROWS} policy events"
        )

    frame = pd.DataFrame(rows, columns=["date", "value"])
    frame["source_url"] = BOK_BASE_RATE_URL
    frame.attrs.update(
        {
            "verified_through": verified_through,
            "latest_event_date": rows[-1][0],
            "verification_extension_removed": extension_removed,
            "source_url": BOK_BASE_RATE_URL,
        }
    )
    return frame


def fetch_bok_base_rate() -> pd.DataFrame:
    """Fetch and parse the official Bank of Korea Base Rate event history."""

    last_error: requests.RequestException | None = None
    for _ in range(_FETCH_ATTEMPTS):
        try:
            response = _SESSION.get(BOK_BASE_RATE_URL, headers=_HEADERS, timeout=45)
            response.raise_for_status()
            source = response.text
        except requests.RequestException as exc:
            # Requests may surface a truncated chunk only while materialising
            # response.text, after the adapter-level retry window has ended.
            last_error = exc
            continue
        return parse_bok_base_rate_html(source)
    assert last_error is not None
    raise last_error


BOK_FETCHERS = {"KR_BOK": fetch_bok_base_rate}
