# Snippets. Copyright (c) 2026 Vinay Prasad. Released under the MIT Licence.
"""Shared core for Snippets: storage, dynamic values, rich clipboard, paste,
triggers, hotkeys. No webview, no pystray here, so it is safe to import from both the
lightweight tray process and the on-demand editor."""
import json, os, sys, time, threading, re, copy, datetime as dt

APP_NAME = "Snippets"
SEARCH_HOTKEY = "ctrl+alt+s"
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SnippetsApp")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "snippets.json")
LOG_FILE  = os.path.join(DATA_DIR, "snippets.log")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_KEEP = 20

def log(*a):
    line = " ".join(str(x) for x in a)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(dt.datetime.now().strftime("%H:%M:%S") + "  " + line + "\n")
    except Exception: pass
    try: print(line)
    except Exception: pass

try: import keyboard
except Exception: keyboard = None
try: import pyperclip
except Exception: pyperclip = None
try: import uiautomation as _uia
except Exception: _uia = None

DEFAULTS = {
    "enabled": True, "date_format": "%d %B %Y", "theme": "dark",
    # First-run examples. Each one demonstrates a DIFFERENT feature, so a new
    # user can see what the app does without reading any docs: plain text, the
    # $| cursor marker, {date}, a {{field}} prompt, a {{field|a,b}} dropdown,
    # and rich formatting.
    #
    # Nothing personal ships with the app: real snippets live in
    # %APPDATA%/SnippetsApp/snippets.json, outside the project folder, and are
    # never bundled into the exe or committed to the repo.
    "snippets": [
        {"id": "ex1", "triggers": [":hello"], "hotkey": "", "label": "Plain text",
         "tags": ["examples"],
         "text": "Thanks for getting in touch. I will take a look and come back to you shortly."},

        {"id": "ex2", "triggers": [":sig"], "hotkey": "", "label": "Cursor marker",
         "tags": ["examples"],
         "text": "Hi $|,\n\nBest regards"},

        {"id": "ex3", "triggers": [":today"], "hotkey": "", "label": "Today's date",
         "tags": ["examples"],
         "text": "As of {date}, this is complete. Let me know if anything else is needed."},

        {"id": "ex4", "triggers": [":intro"], "hotkey": "", "label": "Fill-in fields",
         "tags": ["examples"],
         "text": "Hi {{Name}}, thanks for your note about {{Topic}}.\nI will follow up by {{Day}}."},

        {"id": "ex5", "triggers": [":status"], "hotkey": "", "label": "Dropdown field",
         "tags": ["examples"],
         "text": "Current status: **{{Status|Not started,In progress,Blocked,Done}}**.\n\n$|"},

        {"id": "ex6", "triggers": [":steps"], "hotkey": "", "label": "Formatting and lists",
         "tags": ["examples"],
         "text": "Please follow the steps below.\n\n"
                 "1. Open the **portal** and sign in.\n"
                 "2. Go to *Settings* and copy the `API key`.\n"
                 "3. Paste it into the form and save.\n\n"
                 "---\nMore detail: [documentation](https://example.com/docs)"},
    ]
}

