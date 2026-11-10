"""Index volume stays on the price history's exact dates and source units."""
import unittest
from unittest.mock import patch

import pandas as pd

from review.a_stock_data import AStockData
from review.analysis import analyze, historical_metrics

DAY = "2026-11-09"


def history_row(day, close, **fields):
    return {"date": day, "close": close, "source": "tencent",
            "source_url": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "fetched_at": "2026-11-10T08:00:00+00:00", **fields}


class IndexVolumeTests(unittest.TestCase):
    def adapter(self):
        return AStockData({"indices": [{"symbol": "sh000001", "name": "上证指数"}]})

    def collect(self, history):
        adapter = self.adapter()
        with patch.object(adapter, "package", return_value=[]), \
                patch("review.a_stock_data.api.tencent_kline", return_value=pd.DataFrame(history)):
            return adapter.indices(DAY)

    def test_preserves_volume_on_same_dated_prices_without_scaling(self):
        rows = [history_row("2026-11-06", 100, volume=427722257),
                history_row(DAY, 110, volume=123.5)]
        data = self.collect(rows)
        quote = data["rows"][0]
        self.assertEqual(quote["history"], [
            {"date": "2026-11-06", "close": 100, "volume": 427722257},
            {"date": DAY, "close": 110, "volume": 123.5},
        ])
        self.assertEqual(quote["volume_unit"], "手")
        self.assertEqual(data["provenance"][0]["provider"], "tencent")
        analyzed = analyze("indices", data["rows"])
        self.assertEqual(analyzed["rows"][0]["history"], quote["history"])
        self.assertEqual(analyzed["rows"][0]["trend"]["days"], 2)

    def test_zero_is_valid_and_invalid_volume_does_not_erase_prices(self):
        for value in (None, True, False, -1, float("nan"), float("inf")):
            with self.subTest(volume=value):
                data = self.collect([history_row("2026-11-06", 100, volume=value),
                                     history_row(DAY, 110, volume=0)])
                history = data["rows"][0]["history"]
                self.assertEqual(len(history), 2)
                self.assertIsNone(history[0]["volume"])
                self.assertEqual(history[0]["close"], 100)
                self.assertEqual(history[1]["volume"], 0)
                self.assertEqual(data["rows"][0]["close"], 110)

    def test_missing_volume_remains_null_and_legacy_price_history_still_works(self):
        data = self.collect([history_row("2026-11-06", 100), history_row(DAY, 110)])
        self.assertEqual([row["volume"] for row in data["rows"][0]["history"]], [None, None])
        legacy = {"symbol": "sh000001", "date": DAY, "close": 110,
                  "history": [{"date": "2026-11-06", "close": 100}, {"date": DAY, "close": 110}]}
        self.assertEqual(historical_metrics(legacy)["days"], 2)


if __name__ == "__main__":
    unittest.main()
