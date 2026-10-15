#!/usr/bin/env python3
"""
update_data.py — Official U.S. Interest Rates Updater for "Yields"

Fetches, validates, normalizes, and merges daily published interest rate
observations from official U.S. government and Federal Reserve sources using
only the Python 3 standard library:
  1. U.S. Department of the Treasury Daily Par Yield Curve XML feed
  2. Federal Reserve Bank of New York EFFR (Effective Federal Funds Rate) API
  3. Federal Reserve Bank of New York SOFR (Secured Overnight Financing Rate) API

Features:
  - First run fetches the current calendar year and previous two calendar years.
  - Subsequent runs merge recent observations into saved history, handling
    revisions, deduplication, and year/month boundaries.
  - Explicit Treasury maturity mapping (including 6-Week BC_1_5MONTH) while
    excluding display duplicate BC_30YEARDISPLAY.
  - Preserves missing values as null (never 0) and does not invent weekend or
    holiday observations.
  - Retains last valid history per source if an individual source fails;
    exits non-zero if all sources fail or no usable data exists.
  - Uses atomic file writes and avoids rewriting the output file when only
    volatile check timestamps changed.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "site" / "data" / "rates.json"
USER_AGENT = "Yields-US-Interest-Rates-Dashboard/1.0 (+https://github.com)"

TREASURY_ENDPOINT = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
TREASURY_DOCS_URL = "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed"
TREASURY_PAGE_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"

EFFR_ENDPOINT = "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json"
SOFR_ENDPOINT = "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
NYFED_DOCS_URL = "https://markets.newyorkfed.org/static/docs/markets-api.html"
EFFR_PAGE_URL = "https://www.newyorkfed.org/markets/reference-rates/effr"
SOFR_PAGE_URL = "https://www.newyorkfed.org/markets/reference-rates/sofr"

# Explicit mapping of official Treasury XML tags to normalized keys, labels, and
# numeric durations (in years and months).
# Note: BC_30YEARDISPLAY is intentionally excluded so it is never treated as an
# additional maturity.
TREASURY_MATURITIES: List[Dict[str, Any]] = [
    {"key": "1M", "xml_tag": "BC_1MONTH", "label": "1-Month", "short_label": "1M", "years": round(1 / 12, 6), "months": 1.0},
    {"key": "6W", "xml_tag": "BC_1_5MONTH", "label": "6-Week", "short_label": "6W", "years": round(1.5 / 12, 6), "months": 1.5},
    {"key": "2M", "xml_tag": "BC_2MONTH", "label": "2-Month", "short_label": "2M", "years": round(2 / 12, 6), "months": 2.0},
    {"key": "3M", "xml_tag": "BC_3MONTH", "label": "3-Month", "short_label": "3M", "years": 0.25, "months": 3.0},
    {"key": "4M", "xml_tag": "BC_4MONTH", "label": "4-Month", "short_label": "4M", "years": round(4 / 12, 6), "months": 4.0},
    {"key": "6M", "xml_tag": "BC_6MONTH", "label": "6-Month", "short_label": "6M", "years": 0.5, "months": 6.0},
    {"key": "1Y", "xml_tag": "BC_1YEAR", "label": "1-Year", "short_label": "1Y", "years": 1.0, "months": 12.0},
    {"key": "2Y", "xml_tag": "BC_2YEAR", "label": "2-Year", "short_label": "2Y", "years": 2.0, "months": 24.0},
    {"key": "3Y", "xml_tag": "BC_3YEAR", "label": "3-Year", "short_label": "3Y", "years": 3.0, "months": 36.0},
    {"key": "5Y", "xml_tag": "BC_5YEAR", "label": "5-Year", "short_label": "5Y", "years": 5.0, "months": 60.0},
    {"key": "7Y", "xml_tag": "BC_7YEAR", "label": "7-Year", "short_label": "7Y", "years": 7.0, "months": 84.0},
    {"key": "10Y", "xml_tag": "BC_10YEAR", "label": "10-Year", "short_label": "10Y", "years": 10.0, "months": 120.0},
    {"key": "20Y", "xml_tag": "BC_20YEAR", "label": "20-Year", "short_label": "20Y", "years": 20.0, "months": 240.0},
    {"key": "30Y", "xml_tag": "BC_30YEAR", "label": "30-Year", "short_label": "30Y", "years": 30.0, "months": 360.0},
]

IGNORED_TREASURY_TAGS = {"BC_30YEARDISPLAY", "Id", "NEW_DATE"}

XML_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}


class DataSourceError(Exception):
    """Raised when fetching or parsing an official data source fails."""


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format (seconds precision)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_valid_iso_date(date_str: Any) -> bool:
    """Validate YYYY-MM-DD calendar date format."""
    if not isinstance(date_str, str) or len(date_str) != 10:
        return False
    try:
        datetime.date.fromisoformat(date_str)
        return True
    except ValueError:
        return False


def parse_optional_float(raw_value: Any, min_val: float = -20.0, max_val: float = 100.0) -> Optional[float]:
    """
    Parse a numeric rate in percentage units (e.g., 4.25 means 4.25%).
    Returns None if raw_value is None, empty, or whitespace.
    Raises ValueError if raw_value is non-numeric or outside plausible bounds.
    """
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        val = float(raw_value)
    elif isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped == "" or stripped.lower() in {"null", "n/a", "na", "none"}:
            return None
        val = float(stripped)
    else:
        raise ValueError(f"Unsupported rate value type: {type(raw_value)!r}")

    if val != val or val < min_val or val > max_val:  # NaN or out-of-range check
        raise ValueError(f"Rate value {val} outside plausible percentage range [{min_val}, {max_val}]")
    return round(val, 4)


def fetch_url_with_retries(
    url: str,
    timeout: float = 20.0,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
    accept: str = "*/*",
    opener: Optional[Callable[..., Any]] = None,
) -> bytes:
    """
    Download bytes from a URL with bounded retries, exponential backoff, and timeout.
    """
    open_fn = opener or urllib.request.urlopen
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with open_fn(req, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if status and (status < 200 or status >= 300):
                    raise DataSourceError(f"HTTP status {status} from {url}")
                body = response.read()
                if not body:
                    raise DataSourceError(f"Empty response body from {url}")
                return body
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, DataSourceError) as exc:
            last_exc = exc
            if attempt < max_retries:
                sleep_time = backoff_seconds * (2 ** (attempt - 1))
                time.sleep(sleep_time)

    raise DataSourceError(f"Failed to fetch {url} after {max_retries} attempts: {last_exc}") from last_exc


def build_treasury_url(year: Optional[int] = None, year_month: Optional[str] = None) -> str:
    """
    Build the official U.S. Treasury XML feed URL for a given year (YYYY) or month (YYYYMM).
    """
    if year_month is not None:
        params = {
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value_month": str(year_month),
        }
    elif year is not None:
        params = {
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value": str(year),
        }
    else:
        raise ValueError("Either year or year_month must be provided for Treasury URL")

    return f"{TREASURY_ENDPOINT}?{urllib.parse.urlencode(params)}"


def parse_treasury_xml(xml_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parse official U.S. Treasury daily_treasury_yield_curve XML bytes into a list of
    chronological daily observations:
      [{"date": "YYYY-MM-DD", "yields": {"1M": 4.38, "6W": 4.36, ...}}, ...]
    Missing maturities on a given date are explicitly stored as None (JSON null).
    BC_30YEARDISPLAY is ignored and never treated as an additional maturity.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DataSourceError(f"Invalid XML from Treasury feed: {exc}") from exc

    m_null_attr = f"{{{XML_NAMESPACES['m']}}}null"
    entries = root.findall("atom:entry", XML_NAMESPACES)
    by_date: Dict[str, Dict[str, Optional[float]]] = {}

    for entry in entries:
        props = entry.find(".//m:properties", XML_NAMESPACES)
        if props is None:
            continue

        date_elem = props.find("d:NEW_DATE", XML_NAMESPACES)
        if date_elem is None or not date_elem.text:
            continue

        raw_date = date_elem.text.strip()[:10]
        if not is_valid_iso_date(raw_date):
            raise DataSourceError(f"Invalid NEW_DATE in Treasury XML: {date_elem.text!r}")

        yields_map: Dict[str, Optional[float]] = {}
        for mat in TREASURY_MATURITIES:
            key = mat["key"]
            xml_tag = mat["xml_tag"]
            elem = props.find(f"d:{xml_tag}", XML_NAMESPACES)
            if elem is None:
                yields_map[key] = None
                continue
            if elem.attrib.get(m_null_attr, "").lower() == "true":
                yields_map[key] = None
                continue
            try:
                yields_map[key] = parse_optional_float(elem.text)
            except ValueError as exc:
                raise DataSourceError(f"Invalid yield value for {xml_tag} on {raw_date}: {exc}") from exc

        # Only keep observations that have at least one non-null maturity yield
        if any(v is not None for v in yields_map.values()):
            by_date[raw_date] = yields_map

    observations = [
        {"date": d, "yields": by_date[d]}
        for d in sorted(by_date.keys())
    ]
    return observations


def parse_nyfed_json(json_bytes: bytes, expected_type: str) -> List[Dict[str, Any]]:
    """
    Parse NY Fed Markets API JSON response (EFFR or SOFR) into chronological observations.
    Uses effectiveDate and percentRate from refRates, plus targetRateFrom / targetRateTo
    for EFFR when present.
    """
    try:
        payload = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Invalid JSON from NY Fed ({expected_type}): {exc}") from exc

    if not isinstance(payload, dict) or "refRates" not in payload or not isinstance(payload["refRates"], list):
        raise DataSourceError(f"Unexpected JSON structure from NY Fed ({expected_type}): missing 'refRates' list")

    by_date: Dict[str, Dict[str, Any]] = {}
    for item in payload["refRates"]:
        if not isinstance(item, dict):
            continue
        rate_type = str(item.get("type", "")).strip().upper()
        if rate_type and rate_type != expected_type.upper():
            continue

        raw_date = str(item.get("effectiveDate", "")).strip()
        if not is_valid_iso_date(raw_date):
            continue

        try:
            rate_val = parse_optional_float(item.get("percentRate"))
        except ValueError as exc:
            raise DataSourceError(f"Invalid percentRate in {expected_type} on {raw_date}: {exc}") from exc

        if rate_val is None:
            continue

        obs: Dict[str, Any] = {
            "date": raw_date,
            "rate": rate_val,
            "percentile1": parse_optional_float(item.get("percentPercentile1")),
            "percentile25": parse_optional_float(item.get("percentPercentile25")),
            "percentile75": parse_optional_float(item.get("percentPercentile75")),
            "percentile99": parse_optional_float(item.get("percentPercentile99")),
            "volumeInBillions": parse_optional_float(item.get("volumeInBillions"), min_val=0.0, max_val=100000.0),
            "revisionIndicator": str(item.get("revisionIndicator") or "").strip(),
        }

        if expected_type.upper() == "EFFR":
            obs["targetRateFrom"] = parse_optional_float(item.get("targetRateFrom"))
            obs["targetRateTo"] = parse_optional_float(item.get("targetRateTo"))

        by_date[raw_date] = obs

    return [by_date[d] for d in sorted(by_date.keys())]


def merge_observations_by_date(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    min_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Merge existing and incoming observations keyed by 'date'.
    - Incoming observations for the same date overwrite existing ones (supporting revisions).
    - Deduplicates by date and sorts chronologically (ascending).
    - Optionally trims observations older than min_date (YYYY-MM-DD).
    """
    merged: Dict[str, Dict[str, Any]] = {}

    for item in existing:
        if isinstance(item, dict) and is_valid_iso_date(item.get("date")):
            d = item["date"]
            if min_date and d < min_date:
                continue
            merged[d] = copy.deepcopy(item)

    for item in incoming:
        if isinstance(item, dict) and is_valid_iso_date(item.get("date")):
            d = item["date"]
            if min_date and d < min_date:
                continue
            merged[d] = copy.deepcopy(item)

    return [merged[d] for d in sorted(merged.keys())]


