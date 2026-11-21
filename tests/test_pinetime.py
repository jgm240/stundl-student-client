import sys, tempfile, os, unittest
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SSC_DATA_DIR"] = tempfile.mkdtemp()
import core
core.HOME = Path(os.environ["SSC_DATA_DIR"])
import pinetime


class PineTimeTests(unittest.TestCase):
    def test_current_time_bytes(self):
        b = pinetime.current_time_bytes(datetime(2026, 11, 23, 8, 5, 9))   # a Monday
        self.assertEqual(list(b), [0xEA, 0x07, 11, 23, 8, 5, 9, 1, 0, 0])

    def test_alert_bytes(self):
        b = pinetime.alert_bytes("Homework", "tomorrow · Math:\n p.12")
        self.assertEqual(b[:2], bytes([0, 1]))
        self.assertEqual(b[2:].decode().split("\0"), ["Homework", "tomorrow · Math: p.12"])
        self.assertLessEqual(len(pinetime.alert_bytes("x" * 500, "y" * 500)), 2 + 142)

    def test_notifications_from_menu_model(self):
        m = dict(sections=[("Alerts", ["Math cancelled 08:00"]), ("Today", ["08:00 Math R1", "09:00 Art"]), ("Tomorrow", ["x"])],
                 homework=[(1, "! 2 d overdue · Math: a"), (2, "tomorrow · Art: b"), (3, "in 5 d · Bio: c")])
        n = pinetime.notifications(m)
        self.assertEqual([t for _, t, _ in n], ["Schedule change", "First lesson today", "Homework", "Homework"])
        self.assertEqual(n[1][2], "08:00 Math R1")

    def test_only_unsent_are_sent(self):
        items = [("a", "t", "m"), ("b", "t", "m")]
        st = {"sent": {}}
        todo, today = pinetime.unsent(items, st)
        self.assertEqual(len(todo), 2)
        pinetime.save_state(st, ["a"], today)
        todo, _ = pinetime.unsent(items, pinetime.load_state())
        self.assertEqual([k for k, _, _ in todo], ["b"])

    def test_sync_without_watch(self):
        self.assertIn("No watch set up", pinetime.sync())


if __name__ == "__main__":
    unittest.main()
