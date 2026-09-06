"""Write dist/version.json to accompany dist/Snippets.exe in a release.

The app reads this file to decide whether an update is available, and uses the
checksum in it to prove the download arrived intact before replacing itself.
Generating it from the exe that was just built means the two can never drift
apart, which is the whole point of doing it here rather than by hand.

Run automatically by BUILD.bat. Upload BOTH files to the GitHub release.
"""
import hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(ROOT, "dist", "Snippets.exe")
OUT = os.path.join(ROOT, "dist", "version.json")


def app_version():
    """Read VERSION out of brain.py rather than importing it, so this works
    without pulling in the tray, webview and keyboard dependencies."""
    with open(os.path.join(ROOT, "brain.py"), encoding="utf-8") as f:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("could not find VERSION in brain.py")
    return m.group(1)


VERSION_INFO = os.path.join(ROOT, "version_info.txt")

AUTHOR = "Vinay Prasad"
YEAR = "2026"


def write_version_info():
    """Windows version resource for the exe.

    This is what fills in the Details tab of the file's Properties dialog, so
    the author and licence travel with the binary itself rather than only
    living in the repository. Generated from VERSION so it cannot fall out of
    step with the build.
    """
    v = app_version()
    parts = [int(x) for x in v.split(".")] + [0, 0, 0, 0]
    quad = tuple(parts[:4])
    body = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(q)s, prodvers=%(q)s, mask=0x3f, flags=0x0,
    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '%(author)s'),
      StringStruct('FileDescription', 'Snippets, a Windows text expander'),
      StringStruct('FileVersion', '%(v)s'),
      StringStruct('InternalName', 'Snippets'),
      StringStruct('LegalCopyright',
                   'Copyright (c) %(year)s %(author)s. Released under the MIT Licence.'),
      StringStruct('OriginalFilename', 'Snippets.exe'),
      StringStruct('ProductName', 'Snippets'),
      StringStruct('ProductVersion', '%(v)s'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % {"q": quad, "v": v, "author": AUTHOR, "year": YEAR}
    with open(VERSION_INFO, "w", encoding="utf-8") as f:
        f.write(body)
    print("version_info.txt written for v%s" % v)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "versioninfo":
        write_version_info(); return
    if not os.path.exists(EXE):
        raise SystemExit("dist/Snippets.exe not found, build first")
    with open(EXE, "rb") as f:
        data = f.read()
    manifest = {
        "version": app_version(),
        "exe": "Snippets.exe",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print("version.json written for v%s (%.1f MB)" % (manifest["version"], len(data) / 1048576))
    print("Upload dist\\Snippets.exe AND dist\\version.json to the release.")


if __name__ == "__main__":
    main()
