"""Market RS inputs are derived only from exact dated, explicitly mocked sources."""
import calendar
import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from review.sepa.market import HORIZONS, collect_market_returns, collect_return_anchors

AS_OF = "2026-11-18"
FETCHED = "2026-11-19T12:00:00+08:00"


def month_fixture(year, month):
    # Synthetic official-source fixture, intentionally with extra weekday closes.
    holidays = {f"2026-10-{n:02d}" for n in range(1, 9)}
    return {"source": "深交所（离线测试月历）", "fetched_at": FETCHED,
            "source_url": "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList",
            "rows": [{"date": (d := date(year, month, n)).isoformat(),
                      "is_open": d.weekday() < 5 and d.isoformat() not in holidays}
                     for n in range(1, calendar.monthrange(year, month)[1] + 1)]}


def anchors_fixture():
    days = []
    start = date.fromisoformat(AS_OF) - timedelta(days=560)
    for offset in range(560):
        d = start + timedelta(days=offset)
        opened = next(r["is_open"] for r in month_fixture(d.year, d.month)["rows"]
                      if r["date"] == d.isoformat())
        if opened:
            days.append(d.isoformat())
    return {horizon: days[-sessions] for horizon, sessions in HORIZONS.items()}


ANCHORS = anchors_fixture()


def raw(stamp, symbol, close=10, **fields):
    return {"date": stamp, "market": symbol[:2], "code": symbol[2:],
            "name": "测试证券", "close": close, "prev_close": close,
            "volume": 100, "amount": 1000, "source": "tdx_daily_package",
            "source_url": "https://www.tdx.com.cn/products/data/data/g4day/" + stamp.replace("-", "") + ".zip",
            "fetched_at": FETCHED, **fields}


def issuer(symbol, stamp="2010-01-01", category=2, **fields):
    return {"market": symbol[:2], "code": symbol[2:], "date": stamp, "category": category,
            "cash_dividend": None, "rights_price": None, "bonus_ratio": None,
            "rights_ratio": None, **fields}


def event_bundle(symbols):
    events = {symbol: [issuer(symbol)] for symbol in symbols}
    return events, {"dataset": "period_adjustment_events", "source": "通达信官网",
                    "provider": "tdx_official_gbbq",
                    "source_url": "https://www.tdx.com.cn/products/data/data/dbf/gbbq.zip",
                    "archive_sha256": "a" * 64, "record_count": len(events),
                    "latest_event_date": "2026-11-18", "fetched_at": FETCHED}


class OfflineAdapter:
    """The AStockData methods used by market.py, with no network implementation."""
    def __init__(self):
        self.cache = {}

    def package(self, stamp):
        raise AssertionError("package source must be explicitly mocked")

    def calendar_month(self, year, month):
        raise AssertionError("calendar source must be explicitly mocked")

    @staticmethod
    def provenance(kind, rows):
        unique = {(r["source"], r["source_url"], r["fetched_at"]) for r in rows}
        return [{"dataset": kind, "source": "a-stock-data", "provider": provider,
                 "source_url": url, "fetched_at": fetched}
                for provider, url, fetched in sorted(unique)]


