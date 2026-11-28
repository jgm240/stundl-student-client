"""WebUntis answers in the Untis Mobile form (lessons described by an `elements` list) as well as the older fields, and the
diagnostic that shows the structure of an answer without any text."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402
import untis_direct as u  # noqa: E402

MASTER = {"subjects": [{"id": 3, "name": "M", "longName": "Mathematik"}, {"id": 4, "name": "E", "longName": "English"}],
          "teachers": [{"id": 2, "name": "AB"}, {"id": 5, "name": "CD"}], "rooms": [{"id": 7, "name": "R101"}, {"id": 8, "name": "R202"}],
          "klassen": [{"id": 1, "name": "3A"}]}
MON = date(2026, 11, 23)


def elements(cls=1, te=2, su=3, ro=7, state="REGULAR", org=None):
    out = [{"type": 1, "id": cls, "orgId": cls, "state": "REGULAR"}, {"type": 2, "id": te, "orgId": org.get("te", te) if org else te, "state": state},
           {"type": 3, "id": su, "orgId": su, "state": "REGULAR"}, {"type": 4, "id": ro, "orgId": org.get("ro", ro) if org else ro, "state": state}]
    return out


def mobile_timetable():
    return {"timetable": {"displayableStartDate": 20261123, "periods": [
        {"id": 1, "lessonId": 55, "lessonNumber": 1, "lessonText": "", "periodText": "", "substText": "", "date": 20261123, "startTime": 800, "endTime": 850,
         "cellState": "STANDARD", "elements": elements()},
        {"id": 2, "lessonId": 56, "date": 20261123, "startTime": 900, "endTime": 950, "cellState": "CANCEL", "elements": elements(su=4, state="CANCEL")},
        {"id": 3, "lessonId": 55, "date": 20261124, "startTime": 800, "endTime": 850, "cellState": "SUBSTITUTION", "substText": "covering",
         "elements": elements(te=5, ro=8, state="SUBSTITUTION", org={"te": 2, "ro": 7})}]},
        "masterData": MASTER}


def answer(method, params):
    if method == "getUserData2017":
        return {"userData": {"elemType": 5, "elemId": 9}, "masterData": MASTER}
    if method == "getTimetable2017":
        return mobile_timetable() if params["startDate"] == 20261123 else {"timetable": {"periods": []}}
    if method == "getHomeWork2017":                                   # no subject anywhere in the homework itself: only the lesson it belongs to
        return {"homeWorks": [{"id": 1, "lessonId": 55, "date": 20261120, "dueDate": 20261127, "text": "Exercises p. 42", "completed": False},
                              {"id": 2, "lessonId": 56, "date": 20261121, "dueDate": 20261130, "text": "Vocabulary unit 3", "completed": True}], "lessons": []}
    if method == "getExams2017":
        return {"exams": [{"id": 1, "examDate": 20261203, "startTime": 800, "endTime": 950, "name": "Test", "subject": "M"},
                          {"id": 2, "startDateTime": "2026-12-10T10:15:00", "name": "Vocabulary test", "subjectId": 4},
                          {"id": 3, "date": "2026-12-12", "text": "Oral exam", "su": [{"id": 3}]}]}
    raise AssertionError(method)


def post(url, body):
    method = body["method"]
    return {"result": answer(method, body["params"][0])}


CFG = {"server": "x.webuntis.com", "school": "s", "user": "u", "key": "ABCDEFGH"}


class MobileFormatTests(unittest.TestCase):
    def test_timetable_in_the_elements_form(self):
        lessons = {}
        days = u.periods_to_days(mobile_timetable(), MASTER, lessons)["days"]
        self.assertEqual([d["date"] for d in days], ["2026-11-23", "2026-11-24"])
        first = days[0]["gridEntries"]
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["duration"], {"start": "2026-11-23T08:00", "end": "2026-11-23T08:50"})
        self.assertEqual([i["current"]["shortName"] for i in first[0]["position3"]], ["M"])
        self.assertEqual([i["current"]["shortName"] for i in first[0]["position2"]], ["AB"])
        self.assertEqual([i["current"]["shortName"] for i in first[0]["position4"]], ["R101"])
        self.assertEqual([i["current"]["shortName"] for i in first[0]["position1"]], ["3A"])
        self.assertEqual((first[0]["status"], first[1]["status"]), ("REGULAR", "CANCELLED"))
        sub = days[1]["gridEntries"][0]
        self.assertEqual(sub["status"], "CHANGED")
        self.assertEqual(sub["substitutionText"], "covering")
        self.assertEqual(sub["position2"][0]["removed"]["shortName"], "AB")                    # who it was before
        self.assertEqual(sub["position4"][0]["removed"]["shortName"], "R101")
        self.assertEqual(lessons, {55: "M", 56: "E"})                                          # lesson -> subject, for the homework

    def test_the_old_fields_still_work(self):
        old = {"timetable": {"periods": [{"date": 20261123, "startTime": 800, "endTime": 850, "lessonId": 5, "su": [{"id": 3}], "te": [{"id": 2}], "ro": [{"id": 7}], "kl": [{"id": 1}]}]}}
        lessons = {}
        e = u.periods_to_days(old, MASTER, lessons)["days"][0]["gridEntries"][0]
        self.assertEqual(([i["current"]["shortName"] for i in e["position3"]], lessons), (["M"], {5: "M"}))

    def test_state_from_the_elements_when_there_is_no_cell_state(self):
        for state, want in (("CANCEL", "CANCELLED"), ("SUBSTITUTION", "CHANGED"), ("ADDED", "CHANGED"), ("REGULAR", "REGULAR")):
            p = {"date": 20261123, "startTime": 800, "endTime": 850, "elements": elements(state=state)}
            self.assertEqual(u.periods_to_days({"timetable": {"periods": [p]}}, MASTER)["days"][0]["gridEntries"][0]["status"], want, state)

    def test_a_whole_sync_gives_timetable_homework_subjects_and_exam_dates(self):
        data = u.fetch(CFG, today=MON, post=post)
        self.assertEqual(data["notes"], [])                                                    # nothing to complain about
        week = data["timetable"]["2026-11-23"]["days"]
        self.assertEqual(len(week), 2)
        con_data = {"timetable:2026-11-23": data["timetable"]["2026-11-23"], "homework": data["homework"], "exams": data["exams"]}
        hw = core.homework_items(data["homework"]["data"])
        self.assertEqual({h["id"]: h["subject"] for h in hw}, {"1": "M", "2": "E"})            # the subject came through the lesson of the timetable
        ex = core.exam_items(data["exams"]["data"])
        self.assertEqual(sorted((e["date"], e["subject"]) for e in ex), [("2026-12-03", "M"), ("2026-12-10", "E"), ("2026-12-12", "M")])
        entries = core.tt_entries(data["timetable"]["2026-11-23"])
        self.assertEqual(sorted((e["subject"], e["cancelled"]) for e in entries if e["date"] == "2026-11-23"), [("E", True), ("M", False)])
        self.assertTrue(any(e["room"] == "R202" and e["teacher"] == "CD" for e in entries))

    def test_exam_date_spellings(self):
        self.assertEqual(u._exam_date({"examDate": 20261203}), 20261203)
        self.assertEqual(u._exam_date({"date": "2026-12-03"}), "2026-12-03")
        self.assertEqual(u._exam_date({"startDateTime": "2026-12-03T08:00:00"}), "2026-12-03")
        self.assertEqual(u._exam_date({"start": "2026-12-03 08:00"}), "2026-12-03")
        self.assertIsNone(u._exam_date({"examDate": None, "name": "x"}))
        self.assertEqual(u._exam_time("2026-12-03T08:05:00"), 805)
        self.assertIsNone(u._exam_time(None))


def real_period(pid, lesson, start, end, su=3, te=2, ro=7, flags=("REGULAR",), text=None, tes=None):
    """A period exactly as one school's WebUntis sent it (structure taken from `ssconfig untis shape`)."""
    return {"id": pid, "lessonId": lesson, "startDateTime": start, "endDateTime": end, "foreColor": "#000000", "backColor": "#ffffff",
            "innerForeColor": "", "innerBackColor": "", "text": text or {"lesson": "", "substitution": "", "info": "", "attachments": []},
            "elements": [{"type": "CLASS", "id": 1, "orgId": 1}, {"type": "TEACHER", "id": te, "orgId": (tes or te)}, {"type": "SUBJECT", "id": su, "orgId": su},
                         {"type": "ROOM", "id": ro, "orgId": ro}],
            "can": ["READ_LESSONTOPIC", "READ_HOMEWORK"], "is": list(flags), "homeWorks": [], "exam": None, "isOnlinePeriod": False, "blockHash": -1150000593}


