"""Build the link that turns a user's feedback into an issue on the project.

Submitting opens a pre-filled issue in the browser rather than posting from
the app. That is a deliberate choice, not a shortcut:

  * Posting directly would mean shipping an API token inside the exe. Anyone
    who downloads the app can pull a token straight out of the binary, and it
    would then be usable against the repository by anyone who found it. There
    is no way to embed a write credential in a program you hand to other
    people and have it stay secret.
  * Opening the browser shows the person exactly what is about to be sent
    before they press submit, and their identity comes from their own account
    rather than from anything the app collects behind their back.

Free, needs no server, and nothing is transmitted unless the person clicks
submit themselves.
"""
import urllib.parse

ISSUES_NEW = "https://github.com/vinay121314/snippets/issues/new"

# Browsers and servers both cap URL length. Well under any of those limits,
# and long enough for a real bug report.
MAX_BODY = 4000


def build_body(message, name="", version="", system=""):
    """The issue body. Everything included is shown to the person in the
    browser before they submit, so there is nothing here they have not seen."""
    msg = (message or "").strip()
    if len(msg) > MAX_BODY:
        msg = msg[:MAX_BODY] + "\n\n[truncated]"
    lines = [msg, "", "---"]
    if name.strip():
        lines.append("From: %s" % name.strip())
    if version:
        lines.append("Version: %s" % version)
    if system:
        lines.append("System: %s" % system)
    return "\n".join(lines)


def build_url(kind, message, name="", version="", system="", base=ISSUES_NEW):
    """A GitHub 'new issue' link with the title and body already filled in."""
    kind = (kind or "Feedback").strip()
    first = (message or "").strip().splitlines()
    summary = first[0][:60] if first else "Feedback"
    params = {
        "title": "[%s] %s" % (kind, summary),
        "body": build_body(message, name=name, version=version, system=system),
        "labels": kind.lower(),
    }
    return base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
