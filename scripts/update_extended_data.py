#!/usr/bin/env python3
"""
update_extended_data.py — Extended official datasets for the "Yields" site.

Builds the data behind the Yield Curve, Time Series, Federal Debt, Data
Revisions and sidebar views, using only the Python 3 standard library.

Sources
  1. U.S. Treasury Daily Par Yield Curve XML feed (1990-present, no key)
  2. U.S. Treasury Daily Par Real Yield Curve XML feed (TIPS, 2003-present, no key)
  3. FRED / ALFRED API, St. Louis Fed (needs the FRED_API_KEY environment variable):
       - pre-1990 Treasury yields (back to 1934), Fed funds target/effective rate,
         overnight reverse repo award rate, recession dates,
       - CPI, PCE, PPI, real GDP and unemployment (latest and as first reported),
       - economic release calendar.
  4. U.S. Treasury Fiscal Data API (no key): Daily Treasury Statement (cash balance,
     debt subject to limit, statutory debt limit) and Monthly Statement of the Public Debt.

Outputs (under site/data/, all JSON)
  manifest.json            metadata, source status, series catalog, sidebar data
  curve/YYYY.json          one file per year of daily curves (nominal + real)
  series/<ID>.json         one file per time series (dates + values)
  recessions.json          NBER recession periods
  revisions.json           revision history of key economic indicators
  debt/ceiling.json        debt subject to limit, statutory limit, cash balance
  debt/maturities.json     marketable/nonmarketable debt composition by month

Design rules (same as update_data.py)
  - Missing values stay null; nothing is invented or interpolated (pre-1954 bill
    yields are only available monthly and are shown as monthly observations).
  - A failing source keeps its previously saved files and is marked in manifest.json.
  - Files are only rewritten when their content changes, so scheduled runs do not
    create noisy commits.
  - The FRED API key is read from the environment and is never written to disk or logs.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_data as core  # noqa: E402  (shared fetch/parse helpers)


SCHEMA_VERSION = "2.0"
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "site" / "data"

CURVE_FIRST_YEAR = 1934
NOMINAL_FIRST_YEAR = 1990
REAL_FIRST_YEAR = 2003
# Bump to force a rebuild of the FRED-based 1934-1989 curve files.
PRE1990_BUILD_VERSION = 1

NOMINAL_KEYS: List[str] = [m["key"] for m in core.TREASURY_MATURITIES]
REAL_MATURITIES: List[Dict[str, Any]] = [
    {"key": "5Y", "xml_tag": "TC_5YEAR", "years": 5.0},
    {"key": "7Y", "xml_tag": "TC_7YEAR", "years": 7.0},
    {"key": "10Y", "xml_tag": "TC_10YEAR", "years": 10.0},
    {"key": "20Y", "xml_tag": "TC_20YEAR", "years": 20.0},
    {"key": "30Y", "xml_tag": "TC_30YEAR", "years": 30.0},
]
REAL_KEYS: List[str] = [m["key"] for m in REAL_MATURITIES]

TREASURY_REAL_PAGE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_real_yield_curve"
)
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_DOCS_URL = "https://fred.stlouisfed.org/docs/api/fred/"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/"
FISCAL_BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
FISCAL_DOCS_URL = "https://fiscaldata.treasury.gov/api-documentation/"

# Pre-1990 Treasury yields from FRED, per maturity in priority order:
# (series id, first date, last date, frequency "d" daily or "m" monthly).
PRE1990_SEGMENTS: Dict[str, List[Tuple[str, str, str, str]]] = {
    "3M": [
        ("DGS3MO", "1982-01-04", "1989-12-31", "d"),
        ("DTB3", "1954-01-04", "1981-12-31", "d"),
        ("TB3MS", "1934-01-01", "1953-12-31", "m"),
    ],
    "6M": [
        ("DGS6MO", "1982-01-04", "1989-12-31", "d"),
        ("DTB6", "1958-12-09", "1981-12-31", "d"),
    ],
    "1Y": [
        ("DGS1", "1962-01-02", "1989-12-31", "d"),
        ("GS1", "1953-04-01", "1961-12-31", "m"),
    ],
    "2Y": [("DGS2", "1976-06-01", "1989-12-31", "d")],
    "3Y": [
        ("DGS3", "1962-01-02", "1989-12-31", "d"),
        ("GS3", "1953-04-01", "1961-12-31", "m"),
    ],
    "5Y": [
        ("DGS5", "1962-01-02", "1989-12-31", "d"),
        ("GS5", "1953-04-01", "1961-12-31", "m"),
    ],
    "7Y": [("DGS7", "1969-07-01", "1989-12-31", "d")],
    "10Y": [
        ("DGS10", "1962-01-02", "1989-12-31", "d"),
        ("GS10", "1953-04-01", "1961-12-31", "m"),
    ],
    "20Y": [
        ("DGS20", "1962-01-02", "1986-12-31", "d"),
        ("GS20", "1953-04-01", "1961-12-31", "m"),
    ],
    "30Y": [("DGS30", "1977-02-15", "1989-12-31", "d")],
}

# Daily policy-rate series from FRED (weekend observations are dropped).
FRED_RATE_SERIES: List[Dict[str, Any]] = [
    {"id": "EFFR", "fred": "DFF", "label": "Fed funds effective rate", "group": "fed", "order": 40},
    {"id": "FED_TARGET", "fred": "DFEDTAR", "label": "Fed funds target rate (to 2008)", "group": "fed", "order": 41},
    {"id": "FED_TARGET_LOWER", "fred": "DFEDTARL", "label": "Fed funds target range, lower", "group": "fed", "order": 42},
    {"id": "FED_TARGET_UPPER", "fred": "DFEDTARU", "label": "Fed funds target range, upper", "group": "fed", "order": 43},
    {"id": "RRP_AWARD", "fred": "RRPONTSYAWARD", "label": "Overnight reverse repo award rate (RRP)", "group": "fed", "order": 44},
]

# Economic indicators. transform: "yoy" / "mom" percent change, or "level".
MACRO_METRICS: List[Dict[str, Any]] = [
    {"id": "CPI_YOY", "fred": "CPIAUCNS", "transform": "yoy", "freq": "monthly", "revisions": True, "initial": False,
     "label": "CPI y/y % change (not seasonally adjusted)", "short": "CPI y/y",
     "revisionLabel": "CPI-U NSA — all items, not seasonally adjusted (YoY)"},
    {"id": "CPI_MOM", "fred": "CPIAUCSL", "transform": "mom", "freq": "monthly", "revisions": True, "initial": True,
     "label": "CPI m/m % change (seasonally adjusted)", "short": "CPI m/m",
     "revisionLabel": "CPI-U SA — all items, seasonally adjusted (MoM)"},
    {"id": "PCE_YOY", "fred": "PCEPI", "transform": "yoy", "freq": "monthly", "revisions": True, "initial": True,
     "label": "PCE price index y/y % change", "short": "PCE y/y",
     "revisionLabel": "PCE price index (YoY)"},
    {"id": "PCE_MOM", "fred": "PCEPI", "transform": "mom", "freq": "monthly", "revisions": False, "initial": True,
     "label": "PCE price index m/m % change", "short": "PCE m/m",
     "revisionLabel": "PCE price index (MoM)"},
    {"id": "PPI_YOY", "fred": "PPIFID", "transform": "yoy", "freq": "monthly", "revisions": True, "initial": True,
     "label": "PPI final demand y/y % change (not seasonally adjusted)", "short": "PPI y/y",
     "revisionLabel": "PPI NSA — final demand, not seasonally adjusted (YoY)"},
    {"id": "PPI_MOM", "fred": "PPIFIS", "transform": "mom", "freq": "monthly", "revisions": True, "initial": True,
     "label": "PPI final demand m/m % change (seasonally adjusted)", "short": "PPI m/m",
     "revisionLabel": "PPI SA — final demand, seasonally adjusted (MoM)"},
    {"id": "GDP", "fred": "A191RL1Q225SBEA", "transform": "level", "freq": "quarterly", "revisions": True, "initial": True,
     "label": "Real GDP, annualized q/q % change", "short": "Real GDP",
     "revisionLabel": "Real GDP % change (annualized q/q)"},
    {"id": "UNRATE", "fred": "UNRATE", "transform": "level", "freq": "monthly", "revisions": True, "initial": True,
     "label": "Unemployment rate", "short": "Unemployment",
     "revisionLabel": "Unemployment rate"},
]

# Real-time (ALFRED) history is requested from this observation date onward.
REALTIME_OBSERVATION_START = "1999-01-01"
# A first release later than this after the period end means ALFRED did not
# archive the true first release, so that period is left out of first-release data.
MAX_FIRST_RELEASE_LAG_DAYS = {"monthly": 100, "quarterly": 150}
MAX_REVISION_EVENTS = 8000

CALENDAR_RELEASES: List[Dict[str, str]] = [
    {"series": "CPIAUCSL", "label": "CPI", "url": "https://www.bls.gov/schedule/news_release/cpi.htm"},
    {"series": "PPIFIS", "label": "PPI", "url": "https://www.bls.gov/schedule/news_release/ppi.htm"},
    {"series": "UNRATE", "label": "Jobs report", "url": "https://www.bls.gov/schedule/news_release/empsit.htm"},
    {"series": "A191RL1Q225SBEA", "label": "GDP", "url": "https://www.bea.gov/news/schedule"},
    {"series": "PCEPI", "label": "PCE inflation", "url": "https://www.bea.gov/news/schedule"},
]
FOMC_RELEASE_ID = 101
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_MINUTES_LAG_DAYS = 21  # minutes are released three weeks after each decision
CALENDAR_HORIZON_DAYS = 75

SOURCE_INFO: Dict[str, Dict[str, str]] = {
    "treasuryNominal": {
        "name": "U.S. Treasury — Daily Par Yield Curve Rates (1990–present)",
        "shortName": "Treasury curve",
        "url": core.TREASURY_PAGE_URL,
        "docsUrl": core.TREASURY_DOCS_URL,
    },
    "treasuryReal": {
        "name": "U.S. Treasury — Daily Par Real Yield Curve Rates (TIPS, 2003–present)",
        "shortName": "Treasury real",
        "url": TREASURY_REAL_PAGE_URL,
        "docsUrl": core.TREASURY_DOCS_URL,
    },
    "fred": {
        "name": "FRED / ALFRED, Federal Reserve Bank of St. Louis",
        "shortName": "FRED",
        "url": "https://fred.stlouisfed.org/",
        "docsUrl": FRED_DOCS_URL,
    },
    "fiscal": {
        "name": "U.S. Treasury Fiscal Data — Daily Treasury Statement & Monthly Statement of the Public Debt",
        "shortName": "Fiscal Data",
        "url": "https://fiscaldata.treasury.gov/",
        "docsUrl": FISCAL_DOCS_URL,
    },
}


DataSourceError = core.DataSourceError
Fetcher = Callable[[str], bytes]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def to_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def month_shift(date_str: str, months: int) -> str:
    """Shift a YYYY-MM-01 style date by a number of months (day is kept at 01)."""
    d = to_date(date_str)
    index = d.year * 12 + (d.month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}-01"


def period_end(date_str: str, freq: str) -> datetime.date:
    """Last calendar day of the month (or quarter) starting at date_str."""
    d = to_date(date_str)
    months = 3 if freq == "quarterly" else 1
    end_month_index = d.year * 12 + (d.month - 1) + months - 1
    year, month = end_month_index // 12, end_month_index % 12 + 1
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def parse_number(raw: Any) -> Optional[float]:
    """Parse numbers from FRED ('.' = missing) and Fiscal Data ('null' = missing)."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    else:
        text = str(raw).strip().replace(",", "")
        if text in {"", ".", "null", "NULL", "None", "N/A", "NA", "*"}:
            return None
        try:
            val = float(text)
        except ValueError:
            return None
    if val != val or val in (float("inf"), float("-inf")):
        return None
    return val


