"""Sync with a PineTime smartwatch (InfiniTime firmware) over Bluetooth Low Energy.

What it does: sets the watch's clock (Current Time Service) and sends SSC's alerts - schedule changes, today's first lesson,
homework that is missed or due soon - as watch notifications (Alert Notification Service). Nothing is read back from the
watch, no pairing key or credential is involved: the only thing stored is the watch's Bluetooth address.

InfiniTime has no timetable app, so notifications are the way to get data onto it. Needs `pip install bleak`.
On the JWatch firmware it also fills the "School" screen (next lessons, exams, homework) through the firmware's own
CalendarEventService; stock InfiniTime lacks that service, so there only clock + notifications are sent.
The protocol parts are pure functions (tested); the Bluetooth part has not been run against a real watch in this repo's CI.
"""
import asyncio
import hashlib
import json
from datetime import datetime

import core

CTS_CHAR = "00002a2b-0000-1000-8000-00805f9b34fb"      # Current Time
ANS_NEW_ALERT = "00002a46-0000-1000-8000-00805f9b34fb"  # New Alert
SCHOOL_CHAR = "9214f6b2-01ea-4c6b-8f1e-6a3a3e5f6c10"   # JWatch firmware: CalendarEventService event characteristic (School screen)
SCHOOL_MAX_EVENTS = 5                                  # CalendarEventService::MaxEvents
SCHOOL_TITLE_MAX = 40                                  # CalendarEventService::TitleMaxLength
NAME_PREFIXES = ("InfiniTime", "Pinetime", "PineTime", "JWatch")
MAX_TEXT = 100
CAT_SIMPLE, CAT_EMAIL = 0x00, 0x01


def current_time_bytes(now):
    """Current Time characteristic: year(u16 LE) month day hour min sec weekday(1=Mon) fractions256 adjust-reason."""
    return bytes([now.year & 0xFF, now.year >> 8, now.month, now.day, now.hour, now.minute, now.second,
                  now.isoweekday(), 0, 0])


def alert_bytes(title, message=""):
    """New Alert: category, count, then 'title\\0message' (UTF-8; the watch shows the title above the text)."""
    def clean(s, n):
        return " ".join(str(s).split())[:n]
    text = f"{clean(title, 40)}\0{clean(message, MAX_TEXT)}".encode("utf-8")[:MAX_TEXT + 42]
    return bytes([CAT_SIMPLE, 1]) + text


def _clip(text, n=SCHOOL_TITLE_MAX):
    """Cut to n UTF-8 bytes without splitting a character."""
    return " ".join(str(text).split()).encode("utf-8")[:n].decode("utf-8", "ignore")


def event_bytes(index, total, start, end, title):
    """JWatch School event write: [index, total, start u64 LE, end u64 LE, titleLen, title...] (header 19 bytes)."""
    t = _clip(title).encode("utf-8")
    return bytes([index, total]) + int(start).to_bytes(8, "little") + int(end).to_bytes(8, "little") + bytes([len(t)]) + t


def school_events(st, now=None):
    """The (at most 5) items the School screen shows, soonest first: [(start_epoch, end_epoch, title)].
    Titles carry their kind as a prefix because the firmware colours by it: 'Exam:' orange, 'HW:' blue, lessons white.
    Mix: up to 2 exams and 2 homework items (they matter most), the rest next lessons."""
    now = now or datetime.now()
    nowi = now.strftime("%Y-%m-%dT%H:%M")

    def epoch(iso):
        return int(datetime.fromisoformat(iso).timestamp())     # naive = local time

    lessons = sorted((e for w in st["timetable"].values() for e in w if e["start"] >= nowi and not e["cancelled"]), key=lambda e: e["start"])
    exams = sorted((x for x in st["exams"] if x["date"] >= nowi[:10]), key=lambda x: x["date"])
    hws = sorted((h for h in core.hw_active(st) if h["status"] != "done"), key=lambda h: h["due"])
    pick = []
    for x in exams[:2]:
        t = (x.get("time") or x.get("start") or "08:00")[:5]
        s0 = f"{x['date']}T{t if len(t) == 5 and t[2] == ':' else '08:00'}"
        pick.append((epoch(s0), epoch(s0) + 3600, f"Exam: {x['subject'] or x.get('text', '')}"))
    for h in hws[:2]:
        s0 = f"{h['due']}T08:00"
        pick.append((epoch(s0), epoch(s0) + 3600, f"HW: {h['subject']} {h['text']}".strip()))
    for e in lessons[: SCHOOL_MAX_EVENTS - len(pick)]:
        pick.append((epoch(e["start"]), epoch(e["end"]), f"{e['subject'] or 'Lesson'} {e['room']}".strip()))
    return sorted(pick)[:SCHOOL_MAX_EVENTS]


