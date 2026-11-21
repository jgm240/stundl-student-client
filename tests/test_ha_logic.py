"""Tests for the Home Assistant integration's pure logic (no Home Assistant needed)."""
import asyncio
import importlib.util
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "custom_components" / "ssc_student_client"


def load(name):
    spec = importlib.util.spec_from_file_location(name, D / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


U, P = load("untis_api"), load("parsing")
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
TODAY = date(2026, 11, 20)
MONDAY = TODAY - timedelta(days=4)
CFG = dict(server="mese.webuntis.com", school="my-school", user="max", key="GEZDGNBVGY3TQOJQ")
f = lambda n: int((TODAY + timedelta(n)).strftime("%Y%m%d"))


def fake_post(fail=None, auth_ok=True):
    async def post(url, body):
        m = url.split("m=")[1].split("&")[0]
        a = body["params"][0]["auth"]
        assert a["user"] == "max" and len(a["otp"]) == 6
        if not auth_ok:
            return {"error": {"code": -8520, "message": "bad credentials"}}
        if fail == m:
            return {"error": {"code": -32601, "message": "Method not found"}}
        if m == "getUserData2017":
            return {"result": {"userData": {"elemId": 42, "elemType": 5}, "masterData": {
                "subjects": [{"id": 1, "name": "M"}, {"id": 2, "name": "D"}], "teachers": [{"id": 7, "name": "AB"}], "rooms": [{"id": 9, "name": "R1"}]}}}
        if m == "getTimetable2017":
            if body["params"][0]["startDate"] != int(MONDAY.strftime("%Y%m%d")):
                return {"result": {"timetable": {"periods": []}}}
            d = f(0)
            return {"result": {"timetable": {"periods": [
                {"date": d, "startTime": 800, "endTime": 850, "su": [{"id": 1}], "te": [{"id": 7}], "ro": [{"id": 9}], "cellState": "STANDARD"},
                {"date": d, "startTime": 900, "endTime": 950, "su": [{"id": 2}], "te": [{"id": 7}], "ro": [{"id": 9}], "cellState": "CANCEL"},
                {"broken": True}]}}}
        if m == "getHomeWork2017":
            return {"result": {"lessons": [{"id": 5, "subject": "D"}], "homeWorks": [
                {"id": 1, "lessonId": 5, "dueDate": f(-1), "text": "Essay", "completed": False},
                {"id": 2, "lessonId": 5, "dueDate": f(1), "text": "Vokabeln", "completed": False},
                {"id": 3, "lessonId": 5, "dueDate": f(9), "text": "Referat", "completed": False},
                {"id": 5, "lessonId": 5, "dueDate": f(-40), "text": "uralt", "completed": True}]}}
        if m == "getExams2017":
            return {"result": {"exams": [{"id": 7, "examDate": f(5), "startTime": 800, "endTime": 900, "subject": "Mathe", "name": "SA"}]}}
        return {"result": {}}
    return post


class UntisApiTests(unittest.TestCase):
    def test_totp_matches_rfc6238(self):
        self.assertEqual(U.totp(SECRET, 59_000), "287082")
        self.assertEqual(U.totp(SECRET, 1111111109_000), "081804")
        self.assertEqual(U.totp(SECRET, 1234567890_000), "005924")
        self.assertEqual(U.totp("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", 59_000), "287082")

    def test_qr(self):
        q = U.parse_qr("untis://setschool?url=mese.webuntis.com&school=my-school&user=max.muster&key=GEZDGNBVGY3TQOJQ&schoolNumber=1")
        self.assertEqual(q, dict(server="mese.webuntis.com", school="my-school", user="max.muster", key="GEZDGNBVGY3TQOJQ"))
        self.assertIsNone(U.parse_qr("http://x"))
        self.assertIsNone(U.parse_qr("untis://setschool?url=a&school=b"))

    def test_fetch_all(self):
        data = asyncio.run(U.fetch_all(fake_post(), CFG, TODAY))
        week = data["timetable"][MONDAY.isoformat()]
        self.assertEqual([(e["start"][11:16], e["subject"], e["cancelled"]) for e in week], [("08:00", "M", False), ("09:00", "D", True)])
        self.assertEqual({h["text"]: h["status"] for h in data["homework"]}, {"Essay": "missed", "Vokabeln": "soon", "Referat": "pending"})
        self.assertEqual(data["exams"][0]["subject"], "Mathe")
        self.assertEqual(data["source"], "direct")

    def test_one_failing_part_does_not_break_the_rest(self):
        data = asyncio.run(U.fetch_all(fake_post(fail="getHomeWork2017"), CFG, TODAY))
        self.assertEqual(data["homework"], [])
        self.assertTrue(data["notes"] and "homework" in data["notes"][0])
        self.assertTrue(data["exams"])

    def test_wrong_secret_is_an_auth_error(self):
        with self.assertRaises(U.AuthError):
            asyncio.run(U.fetch_all(fake_post(auth_ok=False), CFG, TODAY))

    def test_sensors_logic_works_on_direct_data(self):
        data = asyncio.run(U.fetch_all(fake_post(), CFG, TODAY))
        now = datetime(2026, 11, 20, 7, 30)
        self.assertEqual(P.next_lesson(data, now)["subject"], "M")
        self.assertEqual(P.homework_counts(data), {"missed": 1, "soon": 1, "pending": 1, "done": 0})
        self.assertEqual(P.next_exam(data, TODAY)["subject"], "Mathe")


class ParentLinkTests(unittest.TestCase):
    def test_link_rules(self):
        tok = "A" * 24
        self.assertTrue(P.parse_link(f"http://192.168.1.20:8766/p/{tok}/wall"))
        self.assertTrue(P.parse_link(f"http://macbook.local:8766/p/{tok}"))
        self.assertTrue(P.parse_link(f"https://school.example/p/{tok}/"))
        for bad in (f"http://8.8.8.8:8766/p/{tok}/", "http://192.168.1.2/x", "nonsense", "http://192.168.1.20:8766/p/short/"):
            self.assertIsNone(P.parse_link(bad))


if __name__ == "__main__":
    unittest.main()
