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

ROLES = ("guest", "user", "admin")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_LEN = {"guest": 6, "user": 6, "admin": 6}
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
    if "prefs" not in [r[1] for r in c.execute("pragma table_info(accounts)")]:
        c.execute("alter table accounts add column prefs text")          # per-user language / theme / accent
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
def create(name, password, role, con=None, who="console"):
    con = con or db()
    _check_name(name)
    if role not in ROLES:
        raise AuthError("role must be guest, user or admin")
    _check_password(role, password)
    if con.execute("select 1 from accounts where name=?", (name,)).fetchone():
        raise AuthError(f"'{name}' already exists")
    con.execute("insert into accounts(name, role, pw, created) values(?,?,?,?)", (name, role, hash_password(password), time.time()))
    con.commit()
    audit(con, who, "account-created", f"{name} ({role})")


def accounts(con=None):
    con = con or db()
    return [dict(name=n, role=r, created=c, last_login=l, disabled=bool(d))
            for n, r, c, l, d in con.execute("select name, role, created, last_login, disabled from accounts order by role desc, name")]


def delete(name, con=None, who="console"):
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError("no such account")
    if row[0] == "admin" and con.execute("select count(*) from accounts where role='admin' and disabled=0").fetchone()[0] <= 1:
        raise AuthError("you can't delete the last admin")
    con.execute("delete from accounts where name=?", (name,))
    con.commit()
    audit(con, who, "account-deleted", name)
    drop_sessions(name)


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
    if row[0] == "admin" and role != "admin" and con.execute("select count(*) from accounts where role='admin' and disabled=0").fetchone()[0] <= 1:
        raise AuthError("you can't demote the last admin")
    con.execute("update accounts set role=? where name=?", (role, name))
    con.commit()
    audit(con, who, "role-changed", f"{name} -> {role}")
    drop_sessions(name)


def set_disabled(name, disabled, con=None, who="console"):
    con = con or db()
    row = con.execute("select role from accounts where name=?", (name,)).fetchone()
    if not row:
        raise AuthError("no such account")
    if disabled and row[0] == "admin" and con.execute("select count(*) from accounts where role='admin' and disabled=0").fetchone()[0] <= 1:
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


def has_admin(con=None):
    return (con or db()).execute("select count(*) from accounts where role='admin' and disabled=0").fetchone()[0] > 0


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
    row = con.execute("select role, pw, disabled from accounts where name=?", (name,)).fetchone()
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
    con.execute("update accounts set last_login=? where name=?", (now, name))
    con.commit()
    audit(con, name, "login", row[0], ip)
    return new_session(name, row[0], ip, hours)


def new_session(name, role, ip="", hours=12, anon=False):
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = dict(name=name, role=role, ip=ip, exp=time.time() + hours * 3600, anon=anon)
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
    return s


def logout(sid):
    SESSIONS.pop(sid or "", None)


def drop_sessions(name):
    for k in [k for k, v in SESSIONS.items() if v["name"] == name]:
        SESSIONS.pop(k, None)
