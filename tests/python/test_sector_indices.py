"""Dated sector observations never substitute an earlier or undated price."""
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from review.a_stock_data import AStockData
from review.calendar import official_holiday_calendar


DAY = "2026-10-08"
PREVIOUS = "2026-09-30"
SETTINGS = {"sector_indices": [
    {"symbol": "sh000986", "name": "中证全指能源指数"},
    {"symbol": "sh000991", "name": "中证全指医药卫生指数"},
]}


def quote(stamp, close):
    return {"date": stamp, "close": close, "source": "tencent",
            "source_url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "fetched_at": "2026-11-10T08:00:00+00:00"}


def history(close=110):
    return pd.DataFrame([quote("2026-09-29", 95), quote(PREVIOUS, 100), quote(DAY, close)])


def calendar_fixture(day):
    # Explicit dates, including National Day closure; not a weekday heuristic.
    return {"date": day, "open": day in {"2026-09-29", PREVIOUS, DAY},
            "source": "official-calendar-fixture", "source_url": "https://fixture.example/calendar",
            "fetched_at": "2026-11-10T08:00:00+00:00"}


class SectorIndexTests(unittest.TestCase):
    def collect(self, frames, settings=SETTINGS):
        adapter = AStockData(settings)
        with patch.object(adapter, "calendar", side_effect=calendar_fixture), \
                patch("review.a_stock_data.api.tencent_kline", side_effect=frames) as provider:
            data = adapter.sector_indices(DAY)
        return data, provider

    def test_official_holiday_calendar_selects_exact_previous_session(self):
        adapter = AStockData(SETTINGS)
        html = (Path(__file__).parent / "fixtures/tdx-holidays-2026.html").read_text()
        def month(year, number):
            return pd.DataFrame(official_holiday_calendar(html, year, number))
        with patch("review.a_stock_data.api.trading_calendar", side_effect=month) as calendars, \
                patch("review.a_stock_data.api.tencent_kline", side_effect=[history(110), history(90)]) as provider:
            data = adapter.sector_indices(DAY)
        self.assertEqual(data["previous_trading_date"], PREVIOUS)
        self.assertEqual(data["status"], "available")
        self.assertEqual(data["sample_size"], 2)
        self.assertFalse(data["universe_complete"])
        self.assertEqual([r["symbol"] for r in data["rows"]], ["sh000986", "sh000991"])
        self.assertAlmostEqual(data["rows"][0]["change_pct"], 10)
        self.assertAlmostEqual(data["rows"][1]["change_pct"], -10)
        for row in data["rows"]:
            self.assertEqual((row["date"], row["previous_date"]), (DAY, PREVIOUS))
        provider.assert_any_call("sh000986", adjust="", start="2026-09-08", end=DAY, count=40)
        self.assertEqual(sorted(call.args for call in calendars.call_args_list), [(2026, 9), (2026, 10)])
        proved = [p["date"] for p in data["provenance"] if p["dataset"] == "sector_indices_calendar"]
        self.assertEqual(proved, [DAY, PREVIOUS])
        self.assertTrue(any(p.get("provider") == "tencent" for p in data["provenance"]))

    def test_missing_true_previous_session_never_uses_nearby_older_bar(self):
        bad = pd.DataFrame([quote("2026-09-29", 95), quote(DAY, 110)])
        data, _ = self.collect([bad, history(90)])
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["sample_size"], 1)
        self.assertEqual(data["rows"][0]["symbol"], "sh000991")
        self.assertTrue(any(PREVIOUS in note and "缺少" in note for note in data["observations"]))

    def test_stale_future_duplicate_disordered_and_invalid_prices_are_rejected(self):
        base = [quote(PREVIOUS, 100), quote(DAY, 110)]
        invalid = [
            [base[0]],
            base + [quote("2026-10-09", 111)],
            [base[0], base[0], base[1]],
            list(reversed(base)),
            [quote("2026-02-30", 100), base[1]],
            [quote(PREVIOUS, 0), base[1]],
            [quote(PREVIOUS, -1), base[1]],
            [base[0], quote(DAY, math.nan)],
            [base[0], quote(DAY, math.inf)],
            [quote(PREVIOUS, 1e-300), quote(DAY, 1e300)],
        ]
        for rows in invalid:
            with self.subTest(rows=rows):
                data, _ = self.collect([pd.DataFrame(rows)], {"sector_indices": SETTINGS["sector_indices"][:1]})
                self.assertEqual(data["status"], "unavailable")
                self.assertEqual(data["rows"], [])

    def test_one_source_failure_keeps_valid_remaining_observations(self):
        data, provider = self.collect([RuntimeError("provider failed"), history(90)])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(data["configured_count"], 2)
        self.assertEqual(data["sample_size"], 1)
        self.assertEqual(data["status"], "partial")
        self.assertTrue(any("provider failed" in note for note in data["observations"]))

    def test_missing_or_closed_calendar_does_not_request_prices(self):
        for cal in (RuntimeError("calendar unavailable"), {**calendar_fixture(DAY), "open": False}):
            with self.subTest(calendar=cal):
                adapter = AStockData(SETTINGS)
                kwargs = {"side_effect": cal} if isinstance(cal, Exception) else {"return_value": cal}
                with patch.object(adapter, "calendar", **kwargs), \
                        patch("review.a_stock_data.api.tencent_kline") as provider:
                    data = adapter.sector_indices(DAY)
                provider.assert_not_called()
                self.assertEqual(data["status"], "unavailable")
                self.assertIsNone(data["previous_trading_date"])

    def test_unconfigured_pool_never_calls_network(self):
        adapter = AStockData({})
        with patch.object(adapter, "calendar") as calendar, \
                patch("review.a_stock_data.api.tencent_kline") as provider:
            data = adapter.sector_indices(DAY)
        calendar.assert_not_called(); provider.assert_not_called()
        self.assertEqual(data["configured_count"], 0)
        self.assertEqual(data["rows"], [])

    def test_sector_failure_does_not_erase_other_supplementary_data(self):
        adapter = AStockData(SETTINGS)
        bonds, basis = {"rows": ["preserved-bond"]}, {"date": DAY}
        with patch.object(adapter, "convertibles", return_value=bonds), \
                patch.object(adapter, "calendar_basis", return_value=basis), \
                patch.object(adapter, "sector_indices", side_effect=RuntimeError("sector failed")):
            data = adapter.market_data(DAY)
        self.assertIs(data["convertibles"], bonds)
        self.assertIs(data["calendar_basis"], basis)
        sector = data["sector_indices"]
        self.assertEqual((sector["date"], sector["sample_size"], sector["status"]), (DAY, 0, "unavailable"))
        self.assertIsNone(sector["previous_trading_date"])
        self.assertTrue(any("sector failed" in note for note in sector["observations"]))

    def test_configured_ten_indices_and_all_available_scope(self):
        settings = json.loads((Path(__file__).parents[2] / "config/review.json").read_text())["primary"]
        configured = settings["sector_indices"]
        self.assertEqual([r["symbol"] for r in configured], [f"sh{code:06d}" for code in range(986, 996)])
        self.assertIn("金融地产", configured[6]["name"])
        self.assertIn("通信服务", configured[8]["name"])
        data, _ = self.collect([history(100 + i) for i in range(10)], settings)
        self.assertEqual((data["status"], data["configured_count"], data["sample_size"]), ("available", 10, 10))
        self.assertFalse(data["universe_complete"])
        self.assertTrue(any("不代表现行全部" in note for note in data["observations"]))


if __name__ == "__main__":
    unittest.main()