class Store:
    def __init__(self, path):
        # deepcopy, not dict(): a shallow copy still shares the snippets LIST
        # with DEFAULTS, so one store editing a snippet would mutate the
        # module-level defaults and every store created afterwards
        self.path=path; self.data=copy.deepcopy(DEFAULTS); self.load()
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f: self.data=json.load(f)
            except Exception as e: log("load failed:", e)
        self.apply_defaults()

    def apply_defaults(self):
        """Fill in every field a snippet may be missing. Runs on load AND
        after a restore, so an older backup comes back with the newer fields
        present instead of half-initialised in memory until the next restart."""
        self.data.setdefault("enabled",True)
        self.data.setdefault("date_format","%d %B %Y")
        self.data.setdefault("theme","dark")     # dark unless the user says otherwise
        if "snippets" not in self.data:
            # deep copy: sharing the module-level list would let one store's
            # edits leak into DEFAULTS and into any other store created later
            self.data["snippets"]=copy.deepcopy(DEFAULTS["snippets"])
        for i,s in enumerate(self.data["snippets"]):
            s.setdefault("id","s%d"%(i+1))
            if "trigger" in s and "triggers" not in s: s["triggers"]=[s.pop("trigger")]
            s.setdefault("triggers",[])
            if isinstance(s["triggers"],str): s["triggers"]=[s["triggers"]]
            s.setdefault("hotkey",""); s.setdefault("label",""); s.setdefault("text","")
            s.setdefault("tags",[]); s.setdefault("word_only",False)
            s.setdefault("scope","personal")  # inert for now; forward-compat for a future team/company hub
    def save(self):
        try:
            self._backup()          # snapshot the PREVIOUS contents first
            with open(self.path,"w",encoding="utf-8") as f:
                json.dump(self.data,f,indent=2,ensure_ascii=False)
            with open(self.path,"r",encoding="utf-8") as f: json.load(f)
            log("save OK"); return True
        except Exception as e:
            log("SAVE FAILED:", e); return False

    def _backup(self):
        """Copy the file as it currently stands on disk into backups/ before
        it gets overwritten. Deliberately snapshots the OLD contents, not the
        new ones: the point is to be able to get back to where you were after
        a bad edit, a bad import, or an accidental delete. Never raises --
        a failed backup must not block the save itself."""
        try:
            if not os.path.exists(self.path): return
            with open(self.path, encoding="utf-8") as f: prev=f.read()
            if not prev.strip(): return
            # Only snapshot when the SNIPPETS actually changed. Otherwise
            # incidental saves (the enable/disable toggle writes the file too)
            # would push real edit history out of the keep-limit within a few
            # clicks.
            try:
                if json.loads(prev).get("snippets")==self.data.get("snippets"): return
            except Exception: pass
            os.makedirs(BACKUP_DIR, exist_ok=True)
            stamp=dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]   # ms: two saves
            dest=os.path.join(BACKUP_DIR,"snippets_%s.json"%stamp)      # in one second collided
            with open(dest,"w",encoding="utf-8") as f: f.write(prev)
            old=list_backups()[BACKUP_KEEP:]         # newest-first, so this is the tail
            for p,_,_ in old:
                try: os.remove(p)
                except Exception: pass
        except Exception as e:
            log("backup skipped:", e)

def list_backups():
    """(path, when, snippet_count) for each backup, newest first."""
    out=[]
    try:
        for n in os.listdir(BACKUP_DIR):
            if not (n.startswith("snippets_") and n.endswith(".json")): continue
            p=os.path.join(BACKUP_DIR,n)
            try:
                with open(p,encoding="utf-8") as f: d=json.load(f)
                cnt=len(d.get("snippets") or [])
            except Exception: cnt=-1
            stamp=n[len("snippets_"):-len(".json")]
            when=None
            for fmt in ("%Y%m%d-%H%M%S-%f","%Y%m%d-%H%M%S"):
                try: when=dt.datetime.strptime(stamp,fmt); break
                except Exception: pass
            if when is None: when=dt.datetime.fromtimestamp(os.path.getmtime(p))
            out.append((p,when,cnt))
    except Exception: pass
    out.sort(key=lambda r:r[1], reverse=True)
    return out

def restore_backup(store, path):
    """Load a backup over the live store. Restoring is itself backed up first
    (via the normal save path), so an accidental restore is undoable too."""
    with open(path,encoding="utf-8") as f: data=json.load(f)
    if not isinstance(data.get("snippets"),list): raise ValueError("not a snippets backup")
    store.data["snippets"]=data["snippets"]
    if "date_format" in data: store.data["date_format"]=data["date_format"]
    store.apply_defaults()
    return store.save()

def _expand_dynamic(text, store):
    out=text
    try:
        now=dt.datetime.now()
        out=out.replace("{date}", now.strftime(store.data.get("date_format","%d %B %Y")))
        out=out.replace("{time}", now.strftime("%I:%M %p"))
        if "{clip}" in out:
            c=""
            try: c=pyperclip.paste() if pyperclip else ""
            except Exception: c=""
            out=out.replace("{clip}", c)
    except Exception as e: log("dyn err:", e)
    return out

def _find_duplicate_trigger(snippets):
    """Exact/case-sensitive, mirroring the runtime matcher (_buffer.endswith)
    exactly -- authoritative gate, mirrors the same check in editor_ui.html's
    findDuplicateTrigger() so the two can't disagree."""
    seen={}
    for s in snippets:
        for raw in (s.get("triggers") or []):
            t=(raw or "").strip()
            if not t: continue
            if t in seen: return t, seen[t]
            seen[t]=s.get("label") or "Untitled"
    return None, None

_FIELD_RE=re.compile(r"\{\{([^{}|]+)(?:\|([^{}]*))?\}\}")

def extract_field_specs(text):
    """Ordered, de-duplicated placeholders as (label, choices).

    Two forms, both double-brace so they can never collide with the
    single-brace {date}/{time}/{clip} dynamic values:
        {{Name}}                        -- free text box
        {{Status|Open,Blocked,Done}}    -- dropdown, first option preselected
    An empty or malformed option list degrades to a free text box rather
    than erroring, so a typo in a snippet can't block an expansion."""
    out=[]; seen=set()
    for m in _FIELD_RE.finditer(text or ""):
        label=m.group(1).strip()
        if not label or label in seen: continue
        seen.add(label)
        raw=m.group(2)
        choices=[c.strip() for c in raw.split(",")] if raw is not None else []
        out.append((label,[c for c in choices if c]))
    return out

