"""End-to-end cursor-placement tests against REAL targets.

Slow (~40s) and needs an interactive desktop, so it's separate from
test_core.py -- but it is the layer that has caught every serious bug in this
app: the 64-bit clipboard pointer truncation, the list-boundary drift, the
live-range bookmark landing at the end of the document, and the
non-degenerate-range bug that silently destroyed 13 characters of real text.

Two targets on purpose, because they fail differently:
  * a WebView2 contenteditable, standing in for any web-based editor.
    Ground truth read from the page's OWN JS selection API, which is
    independent of UI Automation.
  * a plain Win32 text box. Ground truth by typing a marker at the caret
    and reading the document back.

    python tests/test_live.py
"""
import os, sys, time, json, threading, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import snip_core as core

RESULTS = []
def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("  %-28s %s  %s" % (name, "PASS" if ok else "FAIL", detail), flush=True)

# ---------------------------------------------------------------- web editor
HTML = """<html><body style="margin:0">
<div id=ed contenteditable style="width:100%;height:98vh;font:14px Segoe UI;padding:8px;outline:none"></div>
<script>
const ed=document.getElementById('ed');
function reset(pre){ ed.innerHTML=pre||''; ed.focus();
  const r=document.createRange(); r.selectNodeContents(ed); r.collapse(false);
  const s=window.getSelection(); s.removeAllRanges(); s.addRange(r); return 'ok'; }
function info(){ const s=window.getSelection(); if(!s.rangeCount) return JSON.stringify({err:'nosel'});
  const r=s.getRangeAt(0);
  const pre=document.createRange();  pre.selectNodeContents(ed);  pre.setEnd(r.startContainer,r.startOffset);
  const post=document.createRange(); post.selectNodeContents(ed); post.setStart(r.startContainer,r.startOffset);
  return JSON.stringify({before:pre.toString(), after:post.toString()}); }
reset();
</script></body></html>"""

# (name, text, expected text immediately BEFORE caret, expected immediately AFTER)
CASES = [
    ("plain short",     "Hello $|world",                      "Hello", "world"),
    ("plain long",      "A fairly long single line of prose that runs on for a while $|and then keeps going afterwards too.",
                        "for a while", "and then keeps"),
    ("bold around $|",  "hey **I know, $|** and more",         "I know,", "and more"),
    ("list after",      "Intro line $|\n\n1. first item\n2. second item", "Intro line", "first item"),
    ("long + list",     "The $| workshop has published its schedule.\nSteps:\n\n1. Log in to the portal and open the console.\n2. Pick a paper.\n3. Submit.\n\n**Thanks.**",
                        "The", "workshop has"),
]

def norm(s):
    return (s or "").replace("\u00a0", " ").replace("\r", "").replace("\n", "")