def real_answer(method, params):
    if method == "getUserData2017":
        return {"userData": {"elemType": "STUDENT", "elemId": 4362, "displayName": "x", "children": [], "klassenIds": [], "rights": ["CLASSREGISTER"]},
                "masterData": MASTER, "settings": {}}
    if method == "getTimetable2017":
        if params["startDate"] != 20261123:
            return {"timetable": {"periods": []}, "masterData": MASTER}
        return {"timetable": {"displayableStartDate": "2026-11-23", "displayableEndDate": "2026-11-29", "periods": [
            real_period(1, 71988, "2026-11-23T08:00Z", "2026-11-23T08:50Z"),
            real_period(2, 72111, "2026-11-23T09:00Z", "2026-11-23T09:50Z", su=4, flags=("CANCELLED",)),
            real_period(3, 71988, "2026-11-24T08:00Z", "2026-11-24T08:50Z", te=5, ro=8, tes=2, flags=("SUBSTITUTION",),
                        text={"lesson": "", "substitution": "covering", "info": "bring a calculator", "attachments": []})]}, "masterData": MASTER}
    if method == "getHomeWork2017":                                   # no subject in the homework: only lessonId, and lessonsById tells the subject
        return {"homeWorks": [{"id": 13297, "lessonId": 72111, "startDate": "2026-11-20", "endDate": "2026-11-27", "text": "Exercises p. 42", "remark": None, "completed": False, "attachments": []},
                              {"id": 13328, "lessonId": 71988, "startDate": "2026-11-21", "endDate": "2026-11-30", "text": "Read chapter 5", "remark": None, "completed": True, "attachments": []}],
                "lessonsById": {"72111": {"id": 72111, "subjectId": 4, "klassenIds": [1], "teacherIds": [2]}, "71988": {"id": 71988, "subjectId": 3, "klassenIds": [1], "teacherIds": [2]}}}
    if method == "getExams2017":
        return {"type": "STUDENT", "id": 4362, "exams": [
            {"id": 15442, "examType": "Schularbeit", "startDateTime": "2026-12-03T08:00Z", "endDateTime": "2026-12-03T09:50Z", "departmentId": 0, "subjectId": 3, "klasseIds": [1], "roomIds": [7],
             "teacherIds": [2], "invigilators": [], "name": "Test fractions", "text": ""},
            {"id": 15529, "examType": "Test", "startDateTime": "2026-12-10T10:15", "endDateTime": "2026-12-10T11:05", "subjectId": 4, "name": "", "text": "Vocabulary"}]}
    raise AssertionError(method)