def round_or_none(val: Optional[float], digits: int) -> Optional[float]:
    if val is None:
        return None
    if digits <= 0:
        return int(round(val, digits))
    rounded = round(val, digits)
    return 0.0 if rounded == 0 else rounded


def is_weekday(date_str: str) -> bool:
    return to_date(date_str).weekday() < 5


def redact(message: str, secret: Optional[str]) -> str:
    """Remove an API key from any text before it is logged or saved."""
    text = re.sub(r"(api_key=)[^&\s'\"]+", r"\1REDACTED", str(message))
    if secret:
        text = text.replace(secret, "REDACTED")
    return text


def canonical_json(data: Any, compact: bool = True) -> str:
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n"
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_json_if_changed(path: Path, data: Any, compact: bool = True) -> bool:
    """Atomically write JSON when the serialized content differs. Returns True if written."""
    text = canonical_json(data, compact=compact)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    return True


def load_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# U.S. Treasury yield curve feeds (nominal and real)
# ---------------------------------------------------------------------------

def build_treasury_feed_url(feed: str, year: int) -> str:
    params = {"data": feed, "field_tdr_date_value": str(year)}
    return f"{core.TREASURY_ENDPOINT}?{urllib.parse.urlencode(params)}"


def parse_treasury_real_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse the daily_treasury_real_yield_curve feed into [{date, yields: {5Y: ...}}]."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DataSourceError(f"Invalid XML from Treasury real yield feed: {exc}") from exc

    ns = core.XML_NAMESPACES
    m_null_attr = f"{{{ns['m']}}}null"
    by_date: Dict[str, Dict[str, Optional[float]]] = {}
    for entry in root.findall("atom:entry", ns):
        props = entry.find(".//m:properties", ns)
        if props is None:
            continue
        date_elem = props.find("d:NEW_DATE", ns)
        if date_elem is None or not date_elem.text:
            continue
        raw_date = date_elem.text.strip()[:10]
        if not core.is_valid_iso_date(raw_date):
            raise DataSourceError(f"Invalid NEW_DATE in Treasury real yield XML: {date_elem.text!r}")
        values: Dict[str, Optional[float]] = {}
        for mat in REAL_MATURITIES:
            elem = props.find(f"d:{mat['xml_tag']}", ns)
            if elem is None or elem.attrib.get(m_null_attr, "").lower() == "true":
                values[mat["key"]] = None
                continue
            try:
                values[mat["key"]] = core.parse_optional_float(elem.text)
            except ValueError as exc:
                raise DataSourceError(f"Invalid real yield for {mat['xml_tag']} on {raw_date}: {exc}") from exc
        if any(v is not None for v in values.values()):
            by_date[raw_date] = values
    return [{"date": d, "yields": by_date[d]} for d in sorted(by_date)]


