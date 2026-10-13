import unittest
from pathlib import Path
from review.calendar import official_holiday_calendar


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.html = (Path(__file__).parent / "fixtures/tdx-holidays-2026.html").read_text()

    def test_holiday_and_makeup_workday_stay_closed(self):
        rows = {r["date"]: r["is_open"] for r in official_holiday_calendar(self.html, 2026, 10)}
        self.assertEqual(len(rows), 31)
        self.assertFalse(rows["2026-10-01"])
        self.assertFalse(rows["2026-10-10"])
        self.assertTrue(rows["2026-10-13"])

    def test_unpublished_year_cannot_be_treated_as_normal_weekdays(self):
        with self.assertRaisesRegex(ValueError, "尚不完整"):
            official_holiday_calendar(self.html, 2027, 1)

    def test_partial_market_scope_cannot_be_used_for_scheduling(self):
        with self.assertRaisesRegex(ValueError, "市场范围"):
            official_holiday_calendar(self.html.replace("北京市场", ""), 2026, 10)
