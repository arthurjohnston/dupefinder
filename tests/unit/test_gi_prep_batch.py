"""general_index/gi_prep_batch.py: the overnight-run controls (--until, --window-budget, the usage-limit stop)."""
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "general_index"))

import gi_prep_batch as pb  # noqa: E402


class TestNextTime(unittest.TestCase):
    def test_later_today(self):
        now = datetime.datetime(2026, 11, 1, 12, 0)
        self.assertEqual(pb.next_time("19:00", now), datetime.datetime(2026, 11, 1, 19, 0))

    def test_past_means_tomorrow(self):
        now = datetime.datetime(2026, 11, 1, 19, 0, 5)
        self.assertEqual(pb.next_time("05:00", now), datetime.datetime(2026, 11, 2, 5, 0))


class TestHitLimit(unittest.TestCase):
    def test_limit_message(self):
        res = {"is_error": True, "result": "You've hit your 5-hour limit · resets 12:10pm (America/Los_Angeles)"}
        self.assertTrue(pb.hit_limit(res))

    def test_ordinary_results(self):
        self.assertFalse(pb.hit_limit({"result": "9 findings proposed, journal contacts found."}))
        self.assertFalse(pb.hit_limit({}))


class TestWindowSpend(unittest.TestCase):
    def test_only_the_last_five_hours_and_running_at_estimate(self):
        now = 100_000.0
        finished = [(now - pb.WINDOW - 1, 50.0), (now - 60, 3.0), (now - 600, 4.0)]
        self.assertAlmostEqual(pb.window_spend(finished, 2, now, 3.5), 3.0 + 4.0 + 7.0)


if __name__ == "__main__":
    unittest.main()