def extract_fields(text):
    """Just the placeholder names, in order."""
    return [label for label,_ in extract_field_specs(text)]

def apply_fields(text, values):
    """Substitute chosen values back in. Matches on the LABEL, so it replaces
    the whole placeholder including any |options list."""
    def sub(m):
        label=m.group(1).strip()
        return values.get(label, m.group(0)) if values else m.group(0)
    return _FIELD_RE.sub(sub, text or "")

def _md_to_html(text):
    def esc(x): return x.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    def inline(s):
        links=[]; codes=[]
        def _l(m): links.append((m.group(1),m.group(2))); return "\x00L%d\x00"%(len(links)-1)
        def _c(m): codes.append(m.group(1)); return "\x00K%d\x00"%(len(codes)-1)
        s=re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _l, s)
        s=re.sub(r"`([^`]+)`", _c, s)   # extract BEFORE escaping so ** / * inside code aren't touched below
        s=esc(s)
        s=re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s=re.sub(r"(^|[^*])\*([^*]+)\*", r"\1<i>\2</i>", s)
        for i,(lbl,url) in enumerate(links):
            s=s.replace("\x00L%d\x00"%i, '<a href="%s">%s</a>'%(esc(url),esc(lbl)))
        for i,code in enumerate(codes):
            s=s.replace("\x00K%d\x00"%i, '<code>%s</code>'%esc(code))
        return s
    lines=text.split("\n"); out=[]; i=0
    while i < len(lines):
        ln=lines[i]
        if re.match(r"^-{3,}$", ln.strip()):
            out.append("<hr>"); i+=1; continue
        m_ol=re.match(r"^(\d+)\.\s", ln)
        if m_ol:
            # Honour the FIRST item's number instead of always restarting at 1.
            # Splitting the text at $| can put the tail of a list into its own
            # paste (e.g. "$|" inside item 1, items 2-7 pasted separately) --
            # without start=, the browser renumbered those from 1.
            first=int(m_ol.group(1))
            out.append("<ol>" if first==1 else '<ol start="%d">'%first)
            while i<len(lines) and re.match(r"^\d+\.\s", lines[i]):
                out.append("<li>"+inline(re.sub(r"^\d+\.\s","",lines[i]))+"</li>"); i+=1
            out.append("</ol>"); continue
        if re.match(r"^[-*]\s", ln):
            out.append("<ul>")
            while i<len(lines) and re.match(r"^[-*]\s", lines[i]):
                out.append("<li>"+inline(re.sub(r"^[-*]\s","",lines[i]))+"</li>"); i+=1
            out.append("</ul>"); continue
        out.append(inline(ln)+("<br>" if i<len(lines)-1 else "")); i+=1
    # joined with "" not "\n": every real line break is already explicit via
    # <br>/<li>/<ol>/<ul> tags, so a literal newline between fragments serves
    # no structural purpose here -- but a receiving app that treats pasted
    # HTML whitespace as significant (e.g. white-space:pre-wrap, which many
    # rich-text editors apply to preserve pasted formatting) renders each one
    # as an extra visible blank line, which is what was actually causing both
    # the ugly extra gaps around lists AND the $| cursor-position drift near
    # them (previously "fixed" by approximating around it in _visible_len
    # instead of removing the source of the extra whitespace).
    return "".join(out)

def _has_fmt(t): return bool(re.search(r"\[[^\]]+\]\([^)]+\)", t) or "**" in t or re.search(r"(?m)^\d+\.\s", t) or re.search(r"(?m)^[-*]\s", t) or "`" in t or re.search(r"(?m)^-{3,}$", t))
def _plain_from_md(t):
    t=re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", t); t=t.replace("**","")
    t=re.sub(r"`([^`]+)`", r"\1", t)
    return re.sub(r"(?m)^-{3,}$", "----------", t)

def _make_cf_html(frag):
    header=("Version:0.9\r\nStartHTML:{sh:010d}\r\nEndHTML:{eh:010d}\r\n"
            "StartFragment:{sf:010d}\r\nEndFragment:{ef:010d}\r\n")
    pre='<html><body><!--StartFragment--><span style="color:inherit;background:transparent">'
    post="</span><!--EndFragment--></body></html>"
    dummy=header.format(sh=0,eh=0,sf=0,ef=0)
    sh=len(dummy.encode("utf-8")); sf=sh+len(pre.encode("utf-8"))
    ef=sf+len(frag.encode("utf-8")); eh=ef+len(post.encode("utf-8"))
    return header.format(sh=sh,eh=eh,sf=sf,ef=ef)+pre+frag+post

