"""Cross-period prices use bulk exact-date snapshots and verified event data."""
import http.client
import json
import unittest
from unittest.mock import MagicMock, patch

from review.a_stock_data import AStockData
from review.period_data import collect_period_prices, official_events


DAY = "2026-11-10"
ANCHORS = {"year": "2025-12-31", "month": "2026-10-30", "week": "2026-11-06"}


def current(symbol, close=11, **fields):
    return {"symbol": symbol, "date": DAY, "close": close, **fields}


def raw(stamp, symbol, close=10):
    return {"date": stamp, "market": symbol[:2], "code": symbol[2:], "name": "测试证券",
            "close": close, "prev_close": close, "volume": 100, "amount": 1000,
            "source": "tdx", "source_url": "https://fixture.example/" + stamp + ".zip",
            "fetched_at": "2026-11-10T18:00:00+08:00"}


def event(symbol="sh600001", stamp="2026-06-01", category=1, **fields):
    return {"market": symbol[:2], "code": symbol[2:], "date": stamp, "category": category,
            "cash_dividend": 10, "rights_ratio": 0, "rights_price": 0, "bonus_ratio": 0, **fields}


def initial_event(symbol):
    return event(symbol, stamp="2010-01-01", category=2,
                 cash_dividend=None, rights_ratio=None, rights_price=None, bonus_ratio=None)


def bundle(symbols=("sh600001", "sz000001")):
    return {symbol: [event() if symbol == "sh600001" else initial_event(symbol)] for symbol in symbols}, \
        {"dataset": "period_adjustment_events", "provider": "fixture-gbbq"}


