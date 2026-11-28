"""Accounts, roles and sessions for SSC Server.

Roles
  guest   Parent view, limited to the sections the admin allows for guests
  user    Parent view ("normal" account), the sections the admin allows for users
  admin   the full SSC app + the admin panel

Passwords are never stored: only salted scrypt hashes. Failed logins are throttled per account and per address.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time

import core

BASE_ROLES = ("guest", "user", "admin")          # what the installer and the global administrators work with
ROLES = BASE_ROLES + ("student",)                 # student: one profile in one space; admin with a space: that space's administrator
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_LEN = {"guest": 6, "user": 6, "admin": 6, "student": 6}
N, R, P = 2 ** 14, 8, 1


class AuthError(Exception):
    pass


def db():
    core.HOME.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(core.HOME / "accounts.db")
    c.executescript("""
        create table if not exists accounts(name text primary key, role text not null, pw text, created real, last_login real, disabled integer default 0);
        create table if not exists audit(id integer primary key autoincrement, at real, who text, event text, detail text, ip text);
    """)
    have = [r[1] for r in c.execute("pragma table_info(accounts)")]
    for col, kind in (("prefs", "text"),            # per-user language / theme / accent
                      ("expires", "real"),          # end of the last valid day (epoch seconds); null = never
                      ("sections", "text"),         # this account's own list of Parent sections ("" = the role's list)
                      ("profiles", "text"),         # the children/schools this account may see ("" = all of its space)
                      ("space", "text")):           # the space this account belongs to ("" = none: the server's own administrators and accounts)
        if col not in have:
            c.execute(f"alter table accounts add column {col} {kind}")
            c.commit()
    try:
        os.chmod(core.HOME / "accounts.db", 0o600)
    except OSError:
        pass
    return c


# ------------------------------------------------------------------ passwords
PBKDF2_ROUNDS = 600_000


def hash_password(password, salt=None):
    """scrypt where Python has it (Debian does); PBKDF2-SHA256 otherwise. The algorithm is part of the stored string."""
    salt = salt or secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=64 * 1024 * 1024)
        return f"scrypt${N}${R}${P}${salt.hex()}${h.hex()}"
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS, 32)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${h.hex()}"


def check_password(password, stored):
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt":
            _, n, r, p, salt, h = parts
            calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        elif parts[0] == "pbkdf2":
            _, rounds, salt, h = parts
            calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds), 32)
        else:
            return False
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def _check_name(name):
    if not NAME_RE.match(name or ""):
        raise AuthError("account names: 2-32 characters, lowercase letters, digits, . _ -")


def _check_password(role, password):
    if len(password or "") < MIN_LEN[role]:
        raise AuthError(f"a {role} password needs at least {MIN_LEN[role]} characters")
    if password.lower() in ("password", "123456", "1234567", "12345678", "123456789012", "passwordpassword", "qwerty", "111111", "000000", "abcdef", "654321"):
        raise AuthError("that password is far too common")


# ------------------------------------------------------------------ accounts
def _check_space(role, space):
    if space and space not in core.space_ids():
        raise AuthError("no such space")
    if role == "student" and not space:
        raise AuthError("a student belongs to a space")


def create(name, password, role, con=None, who="console", space=""):
    con = con or db()
    _check_name(name)
    if role not in ROLES:
        raise AuthError("role must be guest, user, student or admin")
    _check_space(role, space)
    _check_password(role, password)
    if con.execute("select 1 from accounts where name=?", (name,)).fetchone():
        raise AuthError(f"'{name}' already exists")
    con.execute("insert into accounts(name, role, pw, created, space) values(?,?,?,?,?)", (name, role, hash_password(password), time.time(), space or ""))
    con.commit()
    audit(con, who, "account-created", f"{name} ({role}{', space ' + space if space else ''})")


def accounts(con=None):
    con = con or db()
    return [dict(name=n, role=r, created=c, last_login=l, disabled=bool(d), expires=e,
                 sections=[x for x in (s or "").split(",") if x], profiles=[x for x in (p or "").split(",") if x], space=sp or "")
            for n, r, c, l, d, e, s, p, sp in con.execute("select name, role, created, last_login, disabled, expires, sections, profiles, space from accounts order by role desc, name")]


def accounts_in(space, con=None):
    """The accounts of one space ('' = the ones that belong to no space)."""
    return [a for a in accounts(con) if a["space"] == (space or "")]


def space_of(name, con=None):
    row = (con or db()).execute("select space from accounts where name=?", (name,)).fetchone()
    return (row[0] or "") if row else None


def count_global_admins(con):
    return con.execute("select count(*) from accounts where role='admin' and disabled=0 and coalesce(space,'')=''").fetchone()[0]


def set_space(name, space, con=None, who="console"):
    """Move an account into a space ('' = out of every space). Its profile list is cleared; its open sessions end."""
    con = con or db()
    row = con.execute("select role, coalesce(space,'') from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError("no such account")
    _check_space(row[0], space)
    if row[0] == "admin" and not row[1] and space and count_global_admins(con) <= 1:
        raise AuthError("you can't move the last server administrator into a space")
    con.execute("update accounts set space=?, profiles='' where name=?", (space or "", name))
    con.commit()
    audit(con, who, "account-space", f"{name} -> {space or '(none)'}")
    drop_sessions(name)


def expiry_epoch(day):
    """'2026-12-31' -> the end of that day (local time) as epoch seconds; '' / None -> None (never)."""
    if not day:
        return None
    import datetime
    try:
        d = datetime.date.fromisoformat(str(day).strip())
    except ValueError:
        raise AuthError("the end date looks like 2026-12-31")
    return time.mktime((d + datetime.timedelta(days=1)).timetuple()) - 1


def set_limits(name, expires=None, sections=None, profiles=None, con=None, who="console", all_sections=(), all_profiles=()):
    """Per account: until when it works, which Parent sections it sees, which children it sees. None = leave as is."""
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError(f"no account '{name}'")
    if row[0] == "admin" and not auth_space(con, name) and (expires or sections or profiles):
        raise AuthError("the server's administrators can't be limited (that would risk locking everybody out)")
    parts, args = [], []
    if expires is not None:
        parts.append("expires=?")
        args.append(expiry_epoch(expires))
    if sections is not None:
        secs = [s for s in sections if s]
        if any(s not in all_sections for s in secs):
            raise AuthError("sections: choose from " + ", ".join(all_sections))
        parts.append("sections=?")
        args.append(",".join(secs))
    if profiles is not None:
        ids = [p for p in profiles if p]
        if any(p not in all_profiles for p in ids):
            raise AuthError("unknown child/profile")
        parts.append("profiles=?")
        args.append(",".join(ids))
    if parts:
        con.execute(f"update accounts set {', '.join(parts)} where name=?", (*args, name))
        con.commit()
        audit(con, who, "account-limits", f"{name}: " + ", ".join(p.split("=")[0] for p in parts))


def limits_for(name, con=None):
    """-> {'expires': epoch|None, 'sections': [..], 'profiles': [..]} (empty lists = no limit)."""
    con = con or db()
    row = con.execute("select expires, sections, profiles from accounts where name=?", (name,)).fetchone()
    if not row:
        return {"expires": None, "sections": [], "profiles": []}
    return {"expires": row[0], "sections": [x for x in (row[1] or "").split(",") if x], "profiles": [x for x in (row[2] or "").split(",") if x]}


def expired(name, con=None, now=None):
    e = limits_for(name, con)["expires"]
    return bool(e) and (now or time.time()) > e


def delete(name, con=None, who="console"):
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError("no such account")
    if row[0] == "admin" and not auth_space(con, name) and count_global_admins(con) <= 1:
        raise AuthError("you can't delete the last admin")
    con.execute("delete from accounts where name=?", (name,))
    con.commit()
    audit(con, who, "account-deleted", name)
    drop_sessions(name)


def change_own_password(name, current, new, con=None, ip=""):
    """A signed-in account changes its own password: the current one must be right (and wrong guesses are throttled like logins)."""
    con = con or db()
    now = time.time()
    keys = [("ip", ip), ("pw:" + name, name)]
    if _blocked(keys, now):
        raise AuthError("Too many attempts. Wait a little and try again.")
    row = con.execute("select role, pw from accounts where name=?", (name,)).fetchone()
    if not row or not check_password(current, row[1]):
        for k in keys:
            _FAILS.setdefault(k, []).append(now)
        audit(con, name, "password-change-failed", "", ip)
        raise AuthError("The current password is wrong.")
    set_password(name, new, con, who=name)


def set_password(name, password, con=None, who="console"):
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError("no such account")
    _check_password(row[0], password)
    con.execute("update accounts set pw=? where name=?", (hash_password(password), name))
    con.commit()
    audit(con, who, "password-changed", name)
    drop_sessions(name)


def set_role(name, role, con=None, who="console"):
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row or role not in ROLES:
        raise AuthError("no such account or role")
    sp = con.execute("select coalesce(space,'') from accounts where name=?", (name,)).fetchone()[0]
    if row[0] == "admin" and not sp and role != "admin" and count_global_admins(con) <= 1:
        raise AuthError("you can't demote the last admin")
    _check_space(role, sp)
    con.execute("update accounts set role=? where name=?", (role, name))
    con.commit()
    audit(con, who, "role-changed", f"{name} -> {role}")
    drop_sessions(name)


def set_disabled(name, disabled, con=None, who="console"):
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError("no such account")
    if disabled and row[0] == "admin" and not auth_space(con, name) and count_global_admins(con) <= 1:
        raise AuthError("you can't disable the last admin")
    con.execute("update accounts set disabled=? where name=?", (int(disabled), name))
    con.commit()
    audit(con, who, "account-disabled" if disabled else "account-enabled", name)
    if disabled:
        drop_sessions(name)


def get_prefs(name, con=None):
    import json
    row = (con or db()).execute("select prefs from accounts where name=?", (name,)).fetchone()
    try:
        return core.clean_prefs(json.loads(row[0])) if row and row[0] else {}
    except ValueError:
        return {}


def set_prefs(name, prefs, con=None):
    """Merge into the stored preferences of one account (a user only ever changes their own)."""
    import json
    con = con or db()
    cur = get_prefs(name, con)
    cur.update(core.clean_prefs(prefs))
    con.execute("update accounts set prefs=? where name=?", (json.dumps(cur), name))
    con.commit()
    return cur


def auth_space(con, name):
    return (con.execute("select coalesce(space,'') from accounts where name=?", (name,)).fetchone() or [""])[0]


def has_admin(con=None):
    return count_global_admins(con or db()) > 0


# ------------------------------------------------------------------ audit + throttling
def audit(con, who, event, detail="", ip=""):
    con.execute("insert into audit(at, who, event, detail, ip) values(?,?,?,?,?)", (time.time(), who, event, detail, ip))
    con.execute("delete from audit where id <= (select max(id) from audit) - 2000")
    con.commit()


def audit_log(con=None, limit=100):
    con = con or db()
    return [dict(at=a, who=w, event=e, detail=d, ip=i) for a, w, e, d, i in
            con.execute("select at, who, event, detail, ip from audit order by id desc limit ?", (limit,))]


_FAILS = {}   # key -> [timestamps]


def _blocked(keys, now):
    for k in keys:
        _FAILS[k] = [t for t in _FAILS.get(k, []) if now - t < 900]
        n = len(_FAILS[k])
        if n >= 5 and now - _FAILS[k][-1] < min(900, 2 ** (n - 4) * 5):   # 5 free tries, then 10 s, 20 s, 40 s ... up to 15 min
            return True
    return False


def reset_throttle():
    _FAILS.clear()


# ------------------------------------------------------------------ login + sessions
SESSIONS = {}


def login(name, password, ip="", con=None, hours=12):
    """-> session id. Raises AuthError (same message for every failure: no hint which part was wrong)."""
    con = con or db()
    now = time.time()
    keys = [("ip", ip), ("name", name)]
    if _blocked(keys, now):
        audit(con, name, "login-throttled", "", ip)
        raise AuthError("Too many attempts. Wait a little and try again.")
    row = con.execute("select role, pw, disabled, expires, coalesce(space,'') from accounts where name=?", (name,)).fetchone()
    ok = bool(row) and not row[2] and check_password(password, row[1])
    if not row:
        hash_password(password)   # similar work for unknown names (no timing hint)
    if not ok:
        for k in keys:
            _FAILS.setdefault(k, []).append(now)
        audit(con, name, "login-failed", "", ip)
        raise AuthError("Wrong name or password.")
    for k in keys:
        _FAILS.pop(k, None)
    if row[3] and now > row[3]:                      # the password was right, so saying why is fine
        audit(con, name, "login-expired", "", ip)
        raise AuthError("This account has expired. Ask the administrator.")
    con.execute("update accounts set last_login=? where name=?", (now, name))
    con.commit()
    audit(con, name, "login", row[0], ip)
    return new_session(name, row[0], ip, hours, space=row[4])


def new_session(name, role, ip="", hours=12, anon=False, space=""):
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = dict(name=name, role=role, ip=ip, at=time.time(), exp=time.time() + hours * 3600, anon=anon, space=space)
    return sid


def guest_session(ip="", hours=12):
    """Passwordless guest access (only if the admin turned it on)."""
    return new_session("guest", "guest", ip, hours, anon=True)


def session(sid):
    s = SESSIONS.get(sid or "")
    if not s:
        return None
    if s["exp"] < time.time():
        SESSIONS.pop(sid, None)
        return None
    if not s.get("anon") and (s["role"] != "admin" or s.get("space")) and expired(s["name"]):          # an expiry also ends sessions that are open
        SESSIONS.pop(sid, None)
        return None
    return s


def logout(sid):
    SESSIONS.pop(sid or "", None)


def list_sessions():
    """Who is signed in right now (no session ids: those are secrets)."""
    now = time.time()
    return sorted((dict(name=v["name"], role=v["role"], ip=v["ip"], since=v.get("at"), expires=v["exp"], anonymous=bool(v.get("anon"))) for v in SESSIONS.values() if v["exp"] > now),
                  key=lambda x: (x["name"], x["since"] or 0))


def drop_sessions(name):
    for k in [k for k, v in SESSIONS.items() if v["name"] == name]:
        SESSIONS.pop(k, None)