# GlobalAlloc/GlobalLock return pointer-sized (64-bit) handles. ctypes
# defaults an undeclared WinDLL call to a 32-bit c_int return, which silently
# truncates the real pointer -- the memmove/cast writes below then land on a
# corrupted address and crash with "access violation" (intermittently: it
# only shows up when the real allocation address doesn't happen to fit in 32
# bits, which is common but not universal, hence the flakiness). Declaring
# restype/argtypes as c_void_p makes ctypes marshal the full pointer.
import ctypes as _ctypes
from ctypes import wintypes as _wintypes
_GlobalAlloc=_ctypes.windll.kernel32.GlobalAlloc
_GlobalAlloc.restype=_ctypes.c_void_p; _GlobalAlloc.argtypes=[_ctypes.c_uint,_ctypes.c_size_t]
_GlobalLock=_ctypes.windll.kernel32.GlobalLock
_GlobalLock.restype=_ctypes.c_void_p; _GlobalLock.argtypes=[_ctypes.c_void_p]
_GlobalUnlock=_ctypes.windll.kernel32.GlobalUnlock
_GlobalUnlock.argtypes=[_ctypes.c_void_p]
_SetClipboardData=_ctypes.windll.user32.SetClipboardData
_SetClipboardData.restype=_ctypes.c_void_p; _SetClipboardData.argtypes=[_ctypes.c_uint,_ctypes.c_void_p]
_GlobalFree=_ctypes.windll.kernel32.GlobalFree
_GlobalFree.argtypes=[_ctypes.c_void_p]

def _set_clipboard_html(frag, plain):
    import ctypes
    CF_UNICODETEXT=13; GMEM=0x0002
    u=ctypes.windll.user32
    CF_HTML=u.RegisterClipboardFormatW("HTML Format")
    data=_make_cf_html(frag).encode("utf-8")
    def _alloc(b):
        h=_GlobalAlloc(GMEM,len(b)+1); p=_GlobalLock(h)
        ctypes.memmove(p,b,len(b)); ctypes.cast(p,ctypes.POINTER(ctypes.c_char))[len(b)]=b'\x00'
        _GlobalUnlock(h); return h
    def _allocw(t):
        b=t.encode("utf-16-le"); h=_GlobalAlloc(GMEM,len(b)+2); p=_GlobalLock(h)
        ctypes.memmove(p,b,len(b))
        ctypes.cast(p,ctypes.POINTER(ctypes.c_char))[len(b)]=b'\x00'
        ctypes.cast(p,ctypes.POINTER(ctypes.c_char))[len(b)+1]=b'\x00'
        _GlobalUnlock(h); return h
    if not u.OpenClipboard(0): return False
    try:
        u.EmptyClipboard()
        # SetClipboardData takes ownership of the handle only on success --
        # on failure Windows requires the caller to free it, or it leaks.
        h1=_alloc(data)
        if not _SetClipboardData(CF_HTML,h1): _GlobalFree(h1); return False
        h2=_allocw(plain)
        if not _SetClipboardData(CF_UNICODETEXT,h2): _GlobalFree(h2); return False
        return True
    finally: u.CloseClipboard()

def _strip_list_markers(t):
    # "1. " / "- " prefixes render as a non-navigable auto number/bullet, not
    # literal characters -- they don't count toward a left-arrow distance.
    return "\n".join(re.sub(r"^(?:\d+\.\s+|[-*]\s+)", "", ln) for ln in t.split("\n"))

def _visible_len(after_src, rich):
    """How many LEFT-arrow presses reach $| from the end of the pasted text:
    the count of VISIBLE characters after the marker once rendered. Markdown
    syntax (**, list markers) isn't itself navigable text, and a rich link
    only shows its label (not "label (url)" the way the plain fallback does)
    -- counting raw markdown-source characters (the old behavior) overcounts
    by exactly the amount of markup involved, which is why this only ever
    worked for plain, unformatted text."""
    return len(_visible_text(after_src, rich))

def _visible_text(src, rich, strip_lists=True):
    """The text as it actually ends up RENDERED: markdown syntax and list
    markers removed, links reduced to their label when rich. Shared by the
    keypress fallback (which needs its LENGTH) and by undo-expansion (which
    compares it against what is really in the document before deleting)."""
    after_src = (src or "").replace("$|","")
    t=_strip_list_markers(after_src) if strip_lists else after_src
    if rich: t=re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", t)
    else: t=re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", t)
    return t.replace("**","").replace("`","")

