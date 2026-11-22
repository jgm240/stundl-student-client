"""Updates SSC from its GitHub releases.

Nothing here runs by itself: the user presses "Check for updates" (or runs `ssc update`). It only talks to this project's
repository over HTTPS and every redirect has to stay on GitHub's own hosts.

  desktop app   downloads the release file for this system and refuses to keep it unless its SHA-256 matches the release's
                SHA256SUMS - you then replace the app yourself (a running program can't replace itself safely)
  SSC Server    installs the release's code into /opt/ssc: staged, compile-checked, import-checked, then swapped in with the old
                copy kept for a roll-back, and the service restarted

Honest limit: the server update trusts GitHub's TLS and this repository (a source archive has no signature or checksum of its own).
"""
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import ssc_version

REPO = "jgm240/stundl-student-client"
API = "https://api.github.com"
TRUSTED_HOSTS = ("api.github.com", "github.com", "codeload.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")
UA = "SSC-updater"
MAX_DOWNLOAD = 1_500_000_000
_TEST_ALLOW_LOCAL = False          # tests point the updater at a local fake GitHub; never set outside tests

TAG_OK = re.compile(r"^v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$")

# What a server install consists of (the same files the image build copies). Nothing else is ever written.
ALLOWED = ["core.py", "ssc.py", "vault.py", "jobs.py", "untis_direct.py", "ssc_imgen.py", "ssc_version.py", "updater.py", "menu_model.py",
           "tui.py", "ui.js", "index.html", "parent.html", "LICENSE", "assets/icon.png", "server/*.py", "server/*.html"]
REQUIRED = ["ssc.py", "core.py", "ssc_version.py", "updater.py", "ui.js", "index.html", "parent.html", "server/serverapp.py", "server/ssconfig.py",
            "server/serverconf.py", "server/auth.py", "server/applycfg.py", "server/login.html", "server/admin.html"]


class UpdateError(Exception):
    pass