class SepaMarketTests(unittest.TestCase):
    def collect(self, symbols=("sh600001", "sz000001"), changes=None,
                event_changes=None, calendar_changes=None):
        packages = {stamp: [raw(stamp, symbol, 11 if stamp == AS_OF else 10) for symbol in symbols]
                    for stamp in [AS_OF, *ANCHORS.values()]}
        events = event_bundle(symbols)
        if changes:
            changes(packages)
        if event_changes:
            event_changes(events)
        def packet(stamp):
            value = packages[stamp]
            if isinstance(value, Exception):
                raise value
            return value
        def month(year, number):
            value = month_fixture(year, number)
            if calendar_changes:
                calendar_changes(value, year, number)
            return value
        adapter = OfflineAdapter()
        with patch.object(adapter, "package", side_effect=packet) as download, \
                patch.object(adapter, "calendar_month", side_effect=month) as calendars, \
                patch("review.sepa.market.official_events", return_value=events) as adjustments:
            result = collect_market_returns(adapter, AS_OF)
        return result, download, calendars, adjustments

    def test_exact_sessions_follow_official_calendar_not_calendar_days(self):
        adapter = OfflineAdapter()
        with patch.object(adapter, "calendar_month", side_effect=month_fixture) as calendars:
            result = collect_return_anchors(adapter, AS_OF)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["anchors"], ANCHORS)
        self.assertEqual(calendars.call_count, len({call.args for call in calendars.call_args_list}))
        self.assertNotEqual(result["anchors"]["63d"],
                            (date.fromisoformat(AS_OF) - timedelta(days=63)).isoformat())
        self.assertTrue(all(p["dataset"] == "sepa_rs_calendar" for p in result["provenance"]))

    def test_four_bulk_packets_and_one_event_bundle_are_json_safe(self):
        result, packets, _, adjustments = self.collect()
        self.assertEqual(result["status"], "available")
        self.assertEqual(packets.call_count, 4)
        adjustments.assert_called_once()
        self.assertEqual(result["input_count"], 2)
        self.assertEqual(result["sample_size"], 2)
        self.assertEqual(result["market_universe"]["scope"], "market_sample")
        self.assertFalse(result["market_universe"]["complete"])
        self.assertFalse(result["market_universe"]["listing_roster_verified"])
        self.assertTrue(result["market_universe"]["vetted"])
        self.assertEqual(result["market_universe"]["date"], AS_OF)
        self.assertAlmostEqual(result["rows"][0]["returns_pct"]["252d"], 10)
        self.assertEqual(result["method"], "tdx-affine-qfq")
        self.assertEqual(len([p for p in result["provenance"] if "snapshot_sha256" in p]), 4)
        json.dumps(result, allow_nan=False)

    def test_only_named_active_sh_sz_a_share_ranges_enter_denominator(self):
        symbols = ("sh600001", "sz000001", "sh688001", "bj920001", "sh510300",
                   "sh113001", "sh600002", "sh600003", "sh600004", "sh600005")
        def changes(packages):
            fields = {"sh600002": {"volume": 0}, "sh600003": {"amount": None},
                      "sh600004": {"name": "  "}, "sh600005": {"close": True}}
            for row in packages[AS_OF]:
                row.update(fields.get(row["market"] + row["code"], {}))
        result, _, _, _ = self.collect(symbols=symbols, changes=changes)
        self.assertEqual(result["input_count"], 3)
        self.assertEqual({r["symbol"] for r in result["rows"]}, {"sh600001", "sz000001", "sh688001"})
        self.assertTrue(any("北交所" in s for s in result["observations"]))

    def test_affine_cash_dividend_adjustment_keeps_full_precision(self):
        def changes(bundle):
            bundle[0]["sh600001"].append(issuer("sh600001", stamp="2026-11-01", category=1,
                cash_dividend=10, rights_price=0, bonus_ratio=0, rights_ratio=0))
        result, _, _, _ = self.collect(event_changes=changes)
        row = next(r for r in result["rows"] if r["symbol"] == "sh600001")
        expected = (11 / 9 - 1) * 100
        self.assertEqual(row["returns_pct"]["63d"], expected)
        self.assertEqual(row["returns_pct"]["252d"], expected)
        self.assertNotEqual(row["returns_pct"]["63d"], round(expected, 2))

    def test_missing_historic_close_excludes_security_not_zero_return(self):
        def changes(packages):
            packages[ANCHORS["252d"]] = [r for r in packages[ANCHORS["252d"]] if r["market"] == "sh"]
        result, _, _, _ = self.collect(changes=changes)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["input_count"], 2)
        self.assertEqual([r["symbol"] for r in result["rows"]], ["sh600001"])
        self.assertEqual(result["excluded"]["missing_252d"], 1)

    def test_absent_issuer_record_never_means_identity(self):
        result, _, _, _ = self.collect(event_changes=lambda bundle: bundle[0].pop("sz000001"))
        self.assertEqual(result["status"], "partial")
        self.assertEqual([r["symbol"] for r in result["rows"]], ["sh600001"])
        self.assertEqual(result["excluded"]["missing_or_mismatched_issuer"], 1)

    def test_mismatched_issuer_and_unsupported_events_are_excluded(self):
        def changes(bundle):
            bundle[0]["sh600001"] = [issuer("sz000001")]
            bundle[0]["sz000001"].append(issuer("sz000001", stamp="2026-11-01", category=11))
        result, _, _, _ = self.collect(event_changes=changes)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["market_universe"]["vetted"])
        self.assertEqual(result["rows"], [])

    def test_incomplete_or_unofficial_calendar_never_guesses_dates(self):
        for kind in ("missing_day", "duplicate", "source", "unknown_year"):
            def changes(value, year, month):
                if kind == "missing_day": value["rows"].pop()
                if kind == "duplicate": value["rows"][-1] = dict(value["rows"][0])
                if kind == "source": value["source_url"] = "https://calendar.example/calendar"
                if kind == "unknown_year" and year == 2025: raise ValueError("没有2025官方归档")
            with self.subTest(kind=kind):
                result, packets, _, adjustments = self.collect(calendar_changes=changes)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["rows"], [])
                packets.assert_not_called()
                adjustments.assert_not_called()
                self.assertTrue(any("锚点" in n for n in result["observations"]))

    def test_closed_asof_makes_no_market_requests(self):
        def changes(value, year, month):
            for row in value["rows"]:
                if row["date"] == AS_OF: row["is_open"] = False
        result, packets, _, adjustments = self.collect(calendar_changes=changes)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["input_count"], 0)
        packets.assert_not_called(); adjustments.assert_not_called()

    def test_wrong_date_or_duplicate_or_foreign_packet_is_not_nearby_substitute(self):
        for kind in ("date", "duplicate", "source"):
            def changes(packages):
                packet = packages[ANCHORS["63d"]]
                if kind == "date": packet[0]["date"] = AS_OF
                if kind == "duplicate": packet.append(dict(packet[0]))
                if kind == "source": packet[0]["source_url"] = "https://source.example/snapshot.zip"
            with self.subTest(kind=kind):
                result, packets, _, adjustments = self.collect(changes=changes)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["input_count"], 2)
                self.assertEqual(packets.call_count, 4)
                self.assertFalse(result["market_universe"]["vetted"])
                adjustments.assert_not_called()

    def test_failed_packet_or_events_never_produces_empty_success(self):
        def changes(packages): packages[ANCHORS["126d"]] = OSError("archive unavailable")
        result, _, _, adjustments = self.collect(changes=changes)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["rows"], [])
        adjustments.assert_not_called()
        adapter = OfflineAdapter()
        with patch.object(adapter, "calendar_month", side_effect=month_fixture), \
                patch.object(adapter, "package", side_effect=lambda stamp: [raw(stamp, "sh600001")]), \
                patch("review.sepa.market.official_events", side_effect=ValueError("bad gbbq")) as events:
            result = collect_market_returns(adapter, AS_OF)
        events.assert_called_once()
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(any("未假设复权因子为1" in n for n in result["observations"]))

    def test_stale_or_unverified_event_file_fails_closed(self):
        for kind in ("date", "hash", "source"):
            def changes(bundle):
                if kind == "date": bundle[1]["fetched_at"] = "2026-11-17T23:59:59+08:00"
                if kind == "hash": bundle[1].pop("archive_sha256")
                if kind == "source": bundle[1]["source_url"] = "https://events.example/gbbq.zip"
            with self.subTest(kind=kind):
                result, _, _, _ = self.collect(event_changes=changes)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["rows"], [])

    def test_large_sample_does_not_request_per_security_history(self):
        symbols = tuple(f"sh{600000+i:06d}" for i in range(2000))
        result, packets, _, events = self.collect(symbols=symbols)
        self.assertEqual(result["input_count"], 2000)
        self.assertEqual(len(result["rows"]), 2000)
        self.assertEqual(packets.call_count, 4)
        events.assert_called_once()


if __name__ == "__main__":
    unittest.main()
