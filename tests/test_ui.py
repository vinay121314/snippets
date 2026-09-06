"""Smoke-test the editor UI.

Renders editor_ui.html with sample state and asserts the key pieces appear,
no JavaScript threw, and the theme tokens actually resolve. Cheap insurance:
the editor is one big HTML file driven by string-built markup, so a typo in
the JS shows up as a silently blank pane rather than a traceback.

    python tests/test_ui.py
"""
import sys, os, time, threading, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview

SAMPLE = {"enabled": True, "author": "Test Author", "version": "9.9.9", "snippets": [
    {"id": "a", "triggers": [":one", ":uno"], "label": "First", "text": "**bold** and\n1. x\n2. y",
     "tags": ["demo", "email"], "word_only": False, "hotkey": ""},
    {"id": "b", "triggers": [":two"], "label": "Second", "text": "Hi $|,\n\n{{Name}}",
     "tags": ["demo"], "word_only": True, "hotkey": "ctrl+alt+2"}]}

def run(win):
    ok = True
    try:
        time.sleep(2.0)
        win.evaluate_js("window.__errs=[];window.onerror=function(m){window.__errs.push(m)};")
        win.evaluate_js("STATE=%s;selId='a';renderList();renderEditor();" % json.dumps(SAMPLE))
        time.sleep(0.6)
        errs = win.evaluate_js("JSON.stringify(window.__errs||[])")
        checks = {
            "no js errors":     errs in ("[]", None),
            "about hidden initially": win.evaluate_js("document.getElementById('aboutOverlay').hidden") is True,
            "no byline in sidebar": win.evaluate_js("!document.querySelector('.side .about')"),
            "list has 2 items": win.evaluate_js("document.querySelectorAll('.item').length") == 2,
            "trigger chips":    win.evaluate_js("document.querySelectorAll('.chip').length") >= 3,
            "tag filters":      win.evaluate_js("document.querySelectorAll('.tagfilter').length") == 2,
            "editor fields":    win.evaluate_js("!!document.getElementById('fTrig') && !!document.getElementById('fWordOnly')"),
            "toolbar buttons":  win.evaluate_js("document.querySelectorAll('.tb').length") >= 9,
            "message rendered": "<b>bold</b>" in (win.evaluate_js("document.getElementById('fText').innerHTML") or ""),
            "ol present":       "<ol" in (win.evaluate_js("document.getElementById('fText').innerHTML") or ""),
            "no glass blur":    (win.evaluate_js("getComputedStyle(document.querySelector('.topbar')).backdropFilter") or "none") == "none",
            "body has bg":      (win.evaluate_js("getComputedStyle(document.body).backgroundColor") or "") not in ("", "rgba(0, 0, 0, 0)"),
        }
        for k, v in checks.items():
            print("  %-20s %s" % (k, "PASS" if v else "FAIL"), flush=True)
            ok = ok and bool(v)
        # open the About sheet and check it actually renders
        win.evaluate_js("openAbout()"); time.sleep(0.4)
        about = {
            "about opens":       win.evaluate_js("document.getElementById('aboutOverlay').hidden") is False,
            "about has name":    "Test Author" in (win.evaluate_js("document.getElementById('aboutDoc').innerText") or ""),
            "about has version": "9.9.9" in (win.evaluate_js("document.getElementById('aboutVer').innerText") or ""),
            "credit is last line":  (win.evaluate_js("document.querySelector('#aboutDoc .credit') === document.getElementById('aboutDoc').lastElementChild")),
            "no footer bar":       win.evaluate_js("!document.querySelector('.sheet footer')"),
            "about has sections": (win.evaluate_js("document.querySelectorAll('#aboutDoc h3').length") or 0) >= 7,
            "about explains $|": "$|" in (win.evaluate_js("document.getElementById('aboutDoc').innerText") or ""),
            "about explains undo": "Backspace" in (win.evaluate_js("document.getElementById('aboutDoc').innerText") or ""),
        }
        win.evaluate_js("closeAbout()"); time.sleep(0.25)
        about["about closes"] = win.evaluate_js("document.getElementById('aboutOverlay').hidden") is True
        for k, v in about.items():
            print("  %-20s %s" % (k, "PASS" if v else "FAIL"), flush=True)
            ok = ok and bool(v)
        errs = win.evaluate_js("JSON.stringify(window.__errs||[])")
        if errs not in ("[]", None): print("  JS errors:", errs, flush=True); ok = False
        print("  theme bg:", win.evaluate_js("getComputedStyle(document.body).backgroundColor"), flush=True)
    except Exception as e:
        print("ERR:", e, flush=True); ok = False
    finally:
        win.destroy()
    globals()["OK"] = ok

w = webview.create_window("ui smoke", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "editor_ui.html"), width=1000, height=700)
threading.Thread(target=run, args=(w,), daemon=True).start()
webview.start()
sys.exit(0 if globals().get("OK") else 1)
