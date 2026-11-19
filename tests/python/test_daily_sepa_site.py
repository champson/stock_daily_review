import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from review.daily import with_sepa
from review.sepa.site_report import export_report

DAY = "2026-11-18"


class CombinedDailyTests(unittest.TestCase):
    def test_sepa_delay_keeps_market_data_without_exposing_provider_errors(self):
        base = {"date": DAY, "status": "complete", "sections": {"indices": {"rows": ["existing"]}}}
        before = copy.deepcopy(base)
        loader = Mock(return_value={"status": "failed", "error": "raw-provider-secret"})
        result = with_sepa(base, "/unused", sepa_runner=loader)
        self.assertEqual(base, before)
        self.assertEqual(result["sections"], base["sections"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sepa"]["date"], DAY)
        self.assertIsNone(result["sepa"]["candidate_count"])
        self.assertNotIn("raw-provider-secret", json.dumps(result))

    def test_verified_closed_day_does_not_call_candidate_source(self):
        loader = Mock(side_effect=AssertionError("must not run"))
        base = {"date": DAY, "skipped": True}
        self.assertEqual(with_sepa(base, "/unused", sepa_runner=loader), base)
        loader.assert_not_called()

    def test_existing_success_is_reused_but_wrong_day_cannot_enter_report(self):
        base = {"date": DAY, "status": "complete"}
        loader = Mock(return_value={"status": "already_completed", "output_dir": "/archive"})
        with patch("review.daily.export_report", return_value={"date": DAY, "status": "available"}):
            self.assertEqual(with_sepa(base, "/unused", sepa_runner=loader)["sepa"]["status"], "available")
        with patch("review.daily.export_report", return_value={"date": "2026-11-17", "status": "available"}):
            self.assertEqual(with_sepa(base, "/unused", sepa_runner=loader)["sepa"]["status"], "unavailable")

    def test_site_export_normalizes_volume_and_excludes_private_source_and_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / DAY
            root.mkdir()
            quote = {"volume_unit": "lots", "bars": [{"date": DAY, "open": 10, "close": 11,
                      "high": 12, "low": 9, "volume": 123}]}
            raw = json.dumps(quote).encode()
            (root / "quote.json").write_bytes(raw)
            run = {"date": DAY, "status": "completed", "finished_at": DAY + "T18:00:00+08:00",
                   "candidate_source": {"data_date": DAY, "fetched_at": DAY + "T17:30:00+08:00", "raw": "private"}}
            (root / "run.json").write_text(json.dumps(run))
            report = {"date": DAY, "status": "available", "market_rs": {"date": DAY, "status": "available"},
                      "groups": {key: ["sz002100"] if key == "C" else [] for key in ("A","B","C","D","insufficient","unavailable")},
                      "rows": [{"symbol": "sz002100", "candidate": {"name": "fixture", "private": "no-export"},
                                "category": "C", "close": 11, "trend": {}, "vcp": {"state": "building", "events": ["private-replay"]},
                                "quote": {"source": "tencent", "adjustment": "qfq", "volume_unit": "shares",
                                          "raw_capture_path": "quote.json", "raw_capture_sha256": hashlib.sha256(raw).hexdigest()}}]}
            with patch("review.sepa.site_report.SepaService") as service:
                service.return_value.report.return_value = report
                result = export_report(root)
                self.assertEqual(result["rows"][0]["bars"][0][5], 12300)
                self.assertEqual(result["rows"][0]["bars"][0][6:], [None, None, None])
                for value in ("private", "raw_capture", "quote.json", "events", temporary):
                    self.assertNotIn(value, json.dumps(result))
                (root / "quote.json").write_text("{}")
                with self.assertRaisesRegex(ValueError, "哈希"):
                    export_report(root)


if __name__ == "__main__":
    unittest.main()
