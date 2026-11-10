"""Supplementary market data uses dated quotes and cannot break existing reports."""
import unittest
import calendar
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from review.a_stock_data import AStockData, CB_REVISION
from review.sources import build_report
from review.calendar import official_holiday_calendar
from review.vendor import convertibles as cb_api

DAY = "2026-10-13"


def sourced(**fields):
    return {"source": "fixture", "source_url": "https://fixture.example/data",
            "fetched_at": "2026-11-10T00:00:00+00:00", **fields}


def quote(code="113001", market="sh", **fields):
    return sourced(**{"market": market, "code": code, "date": DAY, "name": "测试转债", "close": 120,
                      "prev_close": 100, "volume": 100, "amount": 12000, **fields})


def bond(code="113001", **fields):
    return sourced(**{"code": code, "status": "listed", "stock_code": "600001", "listing_date": "2026-01-01",
                      "delist_date": None, "bond_price": 999, "stock_price": 999, **fields})


class SupplementaryDataTests(unittest.TestCase):
    def test_uses_dated_package_prices_and_as_of_listing_dates(self):
        adapter = AStockData({})
        mapping = [bond(), bond("113002", listing_date="2026-10-14"),
                   bond("113003", delist_date=DAY),
                   bond("113004", status="delisted", delist_date="2026-11-01")]
        # bond() supports override semantics for simple fixture composition below.
        with patch.object(adapter, "package", return_value=[quote(), quote("113004")]), \
                patch("review.a_stock_data.cb_api.convertible_bonds", return_value=pd.DataFrame(mapping)) as call:
            data = adapter.convertibles(DAY)
        call.assert_called_once_with(include_delisted=True)
        self.assertEqual([r["symbol"] for r in data["rows"]], ["sh113001", "sh113004"])
        self.assertEqual(data["rows"][0]["close"], 120)
        self.assertEqual(data["rows"][0]["previous_close"], 100)
        self.assertEqual(data["rows"][0]["underlying_symbol"], "sh600001")
        self.assertFalse(data["universe_complete"])
        self.assertIn(CB_REVISION, [p["revision"] for p in data["provenance"]])

    def test_unknown_exit_date_missing_quote_and_duplicate_mapping(self):
        adapter = AStockData({})
        mapping = [bond(), bond("113002", status="delisted"), bond("113003")]
        with patch.object(adapter, "package", return_value=[quote()]), \
                patch("review.a_stock_data.cb_api.convertible_bonds", return_value=pd.DataFrame(mapping)):
            data = adapter.convertibles(DAY)
        self.assertEqual(data["uncertain_mapping_count"], 1)
        self.assertEqual(data["missing_quote_count"], 1)
        with patch.object(adapter, "package", return_value=[quote()]), \
                patch("review.a_stock_data.cb_api.convertible_bonds", return_value=pd.DataFrame([bond(), bond()])):
            with self.assertRaisesRegex(ValueError, "重复"):
                adapter.convertibles(DAY)

    def test_optional_collection_failure_is_explicit(self):
        adapter = AStockData({})
        with patch.object(adapter, "convertibles", side_effect=RuntimeError("source failed")), \
                patch.object(adapter, "calendar_basis", return_value={"date": DAY}):
            data = adapter.market_data(DAY)["convertibles"]
        self.assertEqual(data["status"], "unavailable")
        self.assertEqual(data["rows"], [])
        self.assertIn("source failed", data["observations"][0])

    def test_supplementary_sources_fail_independently(self):
        adapter = AStockData({})
        bonds = {"rows": [quote()]}
        with patch.object(adapter, "convertibles", return_value=bonds), \
                patch.object(adapter, "calendar_basis", side_effect=RuntimeError("calendar failed")):
            data = adapter.market_data(DAY)
        self.assertEqual(data["convertibles"], bonds)
        self.assertEqual(data["calendar_basis"]["anchors"], {"year": None, "month": None, "week": None})
        self.assertEqual(data["calendar_basis"]["bias25_dates"], [])
        self.assertIn("calendar failed", data["calendar_basis"]["observations"][0])

    def test_report_survives_optional_data_failure(self):
        class Primary:
            def calendar(self, day):
                return {"date": day, "open": True}

            def collect(self, kind, day):
                return {"rows": [{"symbol": "fixture", "date": day, "close": 1, "option_type": "call"}]}

            def market_data(self, day):
                raise RuntimeError("optional source down")

        report = build_report({}, DAY, primary=Primary())
        self.assertTrue(report["sections"]["indices"]["rows"])
        self.assertEqual(report["market_data"]["convertibles"]["status"], "unavailable")

    def test_index_history_is_requested_from_previous_december(self):
        adapter = AStockData({"indices": [{"symbol": "sh000001", "name": "上证指数", "package_symbol": "sh999999"}]})
        history = [sourced(date="2025-12-31", close=100), sourced(date=DAY, close=120)]
        with patch.object(adapter, "package", return_value=[quote("999999")]), \
                patch("review.a_stock_data.api.tencent_kline", return_value=pd.DataFrame(history)) as call:
            data = adapter.indices(DAY)
        call.assert_called_once_with("sh000001", adjust="", start="2025-12-01", end=DAY, count=320)
        self.assertEqual(data["rows"][0]["history"][0]["date"], "2025-12-31")

    def test_bse_snapshot_fallback_never_invents_history(self):
        adapter = AStockData({"indices": [{"symbol": "bj899050", "name": "北证50"}]})
        with patch.object(adapter, "package", return_value=[quote("899050", "bj")]), \
                patch("review.a_stock_data.api.tencent_kline", side_effect=ValueError("北交所历史不支持")):
            data = adapter.indices(DAY)
        self.assertEqual(data["rows"][0]["symbol"], "bj899050")
        self.assertNotIn("history", data["rows"][0])
        self.assertTrue(data["partial"])

    def test_verified_upstream_mapping_keeps_future_delist_date(self):
        raw = [{"SECURITY_CODE": "113001", "TRADE_MARKET": "CNSESH", "LISTING_DATE": "2020-01-01",
                "DELIST_DATE": "2099-01-01", "CONVERT_STOCK_CODE": "600001"}]
        with patch.object(cb_api, "_em_datacenter_strict", return_value=raw) as call:
            frame = cb_api.convertible_bonds(include_delisted=True)
        self.assertEqual(frame.iloc[0]["status"], "listed")
        self.assertEqual(frame.iloc[0]["delist_date"], "2099-01-01")
        self.assertEqual(call.call_args.args[0], "RPT_BOND_CB_LIST")


