"""
Snippets (brain): the single always-on process. Handles triggers, hotkeys,
paste, tray, AND the glassmorphism editor (one persistent hidden WebView2
window, shown/hidden rather than spawned as a separate process each time).

Threading model (this is what makes one process safe here):
  - keyboard's global hook runs on its own self-contained thread (unchanged).
  - pystray's tray icon runs via run_detached(), which is pystray's own
    documented mechanism for running its message loop on a background thread
    instead of demanding the main thread.
  - pywebview's webview.start() hard-requires the ACTUAL main thread (it
    raises if called from anywhere else), so it owns the main thread and is
    the final blocking call in main(). Nothing else contends for it.
The original 2-process split crashed because of a real bug (see editor
window's EditorApi below), not because splitting was the only way to avoid
WebView2/tray/hook conflicts -- with that bug fixed and pystray on
run_detached(), the three subsystems no longer fight over a thread.

SETUP: pip install keyboard pyperclip pystray pillow pywebview
Run:   python brain.py
"""
import os, sys, time, json, threading, subprocess, ctypes
import snip_core as core
import updater
from snip_core import log, APP_NAME, SEARCH_HOTKEY, DATA_FILE

# ---------------------------------------------------------------------------
# Shown in the About sheet (the ? button in the editor). Single source for
# the credit and version -- nothing else hard-codes either.
AUTHOR  = "Vinay Prasad"
VERSION = "1.0.1"
# ---------------------------------------------------------------------------

MAIN_HOTKEY="ctrl+alt+n"
store=core.Store(DATA_FILE)

# ---- single-instance guard: a named OS mutex, not a lock file. A lock file
# can be left behind by a crash and wrongly block every future launch; a
# named mutex is held by the OS and is automatically released the instant
# the owning process exits for ANY reason (clean exit, crash, kill -- this is
# exactly what "old build still running as a ghost" needs to stop causing). ----
_ERROR_ALREADY_EXISTS=183
_instance_mutex=None
def _acquire_single_instance():
    global _instance_mutex
    h=ctypes.windll.kernel32.CreateMutexW(None, False, "SnippetsApp_SingleInstance_Mutex")
    already=(ctypes.windll.kernel32.GetLastError()==_ERROR_ALREADY_EXISTS)
    if already:
        try: ctypes.windll.kernel32.CloseHandle(h)
        except Exception: pass
        return False
    _instance_mutex=h   # keep a live reference for the process lifetime
    return True

try: import keyboard
except Exception: keyboard=None
try:
    import pystray
    from PIL import Image as PILImage, ImageDraw as PILDraw
except Exception: pystray=None
try: import webview
except Exception: webview=None

log("=== Snippets (brain) starting ===")

# ---- single-hook triggers + hotkeys (no add_hotkey churn -> no access violation) ----
_buffer=""; _BUF=48; _expanding=False; _last_hk=0
_UNDO_WINDOW=20          # seconds after an expansion that backspace still undoes it
_last_fire={"trigger":None,"at":0}

def _all_triggers():
    m=[]
    for s in store.data.get("snippets",[]):
        for t in s.get("triggers",[]):
            t=(t or "").strip()
            if t: m.append((t,s.get("text",""),bool(s.get("word_only"))))
    m.sort(key=lambda x:-len(x[0])); return m
def _word_boundary_ok(buffer,trig):
    """When a trigger is marked word_only, block it firing mid-word (e.g. a
    bare "ther" trigger matching inside "other"). Checks the character
    immediately before the match in the buffer; if that index doesn't exist
    (match at/near the start of the buffer), allow it -- can't prove there's
    a preceding word character, so err permissive rather than block a
    legitimate trigger typed right after a fresh launch or an Enter."""
    idx=len(buffer)-len(trig)-1
    if idx<0: return True
    return not buffer[idx].isalnum()
def _hotkey_map():
    m={}
    for s in store.data.get("snippets",[]):
        hk=(s.get("hotkey") or "").strip().lower()
        if hk: m[hk]=("paste",s.get("text",""))
    m[SEARCH_HOTKEY]=("search",None); m[MAIN_HOTKEY]=("main",None); return m
def _combo_active(combo):
    try: return all(keyboard.is_pressed(p.strip()) for p in combo.split("+") if p.strip())
    except Exception: return False
