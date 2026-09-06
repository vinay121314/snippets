# Snippets. Copyright (c) 2026 Vinay Prasad. Released under the MIT Licence.
"""Send user feedback to a small relay that files it as an issue.

The app posts to an endpoint you control; the endpoint holds the credential
and talks to the issue tracker. It does NOT talk to the tracker directly,
because that would mean compiling an API token into the exe, and anyone who
downloads the app can read a token straight out of the binary. A relay keeps
the credential on a machine you control, and lets you change or switch off
the whole thing later without rebuilding anything that is already installed.

Anything that fails to send is written to an outbox and retried later, so a
report written on a train is not lost.

Like updates, the endpoint can be overridden from HKLM\\Software\\Snippets
(value FeedbackUrl), so a managed deployment can point it somewhere internal.
"""
import json, os, ssl, time, urllib.error, urllib.request

# Set both of these after deploying the relay. See worker/README.md.
ENDPOINT = ""
# Sent as a header so the relay can turn away automated scanners. Not a
# secret: it ships inside the app and can be read out of it. The relay's
# real protection is that its token can only open issues on one repository.
APP_KEY = ""

TIMEOUT = 15
USER_AGENT = "Snippets-Feedback"
MAX_MESSAGE = 4000
MAX_OUTBOX = 50            # stop an unreachable endpoint filling the disk
RETRY_EVERY = 30 * 60      # seconds between flush attempts

try:
    from snip_core import log, DATA_DIR
except Exception:                                     # pragma: no cover
    def log(*a): print(" ".join(str(x) for x in a))
    DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "SnippetsApp")

OUTBOX = os.path.join(DATA_DIR, "outbox")


# ---------------------------------------------------------------- config
def _registry_endpoint():
    try:
        import winreg
    except Exception:
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"Software\Snippets") as k:
                value, _ = winreg.QueryValueEx(k, "FeedbackUrl")
                if value:
                    return value
        except OSError:
            continue
    return None


def endpoint():
    return (_registry_endpoint()
            or os.environ.get("SNIPPETS_FEEDBACK_URL")
            or ENDPOINT).strip()


def configured():
    return bool(endpoint())


# ---------------------------------------------------------------- payload
def build_payload(kind, message, name="", version="", system=""):
    """Exactly what gets sent, and nothing else. Everything here is shown to
    the person in the dialog before they press Send."""
    msg = (message or "").strip()
    if len(msg) > MAX_MESSAGE:
        msg = msg[:MAX_MESSAGE] + "\n\n[truncated]"
    return {
        "kind": (kind or "Feedback").strip(),
        "message": msg,
        "name": (name or "").strip(),
        "version": version or "",
        "system": system or "",
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ---------------------------------------------------------------- network
def _post(url, data, timeout=TIMEOUT):
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if APP_KEY:
        headers["X-App-Key"] = APP_KEY
    req = urllib.request.Request(
        url, data=json.dumps(data).encode("utf-8"), method="POST", headers=headers)
    return urllib.request.urlopen(req, timeout=timeout,
                                  context=ssl.create_default_context())


def send(payload, url=None, poster=_post):
    """Post one report. Raises on failure so the caller can queue it."""
    url = url or endpoint()
    if not url:
        raise RuntimeError("no feedback endpoint configured")
    if not payload.get("message"):
        raise ValueError("message is empty")
    with poster(url, payload) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except Exception:
        return {"ok": True}


# ---------------------------------------------------------------- outbox
def _outbox_dir(d=None):
    d = d or OUTBOX
    os.makedirs(d, exist_ok=True)
    return d


def queue(payload, d=None):
    """Keep a report that could not be sent. Named by time so the order is
    preserved and two saved in the same second cannot collide."""
    d = _outbox_dir(d)
    existing = pending(d)
    if len(existing) >= MAX_OUTBOX:
        log("feedback outbox full, discarding oldest")
        try: os.remove(existing[0])
        except Exception: pass
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    path = os.path.join(d, "fb_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def pending(d=None):
    d = d or OUTBOX
    try:
        return sorted(os.path.join(d, n) for n in os.listdir(d)
                      if n.startswith("fb_") and n.endswith(".json"))
    except Exception:
        return []


def flush(d=None, url=None, poster=_post):
    """Try to send everything waiting. Returns (sent, still_waiting).
    A report is only deleted once it has actually gone."""
    url = url or endpoint()
    if not url:
        return 0, len(pending(d))
    sent = 0
    for path in pending(d):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            try: os.remove(path)              # unreadable, nothing to retry
            except Exception: pass
            continue
        try:
            send(payload, url=url, poster=poster)
        except Exception as e:
            log("feedback still queued:", e)
            break                             # endpoint down, stop trying now
        sent += 1
        try: os.remove(path)
        except Exception: pass
    return sent, len(pending(d))


def send_or_queue(payload, url=None, poster=_post, d=None):
    """Send now, keep it for later if that fails. Returns True when it went
    out immediately."""
    try:
        send(payload, url=url, poster=poster)
        return True
    except Exception as e:
        log("feedback queued for later:", e)
        queue(payload, d=d)
        return False
