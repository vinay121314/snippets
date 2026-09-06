"""Tests for the feedback link builder.

Nothing here touches the network. The point of these is that the report says
what it appears to say: the body must contain only what the person was shown
in the dialog, and the name must be omitted entirely when they clear it.
"""
import os, sys, unittest, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import feedback


def parsed(url):
    q = urllib.parse.urlparse(url).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q, keep_blank_values=True).items()}


class Body(unittest.TestCase):
    def test_message_is_included(self):
        self.assertIn("it crashes on Tuesdays", feedback.build_body("it crashes on Tuesdays"))

    def test_name_included_when_given(self):
        self.assertIn("From: Sam", feedback.build_body("hi", name="Sam"))

    def test_name_omitted_when_cleared(self):
        # a person who clears the box must not be identified anyway
        for blank in ("", "   ", None):
            b = feedback.build_body("hi", name=blank or "")
            self.assertNotIn("From:", b)

    def test_version_and_system_included(self):
        b = feedback.build_body("hi", version="1.0.2", system="Windows-11")
        self.assertIn("Version: 1.0.2", b)
        self.assertIn("System: Windows-11", b)

    def test_long_message_truncated(self):
        b = feedback.build_body("x" * 9000)
        self.assertLess(len(b), 4300)
        self.assertIn("[truncated]", b)


class Url(unittest.TestCase):
    def test_points_at_the_issue_page(self):
        self.assertTrue(feedback.build_url("Bug", "hi").startswith(feedback.ISSUES_NEW + "?"))

    def test_title_carries_kind_and_summary(self):
        q = parsed(feedback.build_url("Bug", "keyboard stops working"))
        self.assertEqual(q["title"], "[Bug] keyboard stops working")

    def test_title_uses_first_line_only(self):
        q = parsed(feedback.build_url("Suggestion", "add dark mode\nand more stuff\nand more"))
        self.assertEqual(q["title"], "[Suggestion] add dark mode")

    def test_long_first_line_trimmed_for_title(self):
        q = parsed(feedback.build_url("Bug", "y" * 200))
        self.assertLessEqual(len(q["title"]), 70)

    def test_special_characters_survive_encoding(self):
        msg = "breaks with & ? # = and \u00e9 accents"
        q = parsed(feedback.build_url("Bug", msg))
        self.assertIn(msg, q["body"])

    def test_body_contains_nothing_beyond_what_was_supplied(self):
        url = feedback.build_url("Bug", "just this", name="", version="", system="")
        body = parsed(url)["body"]
        self.assertNotIn("From:", body)
        self.assertNotIn("Version:", body)
        self.assertNotIn("System:", body)

    def test_url_stays_within_a_sane_length(self):
        url = feedback.build_url("Bug", "z" * 9000, name="Someone", version="1.0.2",
                                 system="Windows-11-10.0.26200-SP0")
        self.assertLess(len(url), 8000, "must not exceed what browsers accept")


if __name__ == "__main__":
    unittest.main(verbosity=2)