def _on_key(e):
    global _buffer,_expanding,_last_hk
    if not keyboard: return
    try:
        n=e.name
        if n and n not in ("ctrl","alt","shift","left ctrl","right ctrl","left alt","right alt","left shift","right shift"):
            if keyboard.is_pressed("ctrl") or keyboard.is_pressed("alt"):
                now=time.time()
                if now-_last_hk>0.4:
                    for combo,(kind,text) in _hotkey_map().items():
                        if _combo_active(combo):
                            _last_hk=now
                            if kind=="paste":
                                threading.Thread(target=lambda t=text:(time.sleep(0.05),core.paste_text(t,store)),daemon=True).start()
                            elif kind=="search": open_search()
                            elif kind=="main": open_editor()
                            return
        if _expanding or not store.data.get("enabled",True): return
        if n is None: return
        # A trigger fires when followed by a TERMINATOR (space/enter/tab) so that
        # typing ':acknowledge' doesn't prematurely fire ':ack'. This also stops
        # mid-word false-positives. Punctuation terminators could be added too.
        is_term = n in ("space","enter","tab")
        if n=="space":
            # check for a trigger match BEFORE adding the space
            if _try_fire(): return
            _buffer+=" "
        elif n=="enter":
            _try_fire(); _buffer=""; return
        elif n=="tab":
            if _try_fire(): return
            _buffer=""
        elif n=="backspace":
            # Backspace as the very first key after an expansion undoes it.
            # _buffer is empty only when nothing has been typed since.
            if (not _buffer and _last_fire.get("trigger")
                    and time.time()-_last_fire.get("at",0) < _UNDO_WINDOW
                    and core.LAST_PASTE.get("start") is not None):
                trig=_last_fire["trigger"]; info=dict(core.LAST_PASTE)
                _last_fire["trigger"]=None
                threading.Thread(target=_undo_expansion,args=(trig,info),daemon=True).start()
                return
            _buffer=_buffer[:-1]; return
        elif len(n)==1:
            _buffer+=n
            # also allow immediate fire if this exact buffer is a trigger AND no
            # longer trigger shares it as a prefix (so unique triggers still feel instant)
            _try_fire(require_unique=True)
        else: return
        if len(_buffer)>_BUF: _buffer=_buffer[-_BUF:]
    except Exception as ex: log("key err:",ex)

def _try_fire(require_unique=False):
    """Fire a matching trigger. If require_unique, only fire when no LONGER
    trigger has the current buffer as a prefix (avoids ':ack' firing while you're
    still typing ':acknowledge'). Returns True if something fired."""
    global _buffer,_expanding
    trigs=_all_triggers()
    for trig,text,word_only in trigs:
        if _buffer.endswith(trig):
            if require_unique:
                # is there a longer trigger that starts with this same trigger?
                if any(other!=trig and other.startswith(trig) for other,_,_ in trigs):
                    return False   # wait, a longer trigger might still be coming
            if word_only and not _word_boundary_ok(_buffer,trig):
                continue   # mid-word match on a word_only trigger -- keep looking
            _expanding=True; _buffer=""
            def _do(k=len(trig),body=text,_trig=trig):
                global _expanding
                try:
                    # detect {{Label}} placeholders BEFORE touching the
                    # document at all -- if the user cancels the popup,
                    # nothing has been backspaced or pasted yet.
                    specs=core.extract_field_specs(body)
                    if specs:
                        values=_fillin_popup(specs)
                        if values is None: return   # canceled: leave everything untouched
                        body=core.apply_fields(body,values)
                    time.sleep(0.02)
                    for _ in range(k): core.keyboard.send("backspace")
                    time.sleep(0.02); core.paste_text(body,store)
                    _last_fire.update({"trigger":_trig,"at":time.time()})
                except Exception as ex: log("expand err:",ex)
                finally: time.sleep(0.12); _expanding=False
            threading.Thread(target=_do,daemon=True).start()
            return True
    return False

