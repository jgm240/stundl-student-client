"""Terminal UI for Stundl Student Client (SSC). Reads only from the local database (works offline)."""
from datetime import datetime

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane

import core


class SSCTUI(App):
    TITLE = "Stundl Student Client"
    BINDINGS = [("q", "quit", "Quit"), ("[", "week(-1)", "Prev week"), ("]", "week(1)", "Next week"),
                ("d", "toggle_done", "Toggle done"), ("o", "toggle_open", "Active/all homework"), ("x", "dismiss", "Dismiss/restore"),
                ("r", "reload", "Reload")]

    def __init__(self):
        super().__init__()
        self.st, self.wk, self.open_only = core.state(), None, True  # True = only homework that still needs attention

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="tt"):
            for tid, name in [("tt", "Timetable"), ("hw", "Homework"), ("ex", "Exams"), ("gr", "Grades"),
                              ("ab", "Absences"), ("pl", "Plan"), ("pe", "Teachers"), ("ch", "Changes"), ("ms", "Messages"), ("se", "Search")]:
                with TabPane(name, id=tid):
                    if tid == "tt":
                        yield Static(id="ttlabel")
                    if tid == "gr":
                        yield Static(id="grlabel")
                    if tid == "se":
                        yield Input(placeholder="search lessons, homework, exams, messages, grades...", id="q")
                    yield DataTable(id="t_" + tid, zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self):
        self.fill()

    def action_reload(self):
        self.st = core.state(); self.fill()

    def action_week(self, n):
        ws = sorted(self.st["timetable"])
        if self.wk in ws and 0 <= ws.index(self.wk) + n < len(ws):
            self.wk = ws[ws.index(self.wk) + n]; self.fill()

    def action_toggle_open(self):
        self.open_only = not self.open_only; self.fill()

    def action_toggle_done(self):
        t = self.query_one("#t_hw", DataTable)
        if self.query_one(TabbedContent).active != "hw" or not t.row_count:
            return
        hid = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        con = core.db()
        core.set_done(con, hid, hid not in core.done_ids(con))
        self.st = core.state(con); self.fill()

    def action_dismiss(self):
        t = self.query_one("#t_hw", DataTable)
        if self.query_one(TabbedContent).active != "hw" or not t.row_count:
            return
        hid = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        h = next(x for x in self.st["homework"] if x["id"] == hid)
        con = core.db()
        core.hw_set_dismiss(con, hid, "restored" if h["dismissed"] else "dismissed")
        self.st = core.state(con); self.fill()

    def on_input_submitted(self, ev):
        t = self.query_one("#t_se", DataTable)
        t.clear(columns=True); t.add_columns("When", "Type", "Title", "Text")
        for r in core.search(ev.value):
            t.add_row(r["date"][:16], r["kind"], r["title"], r["text"])

    def table(self, tid, cols):
        t = self.query_one("#t_" + tid, DataTable)
        t.clear(columns=True); t.add_columns(*cols)
        return t

    def fill(self):
        st = self.st
        ws = sorted(st["timetable"])
        if ws and self.wk not in ws:
            today = datetime.now().date().isoformat()
            self.wk = next((w for w in reversed(ws) if w <= today), ws[0])
        self.sub_title = ("last sync " + datetime.fromtimestamp(st["synced_at"]).strftime("%d.%m. %H:%M")
                          if st["synced_at"] else "no data - run sync")

        t = self.table("tt", ["Date", "Time", "Subject", "Teacher", "Room", "Status / note"])
        self.query_one("#ttlabel", Static).update(f" Week of {self.wk}   ([ / ] to change)" if self.wk else " no data")
        for e in st["timetable"].get(self.wk, []):
            t.add_row(e["date"], e["start"][11:16] + "-" + e["end"][11:16], e["subject"], e["teacher"], e["room"],
                      ("CANCELLED " if e["cancelled"] else "") + e["info"])

        sm = st["homework_summary"]
        t = self.table("hw", ["Status", "Due", "Subject", "Task"])
        order = {"missed": 0, "soon": 1, "pending": 2, "done": 3}
        for x in sorted(st["homework"], key=lambda h: (order[h["status"]], h["due"])):
            if self.open_only and (x["dismissed"] or x["status"] == "done"):
                continue
            label = {"missed": "MISSED", "soon": "due soon", "pending": "pending", "done": "done"}[x["status"]] + (" (dismissed)" if x["dismissed"] else "")
            t.add_row(label, x["due"], x["subject"], x["text"], key=x["id"])
        self.sub_title = (self.sub_title + f" | homework: {sm['missed']} missed, {sm['soon']} soon, {sm['pending']} pending").strip()

        t = self.table("ex", ["Date", "Time", "Subject", "Text"])
        for e in st["exams"]:
            t.add_row(e["date"], e["start"], e["subject"], e["text"])

        t = self.table("gr", ["Subject", "Average", "Marks"])
        for g in st["stats"]["grades"]:
            t.add_row(g["subject"], str(g["average"] or "-"), ", ".join(map(str, g["marks"])))
        ab = st["stats"]["absences"]
        self.query_one("#grlabel", Static).update(
            f" Absences: {ab['total']} ({ab['excused']} excused, {ab['open']} open), {ab['days']} days. Missed lessons: "
            + (", ".join(f"{l['subject']} {l['pct']:.0%} ({l['level']})" for l in ab["limits"][:8]) or "-"))

        t = self.table("ab", ["From", "To", "Excused", "Reason"])
        for a in st["absences"]:
            t.add_row(a["start"], a["end"], "yes" if a["excused"] else "no", a["reason"])

        t = self.table("pl", ["Date", "Time", "Done", "Subject", "Task", "Min"])
        for p in st["plan"]:  # edit in the app or with `plan add / edit / done / rm`
            t.add_row(p["date"], p["start"], "[x]" if p["done"] else "[ ]", p["subject"], p["title"], str(p["minutes"] or ""))

        t = self.table("pe", ["Teacher", "Subjects", "Rooms", "Lessons", "Next"])
        for p in st["people"]:
            t.add_row(p["name"], ", ".join(p["subjects"]), ", ".join(p["rooms"]), str(p["lessons"]), p["next"])

        t = self.table("ch", ["When", "Type", "Change"])
        for c in st["changes"]:
            t.add_row(datetime.fromtimestamp(c["at"]).strftime("%d.%m. %H:%M"), c["kind"], c["text"])

        t = self.table("ms", ["Sent", "From", "Subject", "Preview"])
        for m in st["messages"]:
            t.add_row(m["sent"], m["sender"], m["subject"], m["preview"])
        self.table("se", ["When", "Type", "Title", "Text"])


def run():
    SSCTUI().run()