class PeriodDataTests(unittest.TestCase):
    def collect(self, stocks=None, bonds=None, changes=None, anchors=None, events=None):
        stocks = stocks if stocks is not None else [current("sh600001"), current("sz000001", 20)]
        bonds = bonds if bonds is not None else [current("sh113001", 120)]
        symbols = [r["symbol"] for r in stocks + bonds if isinstance(r, dict) and "symbol" in r]
        basis = {"date": DAY, "anchors": anchors or ANCHORS, "provenance": []}
        packages = {stamp: [raw(stamp, symbol) for symbol in sorted(set(symbols))]
                    for stamp in set(basis["anchors"].values()) if stamp}
        if changes:
            changes(packages)
        def package(stamp):
            value = packages[stamp]
            if isinstance(value, Exception):
                raise value
            return value
        adapter = AStockData({})
        kwargs = {"side_effect": events} if isinstance(events, Exception) else {
            "return_value": events if events is not None else bundle(r["symbol"] for r in stocks
                                                                    if isinstance(r, dict) and "symbol" in r)}
        with patch.object(adapter, "package", side_effect=package) as provider, \
                patch("review.period_data.official_events", **kwargs) as adjustments:
            result = collect_period_prices(adapter, DAY, basis, stocks, bonds)
        return result, provider, adjustments

    def test_bulk_anchors_and_affine_stock_prices_bond_prices_remain_raw(self):
        result, provider, adjustments = self.collect()
        self.assertEqual(set(call.args[0] for call in provider.call_args_list), set(ANCHORS.values()))
        self.assertEqual(provider.call_count, 3)
        adjustments.assert_called_once()
        stock = next(r for r in result["stocks"]["rows"] if r["symbol"] == "sh600001")
        self.assertEqual(stock["close"], 11)
        self.assertEqual(stock["year"], {"date": ANCHORS["year"], "raw_close": 10, "adjusted_close": 9})
        self.assertEqual(stock["month"]["adjusted_close"], 10)
        bond = result["convertibles"]["rows"][0]
        self.assertEqual(bond["year"], {"date": ANCHORS["year"], "raw_close": 10})
        self.assertNotIn("adjusted_close", bond["year"])
        self.assertEqual(result["stocks"]["period_coverage"], {"year": 2, "month": 2, "week": 2})
        self.assertEqual(result["method"], "tdx-affine-qfq")
        self.assertTrue(any(p.get("trading_date") == ANCHORS["year"] for p in result["provenance"]))

    def test_shared_anchor_is_requested_once_and_null_calendar_does_not_guess(self):
        result, provider, _ = self.collect(anchors=dict.fromkeys(ANCHORS, ANCHORS["year"]))
        self.assertEqual(provider.call_count, 1)
        for period in ANCHORS:
            self.assertEqual(result["stocks"]["rows"][0][period]["date"], ANCHORS["year"])
        result, provider, adjustments = self.collect(anchors=dict.fromkeys(ANCHORS))
        provider.assert_not_called(); adjustments.assert_not_called()
        self.assertEqual(result["stocks"]["rows"], [])
        self.assertTrue(any("交易日历" in note for note in result["observations"]))

    def test_one_anchor_failure_keeps_other_periods_and_bonds(self):
        def changes(packages):
            packages[ANCHORS["month"]] = RuntimeError("source unavailable")
        result, provider, _ = self.collect(changes=changes)
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(result["stocks"]["status"], "partial")
        self.assertIsNone(result["stocks"]["rows"][0]["month"])
        self.assertIsNotNone(result["stocks"]["rows"][0]["year"])
        self.assertIsNone(result["convertibles"]["rows"][0]["month"])
        self.assertTrue(any("source unavailable" in note for note in result["observations"]))

    def test_bad_gbbq_never_becomes_identity_but_bond_periods_survive(self):
        result, _, _ = self.collect(events=ValueError("bad complete event file"))
        self.assertEqual(result["stocks"]["status"], "unavailable")
        self.assertEqual(result["stocks"]["rows"], [])
        self.assertEqual(result["convertibles"]["status"], "available")
        self.assertEqual(result["convertibles"]["period_coverage"], {"year": 1, "month": 1, "week": 1})
        self.assertTrue(any("因子1" in note for note in result["stocks"]["observations"]))

    def test_bad_anchor_date_and_duplicate_symbols_are_not_nearby_substitutes(self):
        for kind in ("date", "duplicate"):
            def changes(packages):
                if kind == "date":
                    packages[ANCHORS["week"]][0]["date"] = "2026-11-05"
                else:
                    packages[ANCHORS["week"]].append(dict(packages[ANCHORS["week"]][0]))
            with self.subTest(kind=kind):
                result, _, _ = self.collect(changes=changes)
                self.assertTrue(all(r["week"] is None for r in result["stocks"]["rows"]))
                self.assertTrue(all(r["week"] is None for r in result["convertibles"]["rows"]))
                self.assertIsNotNone(result["stocks"]["rows"][0]["month"])

    def test_new_listing_and_unsupported_adjustment_market_are_excluded_per_period(self):
        def changes(packages):
            packages[ANCHORS["year"]] = [r for r in packages[ANCHORS["year"]] if r["code"] != "000001"]
        stocks = [current("sh600001"), current("sz000001", 20), current("bj920001", 30)]
        result, _, _ = self.collect(stocks=stocks, changes=changes)
        lookup = {r["symbol"]: r for r in result["stocks"]["rows"]}
        self.assertIsNone(lookup["sz000001"]["year"])
        self.assertIsNotNone(lookup["sz000001"]["month"])
        self.assertEqual([lookup["bj920001"][p] for p in ANCHORS], [None, None, None])
        self.assertEqual(result["stocks"]["period_coverage"], {"year": 1, "month": 2, "week": 2})
        self.assertTrue(any("北交所" in note for note in result["stocks"]["observations"]))

    def test_zero_invalid_and_duplicate_current_quotes_do_not_enter_period_pool(self):
        stocks = [current("sh600001"), current("sh600001", 12), current("sz000001", 20),
                  current("sh600002", 0), current("sh600003", True), current("sh600004", -1),
                  current("sh600005", 10, date="2026-11-09")]
        result, _, _ = self.collect(stocks=stocks)
        self.assertEqual([r["symbol"] for r in result["stocks"]["rows"]], ["sz000001"])
        self.assertTrue(any("重复" in note for note in result["stocks"]["observations"]))

    def test_unsupported_event_or_nonpositive_adjusted_anchor_excludes_only_affected_period(self):
        for changes in ([event(category=11)], [event(cash_dividend=200)]):
            with self.subTest(events=changes):
                result, _, _ = self.collect(events=({"sh600001": changes}, {"dataset": "fixture"}))
                row = next(r for r in result["stocks"]["rows"] if r["symbol"] == "sh600001")
                self.assertIsNone(row["year"])
                self.assertIsNotNone(row["month"])
                self.assertTrue(result["stocks"]["quality_examples"])

    def test_missing_issuer_record_never_means_no_corporate_actions(self):
        def changes(packages):
            packages[ANCHORS["year"]] = [r for r in packages[ANCHORS["year"]] if r["code"] != "000001"]
        for issuer_records in (None, []):
            with self.subTest(records=issuer_records):
                events, provenance = bundle(("sh600001",))
                if issuer_records is not None:
                    events["sz000001"] = issuer_records
                result, _, _ = self.collect(events=(events, provenance), changes=changes)
                lookup = {r["symbol"]: r for r in result["stocks"]["rows"]}
                self.assertTrue(all(lookup["sz000001"][period] is None for period in ANCHORS))
                self.assertIsNotNone(lookup["sh600001"]["year"])
                self.assertEqual(result["stocks"]["period_coverage"], {"year": 1, "month": 1, "week": 1})
                notes = " ".join(result["stocks"]["observations"])
                self.assertIn("发行人记录", notes)
                self.assertIn("year 0，month 1，week 1", notes)
                self.assertTrue(any("发行人记录" in item["reason"] for item in result["stocks"]["quality_examples"]))

    def test_compact_cross_market_payload_has_constant_bulk_request_count(self):
        stocks = [current(f"sh{600000+i:06d}", 11) for i in range(5000)]
        result, provider, adjustments = self.collect(stocks=stocks, bonds=[])
        self.assertEqual(len(result["stocks"]["rows"]), 5000)
        self.assertEqual(provider.call_count, 3); adjustments.assert_called_once()
        self.assertLess(len(json.dumps(result)), 2_500_000)

    def test_official_event_download_and_failure_are_cached(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                adapter = AStockData({})
                response = MagicMock(); response.read.return_value = b"fixture archive"
                response.headers.get.return_value = None
                opener = MagicMock(); opener.__enter__.return_value = response
                kwargs = {"side_effect": ValueError("invalid archive")} if failure else {"return_value": [event()]}
                with patch("review.period_data.urllib.request.urlopen", return_value=opener) as fetch, \
                        patch("review.tdx_adjustments.decode_gbbq_zip", **kwargs) as decode:
                    for _ in range(2):
                        if failure:
                            with self.assertRaisesRegex(ValueError, "除权事件包"):
                                official_events(adapter)
                        else:
                            groups, source = official_events(adapter)
                            self.assertEqual(groups["sh600001"], [event()])
                            self.assertIn("archive_sha256", source)
                    fetch.assert_called_once(); decode.assert_called_once()

    def test_incomplete_http_event_download_is_cached_and_keeps_bond_periods(self):
        adapter = AStockData({})
        stocks, bonds = [current("sh600001")], [current("sh113001", 120)]
        basis = {"date": DAY, "anchors": ANCHORS, "provenance": []}
        packages = {stamp: [raw(stamp, "sh600001"), raw(stamp, "sh113001")]
                    for stamp in ANCHORS.values()}
        response = MagicMock()
        response.read.side_effect = http.client.IncompleteRead(b"truncated zip", 100)
        opener = MagicMock()
        opener.__enter__.return_value = response
        with patch.object(adapter, "package", side_effect=lambda stamp: packages[stamp]), \
                patch("review.period_data.urllib.request.urlopen", return_value=opener) as fetch, \
                patch("review.tdx_adjustments.decode_gbbq_zip") as decode:
            for _ in range(2):
                result = collect_period_prices(adapter, DAY, basis, stocks, bonds)
                self.assertEqual(result["stocks"]["status"], "unavailable")
                self.assertEqual(result["stocks"]["rows"], [])
                self.assertEqual(result["convertibles"]["status"], "available")
                self.assertEqual(result["convertibles"]["period_coverage"], {"year": 1, "month": 1, "week": 1})
                self.assertTrue(any("IncompleteRead" in note for note in result["stocks"]["observations"]))
            fetch.assert_called_once()
            response.read.assert_called_once()
            decode.assert_not_called()
        self.assertIn("IncompleteRead", adapter.cache[("period_gbbq_error",)])
        self.assertNotIn(("period_gbbq",), adapter.cache)

    def test_main_stock_cache_and_optional_failure_preserve_other_data(self):
        adapter = AStockData({})
        adapter.cache[("stocks", DAY)] = [current("sh600001")]
        basis = {"date": DAY, "anchors": ANCHORS}
        bonds = {"rows": [current("sh113001", 120)]}
        with patch("review.period_data.collect_period_prices", return_value={"kept": True}) as collector:
            self.assertEqual(adapter.period_prices(DAY, basis, bonds), {"kept": True})
        self.assertIs(collector.call_args.args[3], adapter.cache[("stocks", DAY)])
        with patch.object(adapter, "convertibles", return_value=bonds), \
                patch.object(adapter, "calendar_basis", return_value=basis), \
                patch.object(adapter, "sector_indices", return_value={"sector": "preserved"}), \
                patch.object(adapter, "period_prices", side_effect=RuntimeError("period source failed")):
            data = adapter.market_data(DAY)
        self.assertIs(data["convertibles"], bonds)
        self.assertIs(data["calendar_basis"], basis)
        self.assertEqual(data["sector_indices"], {"sector": "preserved"})
        self.assertEqual(data["period_prices"]["anchors"], ANCHORS)
        self.assertTrue(any("period source failed" in note for note in data["period_prices"]["observations"]))

    def test_stock_cache_matches_main_row_validator_not_unvalidated_package_candidates(self):
        adapter = AStockData({})
        healthy = raw(DAY, "sh600001")
        invalid = {**raw(DAY, "sh600002"), "prev_close": True}
        with patch.object(adapter, "package", return_value=[healthy, invalid]):
            candidates = adapter.stocks(DAY)
        self.assertEqual(len(candidates["rows"]), 2)
        self.assertEqual([r["symbol"] for r in adapter.cache[("stocks", DAY)]], ["sh600001"])


if __name__ == "__main__":
    unittest.main()
