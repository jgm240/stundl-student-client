"""The one place that knows which version of SSC this is."""
import re

VERSION = "1.0.0-beta.6"

_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$")
_STAGE = {"alpha": 0, "beta": 1, "rc": 2, None: 3}          # 1.0.0-beta.9 < 1.0.0-rc.1 < 1.0.0


def parse(v):
    """'1.0.0-beta.5' / 'v1.0.0' -> a sortable tuple, or None if it isn't a release version."""
    m = _TAG.match(str(v or "").strip())
    if not m:
        return None
    major, minor, patch, stage, n = m.groups()
    return (int(major), int(minor), int(patch), _STAGE[stage], int(n or 0))


def is_newer(candidate, current=VERSION):
    a, b = parse(candidate), parse(current)
    return bool(a and b and a > b)