def notifications(model=None):
    """-> [(key, title, message)] from the same data the menu bar shows."""
    import menu_model
    m = model or menu_model.build()
    out = []
    for head, rows in m["sections"]:
        if head == "Alerts":
            out += [(f"alert:{r}", "Schedule change", r) for r in rows]
        elif head == "Today" and rows:
            out.append((f"today:{rows[0]}", "First lesson today", rows[0]))
    for hid, label in m["homework"]:
        if label.startswith("!") or " · " in label and label.split(" · ")[0] in ("today", "tomorrow"):
            out.append((f"hw:{hid}:{label}", "Homework", label.lstrip("! ")))
    return out


def _sent_path():
    return core.HOME / "pinetime-sent.json"


def unsent(items, state):
    """Only what the watch hasn't been told yet (state = {key-hash: day}); old keys expire after 3 days."""
    today = datetime.now().date().isoformat()
    seen = state.get("sent", {})
    return [(k, t, m) for k, t, m in items if hashlib.sha1(k.encode()).hexdigest()[:12] not in seen], today


def load_state():
    try:
        return json.loads(_sent_path().read_text())
    except (OSError, ValueError):
        return {"sent": {}}


def save_state(state, keys, today):
    sent = state.setdefault("sent", {})
    for k in keys:
        sent[hashlib.sha1(k.encode()).hexdigest()[:12]] = today
    for h, d in list(sent.items()):
        if (datetime.now().date() - datetime.fromisoformat(d).date()).days > 3:
            del sent[h]
    _sent_path().write_text(json.dumps(state))


def address():
    return core.config().get("pinetime_address")


def set_address(addr):
    core.save_config({**core.config(), "pinetime_address": addr})


def _bleak():
    try:
        import bleak
        return bleak
    except ImportError:
        raise RuntimeError("Bluetooth support is missing: run  pip install bleak")


async def scan(seconds=8):
    bleak = _bleak()
    found = await bleak.BleakScanner.discover(timeout=seconds)
    return [(d.address, d.name) for d in found if d.name and d.name.startswith(NAME_PREFIXES)]


async def push(addr, items, set_clock=True, timeout=20, events=None):
    bleak = _bleak()
    async with bleak.BleakClient(addr, timeout=timeout) as c:
        if set_clock:
            await c.write_gatt_char(CTS_CHAR, current_time_bytes(datetime.now()), response=True)
        if events is not None and c.services.get_characteristic(SCHOOL_CHAR):   # only the JWatch firmware has the School screen
            for i, (a, b, t) in enumerate(events):
                await c.write_gatt_char(SCHOOL_CHAR, event_bytes(i, len(events), a, b, t), response=True)
            if not events:   # nothing coming up: index 0 with total 0 empties the list
                await c.write_gatt_char(SCHOOL_CHAR, event_bytes(0, 0, 0, 0, ""), response=True)
        for _, title, msg in items:
            await c.write_gatt_char(ANS_NEW_ALERT, alert_bytes(title, msg), response=True)
            await asyncio.sleep(0.4)


def sync(test=False):
    """-> message. Sends the clock and any notifications the watch hasn't seen."""
    addr = address()
    if not addr:
        return "No watch set up. Run: ssc pinetime scan, then ssc pinetime pair <address>"
    state = load_state()
    items = [("test", "SSC", "Test notification")] if test else notifications()
    todo, today = (items, None) if test else unsent(items, state)
    try:
        events = None if test else school_events(core.state())
        asyncio.run(push(addr, todo, events=events))
    except Exception as e:  # BLE errors vary by platform; the watch may simply be out of range
        return f"Watch not reachable ({type(e).__name__}: {e})"
    if not test:
        save_state(state, [k for k, _, _ in todo], today)
    return f"Watch updated: clock set, {len(todo)} notification(s), {0 if events is None else len(events)} School entr{'y' if events is not None and len(events) == 1 else 'ies'}."
