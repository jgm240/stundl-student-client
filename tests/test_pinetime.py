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


    def test_event_wire_format_matches_firmware(self):
        b = pinetime.event_bytes(2, 5, 1_800_000_000, 1_800_003_600, "Math R1")
        self.assertEqual(b[0:2], bytes([2, 5]))
        self.assertEqual(int.from_bytes(b[2:10], "little"), 1_800_000_000)
        self.assertEqual(int.from_bytes(b[10:18], "little"), 1_800_003_600)
        self.assertEqual((b[18], b[19:]), (7, b"Math R1"))
        self.assertEqual(len(b[:19]), 19)
        long = pinetime.event_bytes(0, 1, 0, 0, "Ä" * 60)                 # never splits a character, max 40 bytes
        self.assertLessEqual(long[18], 40)
        long[19:].decode("utf-8")

    def test_school_events_mix_and_prefixes(self):
        now = datetime(2026, 11, 23, 7, 0)
        les = lambda d, h, sub, room="R1", c=False: dict(start=f"{d}T{h}:00", end=f"{d}T{h[:2]}:45", subject=sub, room=room, cancelled=c)
        st = dict(timetable={"w": [les("2026-11-23", "08:00", "Math"), les("2026-11-23", "09:00", "Art", c=True), les("2026-11-23", "10:00", "Bio"),
                                   les("2026-11-24", "08:00", "Eng"), les("2026-11-24", "09:00", "Chem")]},
                  exams=[dict(date="2026-11-26", subject="Physics", text="")],
                  homework=[dict(id=1, due="2026-11-24", subject="Math", text="p.12", status="soon", done=False)],
                  homework_cfg=dict(dismiss_done=True), homework_summary={})
        import core as c
        orig = c.hw_active
        c.hw_active = lambda s: s["homework"]
        try:
            ev = pinetime.school_events(st, now)
        finally:
            c.hw_active = orig
        titles = [t for _, _, t in ev]
        self.assertEqual(len(ev), 5)
        self.assertIn("Exam: Physics", titles)
        self.assertIn("HW: Math p.12", titles)
        self.assertNotIn("Art R1", titles)                                # cancelled lessons are not shown
        self.assertEqual([e[0] for e in ev], sorted(e[0] for e in ev))     # soonest first


if __name__ == "__main__":
    unittest.main()
