"""E-mail from the server: changes (substitutions, new homework, exams ...), a daily digest, and health alerts.

Plain SMTP with STARTTLS or SSL and a verified certificate. The SMTP password is a secret that has to be readable again, so
it lives in the vault (a root/ssc-only file on the server) - never in server.json, never shown, never in a backup.
WebUntis messages (the private inbox) are never mailed."""
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import core
import serverconf

VAULT_KEY = "smtp-password"
MAX_LINES = 40


class MailError(Exception):
    pass


def _vault():
    import os
    os.environ.setdefault("SSC_VAULT", "file")
    import vault
    return vault


def set_password(pw):
    v = _vault()
    if pw:
        v.set(VAULT_KEY, str(pw))
    else:
        v.delete(VAULT_KEY)


def has_password():
    try:
        return bool(_vault().get(VAULT_KEY))
    except Exception:
        return False


def _clean(s, n=200):
    return " ".join(str(s).split())[:n]            # no line breaks in anything that ends up in a header


def build_message(m, subject, body):
    msg = EmailMessage()
    msg["Subject"] = "[SSC] " + _clean(subject)
    msg["From"] = m["sender"]
    msg["To"] = ", ".join(m["to"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="ssc.invalid")
    msg.set_content(body)
    return msg


def recipients(cfg, pid):
    """Who gets the mails about this profile. A profile inside a space: only the addresses its own people set (never the server's list -
    one space must not receive another space's data). A profile outside spaces: its own list if it has one, else the server's list."""
    own = list((core.config().get("profile_mail") or {}).get(pid) or [])
    if core.profile_space(pid):
        return own
    return own or list(cfg["mail"]["to"])


def send(cfg, subject, body, smtp_class=None, password=None, to=None):
    """Send one mail to all recipients (`to`, or the server's list). Raises MailError with a readable reason."""
    m = dict(cfg["mail"], to=list(to) if to is not None else cfg["mail"]["to"])
    if not (m["host"] and m["to"] and m["sender"]):
        raise MailError("the mail settings are incomplete (server, sender, recipient)")
    pw = password if password is not None else (_vault().get(VAULT_KEY) if m["user"] else None)
    if m["user"] and not pw:
        raise MailError("no mail password is stored - enter it in the mail settings")
    ctx = ssl.create_default_context()
    try:
        cls = smtp_class or (smtplib.SMTP_SSL if m["security"] == "ssl" else smtplib.SMTP)
        s = cls(m["host"], m["port"], timeout=20, **({"context": ctx} if m["security"] == "ssl" and not smtp_class else {}))
        try:
            if m["security"] == "starttls":
                s.starttls(context=ctx)
            if m["user"]:
                s.login(m["user"], pw)
            s.send_message(build_message(m, subject, body), from_addr=m["sender"], to_addrs=m["to"])
        finally:
            try:
                s.quit()
            except Exception:
                pass
    except smtplib.SMTPAuthenticationError:
        raise MailError("the mail server refused the user name or password")
    except ssl.SSLError as e:
        raise MailError("the connection could not be made secure (certificate or security setting): " + _clean(e, 120))
    except (smtplib.SMTPException, OSError) as e:
        raise MailError("could not send: " + _clean(e, 160))


# ------------------------------------------------------------------ what to say
def _label(pid):
    labels = {p["id"]: p["label"] for p in core.profile_list()}
    return labels.get(pid, pid) if len(labels) > 1 else ""


def format_changes(changes, pid, kinds):
    """-> (subject, body) or None when nothing the recipients asked about changed."""
    rows = [(k, t) for k, t in changes if k in kinds and k != "message"]
    if not rows:
        return None
    who = _label(pid)
    head = {"timetable": "Timetable", "homework": "Homework", "exam": "Exams", "grade": "Grades"}
    lines = [f"Changes for {who}:" if who else "Changes:", ""]
    for kind in ("timetable", "homework", "exam", "grade"):
        part = [t for k, t in rows if k == kind]
        if part:
            lines += [head[kind] + ":"] + ["  - " + _clean(t, 300) for t in part[:MAX_LINES]]
            if len(part) > MAX_LINES:
                lines.append(f"  ... and {len(part) - MAX_LINES} more")
            lines.append("")
    n = len(rows)
    return f"{n} change{'s' if n != 1 else ''}" + (f" - {who}" if who else ""), "\n".join(lines).rstrip() + "\n"


def after_sync(changes, pid):
    cfg = serverconf.load()
    m = cfg["mail"]
    if not m["enabled"]:
        return
    msg = format_changes(changes, pid, m["kinds"])
    to = recipients(cfg, pid)
    if msg and to:
        send(cfg, *msg, to=to)


def register():
    if after_sync not in core.HOOKS:
        core.HOOKS.append(after_sync)


def _entries(st, day):
    out = []
    for wk in st["timetable"].values():
        out += [e for e in wk if e["date"] == day]
    return sorted(out, key=lambda e: e["start"])


def digest_text(st, pid, today):
    """Tomorrow (or the next day with lessons): timetable, homework due soon, exams in the next two weeks."""
    who = _label(pid)
    lines = [f"Summary for {who}" if who else "Summary", ""]
    day = None
    for i in range(1, 8):
        d = (today + timedelta(days=i)).isoformat()
        if _entries(st, d):
            day = d
            break
    if day:
        lines.append(f"Lessons on {day}:")
        for e in _entries(st, day):
            note = " (CANCELLED)" if e["cancelled"] else ""
            lines.append(f"  {e['start'][11:16]}  {e['subject'] or 'Lesson'}  {e['room'] or ''}  {e['teacher'] or ''}{note}".rstrip())
    else:
        lines.append("No lessons in the next days.")
    lines.append("")
    hw = [h for h in st["homework"] if not h["done"] and not h["dismissed"] and h["due"] and h["due"][:10] <= (today + timedelta(days=7)).isoformat()]
    lines.append("Homework due in the next 7 days:" if hw else "No homework due in the next 7 days.")
    lines += [f"  - {h['subject']}: {_clean(h['text'], 120)} (due {h['due'][:10]})" for h in sorted(hw, key=lambda h: h["due"])[:MAX_LINES]]
    lines.append("")
    ex = [e for e in st["exams"] if e["date"] and today.isoformat() <= e["date"][:10] <= (today + timedelta(days=14)).isoformat()]
    lines.append("Exams in the next 14 days:" if ex else "No exams in the next 14 days.")
    lines += [f"  - {e['date'][:10]}  {e['subject']} {_clean(e['text'], 80)}".rstrip() for e in sorted(ex, key=lambda e: e["date"])[:MAX_LINES]]
    return "\n".join(lines).rstrip() + "\n"


def marker_path():
    return serverconf.state_dir() / "digest.json"


def digest_due(cfg, now=None):
    """True once per day, from the configured time on."""
    m = cfg["mail"]
    now = now or datetime.now()
    if not (m["enabled"] and m["digest"]) or now.strftime("%H:%M") < m["digest_time"]:
        return False
    try:
        import json
        return json.loads(marker_path().read_text()).get("day") != now.date().isoformat()
    except (OSError, ValueError):
        return True


def send_digest(cfg, now=None):
    """One mail per recipient group, with a section for each profile that group may read. Marks the day as done once all went out."""
    import json
    now = now or datetime.now()
    groups = {}
    keep = getattr(core._PROFILE, "id", None)
    try:
        for pid in core.profile_ids():
            core.set_profile(pid)
            to = tuple(recipients(cfg, pid))
            if to and core.db_path().exists():
                groups.setdefault(to, []).append(digest_text(core.state(core.db()), pid, now.date()))
    finally:
        core._PROFILE.id = keep
    for to, parts in groups.items():
        send(cfg, "Daily summary", "\n\n".join(parts), to=list(to))
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"day": now.date().isoformat()}))
    return sum(len(v) for v in groups.values())


def send_alerts(cfg, fresh):
    if not (cfg["mail"]["enabled"] and cfg["mail"]["alerts"] and fresh):
        return
    send(cfg, "Server needs attention" if any(a["level"] == "crit" for a in fresh) else "Server notice",
         "The SSC server reports:\n\n" + "\n".join(f"  - {a['text']}" for a in fresh) + "\n")


def send_test(cfg):
    send(cfg, "Test mail", "This is a test from your SSC Server. If you can read this, mail works.\n")
