from __future__ import annotations

import unittest
from datetime import date

from trmnl_weekly_calendar.month_render import month_boundary_label


class MonthRenderTests(unittest.TestCase):
    def test_month_boundary_label_only_appears_on_first_day(self) -> None:
        self.assertEqual(month_boundary_label(date(2026, 7, 1)), "JUL")
        self.assertEqual(month_boundary_label(date(2026, 8, 1)), "AUG")
        self.assertEqual(month_boundary_label(date(2026, 9, 1)), "SEP")
        self.assertEqual(month_boundary_label(date(2026, 7, 2)), "")


if __name__ == "__main__":
    unittest.main()
