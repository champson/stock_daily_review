"""Fallbacks must preserve publisher identity and reject cross-source prices."""
import unittest
from unittest.mock import patch

from review.a_stock_data import AStockData

DAY = "2026-11-10"


def history():
    return {"name": "集思录转债等权", "price_basis": "集思录转债等权指数点位", "volume_unit": "source",
            "rows": [{"date": stamp, "close": close, "volume": None, "source": "jisilu",
                      "source_url": "https://www.jisilu.cn/webapi/cb/index_history/",
                      "fetched_at": DAY + "T18:00:00+08:00"}
                     for stamp, close in [("2025-12-31", 100), ("2026-11-09", 110), (DAY, 120)]]}


class SpecialSourceTests(unittest.TestCase):
    def test_named_index_preserves_exact_calendar_previous_day(self):
        adapter = AStockData({"special_indices": ["jisilu_convertible_equal_weight"]})
        with patch("review.a_stock_data.fetch_jsl_equal_weight", return_value=history()):
            result = adapter.special_indices(DAY, {"bias25_dates": ["2026-11-09", DAY]})
            gap = adapter.special_indices(DAY, {"bias25_dates": ["2026-11-06", DAY]})
        row = result["rows"][0]
        self.assertEqual(row["name"], "集思录转债等权")
        self.assertEqual(row["previous_close"], 110)
        self.assertIsNone(gap["rows"][0]["previous_close"])
        self.assertEqual(result["provenance"][0]["source"], "jisilu")

    def test_configured_failure_is_explicit_and_unconfigured_never_fetches(self):
        with patch("review.a_stock_data.fetch_jsl_equal_weight", side_effect=RuntimeError("source failed")) as call:
            self.assertEqual(AStockData({}).special_indices(DAY, {})["rows"], [])
            call.assert_not_called()
            result = AStockData({"special_indices": ["jisilu_convertible_equal_weight"]}).special_indices(DAY, {})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["errors"][0]["category"], "source_failed")

    def test_index_fallback_preserves_unit_and_never_masks_price_conflict(self):
        adapter = AStockData({"indices": [{"symbol": "sh932000", "name": "中证2000"}]})
        data = history()
        for row in data["rows"]:
            row["source"] = "eastmoney"
        with patch.object(adapter, "package", return_value=[]), \
                patch.object(adapter, "calendar", side_effect=lambda stamp: {"date": stamp, "open": stamp in (DAY, "2026-11-09")}), \
                patch("review.a_stock_data.api.tencent_kline", side_effect=ValueError("unsupported")), \
                patch("review.a_stock_data.fetch_index_history", return_value=data):
            result = adapter.indices(DAY)
        self.assertEqual(result["rows"][0]["volume_unit"], "source")
        self.assertEqual(result["rows"][0]["provider"], "eastmoney")
        self.assertEqual(result["rows"][0]["previous_close"], 110)
        quote = {"market": "sh", "code": "932000", "date": DAY, "close": 999}
        with patch.object(adapter, "package", return_value=[quote]), \
                patch("review.a_stock_data.api.tencent_kline", side_effect=ValueError("unsupported")), \
                patch("review.a_stock_data.fetch_index_history", return_value=data):
            conflict = adapter.indices(DAY)
        self.assertEqual(conflict["rows"], [])
        self.assertIn("不一致", conflict["observations"][0])

    def test_missing_official_previous_day_is_not_a_daily_return(self):
        adapter = AStockData({"indices": [{"symbol": "sh932000", "name": "中证2000"}]})
        data = history()
        data["rows"] = [data["rows"][0], data["rows"][-1]]
        with patch.object(adapter, "package", return_value=[]), \
                patch.object(adapter, "calendar", side_effect=lambda stamp: {"date": stamp, "open": stamp in (DAY, "2026-11-09")}), \
                patch("review.a_stock_data.api.tencent_kline", side_effect=ValueError("unsupported")), \
                patch("review.a_stock_data.fetch_index_history", return_value=data):
            result = adapter.indices(DAY)
        self.assertIsNone(result["rows"][0]["previous_close"])
        self.assertEqual(len(result["rows"][0]["history"]), 2)
        self.assertTrue(result["partial"])
        self.assertTrue(any("前交易日 2026-11-09" in issue for issue in result["observations"]))
