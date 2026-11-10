import unittest
from unittest.mock import Mock
from review.sources import build_report
from review.analysis import analyze

DAY = "2026-10-13"


class SourcePriorityTests(unittest.TestCase):
    def primary(self):
        p = Mock()
        p.calendar.return_value = {"date": DAY, "open": True, "source": "calendar-fixture"}
        p.collect.return_value = {"rows": [{"symbol": "fixture", "date": DAY, "close": 1, "option_type": "call"}]}
        return p

    def test_healthy_primary_never_initializes_backup(self):
        backup = Mock()
        report = build_report({}, DAY, primary=self.primary(), fallback_factory=backup)
        self.assertEqual(report["source"], "a-stock-data")
        backup.assert_not_called()

    def test_calendar_failure_stops_without_guessing_weekday(self):
        p = self.primary(); p.calendar.side_effect = RuntimeError("calendar down")
        with self.assertRaisesRegex(ValueError, "交易日历均不可用"):
            build_report({}, DAY, primary=p)
        p.collect.assert_not_called()

    def test_holiday_skips_both_sources(self):
        p = self.primary(); p.calendar.return_value["open"] = False
        backup = Mock()
        self.assertTrue(build_report({}, DAY, primary=p, fallback_factory=backup)["skipped"])
        p.collect.assert_not_called(); backup.assert_not_called()

    def test_failed_module_uses_mapped_backup_without_replacing_other_modules(self):
        p = self.primary()
        def collect(kind, day):
            if kind == "stocks": raise RuntimeError("primary down")
            return {"rows": [{"symbol": "fixture", "date": DAY, "close": 1, "option_type": "call"}]}
        p.collect.side_effect = collect
        backup = Mock(); backup.call_tool.return_value = [{"symbol": "backup", "date": DAY, "close": 2}]
        config = {"datasets": {"stocks": {"tool": "verified-tool", "fields": {"symbol": "/symbol", "date": "/date", "close": "/close"}}}}
        report = build_report(config, DAY, primary=p, fallback_factory=lambda: backup)
        self.assertEqual(report["sections"]["stocks"]["source"], "通达信 MCP")
        self.assertEqual(report["sections"]["indices"]["source"], "a-stock-data")
        backup.initialize.assert_called_once()

    def test_options_without_confirmed_expiry_never_get_pcr(self):
        rows = [{"symbol": "fixture"+side, "close": 1, "underlying": "U", "option_type": side, "volume": 100} for side in ("call", "put")]
        metrics = analyze("options", rows)["metrics"]
        self.assertEqual(metrics["groups"], [])
        self.assertEqual(metrics["unassigned_contracts"], 2)


if __name__ == "__main__":
    unittest.main()
