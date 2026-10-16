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
  5. Federal Reserve FOMC meeting calendar page (no key): meeting dates, for the rate
     decision and minutes dates in the sidebar. A saved or built-in schedule is used
     when the page cannot be read.

Outputs (under site/data/, all JSON)
  manifest.json            metadata, source status, series catalog, sidebar data
                           (upcoming events, latest indicators, Fed policy, FOMC meetings)
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
import html as html_lib
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_data as core  # noqa: E402  (shared fetch/parse helpers)


SCHEMA_VERSION = "2.1"
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

# Scheduled data releases. FRED supplies the dates; "metric" is the indicator that
# tells which data period was published last. "oldLabel" reads calendars saved by
# earlier versions of this script.
CALENDAR_RELEASES: List[Dict[str, str]] = [
    {"series": "CPIAUCSL", "kind": "cpi", "label": "CPI inflation", "oldLabel": "CPI", "metric": "CPI_YOY",
     "url": "https://www.bls.gov/schedule/news_release/cpi.htm"},
    {"series": "PPIFIS", "kind": "ppi", "label": "PPI inflation", "oldLabel": "PPI", "metric": "PPI_YOY",
     "url": "https://www.bls.gov/schedule/news_release/ppi.htm"},
    {"series": "UNRATE", "kind": "jobs", "label": "Jobs report", "oldLabel": "Jobs report", "metric": "UNRATE",
     "url": "https://www.bls.gov/schedule/news_release/empsit.htm"},
    {"series": "A191RL1Q225SBEA", "kind": "gdp", "label": "GDP", "oldLabel": "GDP", "metric": "GDP",
     "url": "https://www.bea.gov/news/schedule"},
    {"series": "PCEPI", "kind": "pce", "label": "PCE inflation", "oldLabel": "PCE inflation", "metric": "PCE_YOY",
     "url": "https://www.bea.gov/news/schedule"},
]
DATA_RELEASE_TIME_ET = "08:30"  # BLS and BEA releases
FOMC_RELEASE_TIME_ET = "14:00"  # policy statement and minutes
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_MINUTES_LAG_DAYS = 21  # minutes are released three weeks after each decision
FOMC_HISTORY_YEARS = 6  # meetings kept in the manifest (for mapping rate changes to meetings)
CALENDAR_HORIZON_DAYS = 75

# Meetings as published on the Fed's calendar in October 2026 (2027 dates are
# tentative). Used only when the page cannot be read and no saved schedule exists.
FOMC_MEETINGS_FALLBACK: List[Dict[str, Any]] = [
    {"start": "2025-01-28", "end": "2025-01-29"},
    {"start": "2025-03-18", "end": "2025-03-19", "sep": True},
    {"start": "2025-05-06", "end": "2025-05-07"},
    {"start": "2025-06-17", "end": "2025-06-18", "sep": True},
    {"start": "2025-07-29", "end": "2025-07-30"},
    {"start": "2025-09-16", "end": "2025-09-17", "sep": True},
    {"start": "2025-10-28", "end": "2025-10-29"},
    {"start": "2025-12-09", "end": "2025-12-10", "sep": True},
    {"start": "2026-01-27", "end": "2026-01-28"},
    {"start": "2026-03-17", "end": "2026-03-18", "sep": True},
    {"start": "2026-04-28", "end": "2026-04-29"},
    {"start": "2026-06-16", "end": "2026-06-17", "sep": True},
    {"start": "2026-07-28", "end": "2026-07-29"},
    {"start": "2026-09-15", "end": "2026-09-16", "sep": True},
    {"start": "2026-10-27", "end": "2026-10-28"},
    {"start": "2026-12-08", "end": "2026-12-09", "sep": True},
    {"start": "2027-01-26", "end": "2027-01-27"},
    {"start": "2027-03-16", "end": "2027-03-17", "sep": True},
    {"start": "2027-04-27", "end": "2027-04-28"},
    {"start": "2027-06-08", "end": "2027-06-09", "sep": True},
    {"start": "2027-07-27", "end": "2027-07-28"},
    {"start": "2027-09-14", "end": "2027-09-15", "sep": True},
    {"start": "2027-10-26", "end": "2027-10-27"},
    {"start": "2027-12-07", "end": "2027-12-08", "sep": True},
]