def run_web_editor(win):
    time.sleep(2.5)
    win.evaluate_js("reset('')"); time.sleep(0.4)
    try: core._paste_one("warmup"); core._uia_text_pattern()   # focus/UIA warm-up
    except Exception: pass
    for prefill_name, prefill in (("empty doc", ""),
                                  ("existing content", "Hi team,<div>Earlier text in the reply. </div>")):
        print("\n-- web editor / %s --" % prefill_name, flush=True)
        for name, text, want_before, want_after in CASES:
            win.evaluate_js("reset(%s)" % json.dumps(prefill)); time.sleep(0.4)
            t0 = time.time(); core.paste_text(text, None); el = time.time() - t0
            time.sleep(0.45)
            d = json.loads(win.evaluate_js("info()"))
            before, after = norm(d.get("before")), norm(d.get("after"))
            ok = before.rstrip().endswith(want_before) and after.lstrip().startswith(want_after)
            check("%s / %s" % (prefill_name, name), ok,
                  "%.2fs  ...%r | %r..." % (el, before[-24:], after[:24]))
            check("%s / %s under 1s" % (prefill_name, name), el < 1.0, "%.2fs" % el)

    # ---- list numbering must survive the split at $|
    print("\n-- web editor / list numbering --", flush=True)
    win.evaluate_js("reset('')"); time.sleep(0.4)
    core.paste_text("Steps:\n\n1. First item with $|a marker inside it.\n2. Second item.\n3. Third item.", None)
    time.sleep(0.6)
    # innerText omits CSS list markers, so check the actual mechanism (the
    # start attribute) plus what UI Automation reports, which is what the
    # undo comparison relies on.
    html = win.evaluate_js("document.getElementById('ed').innerHTML") or ""
    check("second list resumes at 2", 'start="2"' in html, repr(html[-120:]))
    try:
        uia = core._uia_text_pattern().DocumentRange.GetText(-1)
    except Exception as e:
        uia = "<uia unavailable: %s>" % e
    for n in ("1.", "2.", "3."):
        check("UIA sees %s" % n, n in uia, "")
    check("no renumbering to 1.", uia.count("1.") == 1, repr(uia[:80]))

    # ---- undo-expansion: must restore the trigger, or change nothing at all
    import brain
    print("\n-- web editor / undo-expansion --", flush=True)
    for name, text in (("plain", "Hello $|world"),
                       ("rich + list", "The $| workshop.\n\n1. Log in.\n2. Submit.")):
        win.evaluate_js("reset('Existing text. ')"); time.sleep(0.4)
        core.paste_text(text, None); time.sleep(0.5)
        info = dict(core.LAST_PASTE)
        check("undo / %s recorded extent" % name, info.get("start") is not None, str(info.get("start")))
        brain._undo_expansion(":trg", info); time.sleep(0.6)
        doc = norm(json.loads(win.evaluate_js("info()")).get("before"))
        check("undo / %s restores trigger" % name, doc.endswith(":trg"), repr(doc[-30:]))
        check("undo / %s keeps prior text" % name, doc.startswith("Existing text."), repr(doc[:20]))

    # a WRONG start offset must abort rather than delete the user's text
    win.evaluate_js("reset('Important existing sentence that must survive. ')"); time.sleep(0.4)
    core.paste_text("Hello $|world", None); time.sleep(0.5)
    bad = dict(core.LAST_PASTE); bad["start"] = 0          # the stale-pattern failure mode
    brain._undo_expansion(":trg", bad); time.sleep(0.5)
    doc = norm(json.loads(win.evaluate_js("info()")).get("before")) + norm(json.loads(win.evaluate_js("info()")).get("after"))
    check("undo / bad offset aborts safely", "Important existing sentence" in doc, repr(doc[:34]))
    win.destroy()

def web_editor_suite():
    import webview
    w = webview.create_window("snippets cursor tests", html=HTML, width=900, height=700)
    threading.Thread(target=run_web_editor, args=(w,), daemon=True).start()
    webview.start()

# ------------------------------------------------------- plain Win32 text box
def plain_textbox_suite():
    import keyboard
    print("\n-- plain Win32 text box --", flush=True)
    subprocess.Popen(["notepad.exe"]); time.sleep(3.0)
    try:
        for name, text, want in [
            ("plain short", "Hello $|world",        "Hello @@world"),
            ("long + list", "The $| workshop has published its schedule.\n\n1. Log in.\n2. Submit.",
                            "The @@ workshop"),
        ]:
            keyboard.send("ctrl+a"); keyboard.send("delete"); time.sleep(0.4)
            core.paste_text(text, None); time.sleep(0.6)
            keyboard.write("@@", exact=False); time.sleep(0.7)     # ground truth: type AT the caret
            doc = core._uia_text_pattern().DocumentRange.GetText(-1).replace("\r", " ")
            check("plain box / %s" % name, want in doc, repr(doc[:56]))
            # the non-degenerate-range regression: nothing may be destroyed
            check("plain box / %s keeps text" % name, "published its schedule" in doc or "world" in doc, "")
    finally:
        subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    if only in ("all", "web"): web_editor_suite()
    if only in ("all", "plain"): plain_textbox_suite()
    bad = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(bad)), flush=True)
    sys.exit(1 if bad else 0)