def real_post(url, body):
    return {"result": real_answer(body["method"], body["params"][0])}


class RealFormatTests(unittest.TestCase):
    """The form one school's WebUntis really sends (startDateTime, elements typed by word, `is` flags, lessonsById, endDate ...)."""

    def test_a_whole_sync_in_the_real_form(self):
        data = u.fetch(CFG, today=MON, post=real_post)
        self.assertEqual(data["notes"], [], data["notes"])                                      # nothing skipped, nothing unknown
        entries = core.tt_entries(data["timetable"]["2026-11-23"])
        mon = sorted((e["start"][11:16], e["subject"], e["room"], e["teacher"], e["cancelled"]) for e in entries if e["date"] == "2026-11-23")
        self.assertEqual(mon, [("08:00", "M", "R101", "AB", False), ("09:00", "E", "R101", "AB", True)])
        tue = [e for e in entries if e["date"] == "2026-11-24"][0]
        self.assertEqual((tue["teacher"], tue["room"], tue["substitution"], tue["note"]), ("CD", "R202", "covering", "bring a calculator"))
        hw = core.homework_items(data["homework"]["data"])
        self.assertEqual({h["id"]: (h["subject"], h["due"]) for h in hw}, {"13297": ("E", "2026-11-27"), "13328": ("M", "2026-11-30")})
        self.assertEqual({h["id"]: h["completed"] for h in hw}, {"13297": False, "13328": True})
        ex = core.exam_items(data["exams"]["data"])
        self.assertEqual(sorted((e["date"], e["subject"]) for e in ex), [("2026-12-03", "M"), ("2026-12-10", "E")])
        self.assertEqual([e["start"] for e in ex if e["date"] == "2026-12-03"][0][-5:], "08:00")

    def test_what_cannot_be_read_is_reported_not_swallowed(self):
        bad = {"timetable": {"periods": [real_period(1, 1, "2026-11-23T08:00Z", "2026-11-23T08:50Z"), {"id": 2, "lessonId": 1, "startDateTime": "soon"}, {"id": 3}]}}
        notes = []
        days = u.periods_to_days(bad, MASTER, None, notes)["days"]
        self.assertEqual(len(days[0]["gridEntries"]), 1)
        self.assertEqual(len(notes), 1)
        self.assertIn("2 lesson(s) could not be read", notes[0])
        self.assertIn("shape", notes[0])

    def test_flags(self):
        for flags, want in ((["REGULAR"], "REGULAR"), (["CANCELLED"], "CANCELLED"), (["SUBSTITUTION"], "CHANGED"), (["ADDITIONAL"], "CHANGED"),
                            (["ROOM_SUBSTITUTION"], "CHANGED"), ([], "REGULAR"), (["REGULAR", "CANCELLED"], "CANCELLED")):
            self.assertEqual(u._period_state({"is": flags}), want, flags)

    def test_the_whole_thing_lands_in_the_database(self):
        import tempfile
        con = core.sqlite3.connect(":memory:")
        con.executescript("create table store(key text primary key, fetched_at real, body text);"
                          "create table changes(id integer primary key autoincrement, at real, kind text, text text, seen integer default 0);")
        changes, notes = u.sync(con=con, cfg=CFG, post=real_post, today=MON)
        self.assertEqual(notes, [])
        keys = {r[0] for r in con.execute("select key from store")}
        self.assertTrue({"homework", "exams", "timetable:2026-11-23"} <= keys)
        st = core.load(con)
        self.assertEqual(len(core.tt_entries(st["timetable:2026-11-23"][1])), 3)


