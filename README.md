# Snippets

A Windows text expander. Type a short trigger like `:review` anywhere and it
expands into a full, formatted block of text, with bold, lists and links
preserved, the cursor left exactly where you want it, and prompts for the bits
that change each time.

Single portable `.exe`. No installer, no admin rights, no runtime to install.

## Features

- **Triggers**: one or many per snippet (`:review`, `:rev`), with optional
  whole-word-only matching and an optional global hotkey.
- **Rich text**: bold, italic, links, inline `code`, horizontal rules, and
  numbered or bulleted lists, pasted as real formatted text.
- **`$|` cursor marker**: the caret lands exactly there after expanding,
  instantly, with no visible cursor travel. Placement is done through the
  Windows accessibility layer in a single operation, so it stays accurate and
  fast no matter how long the snippet is.
- **`{{Field}}` prompts**: asks for a value before expanding.
  `{{Status|Open,Blocked,Done}}` renders a dropdown instead of a text box.
- **`{date}`, `{time}`, `{clip}`**: resolved fresh on every expansion.
- **Undo**: press Backspace immediately after an expansion to put your
  trigger back.
- **Duplicate-trigger protection**: the editor refuses to save two snippets
  that share a trigger, so a snippet can never be shadowed.
- **Tags and search**: filter the list by tag chips or free text.
- **Automatic backups**: every change to your snippets is snapshotted first,
  and any of the last 20 can be restored from the tray menu.
- **Import and export**: JSON, one snippet or all of them.
- **Follows your Windows light and dark theme.**

## Install

Download `Snippets.exe` from [Releases](../../releases) and run it. It sits in
the system tray.

Right-click the tray icon for **Edit snippets**, **Search**, **Restore
backup...**, **Start with Windows**, and to enable or disable expansion.

An **About and help** page is built into the editor, behind the `?` button in
the top bar. It explains every field and marker.

## Build from source

Requires Python 3.10 or newer on Windows.

```
pip install pywebview pystray pillow keyboard pyperclip uiautomation comtypes pyinstaller
BUILD.bat
```

`BUILD.bat` runs the test suite first and refuses to build if it fails. The
exe appears in `dist\Snippets.exe`.

## Tests

```
python tests/test_core.py     fast, headless, no GUI (about 0.2s)
python tests/test_live.py     end-to-end against real editing surfaces
python tests/test_ui.py       renders the editor and checks it wired up
```

`test_live.py` is the layer that matters. It verifies cursor placement against
independent ground truth: for a web-based editor it reads the page's own
JavaScript selection API, and for a plain Windows text box it types a marker
at the caret and reads the document back. Those two surfaces fail in different
ways, so both are covered.

## Where your data lives

```
%APPDATA%\SnippetsApp\snippets.json     your snippets
%APPDATA%\SnippetsApp\backups\          the last 20 snapshots
%APPDATA%\SnippetsApp\snippets.log      activity log
```

Your snippets are stored **outside the project folder** and are never bundled
into the exe or committed to this repo. A fresh install starts with six example
snippets that demonstrate each feature.

## Author

Created by **Vinay Prasad**.

## Licence

MIT, see [LICENSE](LICENSE).