# ------------------------------------------------------------------ talking to GitHub (and only GitHub)
def _trusted(url):
    u = urllib.parse.urlparse(url)
    if _TEST_ALLOW_LOCAL and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"):
        return True
    return u.scheme == "https" and (u.hostname or "") in TRUSTED_HOSTS


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted(newurl):
            raise UpdateError(f"refused a redirect to {urllib.parse.urlparse(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, timeout=30):
    if not _trusted(url):
        raise UpdateError("refused to contact " + (urllib.parse.urlparse(url).hostname or "that address"))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json, application/octet-stream"})
    try:
        return urllib.request.build_opener(_Redirects()).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub answered {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"can't reach GitHub: {getattr(e, 'reason', e)}")


def api_base():
    return API


def releases():
    """Published releases of the project, newest version first. Drafts and odd tags (like '.untested') are ignored."""
    with _open(f"{api_base()}/repos/{REPO}/releases?per_page=30") as r:
        data = json.loads(r.read().decode("utf-8"))
    out = []
    for x in data if isinstance(data, list) else []:
        tag = str(x.get("tag_name", ""))
        if x.get("draft") or not TAG_OK.match(tag):
            continue
        out.append(dict(tag=tag, version=tag[1:], prerelease=bool(x.get("prerelease")), url=x.get("html_url", ""), notes=str(x.get("body") or ""),
                        published=x.get("published_at", ""),
                        assets=[dict(name=a.get("name", ""), size=a.get("size", 0), url=a.get("browser_download_url", "")) for a in x.get("assets", [])]))
    out.sort(key=lambda r: ssc_version.parse(r["tag"]), reverse=True)
    return out


def check(current=None, include_prerelease=True):
    cur = current or ssc_version.VERSION
    rels = [r for r in releases() if include_prerelease or not r["prerelease"]]
    latest = rels[0] if rels else None
    return dict(current=cur, flavor=flavor(), latest=latest["version"] if latest else None, tag=latest["tag"] if latest else None,
                update_available=bool(latest and ssc_version.is_newer(latest["tag"], cur)), url=latest["url"] if latest else "",
                notes=(latest["notes"][:4000] if latest else ""), asset=(pick_asset(latest) or {}).get("name") if latest else None)


def flavor():
    """'server' (installed from the SSC Server image), 'app' (a packaged desktop app) or 'source' (run from a folder)."""
    if getattr(sys, "frozen", False):
        return "app"
    here = Path(__file__).resolve().parent
    if here == Path(os.environ.get("SSC_SERVER_ROOT", "/opt/ssc")).resolve():
        return "server"
    return "source"


# ------------------------------------------------------------------ desktop apps: download + verify
def pick_asset(release, platform=None):
    plat = platform or sys.platform
    want = "windows.zip" if plat.startswith("win") else "linux.tar.gz" if plat.startswith("linux") else None
    if not want:
        return None                                    # no prebuilt app is attached for macOS: build it with build_app.sh
    return next((a for a in release["assets"] if a["name"].endswith(want)), None)


def parse_sums(text):
    out = {}
    for line in str(text).splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def download(url, dest, sha256=None, progress=None):
    """Stream to a temporary file, check size and SHA-256, and only then move it into place."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    h, n = hashlib.sha256(), 0
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as f, _open(url, timeout=60) as r:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_DOWNLOAD:
                    raise UpdateError("the download is far bigger than expected - stopped")
                h.update(chunk)
                f.write(chunk)
                if progress:
                    progress(n)
        if sha256 and h.hexdigest() != sha256.lower():
            raise UpdateError("the download does not match its checksum - it was NOT saved")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


def download_app(dest_dir, tag=None, platform=None, progress=None):
    rels = releases()
    rel = next((r for r in rels if r["tag"] == tag), None) if tag else (rels[0] if rels else None)
    if not rel:
        raise UpdateError("no release found")
    asset = pick_asset(rel, platform)
    if not asset:
        raise UpdateError("this release has no prebuilt app for this system (on a Mac: build it with ./build_app.sh)")
    sums = next((a for a in rel["assets"] if a["name"] == "SHA256SUMS"), None)
    if not sums:
        raise UpdateError("this release has no SHA256SUMS - refusing to download an unchecked file")
    with _open(sums["url"]) as r:
        expected = parse_sums(r.read().decode("utf-8", "replace")).get(asset["name"])
    if not expected:
        raise UpdateError(f"SHA256SUMS has no entry for {asset['name']}")
    return download(asset["url"], Path(dest_dir) / asset["name"], expected, progress), rel["version"]


# ------------------------------------------------------------------ SSC Server: install the code
def allowed(path):
    return any(fnmatch.fnmatch(path, pat) for pat in ALLOWED)


def safe_extract(tar_path, stage):
    """Unpack only whitelisted regular files (the archive's top folder is dropped). Anything odd aborts the update."""
    stage, count = Path(stage), 0
    with tarfile.open(tar_path, "r:gz") as tar:
        for m in tar:
            parts = [p for p in m.name.split("/") if p not in ("", ".")]
            if len(parts) < 2:
                continue
            rel = "/".join(parts[1:])
            if m.isdir():
                continue
            if not m.isreg():
                raise UpdateError(f"the archive contains something that is not a plain file ({rel}) - stopped")
            if ".." in parts or m.name.startswith("/") or any(p.startswith("..") for p in parts):
                raise UpdateError(f"unsafe path in the archive ({m.name}) - stopped")
            if not allowed(rel):
                continue
            if m.size > 8 * 1024 * 1024:
                raise UpdateError(f"{rel} is unexpectedly large - stopped")
            target = (stage / rel).resolve()
            if stage.resolve() not in target.parents:
                raise UpdateError(f"unsafe path in the archive ({m.name}) - stopped")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def verify_stage(stage):
    missing = [f for f in REQUIRED if not (Path(stage) / f).exists()]
    if missing:
        raise UpdateError("the release is missing files: " + ", ".join(missing))
    for f in Path(stage).rglob("*.py"):
        r = _run([sys.executable, "-m", "py_compile", str(f)])
        if r.returncode != 0:
            raise UpdateError(f"{f.name} does not compile: {(r.stderr or r.stdout).strip()[-200:]}")
    scratch = tempfile.mkdtemp(prefix="ssc-smoke-")
    try:     # the new code must at least import (and read its settings) on this machine
        code = "import sys;sys.path[:0]=[%r,%r];import core,serverconf,auth,serverapp,ssc_version,updater;print(ssc_version.VERSION)" % (str(stage), str(Path(stage) / "server"))
        r = _run([sys.executable, "-I", "-c", code], env={**os.environ, "SSC_DATA_DIR": scratch, "SSC_VAULT": "file"})
        if r.returncode != 0:
            raise UpdateError("the new version does not start: " + (r.stderr or r.stdout).strip()[-300:])
        return r.stdout.strip()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def install_server(tag=None, root="/opt/ssc", run=None, log=print):
    """Install the newest (or the given) release into `root`. Returns the new version. Raises UpdateError and changes nothing
    if anything looks wrong."""
    run = run or _run
    root = Path(root)
    rels = releases()
    rel = next((r for r in rels if r["tag"] == tag), None) if tag else (rels[0] if rels else None)
    if not rel:
        raise UpdateError("no release found")
    current = ssc_version.VERSION
    if tag is None and not ssc_version.is_newer(rel["tag"], current):
        raise UpdateError(f"already up to date ({current})")
    log(f"Downloading {rel['tag']} ...")
    work = Path(tempfile.mkdtemp(prefix="ssc-update-", dir=str(root.parent)))
    try:
        tarball = download(f"https://codeload.github.com/{REPO}/tar.gz/refs/tags/{rel['tag']}" if not _TEST_ALLOW_LOCAL
                           else f"{api_base()}/tarball/{rel['tag']}", work / "src.tar.gz")
        stage = work / "stage"
        n = safe_extract(tarball, stage)
        log(f"Unpacked {n} files; checking them ...")
        version = verify_stage(stage)
        if version != rel["version"]:
            raise UpdateError(f"the code says it is {version}, but the release is {rel['version']} - stopped")
        (stage / "VERSION").write_text(version + "\n")
        for dirpath, dirs, files in os.walk(stage):
            os.chmod(dirpath, 0o755)
            for f in files:
                os.chmod(os.path.join(dirpath, f), 0o644)
        prev = root.parent / (root.name + ".prev")
        if prev.exists():
            shutil.rmtree(prev)
        if root.exists():
            os.rename(root, prev)
        try:
            os.rename(stage, root)
        except OSError:
            if prev.exists() and not root.exists():
                os.rename(prev, root)                       # could not swap: put the old one back
            raise
        run(["systemctl", "restart", "ssc-server"])
        log(f"Installed {version}. The previous version is kept in {prev} (ssconfig update --rollback).")
        return version
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rollback(root="/opt/ssc", run=None):
    run = run or _run
    root = Path(root)
    prev = root.parent / (root.name + ".prev")
    if not prev.exists():
        raise UpdateError("there is no previous version to go back to")
    swap = root.parent / (root.name + ".rolledback")
    if swap.exists():
        shutil.rmtree(swap)
    os.rename(root, swap)
    os.rename(prev, root)
    run(["systemctl", "restart", "ssc-server"])
    return (root / "VERSION").read_text().strip() if (root / "VERSION").exists() else "previous"
