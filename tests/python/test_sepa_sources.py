import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from review.sepa.sources import load_bars, load_candidates, normalize_symbol

DAY = "2026-11-18"


def payload(symbol="sz002100", unit="lots"):
    return {"symbol": symbol, "date": DAY, "adjustment": "qfq", "volume_unit": unit,
            "source": "tencent", "source_url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "fetched_at": "2026-11-19T04:56:22.776837+00:00", "bars": [
                {"date": "2026-11-17", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 12},
                {"date": DAY, "open": 11, "high": 13, "low": 10, "close": 12, "volume": None}]}


class CandidateSourcesTests(unittest.TestCase):
    def test_equity_symbol_normalization_keeps_leading_zeros_and_market(self):
        for value, expected in [("002100", "sz002100"), (" SH600519 ", "sh600519"),
                                ("688981.XSHG", "sh688981"), ("000001.SZ", "sz000001"),
                                ("300750.XSHE", "sz300750"), ("920001", "bj920001")]:
            self.assertEqual(normalize_symbol(value), expected)
        for value in (2100, "2100", "sh000001", "sz600519", "000300.SH", "sh932000", "510300", "600519;rm"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_symbol(value)

    def test_manual_candidates_are_sorted_deduplicated_without_fake_signal_date(self):
        result = load_candidates(["600519", "sz002100", "600519.SH"], as_of=DAY)
        self.assertEqual(result, [{"symbol": "sh600519", "source": "manual"},
                                  {"symbol": "sz002100", "source": "manual"}])
        with self.assertRaises(ValueError):
            load_candidates(["600519"])

    def test_bom_csv_keeps_claimed_dates_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.csv"
            path.write_text("代码,名称,来源,signal_date\n002100,天康生物,manual_screen,2026-11-17\n002100,duplicate,,\n600519,贵州茅台,,\n", encoding="utf-8-sig")
            result = load_candidates(["002100", "300750"], path, as_of=DAY)
        self.assertEqual([r["symbol"] for r in result], ["sh600519", "sz002100", "sz300750"])
        self.assertNotIn("signal_date", result[0])
        self.assertEqual(result[1]["signal_date"], "2026-11-17")
        self.assertEqual(result[1]["name"], "天康生物")
        self.assertEqual(result[1]["source"], "manual_screen")

    def test_csv_rejects_future_malformed_or_conflicting_dates_and_codes(self):
        values = ["code,signal_date\n002100,2026-11-19\n", "code,signal_date\n002100,11/18/2026\n",
                  "code,symbol\n002100,600519\n", "code,code\n002100,600519\n",
                  "code\n002100,extra\n", "name\n天康生物\n"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.csv"
            for content in values:
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_candidates(csv_path=path, as_of=DAY)


class BarsSourcesTests(unittest.TestCase):
    def offline(self, value, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sz002100.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8-sig")
            return load_bars("002100", DAY, price_dir=directory, **kwargs)

    def test_offline_prices_preserve_null_volume_and_short_history_without_padding(self):
        result = self.offline(payload())
        self.assertEqual(result["bars"][0]["volume"], 1200)
        self.assertIsNone(result["bars"][1]["volume"])
        self.assertEqual(len(result["bars"]), 2)
        self.assertEqual(result["volume_unit"], "shares")
        self.assertFalse(result["history_sufficient"])
        self.assertEqual(result["fetched_at"], "2026-11-19T04:56:22.776837+00:00")
        self.assertEqual(result["source_url"], payload()["source_url"])
        self.assertTrue(any("null" in note for note in result["observations"]))

    def test_offline_capture_retains_original_bytes_and_units(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            raw = ("\ufeff" + json.dumps(payload())).encode()
            (target / "sz002100.json").write_bytes(raw)
            result = load_bars("002100", DAY, price_dir=target, capture_dir=target / "raw", min_bars=2)
            self.assertEqual(Path(result["raw_capture_path"]).read_bytes(), raw)
            self.assertEqual(result["raw_capture_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertTrue(result["history_sufficient"])
            self.assertEqual(result["provenance"]["raw_volume_unit"], "lots")

    def test_rejects_ambiguous_basis_stale_future_duplicate_and_invalid_prices(self):
        mutations = [lambda p: p.update(adjustment="none"), lambda p: p.update(volume_unit="unknown"),
                     lambda p: p.update(date="2026-11-17"), lambda p: p.update(symbol="600519"),
                     lambda p: p.update(source=""), lambda p: p.update(fetched_at="2026-11-19"),
                     lambda p: p["bars"][-1].update(date="2026-11-19"),
                     lambda p: p["bars"][-1].update(date="2026-11-17"),
                     lambda p: p["bars"][-1].update(close=0),
                     lambda p: p["bars"][-1].update(high=11),
                     lambda p: p["bars"][-1].update(volume=-1),
                     lambda p: p["bars"][-1].update(volume=True)]
        for mutation in mutations:
            value = payload(); mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.offline(value)
        value = payload(); value["bars"].pop()
        with self.assertRaisesRegex(ValueError, "过期"):
            self.offline(value)

    def test_live_adapter_requests_explicit_qfq_interval_and_converts_actual_units(self):
        import pandas as pd
        for symbol, expected in (("sz002100", 1200), ("sh688981", 12)):
            value = payload(symbol)
            frame = pd.DataFrame([{**r, "code": symbol, "adjust": "qfq", "source": value["source"],
                                   "source_url": value["source_url"], "fetched_at": value["fetched_at"]}
                                  for r in value["bars"]])
            with patch("review.vendor.astock.tencent_kline", return_value=frame) as provider:
                result = load_bars(symbol, DAY)
            self.assertEqual(result["bars"][0]["volume"], expected)
            self.assertIsNone(result["bars"][1]["volume"])
            self.assertEqual(provider.call_args.kwargs["adjust"], "qfq")
            self.assertEqual(provider.call_args.kwargs["end"], DAY)
            self.assertLess(provider.call_args.kwargs["start"], "2025-06-01")
            self.assertFalse(result["history_sufficient"])

    def test_live_rejects_mixed_adjustment_and_source_symbol(self):
        import pandas as pd
        value = payload()
        for field, wrong in (("adjust", "none"), ("code", "sz000001")):
            rows = [{**r, "code": "sz002100", "adjust": "qfq", "source": value["source"],
                     "source_url": value["source_url"], "fetched_at": value["fetched_at"]}
                    for r in value["bars"]]
            rows[0][field] = wrong
            with patch("review.vendor.astock.tencent_kline", return_value=pd.DataFrame(rows)), \
                    self.assertRaisesRegex(ValueError, "标记不匹配"):
                load_bars("sz002100", DAY)

    def test_live_rejects_unfinished_today_and_future_before_network_fetch(self):
        from zoneinfo import ZoneInfo
        with patch("review.sepa.sources._now_shanghai", return_value=datetime(2026, 11, 19, 14, 50, tzinfo=ZoneInfo("Asia/Shanghai"))), \
                patch("review.vendor.astock.tencent_kline") as provider:
            with self.assertRaisesRegex(ValueError, "15:15"):
                load_bars("002100", "2026-11-19")
            with self.assertRaisesRegex(ValueError, "未来"):
                load_bars("002100", "2026-11-20")
        provider.assert_not_called()

    def test_declared_adjustment_date_is_retained_but_not_certified(self):
        value = payload()
        value["adjust_as_of"] = "2026-11-19"
        result = self.offline(value)
        self.assertEqual(result["provenance"]["declared_adjust_as_of"], "2026-11-19")
        self.assertFalse(result["provenance"]["point_in_time_certified"])
        value["as_of"] = "2026-11-17"
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.offline(value)


if __name__ == "__main__":
    unittest.main()