def normalize_treasury_observation(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure every configured maturity key is present in obs['yields'] (missing -> None)."""
    raw_yields = obs.get("yields", {}) if isinstance(obs.get("yields"), dict) else {}
    normalized_yields: Dict[str, Optional[float]] = {}
    for mat in TREASURY_MATURITIES:
        key = mat["key"]
        val = raw_yields.get(key)
        normalized_yields[key] = parse_optional_float(val) if val is not None else None
    return {
        "date": obs["date"],
        "yields": normalized_yields,
    }


def compute_basis_points_change(current_rate: Optional[float], previous_rate: Optional[float]) -> Optional[float]:
    """
    Compute change in basis points (1% = 100 bps) between two percentage rates.
    Returns None if either rate is None.
    """
    if current_rate is None or previous_rate is None:
        return None
    return round((current_rate - previous_rate) * 100.0, 2)


def compute_spread_series(treasury_observations: List[Dict[str, Any]], long_key: str = "10Y", short_key: str = "2Y") -> List[Dict[str, Any]]:
    """
    Compute the Treasury yield spread (e.g. 10-Year minus 2-Year) strictly using
    observations from the exact same date. If either maturity is missing on a date,
    the spread for that date is None (null).
    """
    result: List[Dict[str, Any]] = []
    for obs in treasury_observations:
        d = obs.get("date")
        yields = obs.get("yields", {})
        if not is_valid_iso_date(d) or not isinstance(yields, dict):
            continue
        long_val = yields.get(long_key)
        short_val = yields.get(short_key)
        if long_val is not None and short_val is not None:
            spread = round(long_val - short_val, 4)
        else:
            spread = None
        result.append({
            "date": d,
            "spread": spread,
            long_key: long_val,
            short_key: short_val,
        })
    return result


def determine_treasury_fetch_periods(
    today: datetime.date,
    existing_observations: List[Dict[str, Any]],
    lookback_days: int = 45,
) -> List[Tuple[str, str]]:
    """
    Determine which Treasury XML queries to execute.
    - First run (no existing observations): fetch full years [today.year - 2, today.year - 1, today.year].
    - Subsequent runs: fetch recent months covering [today - lookback_days .. today].
      If the lookback window crosses a year boundary or spans multiple months, fetches
      each month (or full year if more compact) so revisions and year boundaries are covered.
    Returns list of (param_type, param_value) where param_type is 'year' or 'month'.
    """
    min_year = today.year - 2
    if not existing_observations:
        return [("year", str(y)) for y in range(min_year, today.year + 1)]

    # Verify existing history actually covers the earlier years; if a new calendar year
    # started or history has a large gap, fetch the needed years/months.
    earliest_date_str = existing_observations[0].get("date")
    latest_date_str = existing_observations[-1].get("date")
    try:
        earliest_date = datetime.date.fromisoformat(earliest_date_str) if earliest_date_str else None
        latest_date = datetime.date.fromisoformat(latest_date_str) if latest_date_str else None
    except ValueError:
        earliest_date = None
        latest_date = None

    # If latest observation is missing/stale (>60 days) or saved history does not
    # start in or before min_year (e.g. incomplete initial seed), fetch full 3-year window.
    if (
        latest_date is None
        or earliest_date is None
        or (today - latest_date).days > 60
        or earliest_date.year > min_year
    ):
        return [("year", str(y)) for y in range(min_year, today.year + 1)]

    start_date = min(today - datetime.timedelta(days=lookback_days), latest_date - datetime.timedelta(days=14))
    if start_date.year < min_year:
        start_date = datetime.date(min_year, 1, 1)

    months: List[Tuple[str, str]] = []
    cursor = datetime.date(start_date.year, start_date.month, 1)
    end_month = datetime.date(today.year, today.month, 1)
    while cursor <= end_month:
        months.append(("month", f"{cursor.year:04d}{cursor.month:02d}"))
        if cursor.month == 12:
            cursor = datetime.date(cursor.year + 1, 1, 1)
        else:
            cursor = datetime.date(cursor.year, cursor.month + 1, 1)

    return months


def fetch_treasury_observations(
    today: datetime.date,
    existing_observations: List[Dict[str, Any]],
    fetcher: Callable[[str], bytes],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Fetch and parse Treasury observations for the required periods.
    Returns (observations, list_of_fetched_urls).
    """
    periods = determine_treasury_fetch_periods(today, existing_observations)
    all_incoming: List[Dict[str, Any]] = []
    urls_used: List[str] = []

    for p_type, p_val in periods:
        if p_type == "year":
            url = build_treasury_url(year=int(p_val))
        else:
            url = build_treasury_url(year_month=p_val)
        urls_used.append(url)
        xml_bytes = fetcher(url)
        parsed = parse_treasury_xml(xml_bytes)
        all_incoming.extend(parsed)

    if not all_incoming and not existing_observations:
        raise DataSourceError("Treasury XML feed returned 0 valid observations.")

    min_date = f"{today.year - 2:04d}-01-01"
    merged = merge_observations_by_date(existing_observations, all_incoming, min_date=min_date)
    normalized = [normalize_treasury_observation(obs) for obs in merged]
    if not normalized:
        raise DataSourceError("No Treasury observations available after merging.")
    return normalized, urls_used


def build_nyfed_url(base_endpoint: str, start_date: str, end_date: str) -> str:
    """Build NY Fed search.json URL with startDate and endDate in YYYY-MM-DD format."""
    params = {
        "startDate": start_date,
        "endDate": end_date,
    }
    return f"{base_endpoint}?{urllib.parse.urlencode(params)}"


def fetch_nyfed_series(
    rate_type: str,
    base_endpoint: str,
    today: datetime.date,
    existing_observations: List[Dict[str, Any]],
    fetcher: Callable[[str], bytes],
    lookback_days: int = 45,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch and merge NY Fed observations (EFFR or SOFR).
    On first run, fetches from Jan 1 of (today.year - 2) through today.
    On subsequent runs, fetches recent window (at least lookback_days back from latest observation
    and today) to capture revisions and year boundaries.
    """
    min_date = f"{today.year - 2:04d}-01-01"
    if not existing_observations:
        start_date_str = min_date
    else:
        earliest_str = existing_observations[0].get("date")
        latest_str = existing_observations[-1].get("date")
        try:
            earliest_dt = datetime.date.fromisoformat(earliest_str) if earliest_str else None
            latest_dt = datetime.date.fromisoformat(latest_str) if latest_str else None
        except ValueError:
            earliest_dt = None
            latest_dt = None

        if (
            latest_dt is None
            or earliest_dt is None
            or (today - latest_dt).days > 90
            or earliest_dt.year > (today.year - 2)
        ):
            start_date_str = min_date
        else:
            start_dt = min(today - datetime.timedelta(days=lookback_days), latest_dt - datetime.timedelta(days=14))
            start_date_str = max(min_date, start_dt.isoformat())

    end_date_str = today.isoformat()
    url = build_nyfed_url(base_endpoint, start_date_str, end_date_str)
    json_bytes = fetcher(url)
    incoming = parse_nyfed_json(json_bytes, expected_type=rate_type)

    if not incoming and not existing_observations:
        raise DataSourceError(f"NY Fed {rate_type} endpoint returned 0 observations.")

    merged = merge_observations_by_date(existing_observations, incoming, min_date=min_date)
    if not merged:
        raise DataSourceError(f"No {rate_type} observations available after merging.")
    return merged, url


def load_existing_dataset(path: Path) -> Optional[Dict[str, Any]]:
    """Load existing rates.json if present and valid JSON; return None if missing."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError):
        return None


def strip_volatile_timestamps(dataset: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Return a copy of dataset with volatile check timestamps removed so two datasets
    can be compared for meaningful content or status changes.
    """
    if not isinstance(dataset, dict):
        return None
    cleaned = copy.deepcopy(dataset)
    cleaned.pop("lastCheckedAt", None)
    cleaned.pop("updatedAt", None)
    sources = cleaned.get("sources")
    if isinstance(sources, dict):
        for src in sources.values():
            if isinstance(src, dict):
                src.pop("lastCheckedAt", None)
                src.pop("lastSuccessAt", None)
                src.pop("fetchedUrls", None)
                src.pop("fetchedUrl", None)
    return cleaned


def has_meaningful_changes(old_dataset: Optional[Dict[str, Any]], new_dataset: Dict[str, Any]) -> bool:
    """
    Return True if new_dataset has meaningful changes in observations, metadata, or
    source statuses compared to old_dataset (ignoring volatile check timestamps).
    """
    if old_dataset is None:
        return True
    return strip_volatile_timestamps(old_dataset) != strip_volatile_timestamps(new_dataset)


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """
    Atomically write JSON data to path using a temporary file in the same directory
    followed by os.replace().
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def update_all_data(
    existing_dataset: Optional[Dict[str, Any]] = None,
    today: Optional[datetime.date] = None,
    fetcher: Optional[Callable[[str], bytes]] = None,
    now_iso: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, bool]]:
    """
    Execute the update across all three official sources (Treasury, EFFR, SOFR).
    - If an individual source fails, retains its last valid history from existing_dataset
      and records the error in sources[source].status = "error" / errorMessage.
    - If every source fails, or if a source fails with no usable initial history, raises
      DataSourceError so the caller can fail the workflow and keep the previous site.
    Returns (new_dataset, source_success_map).
    """
    if today is None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
    if now_iso is None:
        now_iso = utc_now_iso()
    if fetcher is None:
        fetcher = lambda url: fetch_url_with_retries(url)

    prev_sources = existing_dataset.get("sources", {}) if isinstance(existing_dataset, dict) else {}

    prev_treasury = prev_sources.get("treasury", {}) if isinstance(prev_sources.get("treasury"), dict) else {}
    prev_effr = prev_sources.get("effr", {}) if isinstance(prev_sources.get("effr"), dict) else {}
    prev_sofr = prev_sources.get("sofr", {}) if isinstance(prev_sources.get("sofr"), dict) else {}

    prev_treasury_obs = prev_treasury.get("observations", []) if isinstance(prev_treasury.get("observations"), list) else []
    prev_effr_obs = prev_effr.get("observations", []) if isinstance(prev_effr.get("observations"), list) else []
    prev_sofr_obs = prev_sofr.get("observations", []) if isinstance(prev_sofr.get("observations"), list) else []

    success_map: Dict[str, bool] = {"treasury": False, "effr": False, "sofr": False}

    # 1. Treasury
    try:
        treasury_obs, treasury_urls = fetch_treasury_observations(today, prev_treasury_obs, fetcher)
        success_map["treasury"] = True
        treasury_status = "ok"
        treasury_error = None
        treasury_last_success = now_iso
    except Exception as exc:
        treasury_obs = copy.deepcopy(prev_treasury_obs)
        treasury_urls = prev_treasury.get("fetchedUrls", [build_treasury_url(year=today.year)])
        treasury_status = "error"
        treasury_error = str(exc)
        treasury_last_success = prev_treasury.get("lastSuccessAt")

    # 2. EFFR
    try:
        effr_obs, effr_url = fetch_nyfed_series("EFFR", EFFR_ENDPOINT, today, prev_effr_obs, fetcher)
        success_map["effr"] = True
        effr_status = "ok"
        effr_error = None
        effr_last_success = now_iso
    except Exception as exc:
        effr_obs = copy.deepcopy(prev_effr_obs)
        effr_url = prev_effr.get("fetchedUrl", EFFR_ENDPOINT)
        effr_status = "error"
        effr_error = str(exc)
        effr_last_success = prev_effr.get("lastSuccessAt")

    # 3. SOFR
    try:
        sofr_obs, sofr_url = fetch_nyfed_series("SOFR", SOFR_ENDPOINT, today, prev_sofr_obs, fetcher)
        success_map["sofr"] = True
        sofr_status = "ok"
        sofr_error = None
        sofr_last_success = now_iso
    except Exception as exc:
        sofr_obs = copy.deepcopy(prev_sofr_obs)
        sofr_url = prev_sofr.get("fetchedUrl", SOFR_ENDPOINT)
        sofr_status = "error"
        sofr_error = str(exc)
        sofr_last_success = prev_sofr.get("lastSuccessAt")

    # If every source failed, fail the run
    if not any(success_map.values()):
        errors_summary = f"Treasury: {treasury_error}; EFFR: {effr_error}; SOFR: {sofr_error}"
        raise DataSourceError(f"All official data sources failed to update. Details: {errors_summary}")

    # Ensure we have usable initial data for every source (never generate fake rates)
    missing_initial = []
    if not treasury_obs:
        missing_initial.append(f"Treasury ({treasury_error or 'empty'})")
    if not effr_obs:
        missing_initial.append(f"EFFR ({effr_error or 'empty'})")
    if not sofr_obs:
        missing_initial.append(f"SOFR ({sofr_error or 'empty'})")

    if missing_initial:
        raise DataSourceError(
            "Usable data is missing and no previous valid history exists for: "
            + ", ".join(missing_initial)
        )

    treasury_latest_date = treasury_obs[-1]["date"]
    effr_latest_date = effr_obs[-1]["date"]
    sofr_latest_date = sofr_obs[-1]["date"]

    candidate_dataset: Dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": now_iso,
        "lastCheckedAt": now_iso,
        "historyYears": [today.year - 2, today.year - 1, today.year],
        "sources": {
            "treasury": {
                "id": "treasury",
                "name": "U.S. Department of the Treasury — Daily Par Yield Curve Rates",
                "shortName": "U.S. Treasury",
                "endpoint": TREASURY_ENDPOINT,
                "documentationUrl": TREASURY_DOCS_URL,
                "sourcePageUrl": TREASURY_PAGE_URL,
                "fetchedUrls": treasury_urls,
                "status": treasury_status,
                "errorMessage": treasury_error,
                "lastCheckedAt": now_iso,
                "lastSuccessAt": treasury_last_success,
                "latestObservationDate": treasury_latest_date,
                "observationCount": len(treasury_obs),
                "maturities": TREASURY_MATURITIES,
                "observations": treasury_obs,
            },
            "effr": {
                "id": "effr",
                "name": "Federal Reserve Bank of New York — Effective Federal Funds Rate (EFFR)",
                "shortName": "NY Fed EFFR",
                "endpoint": EFFR_ENDPOINT,
                "documentationUrl": NYFED_DOCS_URL,
                "sourcePageUrl": EFFR_PAGE_URL,
                "fetchedUrl": effr_url,
                "status": effr_status,
                "errorMessage": effr_error,
                "lastCheckedAt": now_iso,
                "lastSuccessAt": effr_last_success,
                "latestObservationDate": effr_latest_date,
                "observationCount": len(effr_obs),
                "observations": effr_obs,
            },
            "sofr": {
                "id": "sofr",
                "name": "Federal Reserve Bank of New York — Secured Overnight Financing Rate (SOFR)",
                "shortName": "NY Fed SOFR",
                "endpoint": SOFR_ENDPOINT,
                "documentationUrl": NYFED_DOCS_URL,
                "sourcePageUrl": SOFR_PAGE_URL,
                "fetchedUrl": sofr_url,
                "status": sofr_status,
                "errorMessage": sofr_error,
                "lastCheckedAt": now_iso,
                "lastSuccessAt": sofr_last_success,
                "latestObservationDate": sofr_latest_date,
                "observationCount": len(sofr_obs),
                "observations": sofr_obs,
            },
        },
    }

    # Preserve previous timestamps if no meaningful observations or source statuses changed
    if existing_dataset is not None and not has_meaningful_changes(existing_dataset, candidate_dataset):
        candidate_dataset["updatedAt"] = existing_dataset.get("updatedAt", now_iso)
        candidate_dataset["lastCheckedAt"] = existing_dataset.get("lastCheckedAt", now_iso)
        for src_key in ("treasury", "effr", "sofr"):
            if src_key in prev_sources and isinstance(prev_sources[src_key], dict):
                candidate_dataset["sources"][src_key]["lastCheckedAt"] = prev_sources[src_key].get("lastCheckedAt", now_iso)
                candidate_dataset["sources"][src_key]["lastSuccessAt"] = prev_sources[src_key].get("lastSuccessAt", candidate_dataset["sources"][src_key]["lastSuccessAt"])

    return candidate_dataset, success_map


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download and update official U.S. Treasury, EFFR, and SOFR interest rate data."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSON file path (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP request timeout in seconds (default: 20.0)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Maximum HTTP request attempts per URL (default: 3)",
    )
    parser.add_argument(
        "--force-timestamp",
        action="store_true",
        help="Always write updated check timestamps even if observations and statuses are unchanged.",
    )
    args = parser.parse_args(argv)

    output_path: Path = args.output
    existing = load_existing_dataset(output_path)

    def fetcher(url: str) -> bytes:
        accept = "application/json" if url.endswith(".json") or ".json?" in url else "application/xml, text/xml, */*"
        return fetch_url_with_retries(url, timeout=args.timeout, max_retries=args.retries, accept=accept)

    try:
        new_dataset, success_map = update_all_data(
            existing_dataset=existing,
            fetcher=fetcher,
        )
    except DataSourceError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[ERROR] Unexpected failure updating data: {exc}", file=sys.stderr)
        return 1

    meaningful_change = has_meaningful_changes(existing, new_dataset)
    if meaningful_change or args.force_timestamp or not output_path.exists():
        atomic_write_json(output_path, new_dataset)
        change_note = "updated (meaningful changes detected)" if meaningful_change else "written (forced timestamp)"
    else:
        change_note = "unchanged (no new observations or status changes; skipped rewrite)"

    for src_id, ok in success_map.items():
        src_info = new_dataset["sources"][src_id]
        status_label = "OK" if ok else f"RETAINED ({src_info.get('errorMessage')})"
        print(
            f"[{src_id.upper()}] {status_label} — "
            f"{src_info.get('observationCount')} observations, "
            f"latest: {src_info.get('latestObservationDate')}"
        )

    print(f"[DONE] {output_path}: {change_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
