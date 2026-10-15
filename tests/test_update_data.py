"""
Unit tests for scripts/update_data.py.

Covers:
  - Treasury XML parsing (explicit maturity mapping, 6-week BC_1_5MONTH, ignoring BC_30YEARDISPLAY)
  - NY Fed EFFR & SOFR JSON parsing (including target range fields for EFFR)
  - Preserving missing values as None (JSON null), never replacing with 0
  - Revisions and deduplication by observation date
  - Year-boundary and first-run vs. incremental fetch period calculation
  - Basis-point change calculations and same-date 10Y-2Y spread calculations
  - Partial source failure retention, total failure rejection, and missing initial data handling
  - Meaningful change detection (ignoring volatile check timestamps) and atomic file writes
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import update_data  # noqa: E402


SAMPLE_TREASURY_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:Id m:type="Edm.Int32">1</d:Id>
        <d:NEW_DATE m:type="Edm.DateTime">2025-12-31T00:00:00</d:NEW_DATE>
        <d:BC_1MONTH m:type="Edm.Double">4.38</d:BC_1MONTH>
        <d:BC_1_5MONTH m:type="Edm.Double">4.36</d:BC_1_5MONTH>
        <d:BC_2MONTH m:type="Edm.Double">4.35</d:BC_2MONTH>
        <d:BC_3MONTH m:type="Edm.Double">4.32</d:BC_3MONTH>
        <d:BC_4MONTH m:type="Edm.Double">4.31</d:BC_4MONTH>
        <d:BC_6MONTH m:type="Edm.Double">4.23</d:BC_6MONTH>
        <d:BC_1YEAR m:type="Edm.Double">4.03</d:BC_1YEAR>
        <d:BC_2YEAR m:type="Edm.Double">3.89</d:BC_2YEAR>
        <d:BC_3YEAR m:type="Edm.Double">3.89</d:BC_3YEAR>
        <d:BC_5YEAR m:type="Edm.Double">3.96</d:BC_5YEAR>
        <d:BC_7YEAR m:type="Edm.Double">4.09</d:BC_7YEAR>
        <d:BC_10YEAR m:type="Edm.Double">4.23</d:BC_10YEAR>
        <d:BC_20YEAR m:type="Edm.Double">4.62</d:BC_20YEAR>
        <d:BC_30YEAR m:type="Edm.Double">4.59</d:BC_30YEAR>
        <d:BC_30YEARDISPLAY m:type="Edm.Double">4.59</d:BC_30YEARDISPLAY>
      </m:properties>
    </content>
  </entry>
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:Id m:type="Edm.Int32">2</d:Id>
        <d:NEW_DATE m:type="Edm.DateTime">2025-01-02T00:00:00</d:NEW_DATE>
        <d:BC_1MONTH m:type="Edm.Double">4.45</d:BC_1MONTH>
        <d:BC_1_5MONTH m:null="true" />
        <d:BC_3MONTH m:type="Edm.Double">4.36</d:BC_3MONTH>
        <d:BC_2YEAR m:type="Edm.Double">4.25</d:BC_2YEAR>
        <d:BC_5YEAR m:type="Edm.Double">4.38</d:BC_5YEAR>
        <d:BC_10YEAR m:type="Edm.Double">4.57</d:BC_10YEAR>
        <d:BC_30YEAR m:type="Edm.Double">4.79</d:BC_30YEAR>
        <d:BC_30YEARDISPLAY m:type="Edm.Double">9.99</d:BC_30YEARDISPLAY>
      </m:properties>
    </content>
  </entry>
</feed>
"""

SAMPLE_EFFR_JSON = json.dumps({
    "refRates": [
        {
            "effectiveDate": "2026-01-02",
            "type": "EFFR",
            "percentRate": 4.33,
            "percentPercentile1": 4.31,
            "percentPercentile25": 4.32,
            "percentPercentile75": 4.34,
            "percentPercentile99": 4.40,
            "targetRateFrom": 4.25,
            "targetRateTo": 4.50,
            "volumeInBillions": 102,
            "revisionIndicator": "",
        },
        {
            "effectiveDate": "2025-12-31",
            "type": "EFFR",
            "percentRate": 4.33,
            "percentPercentile1": 4.30,
            "percentPercentile25": 4.32,
            "percentPercentile75": 4.34,
            "percentPercentile99": 4.38,
            "targetRateFrom": 4.25,
            "targetRateTo": 4.50,
            "volumeInBillions": 98,
            "revisionIndicator": "",
        },
    ]
}).encode("utf-8")