@unittest.skipUnless(hasattr(__import__("time"), "tzset"), "needs time.tzset")
class ZoneTests(unittest.TestCase):
    """The trailing Z on WebUntis times: sometimes local school time, sometimes really UTC. The school's time grid decides."""
    def setUp(self):
        import time
        self.old = _os.environ.get("TZ")
        _os.environ["TZ"] = "Europe/Vienna"
        time.tzset()

    def tearDown(self):
        import time
        if self.old is None:
            _os.environ.pop("TZ", None)
        else:
            _os.environ["TZ"] = self.old
        time.tzset()

    GRID = {"timeGrid": {"days": [{"day": "MON", "units": [{"label": "1", "startTime": "08:00", "endTime": "08:50"}, {"label": "2", "startTime": "09:00", "endTime": "09:50"},
                                                             {"label": "3", "startTime": "10:05", "endTime": "10:55"}]}]}, "subjects": [{"id": 3, "name": "M"}]}

    def periods(self, *starts):
        return {"timetable": {"periods": [real_period(i, 1, f"2026-11-23T{s}Z", f"2026-11-23T{s}Z") for i, s in enumerate(starts)]}}

    def starts(self, res):
        return [e["duration"]["start"][11:] for e in u.periods_to_days(res, self.GRID)["days"][0]["gridEntries"]]

    def test_times_that_fit_the_grid_are_kept(self):
        self.assertEqual(self.starts(self.periods("08:00", "09:00", "10:05")), ["08:00", "09:00", "10:05"])

    def test_times_that_are_really_utc_are_converted(self):
        self.assertEqual(self.starts(self.periods("07:00", "08:00", "09:05")), ["08:00", "09:00", "10:05"])      # November: UTC+1 in Vienna

    def test_summer_time(self):
        res = {"timetable": {"periods": [real_period(i, 1, f"2026-07-06T{s}Z", f"2026-07-06T{s}Z") for i, s in enumerate(("06:00", "07:00"))]}}
        got = [e["duration"]["start"][11:] for e in u.periods_to_days(res, self.GRID)["days"][0]["gridEntries"]]
        self.assertEqual(got, ["08:00", "09:00"])                                                               # UTC+2

    def test_without_a_grid_nothing_is_guessed(self):
        res = self.periods("07:00")
        self.assertEqual([e["duration"]["start"][11:] for e in u.periods_to_days(res, {"subjects": []})["days"][0]["gridEntries"]], ["07:00"])

    def test_grid_formats(self):
        self.assertEqual(u._grid_starts({"timeGrid": {"days": [{"units": [{"startTime": "0755"}, {"startTime": "T08:50"}, {"startTime": "x"}]}]}}), {"07:55", "08:50"})


class ShapeTests(unittest.TestCase):
    def test_no_text_survives(self):
        raw = {"homeWorks": [{"id": 7, "text": "Secret homework about Anna", "remark": "ask Mr Huber", "dueDate": 20261201, "completed": False, "state": "STANDARD"}] * 5,
               "teachers": [{"id": 2, "name": "Huber", "longName": "Mr Huber"}], "user": "anna.s", "empty": ""}
        s = json.dumps(u.shape(raw))
        for secret in ("Secret", "Anna", "Huber", "anna.s"):
            self.assertNotIn(secret, s)
        self.assertIn("20261201", s)                                   # numbers (dates, times, ids) stay: that is the point
        self.assertIn("STANDARD", s)                                   # so do ALL-CAPS state words
        self.assertIn("<text>", s)
        self.assertIn("5 items in total", s)                            # long lists are cut

    def test_shape_of_a_real_looking_answer_keeps_the_keys(self):
        s = u.shape(answer("getTimetable2017", {"startDate": 20261123}))
        p = s["timetable"]["periods"][0]
        self.assertEqual(set(("lessonId", "date", "startTime", "elements", "cellState")) - set(p), set())
        self.assertEqual(p["cellState"], "STANDARD")
        self.assertEqual(p["elements"][0], {"type": 1, "id": 1, "orgId": 1, "state": "REGULAR"})


if __name__ == "__main__":
    unittest.main()
