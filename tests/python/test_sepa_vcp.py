"""Synthetic causal evidence; no market data or network requests."""
from copy import deepcopy
from datetime import date, timedelta
import json
import unittest

from review.sepa.vcp import analyze_vcp


CFG = {"swing_window": 1, "min_base_bars": 10}


def bars(prices, volumes=None):
    values = volumes if volumes is not None else [1000 if i < 10 else 400 for i in range(len(prices))]
    return [{"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
             "open": p, "high": p + .2, "low": p - .2, "close": p, "volume": values[i]}
            for i, p in enumerate(prices)]


def setup():
    # H1=100.2/L1=79.8 (20.36%); H2=98.2/L2=88.8 (9.57%).
    # L2 is observable only at index18, after its right-hand bar has closed.
    return bars([90, 92, 94, 96, 98, 100, 95, 90, 85, 80,
                 85, 90, 94, 98, 96, 94, 92, 89, 91, 92, 93, 94])


def append(data, price, volume=400, low=None):
    row = {"date": (date.fromisoformat(data[-1]["date"]) + timedelta(days=1)).isoformat(),
           "open": price, "high": price + .2, "low": price - .2 if low is None else low,
           "close": price, "volume": volume}
    return [*data, row]


class VcpTests(unittest.TestCase):
    def test_confirmed_two_contractions_report_geometry_and_separate_scores(self):
        source = setup(); original = deepcopy(source)
        result = analyze_vcp(source, CFG)
        self.assertEqual(result["state"], "ready")
        self.assertAlmostEqual(result["pivot"], 98.2)
        self.assertAlmostEqual(result["risk_level"], 88.8)
        self.assertEqual(len(result["swings"]), 2)
        self.assertEqual(result["swings"][1]["low_date"], source[17]["date"])
        self.assertEqual(result["swings"][1]["low_confirmed_at"], source[18]["date"])
        self.assertEqual(result["setup_detected_at"], source[18]["date"])
        self.assertGreater(result["metrics"]["price_contraction_score"], 0)
        self.assertGreater(result["metrics"]["volume_contraction_score"], 0)
        self.assertIn("atr_contraction_score", result["metrics"])
        self.assertIsNotNone(result["metrics"]["atr14"])
        self.assertEqual(source, original)
        json.dumps(result, allow_nan=False)

    def test_unconfirmed_right_window_cannot_publish_the_second_contraction(self):
        source = setup()
        before = analyze_vcp(source[:18], CFG)
        after = analyze_vcp(source[:19], CFG)
        self.assertIsNone(before["pivot"])
        self.assertNotIn(before["state"], ("ready", "breakout"))
        self.assertEqual(after["state"], "ready")
        self.assertTrue(all(e["observed_at"] <= source[17]["date"] for e in before["events"]))

    def test_close_volume_breakout_uses_twenty_previous_bars_not_current(self):
        source = append(setup(), 100, 3000)
        result = analyze_vcp(source, CFG)
        self.assertEqual(result["state"], "breakout")
        baseline = sum(row["volume"] for row in source[-21:-1]) / 20
        self.assertAlmostEqual(result["breakout"]["volume_ratio"], 3000 / baseline)
        self.assertGreater(result["breakout"]["date"], result["pivot_known_at"])
        self.assertTrue(result["breakout"]["volume_confirmed"])

    def test_high_piercing_is_not_close_breakout(self):
        source = append(setup(), 97, 3000)
        source[-1]["high"] = 100
        result = analyze_vcp(source, CFG)
        self.assertEqual(result["state"], "intraday_breakout")
        self.assertIsNone(result["breakout"])
        self.assertFalse(any(e["type"] == "breakout_confirmed" for e in result["events"]))

    def test_missing_or_insufficient_volume_never_becomes_confirmed_breakout(self):
        for volume in (None, 100):
            with self.subTest(volume=volume):
                result = analyze_vcp(append(setup(), 100, volume), CFG)
                self.assertEqual(result["state"], "unconfirmed_breakout")
                self.assertFalse(result["breakout"]["volume_confirmed"])
        source = setup(); source[7]["volume"] = None
        result = analyze_vcp(append(source, 100, 3000), CFG)
        self.assertEqual(result["state"], "unconfirmed_breakout")
        self.assertIsNone(result["metrics"]["volume_contraction_score"])
        self.assertIsNone(result["score"])

    def test_volume_failure_is_not_hidden_by_price_contraction(self):
        source = setup()
        for i, row in enumerate(source):
            row["volume"] = 100 if i < 10 else 1000
        result = analyze_vcp(source, CFG)
        self.assertTrue(result["metrics"]["price_qualified"])
        self.assertFalse(result["metrics"]["volume_shrinking"])
        self.assertEqual(result["metrics"]["volume_contraction_score"], 0)
        self.assertTrue(any("成交量没有连续收缩" in reason for reason in result["reasons"]))

    def test_pivot_frozen_retest_then_failure_without_rewriting_breakout(self):
        breakout = append(setup(), 100, 3000)
        first = analyze_vcp(breakout, CFG)
        retest = append(breakout, 98.3, 100)
        tested = analyze_vcp(retest, CFG)
        failed = analyze_vcp(append(retest, 94, 900), CFG)
        self.assertEqual(tested["state"], "retest")
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(first["pivot"], tested["pivot"])
        self.assertEqual(first["pivot"], failed["pivot"])
        self.assertEqual(first["breakout"], failed["breakout"])
        self.assertEqual(first["events"], [e for e in failed["events"] if e["observed_at"] <= breakout[-1]["date"]])

    def test_same_bar_breakout_and_structure_breach_is_not_resolved_in_our_favor(self):
        result = analyze_vcp(append(setup(), 100, 3000, low=85), CFG)
        self.assertEqual(result["state"], "unconfirmed_breakout")
        self.assertTrue(result["breakout"]["intrabar_sequence_ambiguous"])
        self.assertFalse(result["breakout"]["volume_confirmed"])

    def test_all_closed_prefix_events_are_identical_after_future_bars_arrive(self):
        source = append(append(append(setup(), 100, 3000), 98.3, 100), 94, 900)
        complete = analyze_vcp(source, CFG)
        for count in range(1, len(source) + 1):
            prefix = analyze_vcp(source[:count], CFG)
            emitted = [e for e in complete["events"] if e["observed_at"] <= source[count - 1]["date"]]
            self.assertEqual(prefix["events"], emitted, count)

    def test_old_setup_expires_by_bar_count_and_is_not_rearmed_unchanged(self):
        result = analyze_vcp(setup(), {**CFG, "max_signal_age_bars": 2})
        self.assertEqual(result["state"], "expired")
        self.assertEqual(sum(e["type"] == "setup_detected" for e in result["events"]), 1)
        self.assertTrue(any("过期" in note or "寿命" in note for note in result["reasons"]))

    def test_detection_bar_above_pivot_cannot_backdate_a_breakout(self):
        source = setup()[:19];source[-1].update(open=100, high=100.2, low=99.8, close=100, volume=3000)
        late = analyze_vcp(source, CFG)
        self.assertEqual(late["state"], "forming")
        self.assertIsNone(late["breakout"])
        self.assertTrue(any("不能追认" in note for note in late["reasons"]))
        reset = append(source, 96)
        self.assertEqual(analyze_vcp(reset, CFG)["state"], "ready")
        triggered = analyze_vcp(append(reset, 100, 3000), CFG)
        self.assertEqual(triggered["state"], "breakout")
        self.assertGreater(triggered["breakout"]["date"], late["pivot_known_at"])

    def test_no_geometry_does_not_invent_a_missing_volume_reason(self):
        result = analyze_vcp(bars([100] * 30), CFG)
        self.assertFalse(any("成交量缺失" in note for note in result["reasons"]))

    def test_current_high_and_close_are_independent_of_breakout_confirmation(self):
        source = append(setup(), 97, 3000);source[-1]["high"] = 100
        result = analyze_vcp(source, CFG)
        self.assertTrue(result["metrics"]["high_above_pivot"])
        self.assertFalse(result["metrics"]["close_above_pivot"])
        self.assertEqual(result["metrics"]["current_high"], 100)
        self.assertEqual(result["metrics"]["current_close"], 97)

    def test_widening_swings_do_not_qualify_and_flat_bars_do_not_invent_extrema(self):
        negative = bars([80, 82, 84, 86, 88, 90, 88, 86, 84, 82,
                         86, 90, 94, 98, 93, 88, 83, 78, 80, 82])
        result = analyze_vcp(negative, CFG)
        self.assertIsNone(result["pivot"])
        self.assertFalse(result["metrics"]["price_qualified"])
        flat = analyze_vcp(bars([100] * 30), CFG)
        self.assertEqual(flat["state"], "incomplete")
        self.assertEqual(flat["swings"], [])

    def test_validation_rejects_future_duplicate_and_invalid_inputs(self):
        with self.assertRaises(ValueError):
            analyze_vcp(setup(), {**CFG, "as_of": "2026-01-10"})
        duplicate = setup(); duplicate[1]["date"] = duplicate[0]["date"]
        with self.assertRaises(ValueError):
            analyze_vcp(duplicate, CFG)
        with self.assertRaises(ValueError):
            analyze_vcp(setup(), {**CFG, "max_contractions": 6})
        with self.assertRaises(ValueError):
            analyze_vcp(setup(), {**CFG, "score_weights": {"price": -1}})
        self.assertEqual(analyze_vcp([], CFG)["state"], "incomplete")


if __name__ == "__main__":
    unittest.main()
