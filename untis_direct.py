"""Headless WebUntis sync with the Untis Mobile key (no browser, no password) - used by SSC Server.

Same login as the Home Assistant integration and SSC Mobile: user name + secret key -> time-based one-time code.
The answers are converted into the stored formats SSC already understands (timetable gridEntries, homework, exams),
so every existing view, change alert and Parent page just works.

NOTE: WebUntis's mobile endpoints are internal and undocumented; verified here against a simulated server only.
"""
import base64
import hashlib
import hmac
import json
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

import core
import vault

CRED_KEY = "untis-mobile"


class UntisError(Exception):
    pass


class AuthError(UntisError):
    pass


# ---------------------------------------------------------------- credentials (the secret key lives in the vault only)
def parse_qr(text):
    u = urllib.parse.urlparse((text or "").strip())
    if u.scheme != "untis":
        return None
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    server = (q.get("url") or "").replace("https://", "").replace("http://", "").strip("/")
    if not (server and q.get("school") and q.get("user") and q.get("key")):
        return None
    try:
        _b32(q["key"])
    except Exception:
        return None
    return dict(server=server, school=q["school"], user=q["user"], key=q["key"])


def save_credentials(cfg):
    vault.set(CRED_KEY, json.dumps(cfg))


def load_credentials():
    raw = vault.get(CRED_KEY)
    return json.loads(raw) if raw else None


def clear_credentials():
    vault.delete(CRED_KEY)


def describe():
    c = load_credentials()
    return f"{c['user']} @ {c['school']} ({c['server']})" if c else None