# Sidebar "Latest economic data": metric id, display label, plain-English description.
INDICATOR_SPECS: List[Tuple[str, str, str]] = [
    ("CPI_YOY", "CPI inflation, y/y", "Consumer prices compared with a year earlier (CPI-U, not seasonally adjusted)"),
    ("CPI_MOM", "CPI inflation, m/m", "Consumer prices compared with the month before (seasonally adjusted)"),
    ("PCE_YOY", "PCE inflation, y/y", "The Fed's preferred inflation gauge, compared with a year earlier"),
    ("PPI_YOY", "PPI inflation, y/y", "Producer prices for final demand, compared with a year earlier"),
    ("GDP", "GDP growth, annualized", "Real GDP growth compared with the previous quarter, at an annual rate"),
    ("UNRATE", "Unemployment rate", "Share of the labor force without a job"),
]

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
    "fedCalendar": {
        "name": "Federal Reserve — FOMC meeting calendar",
        "shortName": "FOMC calendar",
        "url": FOMC_CALENDAR_URL,
        "docsUrl": FOMC_CALENDAR_URL,
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
    """
    Years whose Treasury data is missing or may still change, in fetch order:
    the current year, then the previous year (when needed), then missing
    history newest-first (so an interrupted backfill fills recent years first).
    """
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
    return sorted(wanted, reverse=True)


def update_treasury_block(
    curve_years: Dict[int, CurveYear],
    today: datetime.date,
    fetcher: Fetcher,
    block: str,
    pause: Callable[[], None] = lambda: None,
    deadline: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    workers: int = 4,
) -> Tuple[int, Optional[str], int]:
    """
    Fetch nominal or real Treasury curve years into curve_years.
    Recent years are always fetched; missing history is backfilled in parallel
    until `deadline` (a clock() value) passes, and the rest is left for the next run.
    Returns (years fetched, latest date, years still to backfill).
    """
    if block == "nominal":
        feed, first_year, parser = "daily_treasury_yield_curve", NOMINAL_FIRST_YEAR, core.parse_treasury_xml
    else:
        feed, first_year, parser = "daily_treasury_real_yield_curve", REAL_FIRST_YEAR, parse_treasury_real_xml
    keys = NOMINAL_KEYS if block == "nominal" else REAL_KEYS

    def fetch(year: int) -> Tuple[int, List[Dict[str, Any]]]:
        return year, parser(fetcher(build_treasury_feed_url(feed, year)))

    def apply(year: int, observations: List[Dict[str, Any]]) -> None:
        rows = {
            obs["date"]: {key: obs["yields"].get(key) for key in keys}
            for obs in observations
            if obs["date"].startswith(f"{year:04d}-")
        }
        # An empty answer never wipes saved history (it is normal on the first
        # days of January, and otherwise most likely a transient feed problem).
        if not rows:
            return
        model = curve_years.get(year) or CurveYear(year)
        model.source = "treasury"
        model.notes = None
        if block == "nominal":
            model.nominal = rows
        else:
            model.real = rows
        curve_years[year] = model

    years = treasury_years_to_fetch(curve_years, today, first_year, block)
    recent = [y for y in years if y >= today.year - 1]
    backfill = [y for y in years if y < today.year - 1]
    fetched = 0
    for year in recent:
        apply(*fetch(year))
        fetched += 1
        pause()

    remaining = 0
    if backfill:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            index = 0
            while index < len(backfill):
                if deadline is not None and clock() > deadline:
                    remaining = len(backfill) - index
                    break
                chunk = backfill[index:index + max(1, workers)]
                for year, observations in pool.map(fetch, chunk):
                    apply(year, observations)
                    fetched += 1
                index += len(chunk)
                pause()

    latest: Optional[str] = None
    for model in curve_years.values():
        rows = model.nominal if block == "nominal" else model.real
        if rows and model.source == "treasury":
            candidate = max(rows)
            latest = candidate if latest is None or candidate > latest else latest
    if latest is None:
        raise DataSourceError(f"Treasury {block} curve feed returned no observations.")
    return fetched, latest, remaining


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


# ---------------------------------------------------------------------------
# Economic calendar: FOMC meetings and scheduled data releases
# ---------------------------------------------------------------------------

MONTH_NUMBERS = {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name}


def strip_tags(fragment: str) -> str:
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def parse_fomc_calendar(page: str) -> List[Dict[str, Any]]:
    """
    Meetings from the Fed's FOMC calendar page. Each year is a panel headed
    "2026 FOMC Meetings"; each meeting row has a month cell ("October", "Jan/Feb")
    and a date cell ("27-28", "16-17*" for meetings with economic projections,
    "31-1", "22 (notation vote)"). Notation votes and cancelled meetings are skipped.
    """
    gap = r"(?:\s|<[^>]+>)+"  # spaces or inline tags between words
    headings = [
        (m.start(), int(m.group(1)))
        for m in re.finditer(r"\b((?:19|20)\d{2})" + gap + "FOMC" + gap + r"Meetings\b", page, re.I)
    ]
    cell = r'<(\w+)\b[^>]*\bclass=["\'][^"\']*\b{}\b[^"\']*["\'][^>]*>(.*?)</\1\s*>'
    months = [(m.start(), strip_tags(m.group(2))) for m in re.finditer(cell.format("fomc-meeting__month"), page, re.S | re.I)]
    dates = [(m.start(), strip_tags(m.group(2))) for m in re.finditer(cell.format("fomc-meeting__date"), page, re.S | re.I)]
    meetings: Dict[str, Dict[str, Any]] = {}
    for index, (pos, month_text) in enumerate(months):
        next_pos = months[index + 1][0] if index + 1 < len(months) else len(page)
        date_text = next((text for p, text in dates if pos < p < next_pos), None)
        year = next((y for p, y in reversed(headings) if p < pos), None)
        if date_text is None or year is None:
            continue
        month_numbers = [
            MONTH_NUMBERS[part.strip()[:3].lower()]
            for part in re.split(r"[/\-–]", month_text)
            if part.strip()[:3].lower() in MONTH_NUMBERS
        ]
        match = re.match(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?", date_text)
        lowered = date_text.lower()
        if not match or not month_numbers or "notation" in lowered or "cancel" in lowered:
            continue
        first_day = int(match.group(1))
        last_day = int(match.group(2)) if match.group(2) else first_day
        start_month, end_month = month_numbers[0], month_numbers[-1]
        if last_day < first_day and end_month == start_month:
            end_month = start_month % 12 + 1
        try:
            start = datetime.date(year, start_month, first_day)
            end = datetime.date(year + (1 if end_month < start_month else 0), end_month, last_day)
        except ValueError:
            continue
        if not 0 <= (end - start).days <= 3:
            continue
        meeting: Dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat()}
        if "*" in date_text:
            meeting["sep"] = True
        if "unscheduled" in lowered:
            meeting["unscheduled"] = True
        meetings[meeting["start"]] = meeting
    return [meetings[key] for key in sorted(meetings)]


def normalize_meetings(raw: Any) -> List[Dict[str, Any]]:
    """Meetings from saved data; malformed entries are dropped."""
    result: Dict[str, Dict[str, Any]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        start, end = item.get("start"), item.get("end")
        if not (core.is_valid_iso_date(start) and core.is_valid_iso_date(end)):
            continue
        if not 0 <= (to_date(end) - to_date(start)).days <= 3:
            continue
        meeting: Dict[str, Any] = {"start": start, "end": end}
        for flag in ("sep", "unscheduled"):
            if item.get(flag) is True:
                meeting[flag] = True
        result[start] = meeting
    return [result[key] for key in sorted(result)]


def merge_meetings(*sources: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine schedules year by year; a later source replaces the years it covers."""
    by_year: Dict[str, List[Dict[str, Any]]] = {}
    for source in sources:
        years: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for meeting in source:
            years[meeting["start"][:4]].append(dict(meeting))
        by_year.update(years)
    return sorted((m for items in by_year.values() for m in items), key=lambda m: m["start"])


def meeting_label(meeting: Dict[str, Any]) -> str:
    """'Oct 27–28', or 'Jan 31–Feb 1' when a meeting spans two months."""
    start, end = to_date(meeting["start"]), to_date(meeting["end"])
    first = f"{calendar.month_abbr[start.month]} {start.day}"
    if start == end:
        return first
    if start.month == end.month:
        return f"{first}–{end.day}"
    return f"{first}–{calendar.month_abbr[end.month]} {end.day}"


def quarter_before(d: datetime.date) -> datetime.date:
    """First day of the latest quarter that ended before the month of d."""
    index = (d.month - 1) // 3
    return datetime.date(d.year - 1, 10, 1) if index == 0 else datetime.date(d.year, 3 * index - 2, 1)


def release_periods(kind: str, dates: Sequence[str], latest_period: Optional[str]) -> Dict[str, Dict[str, str]]:
    """
    The data period each upcoming release covers: the month before the release for
    monthly data, or the latest finished quarter (advance or revised estimate) for GDP.
    Each guess must follow on from the last period already published; once a date
    does not fit, it and later dates get no label rather than a wrong one.
    """
    labels: Dict[str, Dict[str, str]] = {}
    if not latest_period or not core.is_valid_iso_date(latest_period):
        return labels
    latest = to_date(latest_period).replace(day=1)
    if kind == "gdp":
        next_quarter = to_date(month_shift(latest.isoformat(), 3))
        seen: set = set()
        for date_str in sorted(dates):
            quarter = quarter_before(to_date(date_str))
            if quarter == latest or quarter in seen:
                estimate = "revised"
            elif quarter == next_quarter:
                estimate = "advance"
            else:
                break
            seen.add(quarter)
            labels[date_str] = {
                "period": quarter.isoformat(),
                "periodLabel": period_label(quarter.isoformat(), "quarterly"),
                "estimate": estimate,
            }
        return labels
    expected = to_date(month_shift(latest.isoformat(), 1))
    for date_str in sorted(dates):
        month = to_date(month_shift(date_str[:8] + "01", -1))
        if month == expected:
            expected = to_date(month_shift(expected.isoformat(), 1))
        elif month != latest:  # latest = published earlier today
            break
        labels[date_str] = {"period": month.isoformat(), "periodLabel": period_label(month.isoformat(), "monthly")}
    return labels


def upcoming_events(
    release_dates: Dict[str, List[str]],
    meetings: Sequence[Dict[str, Any]],
    today: datetime.date,
    latest_periods: Optional[Dict[str, str]] = None,
    horizon_days: int = CALENDAR_HORIZON_DAYS,
) -> List[Dict[str, Any]]:
    """
    Fed rate decisions and minutes plus scheduled data releases, sorted by date.
    "time" is U.S. Eastern Time; the site shows it in each visitor's own time zone.
    """
    end = today + datetime.timedelta(days=horizon_days)
    latest_periods = latest_periods or {}
    events: List[Dict[str, Any]] = []
    for release in CALENDAR_RELEASES:
        window = sorted(
            d for d in set(release_dates.get(release["series"]) or [])
            if core.is_valid_iso_date(d) and today <= to_date(d) <= end
        )
        periods = release_periods(release["kind"], window, latest_periods.get(release["metric"]))
        for date_str in window:
            event: Dict[str, Any] = {
                "date": date_str, "time": DATA_RELEASE_TIME_ET, "kind": release["kind"],
                "label": release["label"], "url": release["url"],
            }
            event.update(periods.get(date_str, {}))
            events.append(event)
    for meeting in meetings:
        decision = to_date(meeting["end"])
        label = meeting_label(meeting)
        if today <= decision <= end:
            event = {
                "date": meeting["end"], "time": FOMC_RELEASE_TIME_ET, "kind": "fomc",
                "label": "Fed rate decision", "meeting": label, "url": FOMC_CALENDAR_URL,
            }
            if meeting.get("sep"):
                event["projections"] = True
            events.append(event)
        minutes = decision + datetime.timedelta(days=FOMC_MINUTES_LAG_DAYS)
        if not meeting.get("unscheduled") and today <= minutes <= end:
            events.append({
                "date": minutes.isoformat(), "time": FOMC_RELEASE_TIME_ET, "kind": "minutes",
                "label": "Fed meeting minutes", "meeting": label, "url": FOMC_CALENDAR_URL,
            })
    events.sort(key=lambda e: (e["date"], e["time"], e["label"]))
    return events


def release_dates_from_events(events: Any) -> Dict[str, List[str]]:
    """Data-release dates from a saved event list (used when FRED could not be reached)."""
    by_kind = {r["kind"]: r["series"] for r in CALENDAR_RELEASES}
    by_label = {r["oldLabel"]: r["series"] for r in CALENDAR_RELEASES}
    by_label.update({r["label"]: r["series"] for r in CALENDAR_RELEASES})
    result: Dict[str, List[str]] = defaultdict(list)
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or not core.is_valid_iso_date(event.get("date")):
            continue
        series = by_kind.get(event.get("kind")) or by_label.get(event.get("label"))
        if series:
            result[series].append(event["date"])
    return dict(result)


def fed_policy_summary(
    lower_points: Sequence[Tuple[str, float]],
    upper_points: Sequence[Tuple[str, float]],
    meetings: Sequence[Dict[str, Any]],
    effr: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Current target range, its last change (and the meeting that decided it) and the EFFR."""
    lower, upper = dict(lower_points), dict(upper_points)
    dates = sorted(set(lower) & set(upper))
    if not dates:
        return None
    last = dates[-1]
    summary: Dict[str, Any] = {"lower": round(lower[last], 2), "upper": round(upper[last], 2), "asOf": last}
    for i in range(len(dates) - 1, 0, -1):
        before, after = dates[i - 1], dates[i]
        if (lower[before], upper[before]) == (lower[after], upper[after]):
            continue
        change: Dict[str, Any] = {
            "effective": after,
            "fromLower": round(lower[before], 2), "fromUpper": round(upper[before], 2),
            "toLower": round(lower[after], 2), "toUpper": round(upper[after], 2),
            "bp": int(round((upper[after] - upper[before]) * 100)),
        }
        effective = to_date(after)
        meeting = next(
            (m for m in meetings if to_date(m["end"]) < effective <= to_date(m["end"]) + datetime.timedelta(days=4)),
            None,
        )
        if meeting:
            change["meeting"] = meeting_label(meeting)
            change["meetingEnd"] = meeting["end"]
        summary["lastChange"] = change
        break
    if effr:
        summary["effr"] = effr
    return summary


def latest_effr(rates_payload: Any, fallback: Sequence[Tuple[str, float]]) -> Optional[Dict[str, Any]]:
    """Latest effective fed funds rate: the NY Fed figure saved by update_data.py, else FRED's."""
    try:
        observations = rates_payload["sources"]["effr"]["observations"]
        best = max(
            (o for o in observations if core.is_valid_iso_date(o.get("date")) and isinstance(o.get("rate"), (int, float))),
            key=lambda o: o["date"],
        )
        return {"value": round(float(best["rate"]), 2), "date": best["date"]}
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    if fallback:
        date_str, value = fallback[-1]
        return {"value": round(value, 2), "date": date_str}
    return None


def load_series_points(path: Path) -> List[Tuple[str, float]]:
    """Points from a saved series/<ID>.json file (empty when missing)."""
    payload = load_json(path)
    if not isinstance(payload, dict):
        return []
    dates, values = payload.get("dates") or [], payload.get("values") or []
    return [
        (d, float(v)) for d, v in zip(dates, values)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and core.is_valid_iso_date(d)
    ]


# ---------------------------------------------------------------------------
# U.S. Treasury Fiscal Data API
# ---------------------------------------------------------------------------

def build_fiscal_url(
    endpoint: str, fields: Sequence[str], page: int, page_size: int, sort: str = "record_date", since: Optional[str] = None
) -> str:
    query = (
        f"fields={','.join(fields)}&sort={sort}"
        f"&page[number]={page}&page[size]={page_size}"
    )
    if since:
        query += f"&filter=record_date:gte:{since}"
    return f"{FISCAL_BASE_URL}{endpoint}?{query}"


def fetch_fiscal_rows(
    fetcher: Fetcher,
    endpoint: str,
    fields: Sequence[str],
    page_size: int = 10000,
    max_pages: int = 50,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        body = fetcher(build_fiscal_url(endpoint, fields, page, page_size, since=since))
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
                # The statement shows 0 while the limit is suspended (e.g. June 2023 to January 2025).
                limit = value if value > 0 else None
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


def build_indicators(
    latest: Dict[str, List[Tuple[str, float]]],
    releases: Optional[Dict[str, Dict[str, Tuple[str, int]]]] = None,
) -> List[Dict[str, Any]]:
    """
    Sidebar figures: the latest value, the reading before it, and when the latest
    value was published (releases[metric][period] = (date, revision number)).
    Values keep three decimals so the page rounds them only once.
    """
    metrics = {m["id"]: m for m in MACRO_METRICS}
    releases = releases or {}
    result: List[Dict[str, Any]] = []
    for metric_id, label, about in INDICATOR_SPECS:
        points = latest.get(metric_id) or []
        if not points:
            continue
        freq = metrics[metric_id]["freq"]
        period, value = points[-1]
        item: Dict[str, Any] = {
            "id": metric_id,
            "label": label,
            "about": about,
            "frequency": freq,
            "period": period,
            "periodLabel": period_label(period, freq),
            "value": round(value, 3),
        }
        if len(points) > 1:
            previous_period, previous_value = points[-2]
            item["previous"] = round(previous_value, 3)
            item["previousPeriod"] = previous_period
            item["previousPeriodLabel"] = period_label(previous_period, freq)
        published = releases.get(metric_id, {}).get(period)
        if published:
            item["released"] = published[0]
            if published[1] > 0:
                item["revised"] = True
        result.append(item)
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
        backfill_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
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
        self.clock = clock
        self.deadline = clock() + backfill_seconds if backfill_seconds is not None else None
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
                fetched, latest, remaining = update_treasury_block(
                    curve_years, self.today, self.fetcher, block, pause, deadline=self.deadline, clock=self.clock
                )
                if remaining:
                    note = f"History backfill in progress: {remaining} earlier year(s) are fetched on the next scheduled update."
                    self.statuses[key] = status_entry("partial", note, self.prev_status(key), self.now_iso, latest)
                    self.statuses[key]["lastSuccessAt"] = self.now_iso
                    self.log(f"[{key}] PARTIAL — fetched {fetched} year(s), {remaining} left for the next run, latest {latest}")
                else:
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
        # Latest publication of each period: release_info[metric][period] = (date, revision number).
        release_info: Dict[str, Dict[str, Tuple[str, int]]] = defaultdict(dict)

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
                for _metric, period, when, number, _value, _previous in events:
                    known = release_info[metric["id"]].get(period)
                    if known is None or when >= known[0]:
                        release_info[metric["id"]][period] = (when, number)
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
            outputs["indicators"] = build_indicators(latest_transformed, release_info)
            outputs["latestPeriods"] = {metric_id: points[-1][0] for metric_id, points in latest_transformed.items()}
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

        # 5. Data release calendar (Fed meeting dates come from the Fed's own calendar,
        # because FRED's "FOMC Press Release" lists a release every day).
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
            outputs["releaseDates"] = release_dates

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
        keys = ("bills", "notes", "bonds", "tips", "frn", "otherMarketable", "marketable", "nonmarketable", "total")
        try:
            # Incremental: refetch only the recent window once history is saved.
            saved_ceiling = load_json(self.data_dir / "debt" / "ceiling.json")
            saved_mspd = load_json(self.data_dir / "debt" / "maturities.json")
            limits: Dict[str, Dict[str, Optional[float]]] = {}
            cash: Dict[str, float] = {}
            composition: Dict[str, Dict[str, float]] = {}
            dts_since = mspd_since = None
            if isinstance(saved_ceiling, dict) and saved_ceiling.get("dates"):
                for i, d in enumerate(saved_ceiling["dates"]):
                    saved_limit = saved_ceiling["statutoryLimit"][i]
                    limits[d] = {
                        "subject": saved_ceiling["debtSubjectToLimit"][i],
                        "limit": saved_limit if isinstance(saved_limit, (int, float)) and saved_limit > 0 else None,
                        "outstanding": saved_ceiling["totalOutstanding"][i],
                    }
                    if saved_ceiling["cashBalance"][i] is not None:
                        cash[d] = saved_ceiling["cashBalance"][i]
                dts_since = (to_date(saved_ceiling["dates"][-1]) - datetime.timedelta(days=45)).isoformat()
            if isinstance(saved_mspd, dict) and saved_mspd.get("dates"):
                for i, d in enumerate(saved_mspd["dates"]):
                    composition[d] = {k: (saved_mspd.get(k) or [0] * (i + 1))[i] or 0 for k in keys}
                mspd_since = (to_date(saved_mspd["dates"][-1]) - datetime.timedelta(days=120)).isoformat()

            cash_rows = fetch_fiscal_rows(
                self.fetcher, "/v1/accounting/dts/operating_cash_balance",
                ["record_date", "account_type", "close_today_bal", "open_today_bal"], since=dts_since,
            )
            limit_rows = fetch_fiscal_rows(
                self.fetcher, "/v1/accounting/dts/debt_subject_to_limit",
                ["record_date", "debt_catg", "close_today_bal"], since=dts_since,
            )
            mspd_rows = fetch_fiscal_rows(
                self.fetcher, "/v1/debt/mspd/mspd_table_1",
                ["record_date", "security_type_desc", "security_class_desc", "total_mil_amt"], since=mspd_since,
            )
            limits.update(extract_debt_limit(limit_rows))
            cash.update(extract_operating_cash(cash_rows))
            composition.update(extract_mspd_composition(mspd_rows))
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

    # -- FOMC meeting calendar ------------------------------------------------
    def update_fed_calendar(self, outputs: Dict[str, Any]) -> None:
        saved = normalize_meetings((self.previous_manifest.get("fomc") or {}).get("meetings"))
        known = merge_meetings(FOMC_MEETINGS_FALLBACK, saved)
        try:
            page = self.fetcher(FOMC_CALENDAR_URL).decode("utf-8", errors="replace")
            fresh = parse_fomc_calendar(page)
            if not any(to_date(m["end"]) >= self.today for m in fresh):
                raise DataSourceError("no upcoming meetings found on the FOMC calendar page (its layout may have changed)")
            meetings = merge_meetings(known, fresh)
            status, message = "ok", None
        except Exception as exc:  # keep the saved schedule
            meetings = known
            has_upcoming = any(to_date(m["end"]) >= self.today for m in meetings)
            status = "partial" if has_upcoming else "error"
            message = f"Showing the saved meeting schedule ({exc})" if has_upcoming else str(exc)
        cutoff = str(self.today.year - FOMC_HISTORY_YEARS)
        meetings = [m for m in meetings if m["start"][:4] >= cutoff]
        outputs["fomcMeetings"] = meetings
        latest = meetings[-1]["end"] if meetings else None
        self.statuses["fedCalendar"] = status_entry(status, message, self.prev_status("fedCalendar"), self.now_iso, latest)
        upcoming = next((m for m in meetings if to_date(m["end"]) >= self.today), None)
        self.log(
            f"[fedCalendar] {status.upper()} — {len(meetings)} meetings"
            + (f", next decision {upcoming['end']}" if upcoming else "")
            + (f"; {message}" if message else "")
        )

    def build_events(self, outputs: Dict[str, Any], meetings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        release_dates = outputs.get("releaseDates")
        if release_dates is None:  # FRED unavailable: reuse the saved release dates
            release_dates = release_dates_from_events(self.previous_manifest.get("events"))
        latest_periods: Dict[str, str] = dict(outputs.get("latestPeriods") or {})
        if not latest_periods:
            for item in self.previous_manifest.get("indicators") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and core.is_valid_iso_date(item.get("period")):
                    latest_periods[item["id"]] = max(latest_periods.get(item["id"], ""), item["period"])
        return upcoming_events(release_dates, meetings, self.today, latest_periods)

    def build_fed_summary(self, outputs: Dict[str, Any], meetings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        def points(series_id: str) -> List[Tuple[str, float]]:
            item = outputs["series"].get(series_id)
            return list(item[0]) if item else load_series_points(self.data_dir / "series" / f"{series_id}.json")

        effr = latest_effr(load_json(self.data_dir / "rates.json"), points("EFFR"))
        return fed_policy_summary(points("FED_TARGET_LOWER"), points("FED_TARGET_UPPER"), meetings, effr)

    # -- Orchestration -------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        curve_years = load_curve_years(self.data_dir / "curve")
        outputs: Dict[str, Any] = {"series": {}}

        self.update_fed_calendar(outputs)
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
        meetings = outputs.get("fomcMeetings") or []
        events = self.build_events(outputs, meetings)
        fed = self.build_fed_summary(outputs, meetings)
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
            "events": events,
            "fed": fed if fed is not None else self.previous_manifest.get("fed"),
            "fomc": {"source": FOMC_CALENDAR_URL, "meetings": meetings},
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
    parser.add_argument(
        "--backfill-minutes", type=float, default=12.0,
        help="Time budget for fetching missing Treasury history; the rest continues next run (default: 12)",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("FRED_API_KEY", "").strip() or None

    def fetcher(url: str) -> bytes:
        if "fiscaldata" in url or "stlouisfed" in url:
            accept = "application/json"
        elif "federalreserve.gov" in url:
            accept = "text/html, */*"
        else:
            accept = "application/xml, text/xml, */*"
        return core.fetch_url_with_retries(url, timeout=args.timeout, max_retries=args.retries, accept=accept)

    updater = ExtendedUpdater(
        data_dir=args.data_dir,
        fetcher=fetcher,
        today=datetime.datetime.now(datetime.timezone.utc).date(),
        now_iso=core.utc_now_iso(),
        fred_api_key=api_key,
        log=lambda msg: print(redact(msg, api_key), flush=True),
        backfill_seconds=args.backfill_minutes * 60,
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
