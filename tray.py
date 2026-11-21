"""System tray icon for Windows and Linux (the macOS menu bar app is menubar.py). Same content as the menu bar:
next lesson + homework badge in the tooltip, today / tomorrow / alerts / homework in the menu; click a homework entry
to mark it done or dismiss it."""
import subprocess
import threading
from pathlib import Path

import core
import menu_model


def icon_image():
    from PIL import Image
    for p in (core.APPDIR / "icon.png", Path(__file__).resolve().parent / "assets" / "icon.png"):
        if p.exists():
            return Image.open(p).convert("RGBA").resize((64, 64))
    return Image.new("RGBA", (64, 64), (20, 120, 200, 255))


def run():
    import pystray

    state = {"icon": None}

    def act(fn):
        def inner(icon, item):
            fn()
            refresh()
        return inner

    def build_menu():
        try:
            m = menu_model.build()
        except Exception as e:  # keep the icon alive; show the problem
            return pystray.Menu(pystray.MenuItem(f"error: {e}"[:80], None, enabled=False),
                                pystray.MenuItem("Open SSC", lambda i, it: open_gui()))
        items = []
        for head, rows in m["sections"]:
            items.append(pystray.MenuItem(head, None, enabled=False))
            items += [pystray.MenuItem("  " + r, None, enabled=False) for r in rows]
            items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem(m["homework_header"], None, enabled=False))
        for hid, label in m["homework"]:
            sub = pystray.Menu(
                pystray.MenuItem("Mark as done", act(lambda h=hid: core.set_done(core.db(), h, True))),
                pystray.MenuItem("Dismiss", act(lambda h=hid: core.hw_set_dismiss(core.db(), h, "dismissed"))))
            items.append(pystray.MenuItem("  " + label, sub))
        if m["more"]:
            items.append(pystray.MenuItem(f"  … and {m['more']} more", None, enabled=False))
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Sync now", lambda i, it: sync()),
                  pystray.MenuItem("Open SSC", lambda i, it: open_gui()), pystray.MenuItem("Quit", lambda i, it: i.stop())]
        return pystray.Menu(*items)

    def title_text():
        try:
            m = menu_model.build()
            return ((m["title"] + m["badge"]).strip() or "SSC")[:120]
        except Exception:
            return "SSC"

    def refresh():
        icon = state["icon"]
        if icon is not None:
            icon.title = title_text()
            icon.menu = build_menu()
            icon.update_menu()

    def sync():
        subprocess.Popen(core.self_cmd("sync", "--notify"), stdin=subprocess.DEVNULL)

    def open_gui():
        subprocess.Popen(core.self_cmd("gui", "--no-sync"), stdin=subprocess.DEVNULL)

    def ticker():
        stop = threading.Event()
        while not stop.wait(60):
            refresh()

    icon = pystray.Icon("ssc", icon_image(), title_text(), menu=build_menu())
    state["icon"] = icon
    threading.Thread(target=ticker, daemon=True).start()
    icon.run()
