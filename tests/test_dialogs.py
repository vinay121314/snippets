"""Check the tray dialogs actually put their buttons on screen.

This exists because of a real bug that shipped: the feedback dialog packed its
buttons AFTER an expanding text box, so the buttons were never mapped and the
dialog appeared to have no way to send anything. A test that only walked the
widget tree passed happily, because widgets exist whether or not they are
drawn. Every check here asserts winfo_ismapped and that the widget falls
inside the window, which is the difference between "exists" and "the user can
see it".

Opens and closes real windows, so it needs an interactive desktop.

    python tests/test_dialogs.py
"""
import os, sys, tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("  %-44s %s  %s" % (name, "PASS" if ok else "FAIL", detail), flush=True)


def inspect_dialog(open_fn, geometry=None):
    """Run a dialog, snapshot its buttons once drawn, then close it."""
    found = {"buttons": [], "bottom": 0, "geometry": ""}
    original = tk.Tk

    def patched(*a, **k):
        w = original(*a, **k)
        if geometry:
            w.geometry(geometry)

        def snap():
            try:
                w.update_idletasks(); w.update()
                def walk(x):
                    for c in x.winfo_children():
                        if c.winfo_class() in ("Button", "TButton"):
                            found["buttons"].append(
                                (str(c.cget("text")), bool(c.winfo_ismapped()),
                                 c.winfo_rooty() + c.winfo_height(), c.winfo_rootx()))
                        walk(c)
                walk(w)
                found["bottom"] = w.winfo_rooty() + w.winfo_height()
                found["right"] = w.winfo_rootx() + w.winfo_width()
                found["geometry"] = w.winfo_geometry()
            finally:
                w.destroy()
        w.after(1200, snap)
        return w

    tk.Tk = patched
    try:
        open_fn()
    finally:
        tk.Tk = original
    return found


def assert_buttons_visible(label, found, expect):
    names = [b[0] for b in found["buttons"]]
    check("%s: has %s" % (label, expect),
          all(any(e.lower() in n.lower() for n in names) for e in expect), str(names))
    if not found["buttons"]:
        check("%s: any button drawn" % label, False, "none found")
        return
    for text, mapped, bottom, left in found["buttons"]:
        check("%s: '%s' is drawn on screen" % (label, text), mapped, "")
        check("%s: '%s' fits inside the window" % (label, text),
              bottom <= found["bottom"] and left <= found.get("right", 1 << 30),
              "bottom=%d window=%d" % (bottom, found["bottom"]))


def main():
    # A deliberately cramped window: the failure only showed up when there was
    # not enough room, which is what a smaller screen or higher display
    # scaling produces.
    print("\n-- feedback dialog, cramped window --", flush=True)
    brain._centre = lambda win, w, h: win.geometry("470x385+60+60")
    assert_buttons_visible("feedback", inspect_dialog(brain._feedback_popup),
                           ["Send", "Cancel"])

    print("\n-- feedback dialog, normal window --", flush=True)
    brain._centre = lambda win, w, h: win.geometry("520x470+60+60")
    assert_buttons_visible("feedback", inspect_dialog(brain._feedback_popup),
                           ["Send", "Cancel"])

    print("\n-- restore backup dialog --", flush=True)
    assert_buttons_visible("restore", inspect_dialog(brain._restore_popup),
                           ["Close"] if not __import__("snip_core").list_backups() else ["Restore", "Cancel"])

    print("\n-- fill-in fields dialog --", flush=True)
    specs = [("Name", []), ("Status", ["Open", "Done"]), ("Deadline", [])]
    assert_buttons_visible("fill-in", inspect_dialog(lambda: brain._fillin_popup(specs)),
                           ["Insert", "Cancel"])

    bad = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(bad)), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
