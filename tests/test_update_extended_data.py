"""
Unit tests for scripts/update_extended_data.py.

Fixtures follow the real response formats of the U.S. Treasury XML feeds, the FRED
API and the Treasury Fiscal Data API (values shortened).
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import update_extended_data as ext  # noqa: E402

FAKE_KEY = "abcdef0123456789abcdef0123456789"


def treasury_xml(entries: List[Tuple[str, Dict[str, Optional[float]]]], real: bool = False) -> bytes:
    """Build a feed in the Treasury Atom/OData format."""
    title = "DailyTreasuryRealYieldCurveRateData" if real else "DailyTreasuryParYieldCurveRateData"
    parts = [
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        '<feed xml:base="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml" '
        'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
        'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
        'xmlns="http://www.w3.org/2005/Atom">',
        f'<title type="text">{title}</title>',
    ]
    for idx, (date_str, values) in enumerate(entries):
        props = [f'<d:NEW_DATE m:type="Edm.DateTime">{date_str}T00:00:00</d:NEW_DATE>']
        for tag, value in values.items():
            if value is None:
                props.append(f'<d:{tag} m:null="true" />')
            else:
                props.append(f'<d:{tag} m:type="Edm.Double">{value}</d:{tag}>')
        parts.append(
            f'<entry><id>x{idx}</id><content type="application/xml"><m:properties>'
            f'<d:Id m:type="Edm.Int32">{idx}</d:Id>{"".join(props)}</m:properties></content></entry>'
        )
    parts.append("</feed>")
    return "\n".join(parts).encode("utf-8")


def nominal_values(level: float) -> Dict[str, Optional[float]]:
    tags = ["BC_1MONTH", "BC_2MONTH", "BC_3MONTH", "BC_6MONTH", "BC_1YEAR", "BC_2YEAR", "BC_3YEAR",
            "BC_5YEAR", "BC_7YEAR", "BC_10YEAR", "BC_20YEAR", "BC_30YEAR"]
    return {tag: round(level + i * 0.05, 2) for i, tag in enumerate(tags)}


def real_values(level: float) -> Dict[str, Optional[float]]:
    return {"TC_5YEAR": level, "TC_7YEAR": level + 0.1, "TC_10YEAR": level + 0.2,
            "TC_20YEAR": level + 0.45, "TC_30YEAR": level + 0.6}


def fred_payload(points: List[Tuple[str, str]], realtime: str = "2026-10-15") -> bytes:
    return json.dumps({
        "realtime_start": realtime, "realtime_end": realtime, "count": len(points),
        "observations": [
            {"realtime_start": realtime, "realtime_end": realtime, "date": d, "value": v} for d, v in points
        ],
    }).encode("utf-8")


def fred_realtime_payload(rows: List[Tuple[str, str, str, str]]) -> bytes:
    return json.dumps({
        "realtime_start": "1776-07-04", "realtime_end": "9999-12-31",
        "observations": [
            {"realtime_start": rs, "realtime_end": re, "date": d, "value": v} for d, v, rs, re in rows
        ],
    }).encode("utf-8")


def monthly_dates(start: str, count: int) -> List[str]:
    return [ext.month_shift(start, i) for i in range(count)]


# Shortened copy of the Fed's FOMC calendar page markup (one panel per year).
FOMC_PAGE = """<!DOCTYPE html><html lang="en"><body><div id="article">
<div class="panel panel-default"><div class="panel-heading"><h4><a id="47776">2026 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting" style="">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>September</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">15-16*</div>
    <div class="col-xs-12 col-md-4 col-lg-4 fomc-meeting__minutes"><strong>Minutes:</strong> <a href="/x.htm">HTML</a></div>
  </div>
  <div class="fomc-meeting--shaded row fomc-meeting">
    <div class="fomc-meeting--shaded fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>October</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-2">27-28</div>
  </div>
  <div class="row fomc-meeting">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>December</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">8-9*</div>
  </div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="1">2025 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>August</strong></div>
    <div class="fomc-meeting__date col-xs-4">22 (notation vote)</div></div>
  <div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>December</strong></div>
    <div class="fomc-meeting__date col-xs-4">9-10*</div></div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="2">2023 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>Jan/Feb</strong></div>
    <div class="fomc-meeting__date col-xs-4">31-1</div></div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="3">2020 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>March</strong></div>
    <div class="fomc-meeting__date col-xs-4">3 (unscheduled)</div></div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="4">2027 <span>FOMC</span> Meetings</a></h4></div>
  <div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
    <div class="fomc-meeting__date col-xs-4">26-27</div></div>