def _uia_text_pattern(retries=4):
    """The focused control's UI Automation Text Pattern. Must be fetched
    AFTER the first paste, not before: pasting into an empty/near-empty
    control can replace the element the pattern is bound to, and a pattern
    captured beforehand then reports a stale document (measured: it read
    offset 0 for a caret that was really 112 characters in).

    Retried briefly: called from a separate process immediately after
    Ctrl+V, the first GetFocusedControl() sometimes reports a control with
    no TextPattern while focus is still settling (observed on the first
    trigger fired after startup). Costs nothing when it succeeds first
    time, which is the normal case."""
    if _uia is None: raise RuntimeError("uiautomation unavailable")
    last=None
    for _ in range(max(1,retries)):
        try:
            ctrl=_uia.GetFocusedControl()
            if ctrl is None: raise RuntimeError("no focused control")
            pat=ctrl.GetPattern(_uia.PatternId.TextPattern)
            if pat is None: raise RuntimeError("focused control has no TextPattern")
            return pat
        except Exception as e:
            last=e; time.sleep(0.06)
    raise last

# uiautomation sleeps OPERATION_WAIT_TIME (0.5s) after every range operation
# by default. Four of those per paste added ~2s of pure idle waiting to an
# operation whose real work measures at 0.00-0.05s -- always pass waitTime=0.
def _caret_abs_offset(pat):
    """Absolute character offset of the caret from the start of the
    document, measured from the REAL rendered document rather than
    predicted from the markdown source."""
    sel=pat.GetSelection()
    if not sel: raise RuntimeError("no caret/selection")
    r=pat.DocumentRange.Clone()
    r.MoveEndpointByRange(_uia.TextPatternRangeEndpoint.End, sel[0],
                          _uia.TextPatternRangeEndpoint.Start, waitTime=0)
    return len(r.GetText(-1))

def _set_caret_abs_offset(pat, off):
    """Put the caret at an absolute document offset in ONE operation --
    no synthetic keypresses, so there's no visible cursor travel and the
    cost doesn't scale with distance.

    This is deliberately NOT the same mechanism as the two approaches that
    failed before it, both of which are gone from this file now:

      * The live-range bookmark (capture the caret as a TextRange before
        inserting text, .Select() it afterwards) held a range across a
        document mutation. Re-measured against ground truth, it lands at
        the END of the document for two of the real snippets in use --
        it isn't merely imprecise, it's arbitrarily wrong.
      * Move(TextUnit.Character, -n) counts in UIA character units, which
        do not correspond 1:1 to visible characters across list and block
        boundaries.

    Here the offset is both MEASURED and RESTORED in the same UIA character
    units against the same document, so the two can't disagree; nothing is
    held across the mutation except an integer, and nothing is predicted
    from the markdown source. Verified exact against a live WebView2
    contenteditable (its own JS selection API as an independent oracle,
    which is what caught the bookmark failure) for every snippet in use,
    with and without pre-existing document content."""
    doc=pat.DocumentRange
    r=doc.Clone(); c=doc.Clone()
    # Collapse to the document start using a SEPARATE clone as the source.
    # Passing the same range object as both source and target -- which reads
    # naturally and works fine in web-based editors -- leaves a NON-degenerate
    # range in classic Win32 controls, where it produced a 13-character
    # selection, and since Select() then selects that text, the user's very
    # next keystroke silently overwrote it. Hence also the degeneracy
    # assertion below: never hand Select() a range that isn't a caret.
    r.MoveEndpointByRange(_uia.TextPatternRangeEndpoint.End, c,
                          _uia.TextPatternRangeEndpoint.Start, waitTime=0)
    moved=r.Move(_uia.TextUnit.Character, off, waitTime=0)
    if moved!=off: raise RuntimeError("moved %d of %d"%(moved,off))
    stray=r.GetText(-1)          # a caret range spans no text
    if stray: raise RuntimeError("range spans %d chars -- refusing to Select"%len(stray))
    if not r.Select(waitTime=0): raise RuntimeError("Select() failed")

class _RECT(_ctypes.Structure):
    _fields_=[("left",_wintypes.LONG),("top",_wintypes.LONG),
              ("right",_wintypes.LONG),("bottom",_wintypes.LONG)]
class _GUITHREADINFO(_ctypes.Structure):
    _fields_=[("cbSize",_wintypes.DWORD),("flags",_wintypes.DWORD),
              ("hwndActive",_wintypes.HWND),("hwndFocus",_wintypes.HWND),
              ("hwndCapture",_wintypes.HWND),("hwndMenuOwner",_wintypes.HWND),
              ("hwndMoveSize",_wintypes.HWND),("hwndCaret",_wintypes.HWND),
              ("rcCaret",_RECT)]

