"""Financial behavior tests for independent Trend Template and market RS."""
from datetime import date, timedelta
import json
import math
import unittest

from review.sepa.trend import analyze_trend, build_market_rs


AS_OF = "2026-11-19"


def bars(count=253, declining=False):
    days, cursor = [], date.fromisoformat(AS_OF)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()
    closes = [500 - index if declining else 100 + index for index in range(count)]
    return [{"date": stamp, "open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": 1000} for stamp, close in zip(days, closes)]


def market_config(count=3, scope="market", **settings):
    return {"market_universe": {"id": "test-defined-market", "label": "离线市场范围测试",
                               "date": AS_OF, "scope": scope, "vetted": True,
                               "point_in_time": True, "complete": scope == "market",
                               "eligible_count": count},
            "trend": {"rs_min_samples": 2, **settings}}


def market_row(symbol, value, **fields):
    return {"symbol": symbol, "date": AS_OF,
            "returns_pct": {"63d": value, "126d": value, "252d": value}, **fields}


def evidence(config=None, symbol="C"):
    config = config or market_config()
    snapshot = build_market_rs(AS_OF, [market_row("A", 10), market_row("B", 20), market_row("C", 30)], config)
    return {**next(row for row in snapshot["rows"] if row["symbol"] == symbol),
            "universe": snapshot["universe"], "coverage": snapshot["coverage"]}


def check(result, key):
    return next(item for item in result["checks"] if item["id"] == key)


