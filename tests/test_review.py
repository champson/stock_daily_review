import unittest
from review.analysis import analyze, validate_rows, historical_metrics
from review.runner import build_report

D = "2026-10-13"


class FinanceTests(unittest.TestCase):
    def test_breadth_excludes_missing_previous_close_and_labels_sample(self):
        rows = [{"symbol": "A", "date": D, "close": 11, "previous_close": 10, "amount": 0}, {"symbol": "B", "date": D, "close": 9, "previous_close": 10}, {"symbol": "C", "date": D, "close": 12}]
        result = analyze("stocks", rows)
        self.assertEqual(result["metrics"]["breadth"], {"advances": 1, "declines": 1, "unchanged": 0, "coverage": 2, "total": 3, "universe_complete": False})
        self.assertEqual(result["metrics"]["amount"]["value"], 0)
        self.assertEqual(result["metrics"]["amount"]["coverage"], 1)

    def test_stale_nav_never_produces_etf_premium(self):
        result = analyze("etfs", [{"symbol": "E", "date": D, "close": 2, "nav": 1.9, "nav_date": "2026-10-12"}])
        self.assertIsNone(result["rows"][0]["premium_pct"])

    def test_futures_basis_and_change_use_distinct_benchmarks(self):
        result = analyze("futures", [{"symbol": "F", "date": D, "close": 4010, "spot": 4000, "spot_date": D, "previous_close": 3900, "previous_settlement": 4000}])["rows"][0]
        self.assertEqual(result["basis"], 10)
        self.assertAlmostEqual(result["change_pct"], .25)

    def test_stale_spot_never_produces_futures_basis(self):
        row = analyze("futures", [{"symbol": "F", "date": D, "close": 4010, "spot": 4000, "spot_date": "2026-10-12"}])["rows"][0]
        self.assertIsNone(row["basis"])
        self.assertIsNone(row["basis_pct"])

    def test_options_pcr_does_not_mix_underlying_or_expiry(self):
        rows = [{"symbol": "C1", "date": D, "underlying": "U", "expiry": "2026-10-16", "option_type": "call", "volume": 100}, {"symbol": "P1", "date": D, "underlying": "U", "expiry": "2026-10-16", "option_type": "put", "volume": 50}, {"symbol": "P2", "date": D, "underlying": "U", "expiry": "2026-11-20", "option_type": "put", "volume": 900}]
        groups = analyze("options", rows)["metrics"]["groups"]
        self.assertEqual(groups[0]["volume_pcr"], .5)
        self.assertIsNone(groups[1]["volume_pcr"])
        self.assertIsNone(groups[0]["open_interest_pcr"])

    def test_rejects_stale_duplicate_and_nonfinite_quotes(self):
        valid, rejected = validate_rows([{"symbol": "A", "date": D, "close": 10}, {"symbol": "A", "date": D}, {"symbol": "B", "date": "2026-10-12"}, {"symbol": "C", "date": D, "volume": float("nan")}], D, "stocks")
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 3)

    def test_volume_ratio_uses_previous_five_days(self):
        history = [{"date": "2026-10-" + str(n).zfill(2), "close": n, "amount": 200 if n == 13 else 100} for n in range(8, 14)]
        result = historical_metrics({"date": D, "close": 13, "history": history})
        self.assertEqual(result["amount_ratio_5d"], 2)
        self.assertEqual(result["ma5"], 11)
        self.assertIsNone(result["ma20"])

    def test_calendar_holiday_skips_all_quote_tools(self):
        class Fake:
            calls = 0
            def call_tool(self, name, args):
                self.calls += 1
                return {"date": D, "open": False}
        fake = Fake()
        result = build_report(fake, {"calendar": {"tool": "verified_calendar", "date_path": "/date", "open_path": "/open"}}, D)
        self.assertTrue(result["skipped"])
        self.assertEqual(fake.calls, 1)

    def test_partial_data_failure_does_not_remove_healthy_section(self):
        class Fake:
            def call_tool(self, name, args):
                if name == "calendar":
                    return {"date": D, "open": True}
                return [{"code": "I", "day": D, "price": 101, "prev": 100}]
        config = {"calendar": {"tool": "calendar", "date_path": "/date", "open_path": "/open"}, "datasets": {"indices": {"tool": "quotes", "fields": {"symbol": "/code", "date": "/day", "close": "/price", "previous_close": "/prev"}}}}
        report = build_report(Fake(), config, D)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["sections"]["indices"]["rows"][0]["change_pct"], 1)
        self.assertEqual(len(report["errors"]), 4)


if __name__ == "__main__":
    unittest.main()