def _caret_screen_pos():
    """Screen position of the real Win32 caret, or None if the focused app
    doesn't own one.

    This is the independent oracle that UI Automation can't provide for
    itself: it comes from the window manager's own caret tracking, not from
    the Text Pattern, so it can contradict UIA -- and it does. A plain
    Win32 text box
    accepts _set_caret_abs_offset without complaint (MoveEndpointByUnit
    reports the full requested distance, Select() returns True) and then
    leaves the visible caret exactly where it was; verified by selecting
    caret-to-end and reading the clipboard, which came back empty. Without
    this check that silently degrades to "the $| marker did nothing".

    Web-based editing surfaces draw their own
    caret and own no Win32 caret, so this returns None there -- which is
    the case where UIA was measured exact against the page's own selection
    API, so a None result means "can't verify, trust UIA" rather than
    "failed"."""
    try:
        u32=_ctypes.windll.user32
        u32.GetGUIThreadInfo.argtypes=[_wintypes.DWORD,_ctypes.POINTER(_GUITHREADINFO)]
        gi=_GUITHREADINFO(); gi.cbSize=_ctypes.sizeof(_GUITHREADINFO)
        tid=u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), None)
        if not u32.GetGUIThreadInfo(tid, _ctypes.byref(gi)): return None
        if not gi.hwndCaret: return None
        pt=_wintypes.POINT(gi.rcCaret.left, gi.rcCaret.top)
        u32.ClientToScreen(gi.hwndCaret, _ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        return None

def _select_abs_range(pat, start, end):
    """A NON-degenerate range covering [start,end) in document characters.
    Separate from _set_caret_abs_offset, which refuses anything that isn't a
    caret -- here a real selection is the point."""
    doc=pat.DocumentRange
    def at(off):
        r=doc.Clone(); c=doc.Clone()
        r.MoveEndpointByRange(_uia.TextPatternRangeEndpoint.End, c,
                              _uia.TextPatternRangeEndpoint.Start, waitTime=0)
        if r.Move(_uia.TextUnit.Character, off, waitTime=0)!=off:
            raise RuntimeError("cannot reach offset %d"%off)
        return r
    r=at(start); e=at(end)
    r.MoveEndpointByRange(_uia.TextPatternRangeEndpoint.End, e,
                          _uia.TextPatternRangeEndpoint.Start, waitTime=0)
    return r

# Set by paste_text, consumed by brain's undo-expansion. Holds where the
# insertion began and what went in, so an undo can VERIFY before deleting.
LAST_PASTE={}

def _squash(s):
    """Reduce text to its alphanumeric characters for comparison.

    Deliberately tolerant: the same content differs between the markdown
    source and what UI Automation reports for the rendered result -- HTML
    collapses and re-wraps whitespace, non-breaking spaces appear, and a
    bulleted item reads as "- x" in the source but "• x" in the
    document. Dropping every non-alphanumeric character makes those
    equivalent while still requiring the actual WORDS to match, in order.
    Used only to confirm a range really holds what was pasted before
    undo-expansion deletes it."""
    return "".join(ch for ch in (s or "") if ch.isalnum())

def matches_pasted(got, want, slack=3):
    """Does `got` look like `want` with at most a few characters missing?

    Not a prefix test: the caret ends up at the $| marker, which is in the
    MIDDLE of the inserted text, so the backspace that triggers an undo
    removes a character from the middle, not the end. `got` is therefore
    `want` minus a character somewhere inside it. A subsequence test with a
    tight length bound accepts that while still requiring every remaining
    character to appear in the right order."""
    if not got or not want: return False
    if not (0 <= len(want)-len(got) <= slack): return False
    i=0
    for ch in want:
        if i<len(got) and got[i]==ch: i+=1
    return i==len(got)

def _close_span(b, a, mark):
    """Close an unterminated inline span at the end of `b`, and drop the
    now-orphaned closing marker from the start of `a`."""
    ws=re.search(r"[ 	]*$", b).group(0)          # keep trailing space OUTSIDE
    b=b[:len(b)-len(ws)]+mark+ws                  # the span -- see _balance_split
    i=a.find(mark)
    a=(a[:i]+a[i+len(mark):]) if i>=0 else (mark+a)
    return b,a

def _balance_split(before, after):
    """Splitting at $| can cut an inline span in half, leaving each side
    invalid markdown -- e.g. "**I know, $|**" gives before="**I know, "
    and after="**...", which render as LITERAL asterisks in the pasted
    result. Close the span in `before` and remove its orphaned closer from
    `after` so both halves render exactly as the unsplit text would.

    The closing marker goes before any trailing space rather than after it:
    Browser layout normalizes whitespace at the end of an inline element, and
    closing after the space ("**I know, **") made the space reappear as a
    stray bold <b>&nbsp;</b> at the very END of the pasted block."""
    b,a=before,after
    if b.count("`")%2: b,a=_close_span(b,a,"`")
    if b.count("**")%2: b,a=_close_span(b,a,"**")
    if b.replace("**","").count("*")%2: b,a=_close_span(b,a,"*")
    return b,a

# ---- batched SendInput cursor move -------------------------------------
# All 2*n key events (down+up per Left press) are handed to Windows in ONE
# SendInput call instead of n separate keyboard.send() calls. Correct 64-bit
# ctypes declarations matter here: dwExtraInfo is ULONG_PTR (pointer-sized),
# and the union must be sized off MOUSEINPUT (the largest member) or every
# element after the first lands misaligned -- the same bug class as the
# GlobalAlloc/GlobalLock fix. VK_LEFT is an EXTENDED key, so KEYEVENTF_
# EXTENDEDKEY plus a real scan code are both required; omitting them is what
# made the earlier hand-rolled SendInput attempt land in the wrong place.
_ULONG_PTR=_ctypes.c_size_t
class _MOUSEINPUT(_ctypes.Structure):
    _fields_=[("dx",_wintypes.LONG),("dy",_wintypes.LONG),
              ("mouseData",_wintypes.DWORD),("dwFlags",_wintypes.DWORD),
              ("time",_wintypes.DWORD),("dwExtraInfo",_ULONG_PTR)]
class _KEYBDINPUT(_ctypes.Structure):
    _fields_=[("wVk",_wintypes.WORD),("wScan",_wintypes.WORD),
              ("dwFlags",_wintypes.DWORD),("time",_wintypes.DWORD),
              ("dwExtraInfo",_ULONG_PTR)]
class _HARDWAREINPUT(_ctypes.Structure):
    _fields_=[("uMsg",_wintypes.DWORD),("wParamL",_wintypes.WORD),
              ("wParamH",_wintypes.WORD)]
class _INPUTUNION(_ctypes.Union):
    _fields_=[("mi",_MOUSEINPUT),("ki",_KEYBDINPUT),("hi",_HARDWAREINPUT)]
class _INPUT(_ctypes.Structure):
    _fields_=[("type",_wintypes.DWORD),("u",_INPUTUNION)]

_INPUT_KEYBOARD=1
_KEYEVENTF_EXTENDEDKEY=0x0001
_KEYEVENTF_KEYUP=0x0002
_VK_LEFT=0x25

def _send_left_batch(n):
    """n Left presses in a single SendInput call. Returns the number of
    events Windows accepted; raises on failure so callers can fall back."""
    u32=_ctypes.windll.user32
    u32.SendInput.restype=_wintypes.UINT
    u32.SendInput.argtypes=[_wintypes.UINT,_ctypes.POINTER(_INPUT),_ctypes.c_int]
    u32.MapVirtualKeyW.restype=_wintypes.UINT
    u32.MapVirtualKeyW.argtypes=[_wintypes.UINT,_wintypes.UINT]
    scan=u32.MapVirtualKeyW(_VK_LEFT,0)
    arr=(_INPUT*(n*2))()
    for i in range(n):
        d=arr[i*2]; d.type=_INPUT_KEYBOARD
        d.u.ki.wVk=_VK_LEFT; d.u.ki.wScan=scan
        d.u.ki.dwFlags=_KEYEVENTF_EXTENDEDKEY; d.u.ki.time=0; d.u.ki.dwExtraInfo=0
        up=arr[i*2+1]; up.type=_INPUT_KEYBOARD
        up.u.ki.wVk=_VK_LEFT; up.u.ki.wScan=scan
        up.u.ki.dwFlags=_KEYEVENTF_EXTENDEDKEY|_KEYEVENTF_KEYUP
        up.u.ki.time=0; up.u.ki.dwExtraInfo=0
    sent=u32.SendInput(n*2,arr,_ctypes.sizeof(_INPUT))
    if sent!=n*2:
        raise RuntimeError("SendInput accepted %d of %d events"%(sent,n*2))
    return sent

def _move_cursor_left_keys(n,careful=False):
    """Paced Left presses -- the slowest, last-resort path, kept only for
    when both UI Automation and a batched SendInput are unavailable. A tight
    unpaced loop outruns the receiving app's input queue and silently drops
    presses (measured: 16, then 30+ characters short on the same move
    through list content), so careful=True sends one key at a time with the
    empirically-tuned 0.01s gap -- reliable across every trial, but ~10s for
    an 800-character move, which is exactly why it is no longer the primary
    mechanism."""
    if n<=0: return
    if careful:
        for _ in range(n):
            keyboard.send("left"); time.sleep(0.01)
        return
    sent=0
    while sent<n:
        batch=min(20,n-sent)
        for _ in range(batch): keyboard.send("left")
        sent+=batch
        if sent<n: time.sleep(0.015)

def _move_cursor_left(n):
    """Last-resort fallback only (UI Automation unavailable or refused).
    One batched SendInput first -- all 2n events in a single syscall, which
    is ~5x faster than n paced keyboard.send() calls and doesn't visibly
    crawl -- then the old paced loop if even that fails."""
    if n<=0: return
    try: _send_left_batch(n); return
    except Exception as e: log("batched SendInput failed:", e)
    _move_cursor_left_keys(n, careful=True)

def _paste_one(text):
    """Paste a single piece of text -- rich if IT has markdown formatting,
    plain otherwise -- with no $| handling. paste_text calls this once per
    side of a split (or once for the whole text when there's no marker)."""
    if _has_fmt(text):
        html=_md_to_html(text); plain=_plain_from_md(text)
        if _set_clipboard_html(html,plain):
            time.sleep(0.08+min(0.3,len(html)/8000)); keyboard.send("ctrl+v")
            time.sleep(0.05+min(0.3,len(html)/8000))
            return "RICH"
        text=plain
    pyperclip.copy(text); time.sleep(0.05+min(0.3,len(text)/8000)); keyboard.send("ctrl+v")
    time.sleep(0.05+min(0.3,len(text)/8000))
    return "PLAIN"

def paste_text(raw, store):
    if not pyperclip or not keyboard:
        log("paste skipped: missing deps"); return
    text=_expand_dynamic(raw, store)
    old=""
    try: old=pyperclip.paste()
    except Exception: pass

    def _restore():
        time.sleep(0.6)
        try: pyperclip.copy(old)
        except Exception: pass

    try:
        # Where the insertion starts, for undo-expansion. Best effort only:
        # a pattern fetched before pasting can be stale (see _uia_text_pattern),
        # so this number is NEVER trusted on its own -- the undo re-reads the
        # document and compares its actual contents before deleting anything.
        LAST_PASTE.clear()
        start_off=None
        try: start_off=_caret_abs_offset(_uia_text_pattern(retries=2))
        except Exception: pass

        end_off=None
        if "$|" not in text:
            kind=_paste_one(text)
            try: end_off=_caret_abs_offset(_uia_text_pattern(retries=1))
            except Exception: pass
            log("pasted", kind)
        else:
            idx=text.index("$|"); before,after=text[:idx],text[idx+2:]
            before,after=_balance_split(before,after)
            # Paste in two halves and put the caret back by absolute document
            # offset (see _set_caret_abs_offset). The offset is read from the
            # real rendered document between the two pastes, so nothing is
            # predicted from the markdown source and nothing is held across
            # the mutation but an integer.
            kind=_paste_one(before) if before else "PLAIN"
            pat=target=None
            try:
                pat=_uia_text_pattern(); target=_caret_abs_offset(pat)
            except Exception as e:
                log("UIA caret read failed:", e); pat=None
            if after: kind=_paste_one(after)
            if pat is not None:
                # caret is at the END of the insertion right now, before it
                # gets moved back to the $| point
                try: end_off=_caret_abs_offset(pat)
                except Exception: pass
            placed=False
            if pat is not None:
                try:
                    was=_caret_screen_pos() if after else None
                    _set_caret_abs_offset(pat, target)
                    if was is not None:
                        time.sleep(0.05)
                        if _caret_screen_pos()==was:
                            # Reported success but the real caret never moved
                            # -- see _caret_screen_pos. Plain Win32 boxes do this.
                            raise RuntimeError("caret did not move (UIA Select was a no-op)")
                    placed=True
                except Exception as e:
                    log("UIA caret restore failed:", e)
            if not placed and after:
                # No UI Automation on this control -- fall back to counting
                # visible characters and moving with real keypresses. Fast
                # (one batched SendInput) but only as accurate as the
                # prediction, which drifts by a few characters across list
                # boundaries.
                _move_cursor_left(_visible_len(after, rich=(kind=="RICH")))
            log("pasted", kind, "(split at $|,", "UIA offset)" if placed else "keypress fallback)")

        if start_off is None and end_off is not None:
            # Pre-paste capture failed (focus still settling). Estimate from
            # the end instead: wrong estimates are harmless because the undo
            # re-reads and verifies the range contents before deleting.
            est=len(_visible_text(text,_has_fmt(text),strip_lists=False))
            start_off=max(0,end_off-est)
        if start_off is not None:
            LAST_PASTE.update({"start":start_off,"end":end_off,
                               "text":text.replace("$|",""),   # marker never lands in the document
                               "rich":_has_fmt(text),"at":time.time()})

    except Exception as e:
        log("paste error:", e)
    finally:
        threading.Thread(target=_restore,daemon=True).start()
