"""What the menu bar (macOS) and tray icon (Windows / Linux) show. No GUI imports, so it is shared and testable."""
import core


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
    """' · HW 2!' = 2 homework items missed or due soon ('!' when any is already missed). Empty when all is well."""
    hot = sm["missed"] + sm["soon"]
    return f" · 📚{hot}{'!' if sm['missed'] else ''}" if hot else ""


def homework_label(h):
    return (("! " if h["status"] == "missed" else "") + f"{due_label(h)} · {h['subject']}: {h['text']}")[:64]


def homework_header(sm):
    parts = [f"{sm[k]} {lbl}" for k, lbl in (("missed", "missed"), ("soon", "due soon"), ("pending", "pending")) if sm[k]]
    return "Homework: " + " · ".join(parts) if parts else "Homework: all done 🎉"


def build(st=None):
    """-> dict(title, sections=[(heading, [rows])], homework_header, homework=[(id, label)], more)"""
    st = st or core.state()
    m = core.menu_summary(st)
    sm, active = st["homework_summary"], core.hw_active(st)
    sections = [(head, rows) for head, rows in (("Alerts", m["alerts"]), ("Today", m["today"]), ("Tomorrow", m["tomorrow"])) if rows]
    return dict(title=m["title"], badge=title_badge(sm), sections=sections, homework_header=homework_header(sm),
                homework=[(h["id"], homework_label(h)) for h in active[:10]], more=max(0, len(active) - 10))
