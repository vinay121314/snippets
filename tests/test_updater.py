"""Tests for the self-updater.

Includes a REAL end-to-end run against a local HTTP server: a dummy executable
is published, downloaded, verified and swapped in, and the swapped file is
checked byte for byte. The swap is the dangerous part, since a failure there
leaves the user with no working app, so it is tested against the filesystem
rather than mocked.

    python tests/test_updater.py
"""
import os, sys, io, json, hashlib, tempfile, threading, unittest
import http.server, socketserver, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import updater


class Versions(unittest.TestCase):
    def test_parses_plain_and_v_prefixed(self):
        self.assertEqual(updater.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))

    def test_pads_short_versions(self):
        self.assertEqual(updater.parse_version("2"), (2, 0, 0))
        self.assertEqual(updater.parse_version("2.1"), (2, 1, 0))

    def test_ordering(self):
        self.assertTrue(updater.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(updater.is_newer("1.1.0", "1.0.9"))
        self.assertTrue(updater.is_newer("2.0.0", "1.9.9"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.1"))

    def test_numeric_not_lexicographic(self):
        # "10" must beat "9", which a string compare would get wrong
        self.assertTrue(updater.is_newer("1.10.0", "1.9.0"))

    def test_garbage_does_not_raise(self):
        self.assertEqual(updater.parse_version(None), (0, 0, 0))
        self.assertEqual(updater.parse_version("not-a-version"), (0, 0, 0))
        self.assertFalse(updater.is_newer("", "1.0.0"))


class Config(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("SNIPPETS_UPDATE_URL", "SNIPPETS_DISABLE_UPDATE")}
        updater._registry_config = lambda: {}          # ignore any real policy
    def tearDown(self):
        for k, v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

    def test_default_source(self):
        os.environ.pop("SNIPPETS_UPDATE_URL", None)
        os.environ.pop("SNIPPETS_DISABLE_UPDATE", None)
        c = updater.config()
        self.assertEqual(c["base"], updater.DEFAULT_BASE)
        self.assertFalse(c["disabled"])

    def test_env_override_and_trailing_slash_added(self):
        os.environ["SNIPPETS_UPDATE_URL"] = "http://internal/snips"
        self.assertEqual(updater.config()["base"], "http://internal/snips/")

    def test_env_disable(self):
        os.environ["SNIPPETS_DISABLE_UPDATE"] = "1"
        self.assertTrue(updater.config()["disabled"])

    def test_registry_policy_beats_environment(self):
        os.environ["SNIPPETS_UPDATE_URL"] = "http://from-env/"
        os.environ["SNIPPETS_DISABLE_UPDATE"] = "0"
        updater._registry_config = lambda: {"base": "http://from-it/", "disabled": 1}
        c = updater.config()
        self.assertEqual(c["base"], "http://from-it/")
        self.assertTrue(c["disabled"], "IT policy must win over the environment")

    def test_disabled_source_yields_no_manifest(self):
        updater._registry_config = lambda: {"disabled": 1}
        def boom(url, timeout=0): raise AssertionError("must not hit the network")
        self.assertIsNone(updater.fetch_manifest(opener=boom))


class _FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class CheckFailsSafely(unittest.TestCase):
    def setUp(self):
        updater._registry_config = lambda: {}
        os.environ.pop("SNIPPETS_DISABLE_UPDATE", None)
        os.environ.pop("SNIPPETS_UPDATE_URL", None)

    def test_network_error_returns_none_not_exception(self):
        def boom(url, timeout=0): raise OSError("no route to host")
        self.assertIsNone(updater.check("1.0.0", opener=boom))

    def test_malformed_json_returns_none(self):
        def bad(url, timeout=0): return _FakeResponse(b"<html>404</html>")
        self.assertIsNone(updater.check("1.0.0", opener=bad))

    def test_same_version_reports_nothing(self):
        def ok(url, timeout=0): return _FakeResponse(b'{"version":"1.0.0"}')
        self.assertIsNone(updater.check("1.0.0", opener=ok))

    def test_newer_version_reported(self):
        def ok(url, timeout=0): return _FakeResponse(b'{"version":"1.0.1"}')
        self.assertEqual(updater.check("1.0.0", opener=ok)["version"], "1.0.1")


class DownloadVerification(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        updater._registry_config = lambda: {}
        os.environ.pop("SNIPPETS_DISABLE_UPDATE", None)

    def _payload(self, data):
        def opener(url, timeout=0): return _FakeResponse(data)
        return opener

    def test_checksum_mismatch_rejected(self):
        data = b"x" * 4096
        dest = os.path.join(self.dir, "a.exe")
        with self.assertRaises(RuntimeError) as cm:
            updater.download({"sha256": "00" * 32, "url": "http://x/a.exe"},
                             dest, opener=self._payload(data))
        self.assertIn("checksum", str(cm.exception))

    def test_correct_checksum_accepted(self):
        data = b"y" * 4096
        dest = os.path.join(self.dir, "b.exe")
        updater.download({"sha256": hashlib.sha256(data).hexdigest(), "url": "http://x/b.exe"},
                         dest, opener=self._payload(data))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_truncated_download_rejected(self):
        dest = os.path.join(self.dir, "c.exe")
        with self.assertRaises(RuntimeError) as cm:
            updater.download({"url": "http://x/c.exe"}, dest, opener=self._payload(b"tiny"))
        self.assertIn("implausibly small", str(cm.exception))


class Swap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.exe = os.path.join(self.dir, "Snippets.exe")
        with open(self.exe, "wb") as f: f.write(b"OLD" * 500)

    def test_swap_replaces_and_keeps_old_copy(self):
        new = os.path.join(self.dir, "new.bin")
        with open(new, "wb") as f: f.write(b"NEW" * 500)
        updater.swap_in(new, self.exe)
        with open(self.exe, "rb") as f: self.assertEqual(f.read(), b"NEW" * 500)
        with open(self.exe + ".old", "rb") as f: self.assertEqual(f.read(), b"OLD" * 500)

    def test_failed_swap_rolls_back(self):
        # the new file vanishing mid-swap must leave the original in place
        new = os.path.join(self.dir, "missing.bin")
        with self.assertRaises(Exception):
            updater.swap_in(new, self.exe)
        self.assertTrue(os.path.exists(self.exe), "original executable must survive")
        with open(self.exe, "rb") as f: self.assertEqual(f.read(), b"OLD" * 500)

    def test_cleanup_removes_previous_version(self):
        old = self.exe + ".old"
        with open(old, "wb") as f: f.write(b"stale")
        updater.cleanup_old(self.exe)
        self.assertFalse(os.path.exists(old))

    def test_cleanup_is_silent_when_nothing_to_do(self):
        updater.cleanup_old(self.exe)               # must not raise


class EndToEndOverHttp(unittest.TestCase):
    """The real thing: serve a release over HTTP, check, download, verify, swap."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.served = os.path.join(self.dir, "served"); os.makedirs(self.served)
        self.payload = b"BRAND NEW BUILD " * 500
        with open(os.path.join(self.served, "Snippets.exe"), "wb") as f:
            f.write(self.payload)
        manifest = {"version": "1.0.1", "exe": "Snippets.exe",
                    "sha256": hashlib.sha256(self.payload).hexdigest(),
                    "notes": "test release"}
        with open(os.path.join(self.served, "version.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        served = self.served
        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k): super().__init__(*a, directory=served, **k)
            def log_message(self, *a): pass
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        updater._registry_config = lambda: {}
        os.environ["SNIPPETS_UPDATE_URL"] = "http://127.0.0.1:%d/" % self.port
        os.environ.pop("SNIPPETS_DISABLE_UPDATE", None)

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        os.environ.pop("SNIPPETS_UPDATE_URL", None)

    def test_full_update_cycle(self):
        m = updater.check("1.0.0")
        self.assertIsNotNone(m, "should see the published 1.0.1")
        self.assertEqual(m["version"], "1.0.1")

        exe = os.path.join(self.dir, "Snippets.exe")
        with open(exe, "wb") as f: f.write(b"THE OLD BUILD " * 500)

        updater.perform(m, exe=exe)

        with open(exe, "rb") as f:
            self.assertEqual(f.read(), self.payload, "installed file must be the new build")
        self.assertTrue(os.path.exists(exe + ".old"), "previous version kept for cleanup")
        updater.cleanup_old(exe)
        self.assertFalse(os.path.exists(exe + ".old"))

    def test_up_to_date_client_sees_no_update(self):
        self.assertIsNone(updater.check("1.0.1"))
        self.assertIsNone(updater.check("2.0.0"))

    def test_tampered_download_is_refused_and_original_survives(self):
        # publish a manifest whose checksum does not match the served file
        with open(os.path.join(self.served, "version.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "1.0.1", "exe": "Snippets.exe", "sha256": "00" * 32}, f)
        exe = os.path.join(self.dir, "Snippets.exe")
        with open(exe, "wb") as f: f.write(b"THE OLD BUILD " * 500)
        m = updater.check("1.0.0")
        with self.assertRaises(RuntimeError):
            updater.perform(m, exe=exe)
        with open(exe, "rb") as f:
            self.assertEqual(f.read(), b"THE OLD BUILD " * 500, "must not be replaced")


if __name__ == "__main__":
    unittest.main(verbosity=2)
