"""The CLI retains inputs and keeps data gaps out of the qualified shortlist."""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from review.sepa.__main__ import ROOT, main
from review.sepa.report import classify


DAY = "2025-11-18"


def price_fixture(path):
    dates = []
    day = date.fromisoformat(DAY)
    while len(dates) < 320:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day -= timedelta(days=1)
    bars = []
    for index, stamp in enumerate(reversed(dates)):
        close = 10 + index * 0.025
        bars.append({"date": stamp, "open": close, "low": close - 0.03,
                     "high": close + 0.03, "close": close, "volume": 10000})
    path.write_text(json.dumps({"symbol": "sz002100", "date": DAY, "adjustment": "qfq",
                                "volume_unit": "shares", "source": "synthetic test prices", "bars": bars}))


class SepaCliTests(unittest.TestCase):
    def test_confirmed_breakout_with_expanding_volume_does_not_join_high_quality_group(self):
        trend = {"status": "pass"}
        vcp = {"state": "retest", "metrics": {"price_qualified": True, "volume_shrinking": False,
                                              "volume_data_complete": True}}
        self.assertEqual(classify(trend, vcp), "C")
        vcp["metrics"]["volume_shrinking"] = True
        self.assertEqual(classify(trend, vcp), "B")

    def test_archives_inputs_and_does_not_promote_symbol_without_market_rs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            price_fixture(root / "sz002100.json")
            input_csv = root / "pool.csv"
            input_csv.write_text("code,name\n002100,=formula\n600519,missing fixture\n", encoding="utf-8")
            output = root / "result"
            args = ["run", "--date", DAY, "--csv", str(input_csv), "--price-dir", str(root),
                    "--output-dir", str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["groups"]["A"], [])
            self.assertEqual(report["groups"]["B"], [])
            self.assertEqual(report["groups"]["insufficient"], ["sz002100"])
            self.assertEqual(report["groups"]["unavailable"], ["sh600519"])
            item = report["rows"][0]
            self.assertEqual(item["trend"]["checks"][-1]["status"], "insufficient")
            self.assertEqual((output / report["inputs"]["csv"]).read_bytes(), input_csv.read_bytes())
            self.assertEqual((output / item["quote"]["raw_capture_path"]).read_bytes(),
                             (root / "sz002100.json").read_bytes())
            with (output / "ranking.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["name"], "'=formula")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 1)
            self.assertEqual(json.loads((output / "report.json").read_text()), report)

    def test_wrong_date_market_snapshot_aborts_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            market = root / "market.json"
            market.write_text(json.dumps({"date": "2025-11-17", "rows": []}))
            output = root / "result"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["run", "--date", DAY, "--symbols", "002100", "--price-dir", str(root),
                             "--market-returns", str(market), "--output-dir", str(output)])
            self.assertEqual(code, 1)
            self.assertIn("同报告日", stderr.getvalue())
            self.assertFalse(output.exists())

    def test_independent_dated_market_rows_supply_the_eighth_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            price_fixture(root / "sz002100.json")
            config = json.loads((ROOT / "config" / "sepa.json").read_text())
            # Small artificial universe to exercise the wiring without pretending
            # to clear the production sample-size threshold.
            config["trend"]["rs_min_samples"] = 2
            config_path = root / "sepa.json"
            config_path.write_text(json.dumps(config))
            market = root / "market.json"
            market.write_text(json.dumps({"date": DAY, "market_universe": {
                "date": DAY, "id": "test", "label": "synthetic market fixture", "scope": "market_sample",
                "vetted": True, "point_in_time": True, "complete": False, "eligible_count": 2},
                "rows": [{"symbol": symbol, "date": DAY, "returns_pct": dict.fromkeys(("63d", "126d", "252d"), score)}
                         for symbol, score in (("sz002100", 20), ("sh600519", 5))]}))
            output = root / "result"
            with redirect_stdout(io.StringIO()):
                code = main(["run", "--date", DAY, "--symbols", "002100", "--price-dir", str(root),
                             "--market-returns", str(market), "--config", str(config_path), "--output-dir", str(output)])
            self.assertEqual(code, 0)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["market_rs"]["coverage"]["valid_count"], 2)
            self.assertEqual(report["market_rs"]["universe"]["scope"], "market_sample")
            check = report["rows"][0]["trend"]["checks"][-1]
            self.assertEqual(check["status"], "pass")
            self.assertEqual(check["actual"]["percentile"], 100)
            self.assertEqual((output / report["inputs"]["market_returns"]).read_bytes(), market.read_bytes())
