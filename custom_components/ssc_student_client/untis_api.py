"""Direct WebUntis access for the autonomous Home Assistant mode - no Mac, no browser, no password.

Uses the "Untis Mobile" login: WebUntis shows a QR code (Profile -> Freigaben / Access -> Untis Mobile) that carries a
per-user *secret key*. Together with the user name it lets a client log in with a time-based one-time code (TOTP), exactly
like the official mobile app. Treat the key like a password; you can revoke it in WebUntis by generating a new QR code.

NOTE: these are WebUntis's internal mobile endpoints. They could be checked here only against fakes, not against a real
school server - field names are parsed defensively and every part (timetable, homework, exams) fails independently.
No Home Assistant imports: everything is testable anywhere.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse


class UntisError(Exception):
    pass


class AuthError(UntisError):
    pass


# ---------------------------------------------------------------- the QR code / secret
def parse_qr(text: str) -> dict | None:
    """untis://setschool?url=<server>&school=<name>&user=<u>&key=<secret>&schoolNumber=<n> -> dict, or None"""
    u = urlparse((text or "").strip())
    if u.scheme != "untis":
        return None
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    server = (q.get("url") or "").replace("https://", "").replace("http://", "").strip("/")
    if not (server and q.get("school") and q.get("user") and q.get("key")):
        return None
    return dict(server=server, school=q["school"], user=q["user"], key=q["key"])