class CurveYear:
    """In-memory model of one curve/YYYY.json file."""

    def __init__(self, year: int) -> None:
        self.year = year
        self.source = "treasury" if year >= NOMINAL_FIRST_YEAR else "fred"
        self.nominal: Dict[str, Dict[str, Optional[float]]] = {}
        self.real: Dict[str, Dict[str, Optional[float]]] = {}
        self.notes: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "CurveYear":
        year = int(payload["year"])
        model = cls(year)
        model.source = str(payload.get("source") or model.source)
        model.notes = payload.get("notes")
        dates = payload.get("dates") or []
        for block_name, keys, target in (("nominal", NOMINAL_KEYS, model.nominal), ("real", REAL_KEYS, model.real)):
            block = payload.get(block_name) or {}
            for idx, date_str in enumerate(dates):
                row = {}
                for key in keys:
                    column = block.get(key)
                    val = column[idx] if isinstance(column, list) and idx < len(column) else None
                    row[key] = val if isinstance(val, (int, float)) and not isinstance(val, bool) else None
                if any(v is not None for v in row.values()):
                    target[date_str] = row
        return model

    def to_payload(self) -> Dict[str, Any]:
        dates = sorted(set(self.nominal) | set(self.real))
        payload: Dict[str, Any] = {"year": self.year, "source": self.source, "dates": dates}
        if self.notes:
            payload["notes"] = self.notes
        for block_name, keys, rows in (("nominal", NOMINAL_KEYS, self.nominal), ("real", REAL_KEYS, self.real)):
            block: Dict[str, List[Optional[float]]] = {}
            for key in keys:
                column = [round_or_none((rows.get(d) or {}).get(key), 4) for d in dates]
                if any(v is not None for v in column):
                    block[key] = column
            if block:
                payload[block_name] = block
        return payload

    def last_date(self) -> Optional[str]:
        dates = sorted(set(self.nominal) | set(self.real))
        return dates[-1] if dates else None


def load_curve_years(curve_dir: Path) -> Dict[int, CurveYear]:
    years: Dict[int, CurveYear] = {}
    if not curve_dir.exists():
        return years
    for path in sorted(curve_dir.glob("*.json")):
        payload = load_json(path)
        if isinstance(payload, dict) and isinstance(payload.get("year"), int):
            try:
                years[payload["year"]] = CurveYear.from_payload(payload)
            except (KeyError, TypeError, ValueError):
                continue
    return years


def treasury_years_to_fetch(
    existing: Dict[int, CurveYear], today: datetime.date, first_year: int, block: str
) -> List[int]:
    """Years whose Treasury data is missing or may still change (current/previous year)."""
    wanted: List[int] = []
    for year in range(first_year, today.year + 1):
        model = existing.get(year)
        rows = (model.nominal if block == "nominal" else model.real) if model else {}
        if year == today.year:
            wanted.append(year)
        elif not rows or model is None or model.source != "treasury":
            wanted.append(year)
        elif year == today.year - 1:
            last = max(rows) if rows else ""
            if last < f"{year}-12-28" or today <= datetime.date(today.year, 1, 31):
                wanted.append(year)
    return wanted


def update_treasury_block(
    curve_years: Dict[int, CurveYear],
    today: datetime.date,
    fetcher: Fetcher,
    block: str,
    pause: Callable[[], None] = lambda: None,
) -> Tuple[int, Optional[str]]:
    """Fetch nominal or real Treasury curve years into curve_years. Returns (years fetched, latest date)."""
    if block == "nominal":
        feed, first_year, parser = "daily_treasury_yield_curve", NOMINAL_FIRST_YEAR, core.parse_treasury_xml
    else:
        feed, first_year, parser = "daily_treasury_real_yield_curve", REAL_FIRST_YEAR, parse_treasury_real_xml

    fetched = 0
    for year in treasury_years_to_fetch(curve_years, today, first_year, block):
        observations = parser(fetcher(build_treasury_feed_url(feed, year)))
        model = curve_years.get(year) or CurveYear(year)
        model.source = "treasury"
        model.notes = None
        rows = {
            obs["date"]: {key: obs["yields"].get(key) for key in (NOMINAL_KEYS if block == "nominal" else REAL_KEYS)}
            for obs in observations
            if obs["date"].startswith(f"{year:04d}-")
        }
        # An empty answer never wipes saved history (it is normal on the first
        # days of January, and otherwise most likely a transient feed problem).
        if rows:
            if block == "nominal":
                model.nominal = rows
            else:
                model.real = rows
            curve_years[year] = model
        fetched += 1
        pause()

    latest: Optional[str] = None
    for model in curve_years.values():
        rows = model.nominal if block == "nominal" else model.real
        if rows and model.source == "treasury":
            candidate = max(rows)
            latest = candidate if latest is None or candidate > latest else latest
    if latest is None:
        raise DataSourceError(f"Treasury {block} curve feed returned no observations.")
    return fetched, latest


def build_pre1990_curve(series_values: Dict[str, Dict[str, float]]) -> Dict[int, CurveYear]:
    """Splice FRED daily and monthly series into yearly curve models for 1934-1989."""
    daily_dates_by_month: Dict[str, set] = defaultdict(set)
    monthly_dates: set = set()
    for segments in PRE1990_SEGMENTS.values():
        for series_id, start, end, freq in segments:
            for date_str in series_values.get(series_id, {}):
                if not (start <= date_str <= end):
                    continue
                if freq == "d":
                    daily_dates_by_month[date_str[:7]].add(date_str)
                else:
                    monthly_dates.add(date_str[:7] + "-01")

    row_dates: List[str] = []
    for month_key in sorted(set(daily_dates_by_month) | {d[:7] for d in monthly_dates}):
        if daily_dates_by_month.get(month_key):
            row_dates.extend(sorted(daily_dates_by_month[month_key]))
        else:
            row_dates.append(month_key + "-01")

    years: Dict[int, CurveYear] = {}
    for date_str in row_dates:
        if date_str >= f"{NOMINAL_FIRST_YEAR}-01-01":
            continue
        row: Dict[str, Optional[float]] = {key: None for key in NOMINAL_KEYS}
        for key, segments in PRE1990_SEGMENTS.items():
            for series_id, start, end, freq in segments:
                if freq == "d":
                    covers = start <= date_str <= end
                    value = series_values.get(series_id, {}).get(date_str)
                else:
                    covers = start[:7] <= date_str[:7] <= end[:7]
                    value = series_values.get(series_id, {}).get(date_str[:7] + "-01")
                if covers:
                    row[key] = value
                    break
        if any(v is not None for v in row.values()):
            year = int(date_str[:4])
            model = years.setdefault(year, CurveYear(year))
            model.source = "fred"
            model.nominal[date_str] = row

    for year, model in years.items():
        notes = ["FRED historical series (Board of Governors H.15)."]
        if year < 1954:
            notes.append("Only monthly averages exist before 1954; each point is a monthly observation.")
        elif year < 1962:
            notes.append("1-, 3-, 5-, 10- and 20-year yields before 1962 are monthly averages repeated across each month.")
        if year < 1982:
            notes.append("3- and 6-month bills before 1982 are secondary-market rates on a discount basis.")
        model.notes = " ".join(notes)
    return years