# ---------------------------------------------------------------- TOTP (RFC 6238, SHA-1, 6 digits)
def _b32(secret):
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def totp(secret, at_ms, step=30, digits=6):
    counter = struct.pack(">Q", int(at_ms // 1000 // step))
    h = hmac.new(_b32(secret), counter, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)).zfill(digits)


# ---------------------------------------------------------------- JSON-RPC
def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def call(cfg, method, params=None, post=None, now_ms=None):
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    url = f"https://{cfg['server']}/WebUntis/jsonrpc_intern.do?m={method}&school={urllib.parse.quote(cfg['school'])}&v=i2.2"
    body = {"id": "ssc", "method": method, "jsonrpc": "2.0",
            "params": [{**(params or {}), "auth": {"user": cfg["user"], "otp": totp(cfg["key"], now_ms), "clientTime": now_ms}}]}
    reply = (post or _post)(url, body)
    err = reply.get("error")
    if err:
        msg = str(err.get("message", err))
        if err.get("code") in (-8520, -8504, -8500) or "auth" in msg.lower() or "login" in msg.lower():
            raise AuthError(msg)
        raise UntisError(f"{method}: {msg}")
    return reply.get("result") or {}


# ---------------------------------------------------------------- conversions
def _hhmm(v):
    s = str(int(v)).zfill(4)
    return f"{s[:2]}:{s[2:]}"


def _iso(v):
    s = str(int(v)).zfill(8)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _lookup(master, kind):
    return {x.get("id"): x for x in master.get(kind, [])}


def _items(refs, table, typ):
    out = []
    for r in refs or []:
        cur = table.get(r.get("id"), {})
        name = cur.get("name") or cur.get("longName")
        if not name:
            continue
        item = {"current": {"type": typ, "shortName": name, "longName": cur.get("longName") or name}}
        org = table.get(r.get("orgid")) if r.get("orgid") and r.get("orgid") != r.get("id") else None
        if org and (org.get("name") or org.get("longName")):
            item["removed"] = {"type": typ, "shortName": org.get("name") or org.get("longName")}
        out.append(item)
    return out


def periods_to_days(result, master):
    tabs = {k: _lookup(master, k) for k in ("subjects", "teachers", "rooms", "klassen")}
    days = {}
    for p in (result.get("timetable") or result).get("periods") or []:
        try:
            d = _iso(p["date"])
            state = str(p.get("cellState", "")).upper()
            status = "CANCELLED" if state in ("CANCEL", "CANCELLED") else ("CHANGED" if "SUBST" in state else "REGULAR")
            days.setdefault(d, []).append({
                "duration": {"start": f"{d}T{_hhmm(p['startTime'])}", "end": f"{d}T{_hhmm(p['endTime'])}"}, "status": status,
                "type": "NORMAL_TEACHING_PERIOD", "lessonText": p.get("lstext") or "", "substitutionText": p.get("substText") or "",
                "notesAll": "", "position1": _items(p.get("kl"), tabs["klassen"], "CLASS"),
                "position2": _items(p.get("te"), tabs["teachers"], "TEACHER"), "position3": _items(p.get("su"), tabs["subjects"], "SUBJECT"),
                "position4": _items(p.get("ro"), tabs["rooms"], "ROOM")})
        except (KeyError, ValueError, TypeError):
            continue
    return {"days": [{"date": d, "gridEntries": sorted(g, key=lambda e: e["duration"]["start"])} for d, g in sorted(days.items())]}


def fetch(cfg, today=None, weeks_back=1, weeks_ahead=4, post=None):
    """-> {"timetable": {monday: raw}, "homework": raw|None, "exams": raw|None, "notes": [...]} in SSC's stored formats."""
    today = today or date.today()
    ud = call(cfg, "getUserData2017", post=post)
    master, user = ud.get("masterData") or {}, ud.get("userData") or {}
    elem = {"type": user.get("elemType", 5)}
    if user.get("elemId") is not None:
        elem["id"] = user["elemId"]
    monday = today - timedelta(days=today.weekday())
    out, notes = {"timetable": {}, "homework": None, "exams": None, "notes": notes_list()}, None
    notes = out["notes"]
    for w in range(-weeks_back, weeks_ahead + 1):
        start = monday + timedelta(weeks=w)
        try:
            res = call(cfg, "getTimetable2017", {**elem, "startDate": int(start.strftime("%Y%m%d")),
                                                  "endDate": int((start + timedelta(days=6)).strftime("%Y%m%d"))}, post=post)
            out["timetable"][start.isoformat()] = periods_to_days(res, master)
        except AuthError:
            raise
        except UntisError as e:
            notes.append(f"timetable {start}: {e}")
    span = {**elem, "startDate": int((today - timedelta(days=30)).strftime("%Y%m%d")), "endDate": int((today + timedelta(days=90)).strftime("%Y%m%d"))}
    try:
        r = call(cfg, "getHomeWork2017", span, post=post)
        hw = r.get("homeWorks") or r.get("homeworks") or r.get("homework") or []
        out["homework"] = {"data": {"homeworks": [{"id": h.get("id"), "lessonId": h.get("lessonId"), "dueDate": h.get("dueDate") or h.get("date"),
                                                   "date": h.get("date"), "text": h.get("text", ""), "remark": h.get("remark", ""),
                                                   "completed": bool(h.get("completed") or h.get("done"))} for h in hw],
                                  "lessons": [{"id": l.get("id"), "subject": l.get("subject") or l.get("su") or ""} for l in r.get("lessons", [])]}}
    except AuthError:
        raise
    except UntisError as e:
        notes.append(f"homework: {e}")
    try:
        r = call(cfg, "getExams2017", span, post=post)
        out["exams"] = {"data": {"exams": [{"id": e.get("id"), "examDate": e.get("examDate"), "startTime": e.get("startTime"), "endTime": e.get("endTime"),
                                            "subject": e.get("subject") or e.get("name") or "", "text": e.get("text") or e.get("name") or ""}
                                           for e in r.get("exams", [])]}}
    except AuthError:
        raise
    except UntisError as e:
        notes.append(f"exams: {e}")
    return out


def notes_list():
    return []


def sync(con=None, cfg=None, post=None, today=None):
    """Fetch + store + record changes. -> list of (kind, text) changes."""
    con = con or core.db()
    cfg = cfg or load_credentials()
    if not cfg:
        raise UntisError("No Untis Mobile key set yet. Use: ssconfig untis set '<QR text>'")
    data = fetch(cfg, today=today, post=post)
    changes = []
    for monday, raw in data["timetable"].items():
        core.store_save(con, f"timetable:{monday}", raw, changes)
    if data["homework"] is not None:
        core.store_save(con, "homework", data["homework"], changes)
    if data["exams"] is not None:
        core.store_save(con, "exams", data["exams"], changes)
    core.record_changes(con, changes)
    return changes, data["notes"]
