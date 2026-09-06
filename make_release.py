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


def main():
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