def totp(secret: str, at_ms: int, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (HMAC-SHA1) from a base32 secret."""
    s = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    counter = struct.pack(">Q", int(at_ms // 1000 // step))
    h = hmac.new(key, counter, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def auth_block(user: str, key: str, now_ms: int | None = None) -> dict:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return {"user": user, "otp": totp(key, now_ms), "clientTime": now_ms}


# ---------------------------------------------------------------- JSON-RPC
def rpc_url(server: str, school: str, method: str) -> str:
    from urllib.parse import quote
    return f"https://{server}/WebUntis/jsonrpc_intern.do?m={method}&school={quote(school)}&v=i2.2"


async def call(post, cfg: dict, method: str, params: dict | None = None) -> dict:
    """post(url, json_body) -> parsed JSON dict (HTTP errors raise). Returns the JSON-RPC 'result'."""
    body = {"id": "ssc", "method": method, "jsonrpc": "2.0",
            "params": [{**(params or {}), "auth": auth_block(cfg["user"], cfg["key"])}]}
    reply = await post(rpc_url(cfg["server"], cfg["school"], method), body)
    err = reply.get("error")
    if err:
        msg = str(err.get("message", err))
        if err.get("code") in (-8520, -8504, -8500) or "auth" in msg.lower() or "login" in msg.lower():
            raise AuthError(msg)
        raise UntisError(f"{method}: {msg}")
    return reply.get("result") or {}


# ---------------------------------------------------------------- conversions to SSC's Parent data format
def _hhmm(v) -> str:
    s = str(int(v)).zfill(4)
    return f"{s[:2]}:{s[2:]}"


def _iso(d) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _names(master: dict, kind: str, refs) -> str:
    table = {x.get("id"): x for x in master.get(kind, [])}
    out = []
    for r in refs or []:
        x = table.get(r.get("id") if isinstance(r, dict) else r) or {}
        n = x.get("name") or x.get("longName")
        if n:
            out.append(str(n))
    return ", ".join(out)


def convert_timetable(result: dict, master: dict) -> list[dict]:
    periods = (result.get("timetable") or result).get("periods") or []
    out = []
    for p in periods:
        try:
            d = _iso(p["date"])
            out.append(dict(
                date=d, start=f"{d}T{_hhmm(p['startTime'])}", end=f"{d}T{_hhmm(p['endTime'])}",
                subject=_names(master, "subjects", p.get("su")), teacher=_names(master, "teachers", p.get("te")),
                room=_names(master, "rooms", p.get("ro")), cancelled=str(p.get("cellState", "")).upper() in ("CANCEL", "CANCELLED"),
                info=p.get("lstext") or p.get("substText") or "", topic=p.get("lstext") or "", substitution=p.get("substText") or ""))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda e: e["start"])


def classify_homework(items: list[dict], today: date, soon_days: int = 2) -> list[dict]:
    """Same statuses as SSC: done | missed | soon | pending."""
    out = []
    for x in items:
        due = x["due"]
        try:
            days = (date.fromisoformat(due) - today).days
        except ValueError:
            days = None
        if x.get("done"):
            st = "done"
        elif days is None:
            st = "pending"
        elif days < 0:
            st = "missed"
        elif days <= soon_days:
            st = "soon"
        else:
            st = "pending"
        out.append({**x, "status": st, "days_left": days})
    return out


def convert_homework(result: dict, today: date, soon_days: int = 2) -> list[dict]:
    lessons = {l.get("id"): l for l in (result.get("lessons") or [])}
    raw = result.get("homeWorks") or result.get("homeworks") or result.get("homework") or []
    items = []
    for h in raw:
        les = lessons.get(h.get("lessonId"), {})
        subj = h.get("subject") or les.get("subject") or les.get("su") or ""
        items.append(dict(subject=str(subj), due=_iso(h.get("dueDate") or h.get("date") or "00000000"), text=h.get("text", ""),
                          remark=h.get("remark", ""), done=bool(h.get("completed") or h.get("done"))))
    return classify_homework(items, today, soon_days)


def convert_exams(result: dict) -> list[dict]:
    out = []
    for e in result.get("exams") or []:
        try:
            out.append(dict(date=_iso(e["examDate"]), start=_hhmm(e["startTime"]) if e.get("startTime") else "",
                            end=_hhmm(e["endTime"]) if e.get("endTime") else "",
                            subject=str(e.get("subject") or e.get("name") or ""), text=e.get("text") or e.get("name") or ""))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda e: e["date"])


# ---------------------------------------------------------------- everything the integration needs
async def fetch_all(post, cfg: dict, today: date | None = None, weeks_back: int = 1, weeks_ahead: int = 4) -> dict:
    """-> data in the same shape as SSC's Parent mode, so the sensors / calendar work with either source."""
    today = today or date.today()
    ud = await call(post, cfg, "getUserData2017")            # auth errors surface here
    master = ud.get("masterData") or {}
    user = ud.get("userData") or {}
    elem = dict(id=user.get("elemId"), type=user.get("elemType", 5))
    monday = today - timedelta(days=today.weekday())
    notes: list[str] = []

    timetable: dict[str, list] = {}
    for w in range(-weeks_back, weeks_ahead + 1):
        start = monday + timedelta(weeks=w)
        try:
            res = await call(post, cfg, "getTimetable2017", {**elem, "startDate": int(start.strftime("%Y%m%d")),
                                                             "endDate": int((start + timedelta(days=6)).strftime("%Y%m%d"))})
            timetable[start.isoformat()] = convert_timetable(res, master)
        except AuthError:
            raise
        except UntisError as e:
            notes.append(f"timetable {start}: {e}")
    span = dict(startDate=int((today - timedelta(days=30)).strftime("%Y%m%d")), endDate=int((today + timedelta(days=90)).strftime("%Y%m%d")))
    homework, exams = [], []
    try:
        homework = convert_homework(await call(post, cfg, "getHomeWork2017", {**elem, **span}), today)
    except AuthError:
        raise
    except UntisError as e:
        notes.append(f"homework: {e}")
    try:
        exams = convert_exams(await call(post, cfg, "getExams2017", {**elem, **span}))
    except AuthError:
        raise
    except UntisError as e:
        notes.append(f"exams: {e}")
    return dict(sections=["today", "timetable", "homework", "exams"], synced_at=time.time(), server_time=time.time(),
                timetable=timetable, homework=[h for h in homework if h["status"] != "done" or h["days_left"] is None or h["days_left"] >= -1],
                exams=exams, plan=[], changes=[], source="direct", notes=notes)
