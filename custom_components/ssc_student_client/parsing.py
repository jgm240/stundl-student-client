"""Pure logic for the SSC Home Assistant integration (no Home Assistant imports, so it can be tested anywhere).

Input is the read-only JSON served by SSC's Parent mode at <link>/data.json.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse


# ---------------------------------------------------------------- the Parent link
def is_home_network(host: str) -> bool:
    h = host.lower()
    if h == "localhost" or h.endswith(".local"):
        return True
    try:
        a = ipaddress.ip_address(h)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def parse_link(text: str):
    """-> (base_url_with_trailing_slash, short_id) or None. Same rules as the Mac server and the iOS app."""
    u = urlparse((text or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "p" or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", parts[1]):
        return None
    if u.scheme == "http" and not is_home_network(u.hostname):
        return None  # plain http only on your own network
    port = f":{u.port}" if u.port else ""
    base = f"{u.scheme}://{u.hostname}{port}/p/{parts[1]}/"
    return base, hashlib.sha256(base.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- helpers
def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def lessons(data: dict) -> list[dict]:
    out = [e for week in (data.get("timetable") or {}).values() for e in week]
    return sorted(out, key=lambda e: e["start"])


def today_lessons(data: dict, day: date) -> list[dict]:
    return [e for e in lessons(data) if e["date"] == day.isoformat()]


def next_lesson(data: dict, now: datetime) -> dict | None:
    cutoff = now.strftime("%Y-%m-%dT%H:%M")
    return next((e for e in lessons(data) if e["start"] >= cutoff and not e["cancelled"]), None)


def current_lesson(data: dict, now: datetime) -> dict | None:
    t = now.strftime("%Y-%m-%dT%H:%M")
    return next((e for e in lessons(data) if e["start"] <= t < e["end"] and not e["cancelled"]), None)


def homework_counts(data: dict) -> dict:
    c = {"missed": 0, "soon": 0, "pending": 0, "done": 0}
    for h in data.get("homework") or []:
        c[h.get("status", "pending")] = c.get(h.get("status", "pending"), 0) + 1
    return c


def open_homework(data: dict) -> list[dict]:
    order = {"missed": 0, "soon": 1, "pending": 2}
    hw = [h for h in data.get("homework") or [] if h.get("status") in order]
    return sorted(hw, key=lambda h: (order[h["status"]], h["due"]))


def next_exam(data: dict, today: date) -> dict | None:
    return next((e for e in sorted(data.get("exams") or [], key=lambda e: e["date"]) if e["date"] >= today.isoformat()), None)


def plan_for(data: dict, day: date) -> list[dict]:
    return [p for p in data.get("plan") or [] if p["date"] == day.isoformat()]


# ---------------------------------------------------------------- calendar events (naive local times)
def events(data: dict, start: datetime, end: datetime) -> list[dict]:
    """Lessons, exams, homework due dates and study-plan items between start and end (local time, naive)."""
    out: list[dict] = []
    for e in lessons(data):
        if e["cancelled"]:
            continue
        s, f = _dt(e["start"]), _dt(e["end"])
        if f > start and s < end:
            out.append(dict(kind="lesson", summary=e["subject"] or "Lesson", start=s, end=f, location=e.get("room") or "",
                            description=" ".join(x for x in (e.get("teacher"), e.get("info")) if x)))
    def all_day(kind, day_s, summary, description=""):
        d = date.fromisoformat(day_s)
        if start.date() <= d < end.date() or (start.date() == d):
            out.append(dict(kind=kind, summary=summary, start=d, end=d + timedelta(days=1), location="", description=description))
    for x in data.get("exams") or []:
        all_day("exam", x["date"], f"Exam: {x['subject']}", x.get("text", ""))
    for h in data.get("homework") or []:
        if h.get("status") != "done":
            all_day("homework", h["due"], f"Homework due: {h['subject']}", h["text"])
    for p in data.get("plan") or []:
        d = date.fromisoformat(p["date"])
        if p.get("start"):
            s = datetime.combine(d, datetime.strptime(p["start"], "%H:%M").time())
            f = s + timedelta(minutes=p.get("minutes") or 30)
            if f > start and s < end:
                out.append(dict(kind="plan", summary=f"Study: {p['title']}", start=s, end=f, location="", description=p.get("notes", "")))
        else:
            all_day("plan", p["date"], f"Study: {p['title']}", p.get("notes", ""))
    return sorted(out, key=lambda e: (datetime.combine(e["start"], datetime.min.time()) if isinstance(e["start"], date) and not isinstance(e["start"], datetime) else e["start"]))