class TrendTests(unittest.TestCase):
    def test_complete_rising_history_passes_all_eight_checks(self):
        config = market_config()
        result = analyze_trend(bars(), config, evidence(config))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["checks"]), 8)
        self.assertTrue(all(item["status"] == "pass" for item in result["checks"]))
        self.assertEqual(result["moving_averages"]["ma200_positive_slope_days"], 21)
        self.assertAlmostEqual(result["returns_pct"]["63d"], (352 / 289 - 1) * 100)
        self.assertAlmostEqual(result["returns_pct"]["126d"], (352 / 226 - 1) * 100)
        self.assertAlmostEqual(result["returns_pct"]["252d"], 252)
        json.dumps(result, allow_nan=False)

    def test_missing_rs_does_not_hide_seven_checks_or_returns(self):
        result = analyze_trend(bars(), market_config())
        self.assertEqual(result["status"], "insufficient")
        self.assertTrue(all(item["status"] == "pass" for item in result["checks"][:7]))
        self.assertEqual(check(result, "market_rs")["status"], "insufficient")
        self.assertEqual(result["returns_pct"]["252d"], 252)

    def test_hard_price_failures_win_over_missing_rs_and_keep_actual_values(self):
        data = bars(declining=True)
        result = analyze_trend(data, market_config())
        self.assertEqual(result["status"], "fail")
        self.assertEqual(check(result, "market_rs")["status"], "insufficient")
        self.assertEqual(check(result, "ma200_rising_21")["actual"]["consecutive_positive_days"], 0)
        self.assertAlmostEqual(result["returns_pct"]["252d"], (248 / 500 - 1) * 100)
        self.assertIsNotNone(result["moving_averages"]["ma50"])

    def test_short_history_calculates_independent_checks_and_partial_returns(self):
        result = analyze_trend(bars(80), market_config())
        self.assertEqual(check(result, "price_above_ma50")["status"], "pass")
        self.assertEqual(check(result, "price_above_ma150_ma200")["status"], "insufficient")
        self.assertIsNotNone(result["returns_pct"]["63d"])
        self.assertIsNone(result["returns_pct"]["126d"])
        self.assertIsNone(result["returns_pct"]["252d"])
        self.assertEqual(result["status"], "insufficient")
        # 252 observations cover the 52-week extrema but cannot produce a 252-session return.
        result = analyze_trend(bars(252), market_config(), evidence())
        self.assertTrue(all(item["status"] == "pass" for item in result["checks"]))
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["returns_pct"]["252d"])

    def test_sma200_requires_every_one_of_21_recent_slopes_positive(self):
        data = bars()
        # Replacing this close with the outgoing 200th close makes one exact flat
        # MA200 step, while the end-to-end 21-step MA200 change is still positive.
        position = len(data) - 10
        close = data[position - 200]["close"]
        data[position].update(open=close, close=close, low=close - 1, high=close + 1)
        result = analyze_trend(data, market_config(), evidence())
        slope = check(result, "ma200_rising_21")
        self.assertEqual(slope["status"], "fail")
        self.assertLess(slope["actual"]["consecutive_positive_days"], 21)
        short = analyze_trend(bars(220), market_config())
        self.assertEqual(check(short, "ma200_rising_21")["status"], "insufficient")

    def test_annual_checks_use_intraday_high_low_not_only_closes(self):
        data = bars()
        data[-100]["low"] = 50
        data[-200]["high"] = 704
        result = analyze_trend(data, market_config(), evidence())
        self.assertEqual(result["fifty_two_week"]["low"], 50)
        self.assertEqual(result["fifty_two_week"]["high"], 704)
        self.assertEqual(check(result, "above_52w_low")["status"], "pass")
        self.assertEqual(check(result, "near_52w_high")["status"], "fail")
        # Invalid extrema do not erase independently valid close-based checks.
        data[-100]["low"] = None
        result = analyze_trend(data, market_config(), evidence())
        self.assertEqual(check(result, "above_52w_low")["status"], "insufficient")
        self.assertEqual(check(result, "price_above_ma50")["status"], "pass")

    def test_stale_unvetted_incomplete_or_wrong_symbol_rs_stays_unknown(self):
        for mutate in [
            lambda row: row.update(date="2026-11-18"),
            lambda row: row["universe"].update(vetted=False),
            lambda row: row["universe"].update(scope="candidate_pool"),
            lambda row: row["coverage"].update(ratio=0.2),
            lambda row: row.update(percentile=None),
        ]:
            row = evidence();mutate(row)
            self.assertEqual(check(analyze_trend(bars(), market_config(), row), "market_rs")["status"], "insufficient")
        config = market_config();config["symbol"] = "OTHER"
        self.assertEqual(check(analyze_trend(bars(), config, evidence()), "market_rs")["status"], "insufficient")
        malformed = evidence();malformed["universe"] = "unverified string"
        self.assertEqual(check(analyze_trend(bars(), market_config(), malformed), "market_rs")["status"], "insufficient")

    def test_invalid_price_chronology_is_not_silently_sorted_or_filled(self):
        for data in [bars()[::-1], bars() + [bars()[-1]], [{**bars()[-1], "close": math.nan}], []]:
            result = analyze_trend(data, market_config(), evidence())
            self.assertEqual(result["status"], "insufficient")
            self.assertTrue(all(value is None for value in result["returns_pct"].values()))
            json.dumps(result, allow_nan=False)


