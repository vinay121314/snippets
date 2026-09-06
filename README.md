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
- **Automatic updates**: the app checks for a new version and can install it
  and restart itself, so there is nothing to download by hand.
- **Dark by default**, with a **Dark** switch in the title bar for light mode.
- **Feedback built in**: report a bug or suggest something from the tray menu.
  It opens a pre-filled report in the browser so you can see exactly what is
  being sent before you submit it.
- **Starts with Windows** from first run, and can be switched off in the tray.

## Install

Download `Snippets.exe` from [Releases](../../releases) and run it. It sits in
the system tray.

Right-click the tray icon for **Edit snippets**, **Search**, **Restore
backup...**, **Send feedback...**, **Check for updates**, **Start with
Windows**, and to enable or disable expansion.

It adds itself to Windows startup the first time it runs. Turn that off from
the tray menu if you would rather it did not.

An **About and help** page is built into the editor, behind the `?` button in
the top bar. It explains every field and marker.

## Updates

The app checks for a newer version shortly after starting and every six hours
after that. When one is found, the tray menu offers **Install update**. Choosing
it downloads the new build, verifies its checksum, replaces the running
executable and restarts.

If the check cannot reach the network it fails silently and the app keeps
working.

### For administrators

Update behaviour is controlled from `HKLM\Software\Snippets`, which overrides
everything else, so a managed deployment does not need a special build:

| Value | Type | Effect |
|---|---|---|
| `UpdateUrl` | String | Serve updates from your own location instead. The app expects `version.json` and the executable at that address |
| `DisableUpdate` | DWORD `1` | Turn self-update off completely and manage versions yourself |
| `AutoUpdate` | DWORD `1` | Install updates silently as soon as they are found, with no prompt |

The same three settings are also readable from the environment as
`SNIPPETS_UPDATE_URL`, `SNIPPETS_DISABLE_UPDATE` and `SNIPPETS_AUTO_UPDATE`,
which is useful for testing.

Network access needed: outbound HTTPS to the host serving the release. Nothing
else, and nothing is ever sent, the app only fetches.

## Build from source

Requires Python 3.10 or newer on Windows.

```
pip install pywebview pystray pillow keyboard pyperclip uiautomation comtypes pyinstaller
BUILD.bat
```

`BUILD.bat` runs the test suite first and refuses to build if it fails. It
produces `dist\Snippets.exe` and `dist\version.json`. Publishing a new version
means uploading **both** files to a release: the app reads `version.json` to
learn what is available and to verify the download.

## Tests

```
python tests/test_core.py     fast, headless, no GUI (about 0.2s)
python tests/test_updater.py  update logic, including a real download and swap
python tests/test_feedback.py the feedback report builder
python tests/test_live.py     end-to-end against real editing surfaces
python tests/test_ui.py       renders the editor and checks it wired up
python tests/test_dialogs.py  asserts the tray dialogs draw their buttons
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