# ---------------------------------------------------------------------------
# FRED / ALFRED client
# ---------------------------------------------------------------------------

class FredClient:
    """Minimal FRED API client with throttling (FRED allows 120 requests/minute)."""

    def __init__(
        self,
        api_key: str,
        fetcher: Fetcher,
        min_interval: float = 0.6,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("A FRED API key is required")
        self.api_key = api_key
        self.fetcher = fetcher
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self._last_call: Optional[float] = None
        self.request_count = 0

    def _throttle(self) -> None:
        if self._last_call is not None and self.min_interval > 0:
            wait = self.min_interval - (self.clock() - self._last_call)
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.clock()

    def get(self, path: str, **params: Any) -> Dict[str, Any]:
        query = {k: v for k, v in params.items() if v is not None}
        query["file_type"] = "json"
        query["api_key"] = self.api_key
        url = f"{FRED_BASE_URL}/{path}?{urllib.parse.urlencode(query)}"
        self._throttle()
        self.request_count += 1
        try:
            body = self.fetcher(url)
        except Exception as exc:  # network / HTTP errors carry the URL: redact it
            raise DataSourceError(redact(f"FRED request {path} failed: {exc}", self.api_key)) from None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataSourceError(f"FRED returned invalid JSON for {path}: {exc}") from None
        if not isinstance(payload, dict):
            raise DataSourceError(f"FRED returned an unexpected payload for {path}")
        if "error_code" in payload or "error_message" in payload:
            message = redact(str(payload.get("error_message", "unknown error")), self.api_key)
            raise DataSourceError(f"FRED error for {path}: {message}")
        return payload

    def observations(self, series_id: str, **params: Any) -> List[Tuple[str, float]]:
        payload = self.get("series/observations", series_id=series_id, **params)
        return parse_fred_observations(payload)

    def observations_realtime(self, series_id: str, observation_start: str) -> List[Tuple[str, float, str, str]]:
        payload = self.get(
            "series/observations",
            series_id=series_id,
            realtime_start="1776-07-04",
            realtime_end="9999-12-31",
            observation_start=observation_start,
        )
        return parse_fred_realtime_observations(payload)


def parse_fred_observations(payload: Dict[str, Any]) -> List[Tuple[str, float]]:
    rows = payload.get("observations")
    if not isinstance(rows, list):
        raise DataSourceError("FRED response is missing 'observations'")
    result: Dict[str, float] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        date_str = str(item.get("date", ""))
        value = parse_number(item.get("value"))
        if core.is_valid_iso_date(date_str) and value is not None:
            result[date_str] = value
    return sorted(result.items())


def parse_fred_realtime_observations(payload: Dict[str, Any]) -> List[Tuple[str, float, str, str]]:
    """Rows of (date, value, realtime_start, realtime_end); one row per published version."""
    rows = payload.get("observations")
    if not isinstance(rows, list):
        raise DataSourceError("FRED response is missing 'observations'")
    result: List[Tuple[str, float, str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        date_str = str(item.get("date", ""))
        start = str(item.get("realtime_start", ""))
        end = str(item.get("realtime_end", ""))
        value = parse_number(item.get("value"))
        if value is None or not core.is_valid_iso_date(date_str) or not core.is_valid_iso_date(start):
            continue
        if not core.is_valid_iso_date(end):
            end = "9999-12-31"
        result.append((date_str, value, start, end))
    result.sort(key=lambda r: (r[0], r[2]))
    return result


# ---------------------------------------------------------------------------
# Economic indicator transforms, first releases and revision history
# ---------------------------------------------------------------------------

def base_period(date_str: str, transform: str, freq: str) -> Optional[str]:
    if transform == "level":
        return None
    step = 3 if freq == "quarterly" else 1
    if transform == "mom":
        return month_shift(date_str, -step)
    if transform == "yoy":
        return month_shift(date_str, -12)
    raise ValueError(f"Unknown transform {transform}")


def apply_transform(value: Optional[float], base: Optional[float], transform: str) -> Optional[float]:
    if value is None:
        return None
    if transform == "level":
        return value
    if base is None or base == 0:
        return None
    return (value / base - 1.0) * 100.0


def transform_series(points: Sequence[Tuple[str, float]], transform: str, freq: str) -> List[Tuple[str, float]]:
    values = dict(points)
    result: List[Tuple[str, float]] = []
    for date_str, value in points:
        base_key = base_period(date_str, transform, freq)
        out = apply_transform(value, values.get(base_key) if base_key else None, transform)
        if out is not None:
            result.append((date_str, round(out, 3)))
    return result


class VintageHistory:
    """Every published version of each observation, from FRED real-time rows."""

    def __init__(self, rows: Iterable[Tuple[str, float, str, str]]) -> None:
        self.versions: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
        for date_str, value, start, end in rows:
            self.versions[date_str].append((start, end, value))
        for versions in self.versions.values():
            versions.sort()

    def value_asof(self, date_str: Optional[str], when: str) -> Optional[float]:
        if date_str is None:
            return None
        found = None
        for start, end, value in self.versions.get(date_str, []):
            if start <= when <= end:
                found = value
            elif start > when:
                break
        return found

    def first_release(self, date_str: str) -> Optional[str]:
        versions = self.versions.get(date_str)
        return versions[0][0] if versions else None

    def change_dates(self, date_str: Optional[str]) -> List[str]:
        if date_str is None:
            return []
        return [start for start, _end, _value in self.versions.get(date_str, [])]


def first_release_is_genuine(date_str: str, released: str, freq: str) -> bool:
    lag = (to_date(released) - period_end(date_str, freq)).days
    return 0 <= lag <= MAX_FIRST_RELEASE_LAG_DAYS.get(freq, 100)


def realtime_metric_history(
    history: VintageHistory, transform: str, freq: str, metric_id: str
) -> Tuple[List[Tuple[str, float, str]], List[List[Any]]]:
    """
    For each period: the transformed value as first published, and every later change.
    Returns (initial [(period, value, release date)], events [[metric, period, date, n, value, previous]]).
    Changes are detected at two decimals; the first release is event number 0.
    """
    initial: List[Tuple[str, float, str]] = []
    events: List[List[Any]] = []
    for date_str in sorted(history.versions):
        released = history.first_release(date_str)
        if released is None or not first_release_is_genuine(date_str, released, freq):
            continue
        base_key = base_period(date_str, transform, freq)
        times = sorted(
            {t for t in history.change_dates(date_str) + history.change_dates(base_key) if t >= released}
        )
        previous: Optional[float] = None
        revision_number = 0
        for when in times:
            value = apply_transform(
                history.value_asof(date_str, when),
                history.value_asof(base_key, when) if base_key else None,
                transform,
            )
            if value is None:
                continue
            rounded = round(value, 2)
            if previous is None:
                initial.append((date_str, round(value, 3), when))
                events.append([metric_id, date_str, when, 0, rounded, None])
                previous = rounded
            elif rounded != previous:
                revision_number += 1
                events.append([metric_id, date_str, when, revision_number, rounded, previous])
                previous = rounded
    return initial, events


def recession_periods(points: Sequence[Tuple[str, float]]) -> List[List[str]]:
    """Convert the monthly USREC 0/1 indicator into [start, end] date ranges."""
    periods: List[List[str]] = []
    start: Optional[str] = None
    last: Optional[str] = None
    for date_str, value in points:
        if value >= 0.5:
            if start is None:
                start = date_str
            last = date_str
        elif start is not None and last is not None:
            periods.append([start, period_end(last, "monthly").isoformat()])
            start = last = None
    if start is not None and last is not None:
        periods.append([start, period_end(last, "monthly").isoformat()])
    return periods


def upcoming_events(
    release_dates: Dict[str, List[str]],
    fomc_dates: List[str],
    today: datetime.date,
    horizon_days: int = CALENDAR_HORIZON_DAYS,
) -> List[Dict[str, str]]:
    """Merge release calendars into a sorted list of upcoming events."""
    end = today + datetime.timedelta(days=horizon_days)
    events: List[Dict[str, str]] = []
    labels = {r["series"]: r for r in CALENDAR_RELEASES}
    for series_id, dates in release_dates.items():
        info = labels.get(series_id)
        if not info:
            continue
        for date_str in sorted(set(dates)):
            d = to_date(date_str)
            if today <= d <= end:
                events.append({"date": date_str, "label": info["label"], "url": info["url"]})
    for date_str in sorted(set(fomc_dates)):
        d = to_date(date_str)
        if today <= d <= end:
            events.append({"date": date_str, "label": "FOMC decision", "url": FOMC_CALENDAR_URL})
        minutes = d + datetime.timedelta(days=FOMC_MINUTES_LAG_DAYS)
        if today <= minutes <= end:
            events.append({"date": minutes.isoformat(), "label": "FOMC minutes", "url": FOMC_CALENDAR_URL})
    events.sort(key=lambda e: (e["date"], e["label"]))
    return events


# ---------------------------------------------------------------------------
# U.S. Treasury Fiscal Data API
# ---------------------------------------------------------------------------

def build_fiscal_url(endpoint: str, fields: Sequence[str], page: int, page_size: int, sort: str = "record_date") -> str:
    query = (
        f"fields={','.join(fields)}&sort={sort}"
        f"&page[number]={page}&page[size]={page_size}"
    )
    return f"{FISCAL_BASE_URL}{endpoint}?{query}"


def fetch_fiscal_rows(
    fetcher: Fetcher,
    endpoint: str,
    fields: Sequence[str],
    page_size: int = 10000,
    max_pages: int = 50,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        body = fetcher(build_fiscal_url(endpoint, fields, page, page_size))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataSourceError(f"Fiscal Data returned invalid JSON for {endpoint}: {exc}") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise DataSourceError(f"Fiscal Data response for {endpoint} is missing 'data'")
        rows.extend(r for r in data if isinstance(r, dict))
        meta = payload.get("meta") or {}
        total_pages = meta.get("total-pages") or 1
        if not data or page >= int(total_pages):
            break
        page += 1
    return rows


def extract_operating_cash(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Treasury General Account closing balance ($ millions) per day across DTS formats."""
    by_date: Dict[str, Dict[str, Tuple[Optional[float], Optional[float]]]] = defaultdict(dict)
    for row in rows:
        date_str = str(row.get("record_date", ""))
        if not core.is_valid_iso_date(date_str):
            continue
        by_date[date_str][str(row.get("account_type", "")).strip()] = (
            parse_number(row.get("close_today_bal")),
            parse_number(row.get("open_today_bal")),
        )

    result: Dict[str, float] = {}
    for date_str, accounts in by_date.items():
        value: Optional[float] = None
        for name, (close, opening) in accounts.items():
            lower = name.lower()
            if "treasury general account" in lower and "closing balance" in lower:
                # In the current DTS format the closing balance sits in the "today" column.
                value = close if close is not None else opening
                break
        if value is None:
            for name, (close, _opening) in accounts.items():
                lower = name.lower()
                if lower.startswith("treasury general account") and "opening" not in lower and "closing" not in lower:
                    value = close
                    break
        if value is None and "Federal Reserve Account" in accounts:
            fed_close = accounts["Federal Reserve Account"][0]
            if fed_close is not None:
                tax_and_loan = sum(
                    (close or 0.0)
                    for name, (close, _o) in accounts.items()
                    if name.lower().startswith("tax and loan note accounts")
                )
                value = fed_close + tax_and_loan
        if value is not None:
            result[date_str] = value
    return result


def extract_debt_limit(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    """Debt subject to limit, statutory limit and total debt outstanding ($ millions) per day."""
    by_date: Dict[str, List[Tuple[str, Optional[float]]]] = defaultdict(list)
    for row in rows:
        date_str = str(row.get("record_date", ""))
        if core.is_valid_iso_date(date_str):
            by_date[date_str].append((str(row.get("debt_catg", "")).lower(), parse_number(row.get("close_today_bal"))))

    result: Dict[str, Dict[str, Optional[float]]] = {}
    for date_str, items in by_date.items():
        public = intragov = limit = direct_total = None
        not_subject = other_subject = 0.0
        for category, value in items:
            if value is None:
                continue
            if "statutory debt limit" in category:
                limit = value
            elif "total public debt subject to limit" in category:
                direct_total = value
            elif "held by the public" in category:
                public = value
            elif "intragovernmental" in category:
                intragov = value
            elif "not subject to limit" in category:
                not_subject += value
            elif "other debt subject to limit" in category:
                other_subject += value
        if public is None or intragov is None:
            continue
        subject = direct_total if direct_total is not None else public + intragov - not_subject + other_subject
        result[date_str] = {"subject": subject, "limit": limit, "outstanding": public + intragov}
    return result


def extract_mspd_composition(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Monthly debt composition ($ millions) from MSPD table 1."""
    keys = ("bills", "notes", "bonds", "tips", "frn", "otherMarketable", "marketable", "nonmarketable", "total")
    by_date: Dict[str, Dict[str, float]] = defaultdict(lambda: {k: 0.0 for k in keys})
    seen_totals: Dict[str, set] = defaultdict(set)
    for row in rows:
        date_str = str(row.get("record_date", ""))
        value = parse_number(row.get("total_mil_amt"))
        if not core.is_valid_iso_date(date_str) or value is None:
            continue
        sec_type = str(row.get("security_type_desc", "")).strip().lower()
        sec_class = str(row.get("security_class_desc", "")).strip().lower()
        bucket = by_date[date_str]
        if sec_type == "marketable":
            if sec_class == "bills":
                bucket["bills"] += value
            elif sec_class == "notes":
                bucket["notes"] += value
            elif sec_class == "bonds":
                bucket["bonds"] += value
            elif "inflation" in sec_class:
                bucket["tips"] += value
            elif "floating" in sec_class:
                bucket["frn"] += value
            else:
                bucket["otherMarketable"] += value
        elif sec_type == "total marketable":
            bucket["marketable"] = value
            seen_totals[date_str].add("marketable")
        elif sec_type == "total nonmarketable":
            bucket["nonmarketable"] = value
            seen_totals[date_str].add("nonmarketable")
        elif sec_type in ("total public debt outstanding", "total treasury securities outstanding"):
            bucket["total"] = value
            seen_totals[date_str].add("total")
    result: Dict[str, Dict[str, float]] = {}
    for date_str, bucket in by_date.items():
        if "marketable" not in seen_totals[date_str]:
            bucket["marketable"] = bucket["bills"] + bucket["notes"] + bucket["bonds"] + bucket["tips"] + bucket["frn"] + bucket["otherMarketable"]
        if "total" not in seen_totals[date_str]:
            bucket["total"] = bucket["marketable"] + bucket["nonmarketable"]
        result[date_str] = bucket
    return result


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------

def series_payload(
    series_id: str,
    points: Sequence[Tuple[str, float]],
    digits: int = 4,
    released: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": series_id,
        "dates": [d for d, _v in points],
        "values": [round_or_none(v, digits) for _d, v in points],
    }
    if released:
        payload["released"] = [released.get(d) for d, _v in points]
    return payload


def catalog_entry(
    label: str, group: str, order: int, freq: str, source: str, source_url: str, points: Sequence[Tuple[str, float]], unit: str = "%"
) -> Dict[str, Any]:
    return {
        "label": label,
        "group": group,
        "order": order,
        "frequency": freq,
        "unit": unit,
        "source": source,
        "sourceUrl": source_url,
        "first": points[0][0] if points else None,
        "last": points[-1][0] if points else None,
        "count": len(points),
    }


def period_label(date_str: str, freq: str) -> str:
    d = to_date(date_str)
    if freq == "quarterly":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    return d.strftime("%b %Y")


def build_indicators(latest: Dict[str, List[Tuple[str, float]]]) -> List[Dict[str, Any]]:
    """Sidebar figures: latest CPI, PCE, PPI, GDP (two quarters) and unemployment."""
    wanted = [("CPI_YOY", 1), ("CPI_MOM", 1), ("PCE_YOY", 1), ("PPI_YOY", 1), ("GDP", 2), ("UNRATE", 1)]
    metrics = {m["id"]: m for m in MACRO_METRICS}
    result: List[Dict[str, Any]] = []
    for metric_id, count in wanted:
        points = latest.get(metric_id) or []
        metric = metrics[metric_id]
        for date_str, value in list(reversed(points))[:count]:
            result.append({
                "id": metric_id,
                "label": metric["short"],
                "period": date_str,
                "periodLabel": period_label(date_str, metric["freq"]),
                "value": round(value, 2),
            })
    return result


def status_entry(
    status: str,
    error: Optional[str],
    previous: Dict[str, Any],
    now_iso: str,
    latest: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "status": status,
        "errorMessage": error,
        "lastSuccessAt": now_iso if status == "ok" else previous.get("lastSuccessAt"),
        "latestObservationDate": latest if latest is not None else previous.get("latestObservationDate"),
    }
    if extra:
        entry.update(extra)
    return entry


# ---------------------------------------------------------------------------
# Main update
# ---------------------------------------------------------------------------

class ExtendedUpdater:
    def __init__(
        self,
        data_dir: Path,
        fetcher: Fetcher,
        today: datetime.date,
        now_iso: str,
        fred_api_key: Optional[str],
        fred_min_interval: float = 0.6,
        treasury_pause: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.data_dir = data_dir
        self.fetcher = fetcher
        self.today = today
        self.now_iso = now_iso
        self.fred_api_key = (fred_api_key or "").strip() or None
        self.fred_min_interval = fred_min_interval
        self.treasury_pause = treasury_pause
        self.sleep = sleep
        self.log = log
        self.changed_files: List[str] = []
        self.manifest_path = data_dir / "manifest.json"
        previous = load_json(self.manifest_path)
        self.previous_manifest: Dict[str, Any] = previous if isinstance(previous, dict) else {}
        self.series_catalog: Dict[str, Any] = dict(self.previous_manifest.get("series") or {})
        self.statuses: Dict[str, Any] = {}

    # -- writing helpers ----------------------------------------------------
    def write(self, relative: str, payload: Any, compact: bool = True) -> None:
        if write_json_if_changed(self.data_dir / relative, payload, compact=compact):
            self.changed_files.append(relative)

    def prev_status(self, key: str) -> Dict[str, Any]:
        return dict((self.previous_manifest.get("sources") or {}).get(key) or {})

    # -- Treasury -----------------------------------------------------------
    def update_treasury(self, curve_years: Dict[int, CurveYear]) -> None:
        pause = (lambda: self.sleep(self.treasury_pause)) if self.treasury_pause > 0 else (lambda: None)
        for block, key in (("nominal", "treasuryNominal"), ("real", "treasuryReal")):
            try:
                fetched, latest = update_treasury_block(curve_years, self.today, self.fetcher, block, pause)
                self.statuses[key] = status_entry("ok", None, self.prev_status(key), self.now_iso, latest)
                self.log(f"[{key}] OK — fetched {fetched} year(s), latest {latest}")
            except Exception as exc:  # keep previous files
                message = str(exc)
                self.statuses[key] = status_entry("error", message, self.prev_status(key), self.now_iso)
                self.log(f"[{key}] RETAINED previous data ({message})")

    # -- FRED -----------------------------------------------------------------
    def update_fred(self, curve_years: Dict[int, CurveYear], outputs: Dict[str, Any]) -> None:
        if not self.fred_api_key:
            self.statuses["fred"] = status_entry(
                "missing_key",
                "FRED_API_KEY is not configured; FRED-based data was not refreshed.",
                self.prev_status("fred"),
                self.now_iso,
            )
            self.log("[fred] SKIPPED — FRED_API_KEY is not set")
            return

        client = FredClient(self.fred_api_key, self.fetcher, min_interval=self.fred_min_interval, sleep=self.sleep)
        errors: List[str] = []
        latest_dates: List[str] = []

        def attempt(label: str, func: Callable[[], None]) -> None:
            try:
                func()
            except Exception as exc:
                errors.append(f"{label}: {redact(str(exc), self.fred_api_key)}")

        # 1. Pre-1990 curve (static history; rebuilt only when missing or versioned).
        def pre1990() -> None:
            built = self.previous_manifest.get("curve", {}).get("pre1990Version")
            have_files = any(y < NOMINAL_FIRST_YEAR for y in curve_years)
            if built == PRE1990_BUILD_VERSION and have_files:
                return
            values: Dict[str, Dict[str, float]] = {}
            for segments in PRE1990_SEGMENTS.values():
                for series_id, start, end, _freq in segments:
                    if series_id in values:
                        continue
                    starts = [s for sid, s, _e, _f in sum(PRE1990_SEGMENTS.values(), []) if sid == series_id]
                    ends = [e for sid, _s, e, _f in sum(PRE1990_SEGMENTS.values(), []) if sid == series_id]
                    values[series_id] = dict(client.observations(
                        series_id, observation_start=min(starts), observation_end=max(ends)
                    ))
            rebuilt = build_pre1990_curve(values)
            if not rebuilt:
                raise DataSourceError("FRED returned no pre-1990 Treasury yields")
            for year in [y for y in curve_years if y < NOMINAL_FIRST_YEAR]:
                if year not in rebuilt:
                    del curve_years[year]
            curve_years.update(rebuilt)
            outputs["pre1990Version"] = PRE1990_BUILD_VERSION

        attempt("pre-1990 Treasury yields", pre1990)

        # 2. Policy rates.
        for spec in FRED_RATE_SERIES:
            def policy_rate(spec: Dict[str, Any] = spec) -> None:
                points = [(d, v) for d, v in client.observations(spec["fred"]) if is_weekday(d)]
                if not points:
                    raise DataSourceError(f"{spec['fred']} returned no observations")
                outputs["series"][spec["id"]] = (points, catalog_entry(
                    spec["label"], spec["group"], spec["order"], "daily", f"FRED {spec['fred']}",
                    FRED_SERIES_URL + spec["fred"], points,
                ))
                latest_dates.append(points[-1][0])

            attempt(spec["fred"], policy_rate)

        # 3. Recessions.
        def recessions() -> None:
            points = client.observations("USREC")
            outputs["recessions"] = {
                "source": "NBER based recession indicators (FRED USREC)",
                "sourceUrl": FRED_SERIES_URL + "USREC",
                "periods": recession_periods(points),
            }

        attempt("recessions", recessions)

        # 4. Economic indicators: latest values, first releases and revisions.
        levels_cache: Dict[str, List[Tuple[str, float]]] = {}
        realtime_cache: Dict[str, VintageHistory] = {}
        latest_transformed: Dict[str, List[Tuple[str, float]]] = {}
        revision_events: List[List[Any]] = []
        cpi_release_dates: Dict[str, str] = {}

        for order, metric in enumerate(MACRO_METRICS):
            def one_metric(metric: Dict[str, Any] = metric, order: int = order) -> None:
                fred_id = metric["fred"]
                if fred_id not in levels_cache:
                    levels_cache[fred_id] = client.observations(fred_id)
                points = transform_series(levels_cache[fred_id], metric["transform"], metric["freq"])
                if not points:
                    raise DataSourceError(f"{fred_id} produced no {metric['transform']} values")
                latest_transformed[metric["id"]] = points
                outputs["series"][metric["id"]] = (points, catalog_entry(
                    metric["label"] + " — latest revision", "macro", 60 + order * 2, metric["freq"],
                    f"FRED {fred_id}", FRED_SERIES_URL + fred_id, points,
                ))
                latest_dates.append(points[-1][0])

                if not (metric["initial"] or metric["revisions"]):
                    return
                if fred_id not in realtime_cache:
                    realtime_cache[fred_id] = VintageHistory(
                        client.observations_realtime(fred_id, REALTIME_OBSERVATION_START)
                    )
                initial, events = realtime_metric_history(
                    realtime_cache[fred_id], metric["transform"], metric["freq"], metric["id"]
                )
                if metric["id"] == "CPI_YOY":
                    cpi_release_dates.update({period: released for period, _v, released in initial})
                if metric["initial"] and initial:
                    initial_points = [(period, value) for period, value, _released in initial]
                    released = {period: rel for period, _v, rel in initial}
                    outputs["series"][metric["id"] + "_INITIAL"] = (
                        initial_points,
                        catalog_entry(
                            metric["label"] + " — as first reported", "macro", 61 + order * 2, metric["freq"],
                            f"ALFRED {fred_id} (first release)", "https://alfred.stlouisfed.org/series?seid=" + fred_id,
                            initial_points,
                        ),
                        released,
                    )
                if metric["revisions"]:
                    revision_events.extend(events)

            attempt(metric["id"], one_metric)

        if "CPI_YOY" in outputs["series"] and cpi_release_dates:
            points, entry = outputs["series"]["CPI_YOY"][:2]
            outputs["series"]["CPI_YOY"] = (points, entry, cpi_release_dates)

        if latest_transformed:
            outputs["indicators"] = build_indicators(latest_transformed)
        if revision_events:
            revision_events.sort(key=lambda e: (e[2], e[1], e[0]), reverse=True)
            outputs["revisions"] = {
                "metrics": {
                    m["id"]: {"label": m["revisionLabel"], "short": m["short"], "frequency": m["freq"]}
                    for m in MACRO_METRICS if m["revisions"]
                },
                "columns": ["metric", "period", "revisionDate", "revisionNumber", "value", "previousValue"],
                "truncated": len(revision_events) > MAX_REVISION_EVENTS,
                "events": revision_events[:MAX_REVISION_EVENTS],
            }

        # 5. Release calendar.
        def release_calendar() -> None:
            release_dates: Dict[str, List[str]] = {}
            for release in CALENDAR_RELEASES:
                info = client.get("series/release", series_id=release["series"])
                releases = info.get("releases") or []
                if not releases:
                    continue
                dates_payload = client.get(
                    "release/dates", release_id=releases[0]["id"], include_release_dates_with_no_data="true",
                    sort_order="asc", limit=10000,
                )
                release_dates[release["series"]] = [
                    item["date"] for item in dates_payload.get("release_dates", []) if core.is_valid_iso_date(item.get("date"))
                ]
            fomc_dates: List[str] = []
            fomc_info = client.get("release", release_id=FOMC_RELEASE_ID).get("releases") or []
            if fomc_info and "fomc" in str(fomc_info[0].get("name", "")).lower():
                dates_payload = client.get(
                    "release/dates", release_id=FOMC_RELEASE_ID, include_release_dates_with_no_data="true",
                    sort_order="asc", limit=10000,
                )
                fomc_dates = [
                    item["date"] for item in dates_payload.get("release_dates", []) if core.is_valid_iso_date(item.get("date"))
                ]
            outputs["events"] = upcoming_events(release_dates, fomc_dates, self.today)

        attempt("release calendar", release_calendar)

        status = "ok" if not errors else ("partial" if client.request_count > len(errors) else "error")
        message = "; ".join(errors) if errors else None
        latest = max(latest_dates) if latest_dates else None
        self.statuses["fred"] = status_entry(
            status, message, self.prev_status("fred"), self.now_iso, latest, {"requests": client.request_count}
        )
        if status == "partial":
            self.statuses["fred"]["lastSuccessAt"] = self.now_iso
        self.log(f"[fred] {status.upper()} — {client.request_count} requests" + (f"; {message}" if message else ""))

    # -- Fiscal Data -------------------------------------------------------
    def update_fiscal(self) -> None:
        try:
            cash_rows = fetch_fiscal_rows(
                self.fetcher, "/v1/accounting/dts/operating_cash_balance",
                ["record_date", "account_type", "close_today_bal", "open_today_bal"],
            )
            limit_rows = fetch_fiscal_rows(
                self.fetcher, "/v1/accounting/dts/debt_subject_to_limit",
                ["record_date", "debt_catg", "close_today_bal"],
            )
            mspd_rows = fetch_fiscal_rows(
                self.fetcher, "/v1/debt/mspd/mspd_table_1",
                ["record_date", "security_type_desc", "security_class_desc", "total_mil_amt"],
            )
            cash = extract_operating_cash(cash_rows)
            limits = extract_debt_limit(limit_rows)
            composition = extract_mspd_composition(mspd_rows)
            if not limits or not composition:
                raise DataSourceError("Fiscal Data returned no debt limit or MSPD rows")

            dates = sorted(limits)
            ceiling = {
                "unit": "USD millions",
                "source": "Daily Treasury Statement (Fiscal Data API)",
                "sourceUrl": "https://fiscaldata.treasury.gov/datasets/daily-treasury-statement/",
                "dates": dates,
                "debtSubjectToLimit": [round_or_none(limits[d]["subject"], 0) for d in dates],
                "statutoryLimit": [round_or_none(limits[d]["limit"], 0) for d in dates],
                "totalOutstanding": [round_or_none(limits[d]["outstanding"], 0) for d in dates],
                "cashBalance": [round_or_none(cash.get(d), 0) for d in dates],
            }
            months = sorted(composition)
            keys = ("bills", "notes", "bonds", "tips", "frn", "otherMarketable", "marketable", "nonmarketable", "total")
            maturities = {
                "unit": "USD millions",
                "source": "Monthly Statement of the Public Debt, table 1 (Fiscal Data API)",
                "sourceUrl": "https://fiscaldata.treasury.gov/datasets/monthly-statement-public-debt/",
                "dates": months,
            }
            for key in keys:
                maturities[key] = [round_or_none(composition[m][key], 0) for m in months]
            self.write("debt/ceiling.json", ceiling)
            self.write("debt/maturities.json", maturities)
            self.statuses["fiscal"] = status_entry("ok", None, self.prev_status("fiscal"), self.now_iso, dates[-1])
            self.log(f"[fiscal] OK — DTS through {dates[-1]}, MSPD through {months[-1]}")
        except Exception as exc:
            message = str(exc)
            self.statuses["fiscal"] = status_entry("error", message, self.prev_status("fiscal"), self.now_iso)
            self.log(f"[fiscal] RETAINED previous data ({message})")

    # -- Orchestration -------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        curve_years = load_curve_years(self.data_dir / "curve")
        outputs: Dict[str, Any] = {"series": {}}

        self.update_treasury(curve_years)
        self.update_fred(curve_years, outputs)
        self.update_fiscal()

        # Curve files.
        for year in sorted(curve_years):
            model = curve_years[year]
            if model.nominal or model.real:
                self.write(f"curve/{year}.json", model.to_payload())

        # Treasury-derived series (nominal and real) from all curve years.
        nominal_points: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        real_points: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        year_info: Dict[str, Any] = {}
        for year in sorted(curve_years):
            model = curve_years[year]
            dates = sorted(set(model.nominal) | set(model.real))
            if not dates:
                continue
            year_info[str(year)] = {"first": dates[0], "last": dates[-1], "count": len(dates), "source": model.source}
            for date_str in sorted(model.nominal):
                for key, value in model.nominal[date_str].items():
                    if value is not None:
                        nominal_points[key].append((date_str, value))
            for date_str in sorted(model.real):
                for key, value in model.real[date_str].items():
                    if value is not None:
                        real_points[key].append((date_str, value))

        for order, mat in enumerate(core.TREASURY_MATURITIES):
            points = nominal_points.get(mat["key"]) or []
            if points:
                series_id = f"UST{mat['key']}"
                outputs["series"][series_id] = (points, catalog_entry(
                    f"{mat['label']} Treasury", "yields", order, "daily", "U.S. Treasury / FRED",
                    core.TREASURY_PAGE_URL, points,
                ))
        for order, mat in enumerate(REAL_MATURITIES):
            points = real_points.get(mat["key"]) or []
            if points:
                outputs["series"][f"TIPS{mat['key']}"] = (points, catalog_entry(
                    f"{mat['key']} TIPS real yield", "tips", 20 + order, "daily", "U.S. Treasury (real yield curve)",
                    TREASURY_REAL_PAGE_URL, points,
                ))

        # Series files + catalog.
        for series_id, item in sorted(outputs["series"].items()):
            points, entry = item[0], item[1]
            released = item[2] if len(item) > 2 else None
            digits = 4 if entry["frequency"] == "daily" else 3
            self.write(f"series/{series_id}.json", series_payload(series_id, points, digits, released))
            self.series_catalog[series_id] = entry

        if "recessions" in outputs:
            self.write("recessions.json", outputs["recessions"])
        if "revisions" in outputs:
            self.write("revisions.json", outputs["revisions"])

        previous_curve = self.previous_manifest.get("curve") or {}
        pre1990_version = outputs.get("pre1990Version", previous_curve.get("pre1990Version"))
        if not any(y < NOMINAL_FIRST_YEAR for y in curve_years):
            pre1990_version = None
        years_sorted = sorted(year_info)
        sources = {}
        for key, info in SOURCE_INFO.items():
            sources[key] = dict(info)
            sources[key].update(self.statuses.get(key) or self.prev_status(key))

        manifest: Dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "updatedAt": self.previous_manifest.get("updatedAt"),
            "fredKeyConfigured": bool(self.fred_api_key),
            "sources": sources,
            "curve": {
                "firstDate": year_info[years_sorted[0]]["first"] if years_sorted else None,
                "lastDate": year_info[years_sorted[-1]]["last"] if years_sorted else None,
                "years": year_info,
                "nominalKeys": NOMINAL_KEYS,
                "realKeys": REAL_KEYS,
                "pre1990Version": pre1990_version,
            },
            "series": {k: self.series_catalog[k] for k in sorted(self.series_catalog)},
            "indicators": outputs.get("indicators", self.previous_manifest.get("indicators") or []),
            "events": outputs.get("events", self.previous_manifest.get("events") or []),
            "files": {
                "curve": "curve/{year}.json",
                "series": "series/{id}.json",
                "recessions": "recessions.json",
                "revisions": "revisions.json",
                "debtCeiling": "debt/ceiling.json",
                "debtMaturities": "debt/maturities.json",
            },
        }

        comparable_old = strip_manifest_volatile(self.previous_manifest)
        comparable_new = strip_manifest_volatile(manifest)
        meaningful = bool(self.changed_files) or comparable_old != comparable_new or not manifest["updatedAt"]
        if meaningful or not self.manifest_path.exists():
            manifest["updatedAt"] = self.now_iso
            self.write("manifest.json", manifest, compact=False)
        # Otherwise keep the saved manifest untouched (only check timestamps would
        # differ), so scheduled runs without new data do not create commits.
        return manifest


def strip_manifest_volatile(manifest: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = json.loads(json.dumps(manifest or {}))
    cleaned.pop("updatedAt", None)
    for source in (cleaned.get("sources") or {}).values():
        if isinstance(source, dict):
            source.pop("lastSuccessAt", None)
            source.pop("requests", None)
    return cleaned


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Update extended official datasets for the Yields site.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Output data directory")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="HTTP attempts per request")
    args = parser.parse_args(argv)

    api_key = os.environ.get("FRED_API_KEY", "").strip() or None

    def fetcher(url: str) -> bytes:
        accept = "application/json" if ("fiscaldata" in url or "stlouisfed" in url) else "application/xml, text/xml, */*"
        return core.fetch_url_with_retries(url, timeout=args.timeout, max_retries=args.retries, accept=accept)

    updater = ExtendedUpdater(
        data_dir=args.data_dir,
        fetcher=fetcher,
        today=datetime.datetime.now(datetime.timezone.utc).date(),
        now_iso=core.utc_now_iso(),
        fred_api_key=api_key,
        log=lambda msg: print(redact(msg, api_key)),
    )
    try:
        manifest = updater.run()
    except Exception as exc:
        print(f"[ERROR] Extended update failed: {redact(str(exc), api_key)}", file=sys.stderr)
        return 1

    statuses = {k: v.get("status") for k, v in manifest["sources"].items()}
    print(f"[DONE] {len(updater.changed_files)} file(s) changed; statuses: {statuses}")
    if all(s not in ("ok", "partial") for s in statuses.values()):
        print("[ERROR] Every extended source failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