def _undo_expansion(trigger, info):
    """Revert the expansion that just happened and put the typed trigger back.

    Deleting text is destructive and irreversible, so the range is VERIFIED
    before anything is removed: the recorded start offset is only a hint (a
    pattern fetched before pasting can be stale and report 0 -- see
    _uia_text_pattern), so this re-reads what is actually sitting in the
    document between the recorded start and the live caret and compares it,
    ignoring whitespace, against what was pasted. Any mismatch aborts and
    leaves the document alone. Failing to undo is a minor annoyance;
    deleting the wrong text is not."""
    global _expanding,_buffer
    try:
        time.sleep(0.14)                       # let the user's backspace land first
        start=info.get("start")
        if start is None: return
        pat=core._uia_text_pattern()
        want=core._squash(core._visible_text(info.get("text",""),info.get("rich",True),strip_lists=False))
        # The caret sits at the $| point, i.e. in the MIDDLE of the insertion,
        # so the end comes from what was recorded at paste time. The user's
        # backspace has usually already removed a character, which shortens
        # the document -- try the exact end first (reaching it fails once the
        # document is shorter) and fall back a character at a time.
        # List markers are kept in the expected text here: UI Automation
        # reports the auto-generated "1. " numbers as real document
        # characters, even though they are not arrow-key navigable.
        end=info.get("end")
        if end is None: end=core._caret_abs_offset(pat)
        rng=None; got=""
        for cand in (end,end-1,end-2):
            if cand<=start: continue
            try: r=core._select_abs_range(pat,start,cand)
            except Exception: continue
            got=core._squash(r.GetText(-1))
            if core.matches_pasted(got, want):
                rng=r; break
        if rng is None:
            log("undo aborted: document does not match what was pasted"); return
        # Select() can report success and still leave the real selection
        # collapsed at the caret -- the same silent no-op class that
        # _caret_screen_pos guards against for caret placement. Observed
        # intermittently here: the delete then removed a single character and
        # the trigger was typed into the MIDDLE of the snippet. So confirm the
        # app really is holding the selection we asked for, and retry a couple
        # of times before giving up, since it appears to be a settling race.
        selected=False
        for attempt in range(3):
            try:
                rng.Select(waitTime=0)
            except Exception as e:
                log("undo: Select() raised:", e)
            time.sleep(0.08+0.07*attempt)
            try: live=core._squash(pat.GetSelection()[0].GetText(-1))
            except Exception as e:
                log("undo aborted: cannot read back the selection:", e); return
            if live==got: selected=True; break
        if not selected:
            log("undo aborted: selection did not take (%d chars selected, expected %d)"
                %(len(live),len(got)))
            return
        _expanding=True                        # our own keystrokes must not re-fire
        try:
            core.keyboard.send("delete")
            time.sleep(0.05)
            core.keyboard.write(trigger, exact=False)
            time.sleep(0.05)
        finally:
            _buffer=""; _expanding=False
        log("undid expansion of", trigger)
    except Exception as ex:
        log("undo failed:", ex)
    finally:
        core.LAST_PASTE.clear()

def _warm_keys():
    """All individual key names used by any configured hotkey, plus the base
    modifiers. keyboard.is_pressed() lazily resolves a key name to scan code(s)
    the first time it's asked about that name -- warming only shift/ctrl left
    every OTHER key (digits, letters) unresolved until first real use, which is
    why a brand-new hotkey needed an older one pressed first to "wake up"."""
    parts={"shift","ctrl","alt"}
    for hk in _hotkey_map().keys():
        for p in hk.split("+"):
            p=p.strip()
            if p: parts.add(p)
    return parts

def _warm(tag="startup"):
    try:
        keys=_warm_keys()
        for _ in range(5):
            for k in keys: keyboard.is_pressed(k)
            time.sleep(0.1)
        log("hook warmed (%s, %d keys)"%(tag,len(keys)))
    except Exception as e: log("warm err:",e)

def start_hook():
    if not keyboard: log("KEYBOARD DISABLED"); return
    try:
        # Warm SYNCHRONOUSLY, BEFORE on_press() is registered. keyboard's
        # is_pressed() and on_press() share one lazily-started singleton
        # listener thread; if a real keystroke is the very first thing to ever
        # touch it, that keystroke can race the listener's own startup on a
        # slow/loaded machine and get missed or misread -- which would explain
        # "the trigger I try first sometimes doesn't fire, but works after
        # that" without it being tied to which snippet is oldest/newest at
        # all. Touching it here, before on_press is even registered, means no
        # real keystroke can ever be first through that door.
        try:
            for k in _warm_keys(): keyboard.is_pressed(k)
        except Exception as e: log("pre-warm err:",e)
        keyboard.on_press(_on_key); log("hook started")
        threading.Thread(target=_warm,daemon=True).start()
        # watch the snippets file: reload when the editor saves changes, so
        # deleted/edited snippets take effect immediately (fixes "deleted trigger
        # still fires": the brain was holding a stale in-memory copy). Also
        # re-warm on every reload so a hotkey added while the brain is already
        # running gets its key(s) resolved immediately, not on next use.
        def _watch():
            last=None
            while True:
                try:
                    m=os.path.getmtime(DATA_FILE)
                    if last is not None and m!=last:
                        store.load(); log("snippets reloaded (file changed)")
                        threading.Thread(target=_warm,args=("reload",),daemon=True).start()
                    last=m
                except Exception: pass
                time.sleep(1.0)
        threading.Thread(target=_watch,daemon=True).start()
    except Exception as e: log("hook failed:",e)

# ---- editor: one persistent hidden WebView2 window, shown/hidden on demand ----
_wv_dir=os.path.join(core.DATA_DIR,"wv2data")
try:
    os.makedirs(_wv_dir,exist_ok=True); os.environ["WEBVIEW2_USER_DATA_FOLDER"]=_wv_dir
