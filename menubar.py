"""macOS menu bar widget: school icon + next lesson (+ homework badge) in the bar; today, tomorrow, alerts and your
homework in the menu. Click a homework entry to mark it done or dismiss it."""
import subprocess
from pathlib import Path

import rumps

import core


def icon_path():
    """The template icon: bundled in the packaged app, in assets/ when run from source."""
    for p in (core.APPDIR / "menubar.png", Path(__file__).resolve().parent / "assets" / "menubar.png"):
        if p.exists():
            return str(p)
    return None


def due_label(h):
    d = h.get("days_left")
    if d is None:
        return h["due"][5:]
    if d == 0:
        return "today"
    if d == 1:
        return "tomorrow"
    if d > 1:
        return f"in {d} d"
    return f"{-d} d overdue"


def title_badge(sm):
    """' · 📚2!' = 2 homework items missed or due soon ('!' when any is already missed). Empty when all is well."""
    hot = sm["missed"] + sm["soon"]
    return f" · 📚{hot}{'!' if sm['missed'] else ''}" if hot else ""


def homework_label(h):
    return (("! " if h["status"] == "missed" else "") + f"{due_label(h)} · {h['subject']}: {h['text']}")[:64]


def homework_header(sm):
    parts = [f"{sm[k]} {lbl}" for k, lbl in (("missed", "missed"), ("soon", "due soon"), ("pending", "pending")) if sm[k]]
    return "Homework: " + " · ".join(parts) if parts else "Homework: all done 🎉"


class SSCBar(rumps.App):
    def __init__(self):
        ic = icon_path()
        super().__init__(core.APP_NAME, title="SSC", icon=ic, template=True if ic else None, quit_button="Quit")
        self.timer = rumps.Timer(self.refresh, 60)
        self.timer.start()
        self.refresh(None)

    def refresh(self, _):
        try:
            st = core.state()
            m = core.menu_summary(st)
            sm, active = st["homework_summary"], core.hw_active(st)
        except Exception as e:
            self.title = "!"
            self.menu.clear()
            self.menu = [rumps.MenuItem(f"error: {e}"[:80]), None, rumps.MenuItem("Open SSC", callback=self.open_gui)]
            return
        base = "" if (m["title"] == "SSC" and self.icon) else m["title"]  # the icon already identifies the app
        self.title = (base + title_badge(sm)).removeprefix(" · ") if hasattr(str, "removeprefix") else (base + title_badge(sm)).lstrip(" ·")
        items = []
        for head, rows in (("Alerts", m["alerts"]), ("Today", m["today"]), ("Tomorrow", m["tomorrow"])):
            if rows:
                items += [rumps.MenuItem(head)] + [rumps.MenuItem("  " + r) for r in rows] + [None]
        items.append(rumps.MenuItem(homework_header(sm)))
        for h in active[:10]:
            entry = rumps.MenuItem("  " + homework_label(h))
            entry.add(rumps.MenuItem("Mark as done", callback=lambda _, i=h["id"]: self.hw_done(i)))
            entry.add(rumps.MenuItem("Dismiss", callback=lambda _, i=h["id"]: self.hw_dismiss(i)))
            items.append(entry)
        if len(active) > 10:
            items.append(rumps.MenuItem(f"  … and {len(active) - 10} more"))
        items += [None, rumps.MenuItem("Sync now", callback=self.sync), rumps.MenuItem("Open SSC", callback=self.open_gui)]
        self.menu.clear()
        self.menu = items

    def hw_done(self, hw_id):
        core.set_done(core.db(), hw_id, True)
        self.refresh(None)

    def hw_dismiss(self, hw_id):
        core.hw_set_dismiss(core.db(), hw_id, "dismissed")
        self.refresh(None)

    def sync(self, _):
        subprocess.Popen(core.self_cmd("sync", "--notify"), stdin=subprocess.DEVNULL)

    def open_gui(self, _):
        subprocess.Popen(core.self_cmd("gui", "--no-sync"), stdin=subprocess.DEVNULL)


def run():
    try:  # menu bar only: no Dock icon even though the packaged app is a regular app
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass
    SSCBar().run()
