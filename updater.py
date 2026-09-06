# Snippets. Copyright (c) 2026 Vinay Prasad. Released under the MIT Licence.
"""Self-update from a published release.

Deliberately uses urllib rather than a third-party HTTP library. On Windows,
urllib reads proxy settings from the registry and Python's default SSL context
loads the Windows ROOT certificate store, so a corporate proxy that decrypts
and re-signs HTTPS works with no extra configuration. A library that ships its
own CA bundle would reject the company certificate and fail on exactly the
networks this needs to work on.

It also avoids the GitHub REST API. Unauthenticated API calls are limited to 60
per hour PER IP ADDRESS, and everyone inside one company leaves through the
same address, so a few dozen colleagues checking for updates would exhaust the
whole allowance. The release asset URLs used here are served by CDN and carry
no such limit.

Update sources, highest priority first:
  1. HKLM\\Software\\Snippets   values UpdateUrl / DisableUpdate. Machine wide,
     so an IT admin can point the app at an internal location or switch
     self-update off entirely without touching the app.
  2. SNIPPETS_UPDATE_URL / SNIPPETS_DISABLE_UPDATE environment variables.
  3. The built-in default below.
"""
import os, sys, json, hashlib, ssl, tempfile, urllib.request, urllib.error

DEFAULT_BASE = "https://github.com/vinay121314/snippets/releases/latest/download/"
MANIFEST_NAME = "version.json"
TIMEOUT = 15
USER_AGENT = "Snippets-Updater"

try:
    from snip_core import log
except Exception:                                     # pragma: no cover
    def log(*a): print(" ".join(str(x) for x in a))


# ---------------------------------------------------------------- versions
def parse_version(s):
    """'v1.2.3' -> (1, 2, 3). Unparseable pieces become 0 rather than raising,
    so a malformed tag can never crash the app at startup."""
    out = []
    for part in str(s or "").strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


# ---------------------------------------------------------------- config
def _registry_config():
    try:
        import winreg
    except Exception:
        return {}
    out = {}
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"Software\Snippets") as k:
                for name, key in (("UpdateUrl", "base"), ("DisableUpdate", "disabled"),
                                  ("AutoUpdate", "auto")):
                    try:
                        value, _ = winreg.QueryValueEx(k, name)
                        out.setdefault(key, value)
                    except OSError:
                        pass
        except OSError:
            continue
    return out


def _flag(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def config():
    """Resolved update settings. IT policy in the registry wins over
    everything, which is the point of it."""
    reg = _registry_config()
    disabled = reg.get("disabled")
    if disabled is None:
        disabled = os.environ.get("SNIPPETS_DISABLE_UPDATE")
    auto = reg.get("auto")
    if auto is None:
        auto = os.environ.get("SNIPPETS_AUTO_UPDATE")
    base = reg.get("base") or os.environ.get("SNIPPETS_UPDATE_URL") or DEFAULT_BASE
    if not base.endswith("/"):
        base += "/"
    return {"disabled": _flag(disabled), "auto": _flag(auto), "base": base}


# ---------------------------------------------------------------- network
def _open(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())


def fetch_manifest(opener=_open):
    cfg = config()
    if cfg["disabled"]:
        return None
    with opener(cfg["base"] + MANIFEST_NAME) as r:
        return json.loads(r.read().decode("utf-8"))


def check(current_version, opener=_open):
    """The manifest if a newer version is published, else None. Never raises:
    an update check failing is not a reason to disturb the user."""
    try:
        m = fetch_manifest(opener)
        if not m:
            return None
        if is_newer(m.get("version", ""), current_version):
            return m
        return None
    except Exception as e:
        log("update check failed:", e)
        return None


def download(manifest, dest, opener=_open):
    """Fetch the new exe and verify it before it is allowed anywhere near the
    installed copy. A wrong or truncated download must never be swapped in."""
    cfg = config()
    name = manifest.get("exe") or "Snippets.exe"
    url = manifest.get("url") or (cfg["base"] + name)
    sha = (manifest.get("sha256") or "").lower().strip()
    h = hashlib.sha256()
    total = 0
    with opener(url) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            h.update(chunk); f.write(chunk); total += chunk.__len__()
    if total < 1024:
        raise RuntimeError("downloaded file is implausibly small (%d bytes)" % total)
    if sha and h.hexdigest() != sha:
        raise RuntimeError("checksum mismatch: expected %s, got %s" % (sha, h.hexdigest()))
    return dest


# ---------------------------------------------------------------- swapping
def _old_path(exe):
    return exe + ".old"


def cleanup_old(exe=None):
    """Delete the previous version left behind by the last update. Called at
    startup: the old file cannot be removed while it is still running, which
    is exactly when the swap happens."""
    exe = exe or sys.executable
    old = _old_path(exe)
    try:
        if os.path.exists(old):
            os.remove(old); log("removed previous version")
    except Exception:
        pass                                   # still in use, try again next start


def swap_in(new_file, exe=None):
    """Put new_file in place of the running executable.

    Windows refuses to overwrite a running exe but happily RENAMES one, which
    is what makes this possible at all: move the running file aside, move the
    new one into its place, and delete the old copy on the next start.

    Rolls back if the second move fails, so a failed update can never leave
    the app with no executable at all.
    """
    exe = exe or sys.executable
    old = _old_path(exe)
    if os.path.exists(old):
        try: os.remove(old)
        except Exception: pass
    os.replace(exe, old)                       # allowed even while running
    try:
        os.replace(new_file, exe)
    except Exception:
        os.replace(old, exe)                   # put it back, leave nothing broken
        raise
    return exe


def can_self_update():
    """False when running from source, and when the executable's folder is not
    writable (an install under Program Files without admin rights)."""
    if not getattr(sys, "frozen", False):
        return False
    return os.access(os.path.dirname(sys.executable) or ".", os.W_OK)


def perform(manifest, opener=_open, exe=None):
    """Download, verify, swap. Returns the path to the updated executable.
    The caller restarts it."""
    exe = exe or sys.executable
    tmp = os.path.join(tempfile.gettempdir(), "Snippets-update-%s.exe" % manifest.get("version", "new"))
    download(manifest, tmp, opener=opener)
    try:
        return swap_in(tmp, exe)
    finally:
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception:
            pass