except Exception as e: _wv_dir=None; log("wv dir err:",e)

def _resource(n):
    base=getattr(sys,"_MEIPASS",os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base,n)

class EditorApi:
    # deliberately NO attribute holding the webview Window object here.
    # pywebview's inject_pywebview() walks every non-underscore attribute on
    # the js_api instance via dir() to build the JS bridge; a public `.window`
    # attribute made it descend into window.native (the raw .NET WebView2
    # control) and recurse without bound through AccessibilityObject/Owner
    # chains -- that WAS the "maximum recursion depth" crash. Show/hide of the
    # window is handled by module-level _editor_window instead, never exposed
    # to the js_api object.
    def get_state(self):
        store.load()
        return {"snippets":store.data.get("snippets",[]),"enabled":store.data.get("enabled",True),
                "author":AUTHOR,"version":VERSION}
    def save_snippets(self,snippets):
        try:
            clean=[]
            for i,s in enumerate(snippets or []):
                trigs=[t.strip() for t in (s.get("triggers") or []) if t and t.strip()]
                tags=[t.strip() for t in (s.get("tags") or []) if t and t.strip()]
                clean.append({"id":s.get("id") or "s%d"%(i+1),"triggers":trigs,
                    "hotkey":(s.get("hotkey") or "").strip(),"label":(s.get("label") or "").strip(),"text":s.get("text") or "",
                    "tags":tags,"word_only":bool(s.get("word_only")),"scope":s.get("scope") or "personal"})
            dup_trig,dup_label=core._find_duplicate_trigger(clean)
            if dup_trig:
                return {"ok":False,"error":'"%s" is already used by "%s"'%(dup_trig,dup_label)}
            store.data["snippets"]=clean; return {"ok":store.save()}
        except Exception as e: log("save err:",e); return {"ok":False}
    def set_enabled(self,on):
        store.data["enabled"]=bool(on); store.save(); return {"ok":True}

    # File dialogs reference the module-level _editor_window global directly
    # (never store it on self) -- see the note atop this class on why a
    # public window attribute on the js_api object is the exact thing that
    # caused the earlier reflection-recursion crash.
    def export_snippet(self,sid):
        try:
            s=next((x for x in store.data.get("snippets",[]) if x.get("id")==sid),None)
            if not s: return {"ok":False,"error":"snippet not found"}
            name=(s.get("label") or "snippet").strip().replace("/","-").replace("\\","-") or "snippet"
            paths=_editor_window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename=name+".json", file_types=("JSON files (*.json)",))
            if not paths: return {"ok":False,"cancelled":True}
            path=paths if isinstance(paths,str) else paths[0]
            with open(path,"w",encoding="utf-8") as f: json.dump(s,f,indent=2,ensure_ascii=False)
            return {"ok":True}
        except Exception as e: log("export_snippet err:",e); return {"ok":False,"error":str(e)}

    def export_all(self):
        try:
            paths=_editor_window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename="snippets_export.json", file_types=("JSON files (*.json)",))
            if not paths: return {"ok":False,"cancelled":True}
            path=paths if isinstance(paths,str) else paths[0]
            with open(path,"w",encoding="utf-8") as f:
                json.dump({"snippets":store.data.get("snippets",[])},f,indent=2,ensure_ascii=False)
            return {"ok":True}
        except Exception as e: log("export_all err:",e); return {"ok":False,"error":str(e)}

    def import_snippets(self):
        try:
            paths=_editor_window.create_file_dialog(webview.FileDialog.OPEN, file_types=("JSON files (*.json)",))
            if not paths: return {"ok":False,"cancelled":True}
            path=paths if isinstance(paths,str) else paths[0]
            with open(path,encoding="utf-8") as f: data=json.load(f)
            if isinstance(data,dict) and isinstance(data.get("snippets"),list): incoming=data["snippets"]
            elif isinstance(data,dict): incoming=[data]
            elif isinstance(data,list): incoming=data
            else: return {"ok":False,"error":"unrecognized file format"}
            existing=store.data.get("snippets",[])
            seen_triggers=set()
            for s in existing:
                for t in (s.get("triggers") or []):
                    t=(t or "").strip()
                    if t: seen_triggers.add(t)
            imported=0; skipped=[]
            for i,item in enumerate(incoming):
                if not isinstance(item,dict): continue
                trigs=[t.strip() for t in (item.get("triggers") or []) if t and t.strip()]
                conflict=next((t for t in trigs if t in seen_triggers),None)
                if conflict:
                    skipped.append("%s (%s already in use)"%(item.get("label") or "Untitled",conflict)); continue
                clean={"id":"imp%d_%d"%(int(time.time()*1000),i),"triggers":trigs,
                    "hotkey":(item.get("hotkey") or "").strip(),"label":(item.get("label") or "").strip(),
                    "text":item.get("text") or "","tags":[t.strip() for t in (item.get("tags") or []) if t and t.strip()],
                    "word_only":bool(item.get("word_only")),"scope":"personal"}
                existing.append(clean)
                for t in trigs: seen_triggers.add(t)
                imported+=1
            store.data["snippets"]=existing
            ok=store.save()
            msg="Imported %d"%imported
            if skipped: msg+=", skipped %d: %s"%(len(skipped),"; ".join(skipped))
            return {"ok":ok,"message":msg,"imported":imported,"skipped":len(skipped)}
        except Exception as e: log("import_snippets err:",e); return {"ok":False,"error":str(e)}