class CalendarBasisTests(unittest.TestCase):
    def setUp(self):
        self.html = (Path(__file__).parent / "fixtures/tdx-holidays-2026.html").read_text()

    def month_frame(self, year, month):
        if year == 2026:
            rows = official_holiday_calendar(self.html, year, month)
        else:
            # Test-only provider fixture for Dec 2025, including every closed day.
            rows = [{"date": (d := date(year, month, n)).isoformat(), "is_open": d.weekday() < 5}
                    for n in range(1, calendar.monthrange(year, month)[1] + 1)]
        return pd.DataFrame(rows)

    def test_holiday_anchors_and_exact_25_verified_dates_use_cached_months(self):
        adapter = AStockData({})
        with patch("review.a_stock_data.api.trading_calendar", side_effect=self.month_frame) as provider:
            self.assertTrue(adapter.calendar("2026-10-09")["open"])
            basis = adapter.calendar_basis("2026-10-09")
        self.assertEqual(basis["anchors"], {"year": "2025-12-31", "month": "2026-09-30", "week": "2026-09-30"})
        self.assertEqual(basis["bias25_dates"], [
            "2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
            "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
            "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
            "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28",
            "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"])
        requested = [call.args for call in provider.call_args_list]
        self.assertEqual(len(requested), len(set(requested)))
        self.assertLessEqual(len(requested), 5)

    def test_malformed_month_is_rejected_and_failure_cached(self):
        for transform in (lambda rs: rs[:-1], lambda rs: rs + rs[:1],
                          lambda rs: [{**rs[0], "is_open": 1}] + rs[1:]):
            with self.subTest(transform=transform):
                adapter = AStockData({})
                rows = transform(self.month_frame(2026, 10).to_dict("records"))
                with patch("review.a_stock_data.api.trading_calendar", return_value=pd.DataFrame(rows)) as provider:
                    for attempt in range(2):
                        with self.assertRaisesRegex(ValueError, "月历"):
                            adapter.calendar("2026-10-09")
                provider.assert_called_once()

    def test_failed_year_does_not_remove_month_week_or_bias_proof(self):
        adapter = AStockData({})

        def partial_provider(year, month):
            if year == 2025:
                raise RuntimeError("not published")
            return self.month_frame(year, month)

        with patch("review.a_stock_data.api.trading_calendar", side_effect=partial_provider):
            basis = adapter.calendar_basis("2026-11-10")
        self.assertIsNone(basis["anchors"]["year"])
        self.assertEqual(basis["anchors"]["month"], "2026-10-30")
        self.assertEqual(basis["anchors"]["week"], "2026-11-06")
        self.assertEqual(len(basis["bias25_dates"]), 25)
        self.assertTrue(any("year" in issue for issue in basis["observations"]))

    def test_missing_recent_month_cannot_form_partial_25_date_list(self):
        adapter = AStockData({})

        def partial_provider(year, month):
            if (year, month) == (2026, 10):
                raise RuntimeError("calendar missing")
            return self.month_frame(year, month)

        with patch("review.a_stock_data.api.trading_calendar", side_effect=partial_provider):
            basis = adapter.calendar_basis("2026-11-10")
        self.assertEqual(basis["bias25_dates"], [])
        self.assertIsNone(basis["anchors"]["month"])
        self.assertEqual(basis["anchors"]["week"], "2026-11-06")


if __name__ == "__main__":
    unittest.main()