SAMPLE_SOFR_JSON = json.dumps({
    "refRates": [
        {
            "effectiveDate": "2026-01-02",
            "type": "SOFR",
            "percentRate": 4.35,
            "percentPercentile1": 4.30,
            "percentPercentile25": 4.33,
            "percentPercentile75": 4.39,
            "percentPercentile99": 4.44,
            "volumeInBillions": 2410,
            "revisionIndicator": "",
        },
        {
            "effectiveDate": "2025-12-31",
            "type": "SOFR",
            "percentRate": 4.49,
            "percentPercentile1": 4.40,
            "percentPercentile25": 4.46,
            "percentPercentile75": 4.55,
            "percentPercentile99": 4.62,
            "volumeInBillions": 2550,
            "revisionIndicator": "",
        },
    ]
}).encode("utf-8")


class TestUpdateData(unittest.TestCase):
    def test_treasury_xml_parsing_and_maturity_mapping(self) -> None:
        obs = update_data.parse_treasury_xml(SAMPLE_TREASURY_XML)
        # Should be sorted chronologically ascending
        self.assertEqual(len(obs), 2)
        self.assertEqual(obs[0]["date"], "2025-01-02")
        self.assertEqual(obs[1]["date"], "2025-12-31")

        # 6-week field (BC_1_5MONTH -> 6W) must be mapped when available
        self.assertEqual(obs[1]["yields"]["6W"], 4.36)
        # Missing or m:null="true" values must be None (never 0)
        self.assertIsNone(obs[0]["yields"]["6W"])
        self.assertIsNone(obs[0]["yields"]["2M"])
        self.assertIsNotNone(obs[0]["yields"]["1M"])
        self.assertEqual(obs[0]["yields"]["1M"], 4.45)

        # BC_30YEARDISPLAY must not appear as an extra maturity or override BC_30YEAR
        self.assertNotIn("BC_30YEARDISPLAY", obs[0]["yields"])
        self.assertNotIn("30Y_DISPLAY", obs[0]["yields"])
        self.assertEqual(obs[0]["yields"]["30Y"], 4.79)
        self.assertEqual(len(obs[1]["yields"]), 14)

    def test_nyfed_effr_and_sofr_parsing(self) -> None:
        effr_obs = update_data.parse_nyfed_json(SAMPLE_EFFR_JSON, "EFFR")
        self.assertEqual(len(effr_obs), 2)
        # Sorted ascending by date even though NY Fed returns descending
        self.assertEqual(effr_obs[0]["date"], "2025-12-31")
        self.assertEqual(effr_obs[1]["date"], "2026-01-02")
        self.assertEqual(effr_obs[1]["rate"], 4.33)
        self.assertEqual(effr_obs[1]["targetRateFrom"], 4.25)
        self.assertEqual(effr_obs[1]["targetRateTo"], 4.50)

        sofr_obs = update_data.parse_nyfed_json(SAMPLE_SOFR_JSON, "SOFR")
        self.assertEqual(len(sofr_obs), 2)
        self.assertEqual(sofr_obs[0]["date"], "2025-12-31")
        self.assertEqual(sofr_obs[0]["rate"], 4.49)
        self.assertNotIn("targetRateFrom", sofr_obs[0])

    def test_revisions_and_deduplication(self) -> None:
        existing = [
            {"date": "2025-12-30", "rate": 4.30, "revisionIndicator": ""},
            {"date": "2025-12-31", "rate": 4.31, "revisionIndicator": ""},
        ]
        incoming = [
            # Revised 2025-12-31 observation + new 2026-01-02 observation + duplicate
            {"date": "2025-12-31", "rate": 4.33, "revisionIndicator": "R"},
            {"date": "2026-01-02", "rate": 4.34, "revisionIndicator": ""},
            {"date": "2026-01-02", "rate": 4.35, "revisionIndicator": "R"},
        ]
        merged = update_data.merge_observations_by_date(existing, incoming)
        self.assertEqual([m["date"] for m in merged], ["2025-12-30", "2025-12-31", "2026-01-02"])
        self.assertEqual(merged[1]["rate"], 4.33)
        self.assertEqual(merged[1]["revisionIndicator"], "R")
        self.assertEqual(merged[2]["rate"], 4.35)

    def test_year_boundaries_and_first_run_periods(self) -> None:
        today = datetime.date(2026, 1, 10)
        # First run with empty existing observations -> fetches 3 calendar years (2024, 2025, 2026)
        first_run_periods = update_data.determine_treasury_fetch_periods(today, [])
        self.assertEqual(
            first_run_periods,
            [("year", "2024"), ("year", "2025"), ("year", "2026")],
        )

        # Subsequent run crossing Dec 2025 -> Jan 2026 year boundary (with full 2024..2025 history present)
        existing_obs = [
            {"date": "2024-01-02", "yields": {"10Y": 3.95}},
            {"date": "2025-12-31", "yields": {"10Y": 4.23}},
        ]
        boundary_periods = update_data.determine_treasury_fetch_periods(today, existing_obs, lookback_days=30)
        self.assertEqual(
            boundary_periods,
            [("month", "202512"), ("month", "202601")],
        )

        # If existing history is incomplete (missing 2024), determine_treasury_fetch_periods backfills all 3 years
        incomplete_obs = [{"date": "2025-12-31", "yields": {"10Y": 4.23}}]
        backfill_periods = update_data.determine_treasury_fetch_periods(today, incomplete_obs, lookback_days=30)
        self.assertEqual(
            backfill_periods,
            [("year", "2024"), ("year", "2025"), ("year", "2026")],
        )

    def test_maturity_ordering_and_durations(self) -> None:
        keys = [m["key"] for m in update_data.TREASURY_MATURITIES]
        self.assertEqual(
            keys,
            ["1M", "6W", "2M", "3M", "4M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"],
        )
        years = [m["years"] for m in update_data.TREASURY_MATURITIES]
        self.assertEqual(years, sorted(years))
        self.assertEqual(len(set(years)), len(years))

    def test_production_rates_json_integrity(self) -> None:
        rates_path = ROOT_DIR / "site" / "data" / "rates.json"
        self.assertTrue(rates_path.is_file(), "site/data/rates.json must exist")
        data = json.loads(rates_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schemaVersion"), "1.0")
        for src_id in ("treasury", "effr", "sofr"):
            src = data["sources"][src_id]
            self.assertEqual(src["status"], "ok")
            obs = src["observations"]
            self.assertGreater(len(obs), 500, f"Expected multi-year real history for {src_id}")
            dates = [row["date"] for row in obs]
            self.assertEqual(dates, sorted(dates), f"{src_id} observations must be strictly chronological")
            self.assertEqual(len(dates), len(set(dates)), f"{src_id} observations must be deduplicated")
            # Check percentage units (between 0 and 20%, not 425%)
            if src_id == "treasury":
                latest_yields = obs[-1]["yields"]
                self.assertIn("6W", latest_yields)
                self.assertNotIn("BC_30YEARDISPLAY", latest_yields)
                for val in latest_yields.values():
                    if val is not None:
                        self.assertGreater(val, 0.0)
                        self.assertLess(val, 25.0)
            else:
                latest_rate = obs[-1]["rate"]
                self.assertGreater(latest_rate, 0.0)
                self.assertLess(latest_rate, 25.0)

    def test_basis_points_and_spread_calculations(self) -> None:
        # 4.25% vs 4.18% -> +7.0 bps
        self.assertEqual(update_data.compute_basis_points_change(4.25, 4.18), 7.0)
        # 3.89% vs 4.02% -> -13.0 bps
        self.assertEqual(update_data.compute_basis_points_change(3.89, 4.02), -13.0)
        # Missing value -> None
        self.assertIsNone(update_data.compute_basis_points_change(4.25, None))
        self.assertIsNone(update_data.compute_basis_points_change(None, 4.18))

        # 10Y-2Y spread must only use same-date observations
        treasury_obs = [
            {"date": "2026-01-02", "yields": {"10Y": 4.23, "2Y": 3.89}},
            {"date": "2026-01-05", "yields": {"10Y": 4.28, "2Y": None}},
        ]
        spreads = update_data.compute_spread_series(treasury_obs, "10Y", "2Y")
        self.assertEqual(len(spreads), 2)
        self.assertEqual(spreads[0]["date"], "2026-01-02")
        self.assertEqual(spreads[0]["spread"], 0.34)
        self.assertEqual(spreads[1]["date"], "2026-01-05")
        self.assertIsNone(spreads[1]["spread"])

    def test_partial_failure_retains_previous_data_and_total_failure_raises(self) -> None:
        today = datetime.date(2026, 1, 5)

        def all_ok_fetcher(url: str) -> bytes:
            if "treasury.gov" in url:
                return SAMPLE_TREASURY_XML
            if "effr" in url:
                return SAMPLE_EFFR_JSON
            return SAMPLE_SOFR_JSON

        initial_dataset, initial_success = update_data.update_all_data(
            existing_dataset=None,
            today=today,
            fetcher=all_ok_fetcher,
            now_iso="2026-01-05T15:17:00Z",
        )
        self.assertTrue(all(initial_success.values()))
        self.assertEqual(initial_dataset["sources"]["treasury"]["status"], "ok")

        # Now simulate SOFR failing while Treasury and EFFR succeed
        def sofr_fails_fetcher(url: str) -> bytes:
            if "sofr" in url:
                raise update_data.DataSourceError("Simulated SOFR 503 outage")
            return all_ok_fetcher(url)

        partial_dataset, partial_success = update_data.update_all_data(
            existing_dataset=initial_dataset,
            today=today,
            fetcher=sofr_fails_fetcher,
            now_iso="2026-01-05T23:17:00Z",
        )
        self.assertTrue(partial_success["treasury"])
        self.assertTrue(partial_success["effr"])
        self.assertFalse(partial_success["sofr"])
        self.assertEqual(partial_dataset["sources"]["sofr"]["status"], "error")
        self.assertIn("Simulated SOFR 503 outage", partial_dataset["sources"]["sofr"]["errorMessage"])
        # Last valid SOFR observations and lastSuccessAt are retained
        self.assertEqual(len(partial_dataset["sources"]["sofr"]["observations"]), 2)
        self.assertEqual(partial_dataset["sources"]["sofr"]["lastSuccessAt"], "2026-01-05T15:17:00Z")

        # Now simulate ALL sources failing -> must raise DataSourceError
        def all_fail_fetcher(url: str) -> bytes:
            raise update_data.DataSourceError("Simulated network failure")

        with self.assertRaises(update_data.DataSourceError):
            update_data.update_all_data(
                existing_dataset=partial_dataset,
                today=today,
                fetcher=all_fail_fetcher,
            )

        # Also if initial run has one source failing and no prior history -> must raise DataSourceError
        with self.assertRaises(update_data.DataSourceError):
            update_data.update_all_data(
                existing_dataset=None,
                today=today,
                fetcher=sofr_fails_fetcher,
            )

    def test_meaningful_change_detection_and_atomic_write(self) -> None:
        today = datetime.date(2026, 1, 5)

        def mock_fetcher(url: str) -> bytes:
            if "treasury.gov" in url:
                return SAMPLE_TREASURY_XML
            if "effr" in url:
                return SAMPLE_EFFR_JSON
            return SAMPLE_SOFR_JSON

        ds1, _ = update_data.update_all_data(
            existing_dataset=None,
            today=today,
            fetcher=mock_fetcher,
            now_iso="2026-01-05T15:17:00Z",
        )
        ds2, _ = update_data.update_all_data(
            existing_dataset=ds1,
            today=today,
            fetcher=mock_fetcher,
            now_iso="2026-01-05T23:17:00Z",
        )
        # Because observations and statuses are identical, has_meaningful_changes must be False
        self.assertFalse(update_data.has_meaningful_changes(ds1, ds2))
        self.assertEqual(ds2["updatedAt"], "2026-01-05T15:17:00Z")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "rates.json"
            update_data.atomic_write_json(out_file, ds1)
            loaded = update_data.load_existing_dataset(out_file)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["schemaVersion"], "1.0")


if __name__ == "__main__":
    unittest.main()