_editor_window=None
def _init_editor_window():
    """Create the editor window once, hidden, at startup. It is never
    destroyed -- closing it (the X button) just hides it -- so it stays ready
    for instant reopen and webview.start() has a window to run its loop for."""
    global _editor_window
    if webview is None: log("WEBVIEW DISABLED"); return
    try:
        api=EditorApi()
        _editor_window=webview.create_window(APP_NAME, _resource("editor_ui.html"), js_api=api,
            width=940, height=650, min_size=(820,560), background_color="#2a1a5e", hidden=True)
        def _on_closing():
            _editor_window.hide(); return False   # cancel the real close, hide instead
        _editor_window.events.closing += _on_closing
    except Exception as e:
        log("editor window init failed:", e)

def open_editor(icon=None,item=None):
    if _editor_window is None:
        log("editor window unavailable"); return
    try:
        _editor_window.show()
        try: _editor_window.evaluate_js("boot(0)")   # refresh state in case the file changed
        except Exception: pass
    except Exception as e:
        log("open editor err:", e)
def open_search(icon=None,item=None):
    threading.Thread(target=_search_popup,daemon=True).start()

def open_restore(icon=None,item=None):
    threading.Thread(target=_restore_popup,daemon=True).start()

def _restore_popup():
    """Pick a snapshot to roll back to. Restoring is itself a save, so it
    gets backed up too -- an accidental restore is undoable."""
    try: import tkinter as tk
    except Exception as e: log("tk missing:",e); return
    from tkinter import messagebox
    backups=core.list_backups()
    win=tk.Tk(); win.title("Restore a backup"); win.geometry("470x340")
    win.configure(bg="#1a1330"); win.attributes("-topmost",True)
    tk.Label(win,text="Restore snippets from an earlier point",bg="#1a1330",fg="#e8e2f5",
             font=("Segoe UI",11,"bold")).pack(anchor="w",padx=14,pady=(14,2))
    if not backups:
        tk.Label(win,text="No backups yet -- one is saved automatically\neach time your snippets change.",
                 bg="#1a1330",fg="#a99fc4",justify="left",font=("Segoe UI",10)).pack(anchor="w",padx=14,pady=8)
        tk.Button(win,text="Close",command=win.destroy,relief="flat",bg="#2a2145",fg="#fff",
                  font=("Segoe UI",10)).pack(pady=12)
        _force_foreground(win); win.mainloop(); return
    tk.Label(win,text="Your current snippets are backed up before restoring.",bg="#1a1330",
             fg="#a99fc4",font=("Segoe UI",9)).pack(anchor="w",padx=14,pady=(0,8))
    lb=tk.Listbox(win,bg="#150f28",fg="#e8e2f5",relief="flat",font=("Segoe UI",10),
                  selectbackground="#7c5cff",highlightthickness=0,activestyle="none")
    lb.pack(fill="both",expand=True,padx=14,pady=(0,10))
    for _p,when,cnt in backups:
        lb.insert("end","  %s      %s"%(when.strftime("%d %b %Y  %H:%M:%S"),
                                        "%d snippets"%cnt if cnt>=0 else "unreadable"))
    lb.selection_set(0)
    def do_restore():
        sel=lb.curselection()
        if not sel: return
        path,when,cnt=backups[sel[0]]
        if not messagebox.askyesno("Restore backup",
                "Replace your current snippets with the %d from %s?\n\n"
                "Your current set is backed up first, so this can be undone."
                %(cnt,when.strftime("%d %b %Y %H:%M:%S")),parent=win): return
        try:
            store.load(); core.restore_backup(store,path)
            log("restored backup",os.path.basename(path))
            try:
                if _editor_window is not None: _editor_window.evaluate_js("boot(0)")
            except Exception: pass
            messagebox.showinfo("Restored","Restored %d snippets."%cnt,parent=win); win.destroy()
        except Exception as e:
            log("restore failed:",e); messagebox.showerror("Restore failed",str(e),parent=win)
    row=tk.Frame(win,bg="#1a1330"); row.pack(fill="x",padx=14,pady=(0,14))
    tk.Button(row,text="Cancel",command=win.destroy,relief="flat",bg="#2a2145",fg="#e8e2f5",
              font=("Segoe UI",10),padx=14).pack(side="right")
    tk.Button(row,text="Restore",command=do_restore,relief="flat",bg="#7c5cff",fg="#fff",
              font=("Segoe UI",10,"bold"),padx=14).pack(side="right",padx=(0,8))
    lb.bind("<Double-Button-1>",lambda e:do_restore()); win.bind("<Escape>",lambda e:win.destroy())
    _force_foreground(win); win.mainloop()