class MarketRSTests(unittest.TestCase):
    def test_weighted_scores_rank_the_supplied_market_independently_of_trend(self):
        rows = [market_row("A", 0), market_row("B", 20), market_row("C", 30)]
        rows[0]["returns_pct"]["63d"] = 100
        snapshot = build_market_rs(AS_OF, rows, market_config())
        values = {row["symbol"]: row for row in snapshot["rows"]}
        self.assertEqual(snapshot["status"], "available")
        self.assertEqual(values["A"]["score"], 40)
        self.assertEqual(values["A"]["percentile"], 100)
        self.assertEqual(values["B"]["percentile"], 0)
        self.assertEqual(values["C"]["percentile"], 50)
        # A stock can rank highest in a weak market and still fail its own trend.
        weak = build_market_rs(AS_OF, [market_row("A", -80), market_row("B", -60), market_row("C", -40)], market_config())
        row = {**weak["rows"][-1], "universe": weak["universe"], "coverage": weak["coverage"]}
        self.assertEqual(row["percentile"], 100)
        self.assertEqual(analyze_trend(bars(declining=True), market_config(), row)["status"], "fail")

    def test_average_ties_and_order_are_reproducible(self):
        rows = [market_row("B", 10), market_row("C", 20), market_row("A", 10)]
        first = build_market_rs(AS_OF, rows, market_config())
        second = build_market_rs(AS_OF, list(reversed(rows)), market_config())
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual([row["percentile"] for row in first["rows"]], [25, 25, 100])
        all_tied = build_market_rs(AS_OF, [market_row(symbol, 10) for symbol in "ABC"], market_config())
        self.assertEqual([row["percentile"] for row in all_tied["rows"]], [50, 50, 50])

    def test_market_sample_needs_explicit_opt_in_and_retains_its_label(self):
        config = market_config(scope="market_sample")
        rows = [market_row(symbol, value) for symbol, value in zip("ABC", [10, 20, 30])]
        denied = build_market_rs(AS_OF, rows, config)
        self.assertEqual(denied["status"], "insufficient")
        self.assertTrue(all(row["percentile"] is None for row in denied["rows"]))
        self.assertTrue(all(row["score"] is not None for row in denied["rows"]))
        config["trend"]["allow_market_sample_rs"] = True
        accepted = build_market_rs(AS_OF, rows, config)
        self.assertEqual(accepted["status"], "available")
        self.assertEqual(accepted["universe"]["scope"], "market_sample")
        self.assertIs(accepted["universe"]["complete"], False)
        self.assertEqual(accepted["method"]["id"], "weighted_price_return_percentile_proxy_v1")

    def test_missing_returns_dates_and_duplicates_reduce_coverage_without_zero_fill(self):
        rows = [market_row("A", 10), market_row("B", 20), market_row("C", 30)]
        del rows[-1]["returns_pct"]["252d"]
        snapshot = build_market_rs(AS_OF, rows, market_config())
        self.assertEqual(snapshot["status"], "insufficient")
        self.assertEqual(snapshot["coverage"]["valid_count"], 2)
        self.assertAlmostEqual(snapshot["coverage"]["ratio"], 2 / 3)
        self.assertTrue(all(row["percentile"] is None for row in snapshot["rows"]))
        duplicate = build_market_rs(AS_OF, [market_row("A", 10), market_row("A", 100), market_row("B", 20), market_row("C", 30)], market_config())
        self.assertEqual([row["symbol"] for row in duplicate["rows"]], ["B", "C"])
        self.assertEqual(len(duplicate["excluded"]), 2)
        stale = build_market_rs(AS_OF, [market_row("A", 10), market_row("B", 20), market_row("C", 30, date="2026-11-18")], market_config())
        self.assertEqual(stale["coverage"]["valid_count"], 2)

    def test_vetted_same_date_noncandidate_universe_is_mandatory(self):
        rows = [market_row(symbol, 10) for symbol in "ABC"]
        for mutate in [
            lambda universe: universe.update(vetted=False),
            lambda universe: universe.update(point_in_time=False),
            lambda universe: universe.update(date="2026-11-18"),
            lambda universe: universe.update(scope="candidate_pool"),
            lambda universe: universe.update(complete=False),
            lambda universe: universe.update(candidate_filtered=True),
        ]:
            config = market_config();mutate(config["market_universe"])
            snapshot = build_market_rs(AS_OF, rows, config)
            self.assertEqual(snapshot["status"], "insufficient")
            self.assertTrue(all(row["percentile"] is None for row in snapshot["rows"]))
        default = build_market_rs(AS_OF, rows)
        self.assertEqual(default["status"], "insufficient")
        json.dumps(default, allow_nan=False)

    def test_rs_minimum_sample_count_and_invalid_weights_fail_closed(self):
        config = market_config();config["trend"]["rs_min_samples"] = 1000
        self.assertEqual(build_market_rs(AS_OF, [market_row(symbol, 10) for symbol in "ABC"], config)["status"], "insufficient")
        for weights in ({"63d": 1}, {"63d": 0.5, "126d": 0.5, "252d": 0.5}, {"63d": True, "126d": 0, "252d": 0}):
            config = market_config();config["trend"]["rs_weights"] = weights
            with self.assertRaises(ValueError):
                build_market_rs(AS_OF, [], config)


if __name__ == "__main__":
    unittest.main()
