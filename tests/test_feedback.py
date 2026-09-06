"""Tests for feedback sending and the offline outbox.

Nothing here touches the network: a fake poster stands in for the relay so
failure modes can be forced deliberately.

Two things matter most and are tested hardest:

  * A report is only deleted from the outbox once it has actually gone. A
    queue that drops reports it failed to send is worse than no queue.
  * Nothing is sent beyond what the person typed. Clearing the name box must
    really leave the name out.
"""
import os, sys, json, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import feedback


class _Resp:
    def __init__(self, body=b'{"ok":true,"number":7}'): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): pass


def ok_poster(url, data, timeout=0):
    ok_poster.sent.append((url, data))
    return _Resp()
ok_poster.sent = []


def dead_poster(url, data, timeout=0):
    raise OSError("connection refused")


class Payload(unittest.TestCase):
    def test_message_carried_through(self):
        p = feedback.build_payload("Bug", "  it crashes  ")
        self.assertEqual(p["message"], "it crashes")

    def test_name_kept_when_given(self):
        self.assertEqual(feedback.build_payload("Bug", "x", name=" Sam ")["name"], "Sam")

    def test_cleared_name_stays_empty(self):
        for blank in ("", "   ", None):
            self.assertEqual(feedback.build_payload("Bug", "x", name=blank)["name"], "")

    def test_kind_defaults(self):
        self.assertEqual(feedback.build_payload("", "x")["kind"], "Feedback")

    def test_long_message_truncated(self):
        p = feedback.build_payload("Bug", "z" * 9000)
        self.assertLess(len(p["message"]), feedback.MAX_MESSAGE + 40)
        self.assertIn("[truncated]", p["message"])

    def test_payload_has_no_surprise_fields(self):
        p = feedback.build_payload("Bug", "x")
        self.assertEqual(set(p), {"kind", "message", "name", "version", "system", "sent_at"})


class Sending(unittest.TestCase):
    def setUp(self):
        ok_poster.sent = []
        feedback._registry_endpoint = lambda: None
        os.environ["SNIPPETS_FEEDBACK_URL"] = "http://relay.test/"
    def tearDown(self):
        os.environ.pop("SNIPPETS_FEEDBACK_URL", None)

    def test_posts_to_the_endpoint(self):
        feedback.send(feedback.build_payload("Bug", "hello"), poster=ok_poster)
        url, data = ok_poster.sent[0]
        self.assertEqual(url, "http://relay.test/")
        self.assertEqual(data["message"], "hello")

    def test_refuses_empty_message(self):
        with self.assertRaises(ValueError):
            feedback.send(feedback.build_payload("Bug", "   "), poster=ok_poster)

    def test_refuses_when_no_endpoint_configured(self):
        os.environ.pop("SNIPPETS_FEEDBACK_URL", None)
        feedback.ENDPOINT = ""
        with self.assertRaises(RuntimeError):
            feedback.send(feedback.build_payload("Bug", "hi"), poster=ok_poster)

    def test_registry_endpoint_wins(self):
        feedback._registry_endpoint = lambda: "http://from-it/"
        self.assertEqual(feedback.endpoint(), "http://from-it/")

    def test_non_json_reply_is_still_a_success(self):
        feedback.send(feedback.build_payload("Bug", "hi"),
                      poster=lambda u, d, timeout=0: _Resp(b"OK"))


class Outbox(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        ok_poster.sent = []
        feedback._registry_endpoint = lambda: None
        os.environ["SNIPPETS_FEEDBACK_URL"] = "http://relay.test/"
    def tearDown(self):
        os.environ.pop("SNIPPETS_FEEDBACK_URL", None)

    def test_failed_send_is_queued_not_lost(self):
        sent = feedback.send_or_queue(feedback.build_payload("Bug", "offline note"),
                                      poster=dead_poster, d=self.dir)
        self.assertFalse(sent)
        self.assertEqual(len(feedback.pending(self.dir)), 1)

    def test_successful_send_queues_nothing(self):
        self.assertTrue(feedback.send_or_queue(feedback.build_payload("Bug", "hi"),
                                               poster=ok_poster, d=self.dir))
        self.assertEqual(feedback.pending(self.dir), [])

    def test_flush_sends_and_clears(self):
        for i in range(3):
            feedback.queue(feedback.build_payload("Bug", "note %d" % i), d=self.dir)
        sent, left = feedback.flush(d=self.dir, poster=ok_poster)
        self.assertEqual((sent, left), (3, 0))
        self.assertEqual([d["message"] for _u, d in ok_poster.sent],
                         ["note 0", "note 1", "note 2"], "order must be preserved")

    def test_flush_keeps_reports_when_the_relay_is_down(self):
        feedback.queue(feedback.build_payload("Bug", "keep me"), d=self.dir)
        sent, left = feedback.flush(d=self.dir, poster=dead_poster)
        self.assertEqual((sent, left), (0, 1), "a report that did not send must survive")

    def test_flush_stops_at_the_first_failure(self):
        for i in range(3):
            feedback.queue(feedback.build_payload("Bug", "n%d" % i), d=self.dir)
        calls = {"n": 0}
        def flaky(url, data, timeout=0):
            calls["n"] += 1
            if calls["n"] == 2: raise OSError("dropped")
            return _Resp()
        sent, left = feedback.flush(d=self.dir, poster=flaky)
        self.assertEqual(sent, 1)
        self.assertEqual(left, 2, "the rest stay queued for the next attempt")

    def test_unreadable_file_is_discarded_not_retried_forever(self):
        with open(os.path.join(self.dir, "fb_broken.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        sent, left = feedback.flush(d=self.dir, poster=ok_poster)
        self.assertEqual((sent, left), (0, 0))

    def test_outbox_is_capped(self):
        real = feedback.MAX_OUTBOX
        feedback.MAX_OUTBOX = 3
        try:
            for i in range(6):
                feedback.queue(feedback.build_payload("Bug", "n%d" % i), d=self.dir)
            self.assertLessEqual(len(feedback.pending(self.dir)), 3)
        finally:
            feedback.MAX_OUTBOX = real

    def test_queued_content_survives_a_round_trip(self):
        feedback.queue(feedback.build_payload("Suggestion", "add tabs", name="Sam",
                                              version="1.0.3"), d=self.dir)
        with open(feedback.pending(self.dir)[0], encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["message"], "add tabs")
        self.assertEqual(saved["name"], "Sam")
        self.assertEqual(saved["kind"], "Suggestion")

    def test_flush_without_an_endpoint_keeps_everything(self):
        os.environ.pop("SNIPPETS_FEEDBACK_URL", None)
        feedback.ENDPOINT = ""
        feedback.queue(feedback.build_payload("Bug", "hi"), d=self.dir)
        sent, left = feedback.flush(d=self.dir, poster=ok_poster)
        self.assertEqual((sent, left), (0, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