</div>
</div></body></html>"""


class FakeNetwork:
    """Routes request URLs to canned responses and records them."""

    def __init__(self, fail_hosts: Tuple[str, ...] = ()) -> None:
        self.urls: List[str] = []
        self.fail_hosts = fail_hosts

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        parsed = urllib.parse.urlparse(url)
        if any(host in parsed.netloc for host in self.fail_hosts):
            raise ext.DataSourceError(f"Simulated outage for {url}")
        query = urllib.parse.parse_qs(parsed.query)
        if "treasury.gov" in parsed.netloc and "fiscaldata" not in parsed.netloc:
            return self.treasury(query)
        if "stlouisfed" in parsed.netloc:
            return self.fred(parsed.path, query)
        if "fiscaldata" in parsed.netloc:
            return self.fiscal(parsed.path)
        if "federalreserve.gov" in parsed.netloc:
            return FOMC_PAGE.encode("utf-8")
        raise AssertionError(f"Unexpected URL {url}")

    def treasury(self, query: Dict[str, List[str]]) -> bytes:
        feed = query["data"][0]
        year = int(query["field_tdr_date_value"][0])
        if year not in (2025, 2026):
            return treasury_xml([], real=feed.endswith("real_yield_curve"))
        if feed == "daily_treasury_real_yield_curve":
            return treasury_xml([(f"{year}-01-02", real_values(2.0)), (f"{year}-01-05", real_values(2.1))], real=True)
        return treasury_xml([(f"{year}-01-02", nominal_values(4.0)), (f"{year}-01-05", nominal_values(4.1))])

    def fred(self, path: str, query: Dict[str, List[str]]) -> bytes:
        assert query["api_key"][0] == FAKE_KEY
        if path.endswith("series/observations"):
            series_id = query["series_id"][0]
            if "realtime_start" in query:
                return fred_realtime_payload(self.realtime_rows(series_id))
            return fred_payload(self.latest_points(series_id))
        if path.endswith("series/release"):
            ids = {"CPIAUCSL": 10, "PPIFIS": 46, "UNRATE": 50, "A191RL1Q225SBEA": 53, "PCEPI": 54}
            rid = ids[query["series_id"][0]]
            return json.dumps({"releases": [{"id": rid, "name": f"Release {rid}"}]}).encode()
        if path.endswith("fred/release"):
            return json.dumps({"releases": [{"id": 101, "name": "FOMC Press Release"}]}).encode()
        if path.endswith("release/dates"):
            rid = int(query["release_id"][0])
            dates = {10: ["2026-09-11", "2026-11-10"], 101: ["2026-09-16", "2026-10-28", "2026-12-09"]}.get(rid, ["2026-10-30"])
            return json.dumps({"release_dates": [{"release_id": rid, "date": d} for d in dates]}).encode()
        raise AssertionError(f"Unexpected FRED path {path}")

    @staticmethod
    def latest_points(series_id: str) -> List[Tuple[str, str]]:
        if series_id in ("DGS10", "DGS2", "DGS3MO", "DGS6MO", "DGS1", "DGS3", "DGS5", "DGS7", "DGS30", "DGS20"):
            return [("1985-01-02", "11.50"), ("1985-01-03", "."), ("1985-01-04", "11.40")]
        if series_id in ("TB3MS", "GS1", "GS3", "GS5", "GS10", "GS20"):
            return [("1953-04-01", "2.36"), ("1953-05-01", "2.48")]
        if series_id in ("DTB3", "DTB6"):
            return [("1960-03-01", "3.50")]
        if series_id in ("DFF", "DFEDTARL", "DFEDTARU", "DFEDTAR", "RRPONTSYAWARD"):
            # 2026-10-10 and 2026-10-11 are a weekend and must be dropped.
            return [("2026-10-09", "3.88"), ("2026-10-10", "3.88"), ("2026-10-11", "3.88"), ("2026-10-12", "3.87")]
        if series_id == "USREC":
            return [("2020-01-01", "0"), ("2020-03-01", "1"), ("2020-04-01", "1"), ("2020-05-01", "0")]
        if series_id in ("A191RL1Q225SBEA",):
            return [("2026-01-01", "2.5"), ("2026-04-01", "2.2")]
        if series_id == "UNRATE":
            return [("2026-08-01", "4.3"), ("2026-09-01", "4.2")]
        dates = monthly_dates("2024-01-01", 33)
        return [(d, f"{100 + i * 0.25:.3f}") for i, d in enumerate(dates)]

    @staticmethod
    def realtime_rows(series_id: str) -> List[Tuple[str, str, str, str]]:
        if series_id == "A191RL1Q225SBEA":
            return [("2026-04-01", "1.5", "2026-07-30", "2026-08-27"), ("2026-04-01", "2.2", "2026-08-28", "9999-12-31")]
        if series_id == "UNRATE":
            return [("2026-09-01", "4.2", "2026-10-02", "9999-12-31")]
        return [
            ("2026-06-01", "99.8", "2026-07-15", "9999-12-31"),
            ("2026-07-01", "100.0", "2026-08-12", "2026-09-10"),
            ("2026-07-01", "100.1", "2026-09-11", "9999-12-31"),
            ("2026-08-01", "100.5", "2026-09-11", "9999-12-31"),
        ]

    @staticmethod
    def fiscal(path: str) -> bytes:
        if path.endswith("operating_cash_balance"):
            data = [
                {"record_date": "2026-10-13", "account_type": "Treasury General Account (TGA) Opening Balance", "close_today_bal": "null", "open_today_bal": "872526"},
                {"record_date": "2026-10-13", "account_type": "Treasury General Account (TGA) Closing Balance", "close_today_bal": "null", "open_today_bal": "920214"},
            ]
        elif path.endswith("debt_subject_to_limit"):
            data = [
                {"record_date": "2026-10-13", "debt_catg": "Debt Held by the Public", "close_today_bal": "32483641"},
                {"record_date": "2026-10-13", "debt_catg": "Intragovernmental Holdings", "close_today_bal": "7860238"},
                {"record_date": "2026-10-13", "debt_catg": "Debt Not Subject to Limit", "close_today_bal": "198411"},
                {"record_date": "2026-10-13", "debt_catg": "Statutory Debt Limit", "close_today_bal": "41103996"},
            ]
        else:
            data = [
                {"record_date": "2026-09-30", "security_type_desc": "Marketable", "security_class_desc": "Bills", "total_mil_amt": "7118145.3"},
                {"record_date": "2026-09-30", "security_type_desc": "Total Marketable", "security_class_desc": "_", "total_mil_amt": "7118145.3"},
                {"record_date": "2026-09-30", "security_type_desc": "Total Nonmarketable", "security_class_desc": "_", "total_mil_amt": "100.0"},
                {"record_date": "2026-09-30", "security_type_desc": "Total Public Debt Outstanding", "security_class_desc": "_", "total_mil_amt": "7118245.3"},
            ]
        return json.dumps({"data": data, "meta": {"count": len(data), "total-pages": 1}}).encode()


class TestTreasuryFeeds(unittest.TestCase):
    def test_real_yield_xml_parsing(self) -> None:
        xml = treasury_xml([
            ("2026-10-14", {"TC_5YEAR": 2.64, "TC_7YEAR": 2.73, "TC_10YEAR": 2.84, "TC_20YEAR": 3.09, "TC_30YEAR": 3.24}),
            ("2026-10-01", {"TC_5YEAR": 2.65, "TC_7YEAR": None, "TC_10YEAR": 2.88, "TC_20YEAR": 3.16, "TC_30YEAR": 3.31}),
        ], real=True)
        obs = ext.parse_treasury_real_xml(xml)
        self.assertEqual([o["date"] for o in obs], ["2026-10-01", "2026-10-14"])
        self.assertEqual(obs[1]["yields"]["10Y"], 2.84)
        self.assertIsNone(obs[0]["yields"]["7Y"])
        self.assertEqual(set(obs[0]["yields"]), {"5Y", "7Y", "10Y", "20Y", "30Y"})

    def test_years_to_fetch_backfills_then_only_refreshes_recent_years(self) -> None:
        today = datetime.date(2026, 10, 15)
        self.assertEqual(ext.treasury_years_to_fetch({}, today, 1990, "nominal"), list(range(2026, 1989, -1)))
        existing = {}
        for year in range(1990, 2027):
            model = ext.CurveYear(year)
            model.nominal = {f"{year}-12-29": {"10Y": 4.0}}
            existing[year] = model
        self.assertEqual(ext.treasury_years_to_fetch(existing, today, 1990, "nominal"), [2026])
        # In January the previous year is refreshed as well (late revisions).
        self.assertEqual(ext.treasury_years_to_fetch(existing, datetime.date(2026, 1, 9), 1990, "nominal"), [2026, 2025])

    def test_empty_answer_never_wipes_saved_year(self) -> None:
        model = ext.CurveYear(2026)
        model.nominal = {"2026-01-02": {k: 4.0 for k in ext.NOMINAL_KEYS}}
        years = {2026: model}
        fetched, latest, remaining = ext.update_treasury_block(
            years, datetime.date(2026, 1, 5), lambda url: treasury_xml([]), "nominal"
        )
        self.assertEqual(remaining, 0)
        self.assertEqual(latest, "2026-01-02")
        self.assertIn("2026-01-02", years[2026].nominal)
        self.assertGreater(fetched, 0)

    def test_backfill_stops_at_deadline_and_resumes(self) -> None:
        ticks = iter(range(1000))
        clock = lambda: next(ticks)  # noqa: E731  (each call advances one "second")
        calls: List[int] = []

        def fetch(url: str) -> bytes:
            year = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["field_tdr_date_value"][0])
            calls.append(year)
            return treasury_xml([(f"{year}-06-03", nominal_values(4.0))])

        years: Dict[int, ext.CurveYear] = {}
        fetched, latest, remaining = ext.update_treasury_block(
            years, datetime.date(2026, 10, 15), fetch, "nominal", deadline=2, clock=clock, workers=4
        )
        self.assertEqual(calls[:2], [2026, 2025])  # recent years always first
        self.assertGreater(remaining, 0)
        self.assertEqual(fetched + remaining, 37)
        self.assertEqual(latest, "2026-06-03")
        # Next run continues with the missing (older) years only.
        calls.clear()
        fetched2, _latest, remaining2 = ext.update_treasury_block(years, datetime.date(2026, 10, 15), fetch, "nominal")
        self.assertEqual(remaining2, 0)
        # Missing years, plus the current year and the (incomplete-looking) previous year.
        self.assertEqual(len(calls), remaining + 2)
        self.assertEqual(sorted(y for y in years), list(range(1990, 2027)))

    def test_curve_year_round_trip_keeps_nulls_and_drops_empty_columns(self) -> None:
        model = ext.CurveYear(2026)
        model.nominal = {
            "2026-01-02": {k: (None if k == "4M" else 4.0) for k in ext.NOMINAL_KEYS},
            "2026-01-05": {k: (None if k in ("4M", "6W") else 4.1) for k in ext.NOMINAL_KEYS},
        }
        model.real = {"2026-01-05": {"5Y": 2.0, "7Y": None, "10Y": 2.2, "20Y": 2.5, "30Y": 2.6}}
        payload = model.to_payload()
        self.assertEqual(payload["dates"], ["2026-01-02", "2026-01-05"])
        self.assertNotIn("4M", payload["nominal"])
        self.assertEqual(payload["nominal"]["6W"], [4.0, None])
        self.assertEqual(payload["real"]["5Y"], [None, 2.0])
        again = ext.CurveYear.from_payload(json.loads(json.dumps(payload)))
        self.assertEqual(again.to_payload(), payload)

    def test_pre1990_splice_uses_daily_series_and_monthly_fill(self) -> None:
        values = {
            "TB3MS": {"1934-01-01": 0.72, "1953-12-01": 1.6},
            "GS10": {"1953-04-01": 2.83, "1960-03-01": 4.28},
            "DTB3": {"1960-03-01": 3.31, "1960-03-02": 3.40},
            "DGS10": {"1975-06-02": 7.98},
            "DGS20": {"1975-06-02": 8.20},
            "DTB6": {"1975-06-02": 5.70},
        }
        years = ext.build_pre1990_curve(values)
        # Before 1954 only monthly averages exist: one row per month.
        self.assertEqual(sorted(years[1934].nominal), ["1934-01-01"])
        self.assertEqual(years[1934].nominal["1934-01-01"]["3M"], 0.72)
        # 1960: daily bill dates, 10Y filled from the monthly average of that month.
        self.assertEqual(sorted(years[1960].nominal), ["1960-03-01", "1960-03-02"])
        self.assertEqual(years[1960].nominal["1960-03-02"]["10Y"], 4.28)
        self.assertEqual(years[1960].nominal["1960-03-02"]["3M"], 3.40)
        # 1975: daily 10Y/20Y and 6M; 3M missing that day stays null (never invented).
        row = years[1975].nominal["1975-06-02"]
        self.assertEqual((row["10Y"], row["20Y"], row["6M"]), (7.98, 8.20, 5.70))
        self.assertIsNone(row["3M"])
        self.assertTrue(all(m.source == "fred" for m in years.values()))
        self.assertIn("monthly", years[1934].notes)


class TestFred(unittest.TestCase):
    def test_observation_parsing_skips_missing_values(self) -> None:
        payload = json.loads(fred_payload([("2026-10-09", "4.10"), ("2026-10-12", "."), ("bad", "1")]))
        self.assertEqual(ext.parse_fred_observations(payload), [("2026-10-09", 4.10)])

    def test_client_redacts_key_from_errors_and_throttles(self) -> None:
        sleeps: List[float] = []
        clock = iter([0.0, 0.1, 0.1, 5.0, 5.0])

        def failing(url: str) -> bytes:
            raise ext.DataSourceError(f"Failed to fetch {url} after 3 attempts")

        client = ext.FredClient(FAKE_KEY, failing, min_interval=0.6, sleep=sleeps.append, clock=lambda: next(clock))
        with self.assertRaises(ext.DataSourceError) as ctx:
            client.get("series/observations", series_id="DGS10")
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertIn("REDACTED", str(ctx.exception))
        with self.assertRaises(ext.DataSourceError):
            client.get("series/observations", series_id="DGS2")
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.5)

        error_client = ext.FredClient(
            FAKE_KEY, lambda url: b'{"error_code": 400, "error_message": "Bad Request. The value for variable api_key is not registered."}',
            min_interval=0,
        )
        with self.assertRaises(ext.DataSourceError) as ctx:
            error_client.get("series/observations", series_id="DGS10")
        self.assertIn("not registered", str(ctx.exception))

    def test_transforms(self) -> None:
        points = [(d, 100.0 + i) for i, d in enumerate(monthly_dates("2025-01-01", 14))]
        yoy = dict(ext.transform_series(points, "yoy", "monthly"))
        self.assertAlmostEqual(yoy["2026-01-01"], 12.0)
        self.assertNotIn("2025-12-01", yoy)
        mom = dict(ext.transform_series(points, "mom", "monthly"))
        self.assertAlmostEqual(mom["2025-02-01"], 1.0)
        level = ext.transform_series([("2026-04-01", 2.2)], "level", "quarterly")
        self.assertEqual(level, [("2026-04-01", 2.2)])

    def test_first_release_and_revision_history(self) -> None:
        rows = [
            ("2026-06-01", 99.8, "2026-07-15", "9999-12-31"),
            ("2026-07-01", 100.0, "2026-08-12", "2026-09-10"),
            ("2026-07-01", 100.1, "2026-09-11", "9999-12-31"),
            ("2026-08-01", 100.5, "2026-09-11", "9999-12-31"),
            # First archived 5 years after the period: not a genuine first release.
            ("2000-01-01", 50.0, "2005-01-01", "9999-12-31"),
        ]
        initial, events = ext.realtime_metric_history(ext.VintageHistory(rows), "mom", "monthly", "CPI_MOM")
        initial_map = {p: (v, r) for p, v, r in initial}
        self.assertNotIn("2000-01-01", initial_map)
        self.assertAlmostEqual(initial_map["2026-07-01"][0], 0.2, places=2)
        self.assertEqual(initial_map["2026-07-01"][1], "2026-08-12")
        self.assertAlmostEqual(initial_map["2026-08-01"][0], 0.4, places=2)
        july = [e for e in events if e[1] == "2026-07-01"]
        self.assertEqual([e[3] for e in july], [0, 1])
        self.assertEqual(july[1][2], "2026-09-11")
        self.assertEqual((july[1][4], july[1][5]), (0.3, 0.2))

    def test_recession_periods(self) -> None:
        points = [("1857-06-01", 0.0), ("1857-07-01", 1.0), ("1857-08-01", 1.0), ("1857-09-01", 0.0),
                  ("2020-03-01", 1.0), ("2020-04-01", 1.0)]
        self.assertEqual(ext.recession_periods(points), [["1857-07-01", "1857-08-31"], ["2020-03-01", "2020-04-30"]])

    MEETINGS = [
        {"start": "2026-09-15", "end": "2026-09-16", "sep": True},
        {"start": "2026-10-27", "end": "2026-10-28"},
        {"start": "2026-12-08", "end": "2026-12-09", "sep": True},
    ]

    def test_upcoming_events_one_decision_per_meeting_with_periods(self) -> None:
        events = ext.upcoming_events(
            {
                "CPIAUCSL": ["2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10"],
                "UNRATE": ["2026-11-06", "2027-06-04"],
                "A191RL1Q225SBEA": ["2026-10-29", "2026-11-25", "2026-12-23"],
            },
            self.MEETINGS,
            datetime.date(2026, 10, 15),
            {"CPI_YOY": "2026-09-01", "UNRATE": "2026-09-01", "GDP": "2026-04-01"},
        )
        by_kind = {}
        for e in events:
            by_kind.setdefault(e["kind"], []).append(e)
        # Rate decisions only on meeting days (2:00 pm ET), projections flagged.
        self.assertEqual([(e["date"], e["meeting"], e.get("projections", False)) for e in by_kind["fomc"]],
                         [("2026-10-28", "Oct 27–28", False), ("2026-12-09", "Dec 8–9", True)])
        self.assertTrue(all(e["time"] == "14:00" and e["label"] == "Fed rate decision" for e in by_kind["fomc"]))
        # Minutes three weeks after the decision; September's were before today, December's beyond the horizon.
        self.assertEqual([(e["date"], e["meeting"]) for e in by_kind["minutes"]], [("2026-11-18", "Oct 27–28")])
        # Data releases at 8:30 am ET with the period they cover.
        self.assertEqual([(e["date"], e["periodLabel"]) for e in by_kind["cpi"]],
                         [("2026-11-10", "Oct 2026"), ("2026-12-10", "Nov 2026")])
        self.assertEqual([(e["date"], e["periodLabel"]) for e in by_kind["jobs"]], [("2026-11-06", "Oct 2026")])
        self.assertEqual([(e["periodLabel"], e["estimate"]) for e in by_kind["gdp"]],
                         [("Q3 2026", "advance"), ("Q3 2026", "revised"), ("Q3 2026", "revised")])
        self.assertTrue(all(e["time"] == "08:30" for e in by_kind["cpi"] + by_kind["gdp"]))
        keys = [(e["date"], e["time"], e["label"]) for e in events]
        self.assertEqual(keys, sorted(keys))

    def test_release_periods_only_label_dates_that_follow_on(self) -> None:
        # Published earlier today, then next month's release.
        self.assertEqual(
            {d: v["periodLabel"] for d, v in ext.release_periods("ppi", ["2026-10-15", "2026-11-13"], "2026-09-01").items()},
            {"2026-10-15": "Sep 2026", "2026-11-13": "Oct 2026"},
        )
        # A gap (stale data, delayed release) gets no label rather than a wrong one.
        self.assertEqual(ext.release_periods("cpi", ["2026-11-10", "2026-12-10"], "2026-08-01"), {})
        self.assertEqual(ext.release_periods("gdp", ["2026-10-29"], "2026-01-01"), {})
        self.assertEqual(ext.release_periods("cpi", ["2026-11-10"], None), {})
        # Releases in January cover the fourth quarter of the year before.
        self.assertEqual(ext.release_periods("gdp", ["2027-01-28"], "2026-07-01")["2027-01-28"]["periodLabel"], "Q4 2026")

    def test_parse_fomc_calendar_page(self) -> None:
        meetings = ext.parse_fomc_calendar(FOMC_PAGE)
        self.assertEqual(meetings, [
            {"start": "2020-03-03", "end": "2020-03-03", "unscheduled": True},
            {"start": "2023-01-31", "end": "2023-02-01"},
            {"start": "2025-12-09", "end": "2025-12-10", "sep": True},
            {"start": "2026-09-15", "end": "2026-09-16", "sep": True},
            {"start": "2026-10-27", "end": "2026-10-28"},
            {"start": "2026-12-08", "end": "2026-12-09", "sep": True},
            {"start": "2027-01-26", "end": "2027-01-27"},
        ])
        self.assertEqual(ext.parse_fomc_calendar("<html><body>Page moved</body></html>"), [])
        self.assertEqual(ext.meeting_label({"start": "2023-01-31", "end": "2023-02-01"}), "Jan 31–Feb 1")
        self.assertEqual(ext.meeting_label({"start": "2020-03-03", "end": "2020-03-03"}), "Mar 3")

    def test_merge_and_normalize_meetings(self) -> None:
        saved = ext.normalize_meetings([
            {"start": "2026-10-27", "end": "2026-10-28", "sep": "yes"},
            {"start": "bad", "end": "2026-01-01"},
            {"start": "2026-12-01", "end": "2026-12-30"},
            "junk",
        ])
        self.assertEqual(saved, [{"start": "2026-10-27", "end": "2026-10-28"}])
        merged = ext.merge_meetings(
            [{"start": "2026-01-27", "end": "2026-01-28"}, {"start": "2027-01-26", "end": "2027-01-27"}],
            [{"start": "2026-10-27", "end": "2026-10-28"}],
        )
        # The later source replaces the whole year it covers.
        self.assertEqual([m["start"] for m in merged], ["2026-10-27", "2027-01-26"])

    def test_fed_policy_summary_maps_the_last_move_to_its_meeting(self) -> None:
        lower = [("2026-09-15", 3.5), ("2026-09-16", 3.5), ("2026-09-17", 3.75), ("2026-10-15", 3.75)]
        upper = [("2026-09-15", 3.75), ("2026-09-16", 3.75), ("2026-09-17", 4.0), ("2026-10-15", 4.0)]
        summary = ext.fed_policy_summary(lower, upper, self.MEETINGS, {"value": 3.88, "date": "2026-10-14"})
        self.assertEqual(summary, {
            "lower": 3.75, "upper": 4.0, "asOf": "2026-10-15",
            "lastChange": {"effective": "2026-09-17", "fromLower": 3.5, "fromUpper": 3.75, "toLower": 3.75,
                           "toUpper": 4.0, "bp": 25, "meeting": "Sep 15–16", "meetingEnd": "2026-09-16"},
            "effr": {"value": 3.88, "date": "2026-10-14"},
        })
        cut = ext.fed_policy_summary([("2020-03-13", 1.0), ("2020-03-16", 0.0)], [("2020-03-13", 1.25), ("2020-03-16", 0.25)], [])
        self.assertEqual(cut["lastChange"]["bp"], -100)
        self.assertNotIn("meeting", cut["lastChange"])
        self.assertIsNone(ext.fed_policy_summary([], [], []))

    def test_latest_effr_prefers_the_ny_fed_file(self) -> None:
        rates = {"sources": {"effr": {"observations": [
            {"date": "2026-10-14", "rate": 3.88}, {"date": "2026-10-13", "rate": 3.87}, {"date": "bad", "rate": 9}]}}}
        self.assertEqual(ext.latest_effr(rates, [("2026-10-12", 3.86)]), {"value": 3.88, "date": "2026-10-14"})
        self.assertEqual(ext.latest_effr(None, [("2026-10-12", 3.86)]), {"value": 3.86, "date": "2026-10-12"})
        self.assertEqual(ext.latest_effr({"sources": {}}, []), None)

    def test_indicators_have_prior_reading_and_release_date(self) -> None:
        latest = {
            "CPI_MOM": [("2026-08-01", 0.396), ("2026-09-01", 0.6492)],
            "GDP": [("2026-01-01", 2.5), ("2026-04-01", 2.2)],
        }
        releases = {"CPI_MOM": {"2026-09-01": ("2026-10-14", 0)}, "GDP": {"2026-04-01": ("2026-09-30", 1)}}
        items = ext.build_indicators(latest, releases)
        self.assertEqual([i["id"] for i in items], ["CPI_MOM", "GDP"])  # one row per indicator
        cpi, gdp = items
        self.assertEqual(cpi["value"], 0.649)  # three decimals: the page rounds once (0.6%, not 0.7%)
        self.assertEqual((cpi["previous"], cpi["previousPeriodLabel"]), (0.396, "Aug 2026"))
        self.assertEqual(cpi["released"], "2026-10-14")
        self.assertNotIn("revised", cpi)
        self.assertEqual((gdp["periodLabel"], gdp["previousPeriodLabel"], gdp["released"], gdp["revised"]),
                         ("Q2 2026", "Q1 2026", "2026-09-30", True))
        self.assertTrue(gdp["about"])


class TestFiscalData(unittest.TestCase):
    def test_operating_cash_across_statement_formats(self) -> None:
        rows = [
            {"record_date": "2026-10-13", "account_type": "Treasury General Account (TGA) Opening Balance", "close_today_bal": "null", "open_today_bal": "872526"},
            {"record_date": "2026-10-13", "account_type": "Treasury General Account (TGA) Closing Balance", "close_today_bal": "null", "open_today_bal": "920214"},
            {"record_date": "2022-03-01", "account_type": "Treasury General Account (TGA)", "close_today_bal": "699674", "open_today_bal": "771264"},
            {"record_date": "2015-06-01", "account_type": "Federal Reserve Account", "close_today_bal": "175350", "open_today_bal": "198947"},
            {"record_date": "2006-06-01", "account_type": "Federal Reserve Account", "close_today_bal": "5279", "open_today_bal": "2637"},
            {"record_date": "2006-06-01", "account_type": "Tax and Loan Note Accounts (Table V)", "close_today_bal": "3522", "open_today_bal": "1000"},
        ]
        cash = ext.extract_operating_cash(rows)
        self.assertEqual(cash["2026-10-13"], 920214)
        self.assertEqual(cash["2022-03-01"], 699674)
        self.assertEqual(cash["2015-06-01"], 175350)
        self.assertEqual(cash["2006-06-01"], 5279 + 3522)

    def test_debt_subject_to_limit_and_suspension(self) -> None:
        rows = [
            {"record_date": "2026-10-13", "debt_catg": "Debt Held by the Public", "close_today_bal": "32483641"},
            {"record_date": "2026-10-13", "debt_catg": "Intragovernmental Holdings", "close_today_bal": "7860238"},
            {"record_date": "2026-10-13", "debt_catg": "Debt Not Subject to Limit", "close_today_bal": "474"},
            {"record_date": "2026-10-13", "debt_catg": "Debt Not Subject to Limit", "close_today_bal": "194346"},
            {"record_date": "2026-10-13", "debt_catg": "Debt Not Subject to Limit", "close_today_bal": "3591"},
            {"record_date": "2026-10-13", "debt_catg": "Other Debt Subject to Limit", "close_today_bal": "0"},
            {"record_date": "2026-10-13", "debt_catg": "Statutory Debt Limit", "close_today_bal": "41103996"},
            {"record_date": "2020-06-01", "debt_catg": "Debt Held by the Public", "close_today_bal": "19868880"},
            {"record_date": "2020-06-01", "debt_catg": "Intragovernmental Holdings", "close_today_bal": "5882590"},
            {"record_date": "2020-06-01", "debt_catg": "Less Debt Not Subject to Limit", "close_today_bal": "29637"},
            {"record_date": "2020-06-01", "debt_catg": "Plus Other Debt Subject to Limit", "close_today_bal": "0"},
        ]
        limits = ext.extract_debt_limit(rows)
        self.assertEqual(limits["2026-10-13"]["subject"], 40145468)
        self.assertEqual(limits["2026-10-13"]["limit"], 41103996)
        self.assertEqual(limits["2026-10-13"]["limit"] - limits["2026-10-13"]["subject"], 958528)
        self.assertEqual(limits["2026-10-13"]["outstanding"], 40343879)
        # Suspended limit: no statutory limit row, or a row showing 0.
        self.assertIsNone(limits["2020-06-01"]["limit"])
        zero = ext.extract_debt_limit([
            {"record_date": "2024-03-01", "debt_catg": "Debt Held by the Public", "close_today_bal": "27000000"},
            {"record_date": "2024-03-01", "debt_catg": "Intragovernmental Holdings", "close_today_bal": "7000000"},
            {"record_date": "2024-03-01", "debt_catg": "Statutory Debt Limit", "close_today_bal": "0"},
        ])
        self.assertIsNone(zero["2024-03-01"]["limit"])
        self.assertEqual(limits["2020-06-01"]["subject"], 19868880 + 5882590 - 29637)

    def test_mspd_composition_mapping(self) -> None:
        rows = [
            {"record_date": "2003-01-31", "security_type_desc": "Marketable", "security_class_desc": "Bills", "total_mil_amt": "900"},
            {"record_date": "2003-01-31", "security_type_desc": "Marketable", "security_class_desc": "Notes", "total_mil_amt": "1500"},
            {"record_date": "2003-01-31", "security_type_desc": "Marketable", "security_class_desc": "Inflation-Indexed Notes", "total_mil_amt": "100"},
            {"record_date": "2003-01-31", "security_type_desc": "Marketable", "security_class_desc": "Inflation-Indexed Bonds", "total_mil_amt": "20"},
            {"record_date": "2003-01-31", "security_type_desc": "Marketable", "security_class_desc": "Federal Financing Bank", "total_mil_amt": "15"},
            {"record_date": "2026-09-30", "security_type_desc": "Marketable", "security_class_desc": "Treasury Inflation-Protected Securities", "total_mil_amt": "2172641.6"},
            {"record_date": "2026-09-30", "security_type_desc": "Marketable", "security_class_desc": "Floating Rate Notes", "total_mil_amt": "707975.5"},
            {"record_date": "2026-09-30", "security_type_desc": "Total Marketable", "security_class_desc": "_", "total_mil_amt": "31834878.4"},
            {"record_date": "2026-09-30", "security_type_desc": "Total Nonmarketable", "security_class_desc": "_", "total_mil_amt": "8336946.7"},
            {"record_date": "2026-09-30", "security_type_desc": "Total Public Debt Outstanding", "security_class_desc": "_", "total_mil_amt": "40171825.1"},
        ]
        comp = ext.extract_mspd_composition(rows)
        self.assertEqual(comp["2003-01-31"]["tips"], 120)
        self.assertEqual(comp["2003-01-31"]["notes"], 1500)
        self.assertEqual(comp["2003-01-31"]["otherMarketable"], 15)
        self.assertEqual(comp["2003-01-31"]["marketable"], 900 + 1500 + 120 + 15)
        self.assertAlmostEqual(comp["2026-09-30"]["frn"], 707975.5)
        self.assertAlmostEqual(comp["2026-09-30"]["total"], 40171825.1)

    def test_fiscal_pagination(self) -> None:
        pages = {
            1: {"data": [{"record_date": "2026-01-02"}], "meta": {"total-pages": 2}},
            2: {"data": [{"record_date": "2026-01-05"}], "meta": {"total-pages": 2}},
        }
        seen: List[str] = []

        def fetch(url: str) -> bytes:
            seen.append(url)
            page = int(url.split("page[number]=")[1].split("&")[0])
            return json.dumps(pages[page]).encode()

        rows = ext.fetch_fiscal_rows(fetch, "/v1/debt/mspd/mspd_table_1", ["record_date"], page_size=1)
        self.assertEqual([r["record_date"] for r in rows], ["2026-01-02", "2026-01-05"])
        self.assertIn("page[size]=1", seen[0])
        self.assertIn("fields=record_date", seen[0])


class TestEndToEnd(unittest.TestCase):
    def run_updater(self, data_dir: Path, key: Optional[str], network: FakeNetwork, now: str) -> ext.ExtendedUpdater:
        updater = ext.ExtendedUpdater(
            data_dir=data_dir,
            fetcher=network,
            today=datetime.date(2026, 10, 15),
            now_iso=now,
            fred_api_key=key,
            fred_min_interval=0,
            treasury_pause=0,
            log=lambda msg: None,
        )
        updater.run()
        return updater

    def test_full_run_outputs_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first = self.run_updater(data_dir, FAKE_KEY, FakeNetwork(), "2026-10-15T15:17:00Z")
            manifest = json.loads((data_dir / "manifest.json").read_text())
            self.assertEqual(manifest["schemaVersion"], "2.1")
            self.assertEqual({k: v["status"] for k, v in manifest["sources"].items()},
                             {"treasuryNominal": "ok", "treasuryReal": "ok", "fred": "ok", "fiscal": "ok",
                              "fedCalendar": "ok"})
            self.assertEqual(manifest["updatedAt"], "2026-10-15T15:17:00Z")

            # Curve files: Treasury years plus FRED-based history before 1990.
            curve_2026 = json.loads((data_dir / "curve" / "2026.json").read_text())
            self.assertEqual(curve_2026["source"], "treasury")
            self.assertEqual(curve_2026["real"]["10Y"], [2.2, 2.3])
            self.assertTrue((data_dir / "curve" / "1985.json").exists())
            self.assertTrue((data_dir / "curve" / "1953.json").exists())
            self.assertEqual(manifest["curve"]["firstDate"], "1953-04-01")
            self.assertEqual(manifest["curve"]["years"]["1985"]["source"], "fred")

            # Series: spliced nominal history, TIPS, policy rates without weekends.
            ust10 = json.loads((data_dir / "series" / "UST10Y.json").read_text())
            self.assertEqual(ust10["dates"][0], "1953-04-01")
            self.assertEqual(ust10["dates"][-1], "2026-01-05")
            effr = json.loads((data_dir / "series" / "EFFR.json").read_text())
            self.assertEqual(effr["dates"], ["2026-10-09", "2026-10-12"])
            self.assertTrue((data_dir / "series" / "TIPS10Y.json").exists())
            cpi_mom_initial = json.loads((data_dir / "series" / "CPI_MOM_INITIAL.json").read_text())
            self.assertIn("2026-07-01", cpi_mom_initial["dates"])
            self.assertIn("released", cpi_mom_initial)
            self.assertIn("UST10Y", manifest["series"])
            self.assertEqual(manifest["series"]["UST10Y"]["group"], "yields")

            # Sidebar, recessions, revisions, debt.
            unrate = next(i for i in manifest["indicators"] if i["id"] == "UNRATE")
            self.assertEqual((unrate["value"], unrate["previous"], unrate["released"]), (4.2, 4.3, "2026-10-02"))
            self.assertEqual(sum(1 for i in manifest["indicators"] if i["id"] == "GDP"), 1)
            # Fed decisions come from the Fed's calendar page, not FRED's daily release list.
            fomc = [(e["date"], e["kind"]) for e in manifest["events"] if e["kind"] in ("fomc", "minutes")]
            self.assertEqual(fomc, [("2026-10-28", "fomc"), ("2026-11-18", "minutes"), ("2026-12-09", "fomc")])
            cpi = next(e for e in manifest["events"] if e["kind"] == "cpi")
            self.assertEqual((cpi["date"], cpi["time"], cpi["label"]), ("2026-11-10", "08:30", "CPI inflation"))
            self.assertIn({"start": "2026-10-27", "end": "2026-10-28"}, manifest["fomc"]["meetings"])
            self.assertEqual(manifest["fed"]["effr"], {"value": 3.87, "date": "2026-10-12"})
            recessions = json.loads((data_dir / "recessions.json").read_text())
            self.assertEqual(recessions["periods"], [["2020-03-01", "2020-04-30"]])
            revisions = json.loads((data_dir / "revisions.json").read_text())
            self.assertTrue(any(e[0] == "GDP" and e[3] == 1 for e in revisions["events"]))
            ceiling = json.loads((data_dir / "debt" / "ceiling.json").read_text())
            self.assertEqual(ceiling["statutoryLimit"], [41103996])
            self.assertEqual(ceiling["cashBalance"], [920214])
            self.assertTrue((data_dir / "debt" / "maturities.json").exists())

            # The API key never reaches any output file.
            for path in data_dir.rglob("*.json"):
                self.assertNotIn(FAKE_KEY, path.read_text(), str(path))

            # A second run with identical data changes nothing (no noisy commits).
            before = {p: p.read_text() for p in data_dir.rglob("*.json")}
            second = self.run_updater(data_dir, FAKE_KEY, FakeNetwork(), "2026-10-15T23:17:00Z")
            self.assertEqual(second.changed_files, [])
            after = {p: p.read_text() for p in data_dir.rglob("*.json")}
            self.assertEqual(before, after)
            self.assertGreater(len(first.changed_files), 10)

    def test_missing_key_and_source_outage_keep_previous_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.run_updater(data_dir, FAKE_KEY, FakeNetwork(), "2026-10-15T15:17:00Z")
            # No key: FRED is skipped but its files and catalog entries remain.
            self.run_updater(data_dir, None, FakeNetwork(fail_hosts=("fiscaldata",)), "2026-10-15T23:17:00Z")
            manifest = json.loads((data_dir / "manifest.json").read_text())
            self.assertEqual(manifest["sources"]["fred"]["status"], "missing_key")
            self.assertFalse(manifest["fredKeyConfigured"])
            self.assertEqual(manifest["sources"]["fiscal"]["status"], "error")
            self.assertEqual(manifest["sources"]["fiscal"]["lastSuccessAt"], "2026-10-15T15:17:00Z")
            self.assertIn("EFFR", manifest["series"])
            self.assertTrue((data_dir / "series" / "EFFR.json").exists())
            self.assertTrue((data_dir / "debt" / "ceiling.json").exists())
            self.assertEqual(manifest["updatedAt"], "2026-10-15T23:17:00Z")

    def test_fed_calendar_outage_uses_saved_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.run_updater(data_dir, None, FakeNetwork(fail_hosts=("federalreserve",)), "2026-10-15T15:17:00Z")
            manifest = json.loads((data_dir / "manifest.json").read_text())
            status = manifest["sources"]["fedCalendar"]
            self.assertEqual(status["status"], "partial")
            self.assertIn("saved meeting schedule", status["errorMessage"])
            # Built-in schedule: one decision on Oct 28, none on the days around it.
            decisions = [e["date"] for e in manifest["events"] if e["kind"] == "fomc"]
            self.assertEqual(decisions, ["2026-10-28", "2026-12-09"])
            # The next run reads the page again and reports normally.
            self.run_updater(data_dir, None, FakeNetwork(), "2026-10-15T23:17:00Z")
            manifest = json.loads((data_dir / "manifest.json").read_text())
            self.assertEqual(manifest["sources"]["fedCalendar"]["status"], "ok")

    def test_first_run_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            network = FakeNetwork()
            self.run_updater(data_dir, None, network, "2026-10-15T15:17:00Z")
            self.assertFalse(any("stlouisfed" in u for u in network.urls))
            manifest = json.loads((data_dir / "manifest.json").read_text())
            self.assertEqual(manifest["curve"]["firstDate"], "2025-01-02")
            self.assertIsNone(manifest["curve"]["pre1990Version"])
            self.assertTrue((data_dir / "series" / "UST10Y.json").exists())
            self.assertFalse((data_dir / "series" / "EFFR.json").exists())

    def test_fiscal_data_is_fetched_incrementally_after_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.run_updater(data_dir, None, FakeNetwork(), "2026-10-15T15:17:00Z")
            network = FakeNetwork()
            self.run_updater(data_dir, None, network, "2026-10-15T23:17:00Z")
            fiscal_urls = [u for u in network.urls if "fiscaldata" in u]
            self.assertTrue(fiscal_urls)
            self.assertTrue(all("filter=record_date:gte:" in u for u in fiscal_urls))
            ceiling = json.loads((data_dir / "debt" / "ceiling.json").read_text())
            self.assertEqual(ceiling["dates"], ["2026-10-13"])
            self.assertEqual(ceiling["cashBalance"], [920214])

    def test_write_json_if_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x" / "a.json"
            self.assertTrue(ext.write_json_if_changed(path, {"a": 1}))
            self.assertFalse(ext.write_json_if_changed(path, {"a": 1}))
            self.assertTrue(ext.write_json_if_changed(path, {"a": 2}))

    def test_redact(self) -> None:
        text = f"Failed https://api.stlouisfed.org/fred/x?series_id=A&api_key={FAKE_KEY}&file_type=json"
        cleaned = ext.redact(text, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, cleaned)
        self.assertIn("api_key=REDACTED", cleaned)


if __name__ == "__main__":
    unittest.main()
