import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review.a_stock_data import AStockData


ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "config/calendars/2025-12.json"


class CalendarArchiveTests(unittest.TestCase):
    def setUp(self):
        self.archive = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "2025-12.json"

    def adapter(self, enabled=True, configured=True):
        settings = {"tdx_official_calendar_fallback": enabled}
        if configured:
            settings["calendar_archive_directory"] = self.directory.name
        return AStockData(settings)

    def write(self, value=None):
        self.path.write_text(json.dumps(self.archive if value is None else value,
                                        ensure_ascii=False), encoding="utf-8")

    def unavailable(self):
        return patch("review.a_stock_data.api.trading_calendar", side_effect=RuntimeError("SZSE unavailable"))

    def test_both_live_sources_fail_then_use_complete_archive_and_original_timestamp(self):
        self.write()
        adapter = self.adapter()
        with self.unavailable() as online, \
                patch("review.a_stock_data.urllib.request.urlopen", side_effect=OSError("TDX unavailable")) as fallback:
            month = adapter.calendar_month(2025, 12)
            cached = adapter.calendar_month(2025, 12)
        self.assertIs(month, cached)
        self.assertEqual(online.call_count, 1)
        self.assertEqual(fallback.call_count, 1)
        self.assertEqual(len(month["rows"]), 31)
        self.assertEqual(month["fetched_at"], self.archive["fetched_at"])
        self.assertEqual(month["source_url"], self.archive["source_url"])
        self.assertIn("归档", month["warning"])
        self.assertEqual(month["rows"][-1], {"date": "2025-12-31", "is_open": True})

    def test_online_szse_and_tdx_precede_archive(self):
        adapter = self.adapter()
        live = [{"date": r["date"], "is_open": r["is_open"]} for r in self.archive["rows"]]
        with patch("review.a_stock_data.api.trading_calendar", return_value=object()), \
                patch("review.a_stock_data.records", return_value=live), \
                patch("review.a_stock_data.urllib.request.urlopen") as tdx, \
                patch.object(Path, "open", side_effect=AssertionError("archive should not be read")):
            month = adapter.calendar_month(2025, 12)
        self.assertEqual(month["source"], "a-stock-data / 深交所")
        tdx.assert_not_called()

        adapter = self.adapter()
        adapter.cache[("official_holidays_html",)] = "official cached content"
        with self.unavailable(), \
                patch("review.a_stock_data.official_holiday_calendar", return_value=live), \
                patch.object(Path, "open", side_effect=AssertionError("archive should not be read")):
            month = adapter.calendar_month(2025, 12)
        self.assertEqual(month["source"], "通达信官方休市表（备选）")

    def test_not_configured_or_fallback_disabled_does_not_enable_archive(self):
        self.write()
        for adapter in (self.adapter(configured=False), self.adapter(enabled=False)):
            with self.subTest(settings=adapter.settings), self.unavailable(), \
                    patch("review.a_stock_data.urllib.request.urlopen", side_effect=OSError("TDX unavailable")), \
                    patch.object(Path, "open", side_effect=AssertionError("archive not authorized by settings")):
                with self.assertRaisesRegex(ValueError, "官方月历不可用"):
                    adapter.calendar_month(2025, 12)

    def test_archive_metadata_and_each_day_are_strictly_validated(self):
        mutations = [
            lambda a: a.update(version=True),
            lambda a: a.update(calendar_month="2025-11"),
            lambda a: a.update(source="unverified"),
            lambda a: a.update(source_url="https://example.com/calendar"),
            lambda a: a.update(fetched_at="2026-11-10T08:12:08"),
            lambda a: a.update(extra="ignored?"),
            lambda a: a["rows"].pop(),
            lambda a: a["rows"].__setitem__(0, copy.deepcopy(a["rows"][1])),
            lambda a: a["rows"][0].update(date="2025-11-30"),
            lambda a: a["rows"][0].update(is_open=1),
            lambda a: a["rows"][0].update(unverified="extra"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                archive = copy.deepcopy(self.archive)
                mutate(archive)
                self.write(archive)
                with self.unavailable(), \
                        patch("review.a_stock_data.urllib.request.urlopen", side_effect=OSError("TDX unavailable")):
                    with self.assertRaises(ValueError):
                        self.adapter().calendar_month(2025, 12)

    def test_duplicate_json_fields_and_oversized_archive_are_rejected(self):
        cases = [json.dumps(self.archive).replace('"version": 1', '"version": 1, "version": 1'),
                 " " * 131_073]
        for payload in cases:
            with self.subTest(size=len(payload)):
                self.path.write_text(payload)
                with self.unavailable(), \
                        patch("review.a_stock_data.urllib.request.urlopen", side_effect=OSError("TDX unavailable")):
                    with self.assertRaises(ValueError):
                        self.adapter().calendar_month(2025, 12)

    def test_archive_failure_is_cached_and_wrong_month_is_not_substituted(self):
        self.write()
        adapter = self.adapter()
        with self.unavailable() as online, \
                patch("review.a_stock_data.urllib.request.urlopen", side_effect=OSError("TDX unavailable")) as fallback:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, "2025-11 官方月历不可用"):
                    adapter.calendar_month(2025, 11)
        self.assertEqual(online.call_count, 1)
        self.assertEqual(fallback.call_count, 1)

    def test_calendar_basis_uses_archived_flags_without_weekday_guessing(self):
        archive = copy.deepcopy(self.archive)
        archive["rows"][-1]["is_open"] = False
        self.write(archive)
        adapter = self.adapter()
        # Request 1 January so all three cutoffs search this exact archived
        # December. The final Wednesday is explicitly closed in the fixture.
        with self.unavailable(), \
                patch("review.a_stock_data.urllib.request.urlopen", side_effect=OSError("TDX unavailable")):
            basis = adapter.calendar_basis("2026-01-01")
        self.assertEqual(basis["anchors"]["year"], "2025-12-30")
        self.assertEqual(basis["anchors"]["month"], "2025-12-30")
        december_source = next(p for p in basis["provenance"] if p["calendar_month"] == "2025-12")
        self.assertEqual(december_source["fetched_at"], archive["fetched_at"])
        self.assertTrue(any("归档" in observation for observation in basis["observations"]))


if __name__ == "__main__":
    unittest.main()