def _search_popup():
    try: import tkinter as tk
    except Exception as e: log("tk missing:",e); return
    store.load()
    win=tk.Tk(); win.title("Snippets"); win.geometry("460x360"); win.configure(bg="#1a1330"); win.attributes("-topmost",True)
    ent=tk.Entry(win,bg="#2a2145",fg="#fff",insertbackground="#fff",relief="flat",font=("Segoe UI",12)); ent.pack(fill="x",padx=12,pady=12); ent.focus_force()
    lb=tk.Listbox(win,bg="#150f28",fg="#e8e2f5",relief="flat",font=("Segoe UI",11),selectbackground="#7c5cff",highlightthickness=0,activestyle="none"); lb.pack(fill="both",expand=True,padx=12,pady=(0,12))
    items=store.data.get("snippets",[]); mp=[]
    def refresh(*_):
        q=ent.get().lower(); lb.delete(0,tk.END); mp.clear()
        for s in items:
            hay=(s.get("label","")+" "+" ".join(s.get("triggers",[]))+" "+s.get("text","")).lower()
            if not q or q in hay: lb.insert("end","%-22s %s"%(s.get("label","?")," ".join(s.get("triggers",[])))); mp.append(s)
        if lb.size(): lb.selection_set(0)
    def choose(*_):
        sel=lb.curselection()
        if not sel: return
        s=mp[sel[0]]; win.destroy(); time.sleep(0.25); core.paste_text(s.get("text",""),store)
    ent.bind("<KeyRelease>",refresh); ent.bind("<Down>",lambda e:(lb.focus_set(),lb.selection_set(0)))
    ent.bind("<Return>",choose); lb.bind("<Return>",choose); lb.bind("<Double-Button-1>",choose); win.bind("<Escape>",lambda e:win.destroy())
    refresh(); win.mainloop()

def _force_foreground(win):
    """tkinter's focus_force()/lift() alone don't reliably steal real
    keyboard focus away from whatever the user is currently in when this
    popup is triggered from a background process that's been idle a while --
    which is the normal, realistic way it's ever triggered (Windows
    restricts background processes from stealing foreground focus by
    default). Borrow the current foreground thread's input state just long
    enough to force it -- the standard, widely-used AttachThreadInput
    technique; doesn't need admin rights."""
    try:
        win.update()
        hwnd=win.winfo_id()
        u=ctypes.windll.user32; k=ctypes.windll.kernel32
        fg=u.GetForegroundWindow()
        cur=k.GetCurrentThreadId()
        fg_thread=u.GetWindowThreadProcessId(fg,None)
        if fg_thread and fg_thread!=cur:
            u.AttachThreadInput(fg_thread,cur,True)
            u.SetForegroundWindow(hwnd)
            u.AttachThreadInput(fg_thread,cur,False)
        else:
            u.SetForegroundWindow(hwnd)
    except Exception as e:
        log("force foreground failed:",e)

def _fillin_popup(specs):
    """Prompt for a value for each placeholder before an expansion proceeds.
    Takes (label, choices) pairs: a choices list renders a dropdown, an empty
    one a free text box. Blocks -- like _search_popup, this only ever runs on
    its own dedicated per-expansion thread. Returns {label: value}, or None if
    canceled (caller must leave the document untouched on None: no
    backspacing, no paste). Auto-cancels after 5 minutes as a safety net --
    _expanding stays True the whole time this is open, which silently blocks
    other plain triggers (hotkeys are unaffected; that check runs earlier in
    _on_key), so a forgotten popup shouldn't be able to wedge that forever."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as e: log("tk missing:",e); return None
    specs=[(l,c) for l,c in specs]
    result={"values":None}
    win=tk.Tk(); win.title("Snippets"); win.configure(bg="#1a1330"); win.attributes("-topmost",True)
    win.geometry("420x%d"%(78+46*len(specs)))
    tk.Label(win,text="Fill in the blanks",bg="#1a1330",fg="#e8e2f5",font=("Segoe UI",11,"bold")).pack(pady=(14,8))
    style=ttk.Style()
    try: style.theme_use("clam")
    except Exception: pass
    style.configure("S.TCombobox",fieldbackground="#2a2145",background="#2a2145",
                    foreground="#ffffff",arrowcolor="#c9c0e6",borderwidth=0)
    getters=[]
    for label,choices in specs:
        row=tk.Frame(win,bg="#1a1330"); row.pack(fill="x",padx=14,pady=4)
        tk.Label(row,text=label,bg="#1a1330",fg="#b8b0d0",font=("Segoe UI",9),
                 width=16,anchor="w").pack(side="left")
        if choices:
            var=tk.StringVar(value=choices[0])
            cb=ttk.Combobox(row,textvariable=var,values=choices,state="readonly",
                            style="S.TCombobox",font=("Segoe UI",11))
            cb.pack(side="left",fill="x",expand=True)
            getters.append((label,var.get,cb))
        else:
            ent=tk.Entry(row,bg="#2a2145",fg="#fff",insertbackground="#fff",relief="flat",
                         font=("Segoe UI",11))
            ent.pack(side="left",fill="x",expand=True)
            getters.append((label,ent.get,ent))
    def submit(*_):
        result["values"]={lbl:get() for lbl,get,_w in getters}; win.destroy()
    def cancel(*_): win.destroy()
    btnrow=tk.Frame(win,bg="#1a1330"); btnrow.pack(fill="x",padx=14,pady=12)
    tk.Button(btnrow,text="Cancel",command=cancel,relief="flat").pack(side="right",padx=(6,0))
    tk.Button(btnrow,text="Insert",command=submit,relief="flat",bg="#7c5cff",fg="#fff").pack(side="right")
    win.bind("<Return>",submit); win.bind("<Escape>",cancel)
    _force_foreground(win)
    if getters: getters[0][2].focus_force()
    win.after(5*60*1000,cancel)
    win.mainloop()
    return result["values"]

# ---- run at Windows startup: a shortcut in the Startup folder, no admin/registry ----
_STARTUP_DIR=os.path.join(os.environ.get("APPDATA",""),"Microsoft","Windows","Start Menu","Programs","Startup")
_SHORTCUT_PATH=os.path.join(_STARTUP_DIR,APP_NAME+".lnk")

def _autostart_enabled():
    return os.path.exists(_SHORTCUT_PATH)

def _set_autostart(enable):
    try:
        if not enable:
            if os.path.exists(_SHORTCUT_PATH): os.remove(_SHORTCUT_PATH)
            log("autostart disabled"); return True
        if getattr(sys,"frozen",False):
            target=sys.executable; args=""; workdir=os.path.dirname(target)
        else:
            target=sys.executable; args='"%s"'%os.path.abspath(__file__)
            workdir=os.path.dirname(os.path.abspath(__file__))
        os.makedirs(_STARTUP_DIR,exist_ok=True)
        ps=(
            '$s=New-Object -ComObject WScript.Shell;'
            '$sc=$s.CreateShortcut(%s);'
            '$sc.TargetPath=%s;'
            '$sc.Arguments=%s;'
            '$sc.WorkingDirectory=%s;'
            '$sc.Save()'
        ) % tuple(('"%s"'%p.replace('"','""')) for p in (_SHORTCUT_PATH,target,args,workdir))
        subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command",ps],
            creationflags=subprocess.CREATE_NO_WINDOW, check=True, capture_output=True)
        log("autostart enabled ->", target); return True
    except Exception as e:
        log("autostart err:", e); return False

def _toggle_autostart(icon=None,item=None):
    _set_autostart(not _autostart_enabled())

# ---- self-update -----------------------------------------------------------
_pending_update = {"manifest": None}

def _release_single_instance():
    """Drop the single-instance mutex so a replacement process can start.
    Without this the updated exe launches, finds the mutex still held by the
    process that is on its way out, and shows 'already running' instead of
    coming back up."""
    global _instance_mutex
    try:
        if _instance_mutex:
            ctypes.windll.kernel32.CloseHandle(_instance_mutex)
            _instance_mutex = None
    except Exception:
        pass

def _notify(title, message):
    try:
        if _tray is not None: _tray.notify(message, title)
    except Exception:
        pass

def _refresh_tray():
    try:
        if _tray is not None: _tray.update_menu()
    except Exception:
        pass

def _update_label(i=None):
    m = _pending_update["manifest"]
    return ("Install update v%s" % m.get("version")) if m else "Check for updates"

def _check_for_updates(background=False):
    m = updater.check(VERSION)
    _pending_update["manifest"] = m
    _refresh_tray()
    if m:
        if updater.config()["auto"]:
            log("auto-update policy is on, installing v%s" % m.get("version"))
            _install_update(); return m
        _notify("Snippets", "Version %s is available. Open the tray menu to install."
                % m.get("version"))
    elif not background:
        _notify("Snippets", "You are on the latest version (v%s)." % VERSION)
    return m

def _install_update():
    """Download, verify, swap the executable, and restart into it."""
    m = _pending_update["manifest"]
    try:
        if not m:
            m = _check_for_updates()
            if not m: return
        if not updater.can_self_update():
            if not getattr(sys, "frozen", False):
                _notify("Snippets", "Running from source, so there is nothing to update.")
            else:
                _notify("Snippets", "Cannot update: no write access to the install folder.")
            return
        _notify("Snippets", "Downloading version %s..." % m.get("version"))
        exe = updater.perform(m)
        log("updated to", m.get("version"), "- restarting")
        _release_single_instance()
        subprocess.Popen([exe], close_fds=True)
        try:
            if _tray is not None: _tray.stop()
        except Exception:
            pass
        os._exit(0)
    except Exception as e:
        log("update failed:", e)
        _notify("Snippets", "Update failed: %s" % e)

def _menu_update(icon=None, item=None):
    threading.Thread(target=_install_update if _pending_update["manifest"]
                     else _check_for_updates, daemon=True).start()

def _start_update_watch():
    """One check shortly after startup, then every six hours. Failures are
    silent by design: a machine that cannot reach the update source should
    still be a perfectly good text expander."""
    if updater.config()["disabled"]:
        log("self-update disabled by policy"); return
    def loop():
        time.sleep(20)                       # let the app finish starting first
        while True:
            try: _check_for_updates(background=True)
            except Exception as e: log("update watch error:", e)
            time.sleep(6*60*60)
    threading.Thread(target=loop, daemon=True).start()

# ---- tray ----
_tray=None
def _img():
    im=PILImage.new("RGBA",(64,64),(0,0,0,0)); d=PILDraw.Draw(im)
    on=store.data.get("enabled",True); col=(124,92,255,255) if on else (120,120,130,255)
    d.rounded_rectangle([6,6,58,58],radius=16,fill=(26,19,48,255),outline=col,width=3)
    d.line([20,32,28,42],fill=col,width=6); d.line([28,42,45,22],fill=col,width=6); return im
def _toggle(i=None,it=None):
    store.load(); store.data["enabled"]=not store.data.get("enabled",True); store.save()
    try: _tray.icon=_img()
    except Exception: pass
def _quit(i=None,it=None):
    try: _tray.stop()
    except Exception: pass
    os._exit(0)
def start_tray():
    global _tray
    if pystray is None: log("TRAY DISABLED"); return
    menu=pystray.Menu(
        pystray.MenuItem("Edit snippets", open_editor, default=True),
        pystray.MenuItem("Search", open_search),
        pystray.MenuItem(lambda i:("Disable" if store.data.get("enabled",True) else "Enable")+" expansion", _toggle),
        pystray.MenuItem("Restore backup...", open_restore),
        pystray.MenuItem(_update_label, _menu_update),
        pystray.MenuItem("Start with Windows", _toggle_autostart, checked=lambda i: _autostart_enabled()),
        pystray.MenuItem("Quit", _quit))
    _tray=pystray.Icon(APP_NAME,_img(),APP_NAME,menu); log("tray starting")
    # run_detached() runs pystray's message loop on its OWN background thread
    # (pystray's own supported mechanism for this) instead of blocking here --
    # webview.start() below needs the actual main thread for itself.
    _tray.run_detached()

def main():
    if not _acquire_single_instance():
        log("another instance is already running -- exiting")
        try:
            ctypes.windll.user32.MessageBoxW(None,
                "Snippets is already running (check your system tray).",
                APP_NAME, 0x40)   # MB_ICONINFORMATION
        except Exception: pass
        return
    updater.cleanup_old()          # delete the previous exe left by an update
    start_hook()
    log("Ready. %d snippet(s). v%s"%(len(store.data.get("snippets",[])), VERSION))
    if pystray is not None: start_tray()
    else: log("TRAY DISABLED")
    _start_update_watch()
    _init_editor_window()
    if webview is not None and _editor_window is not None:
        try: webview.start(storage_path=(_wv_dir or None))
        except TypeError: webview.start()
    else:
        while True: time.sleep(3600)
if __name__=="__main__": main()
