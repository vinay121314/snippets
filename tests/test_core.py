"""Fast, headless tests for snip_core's pure logic.

No GUI, no clipboard, no keyboard -- runs in well under a second, so there's
no excuse not to run it before a build. The slow end-to-end checks that need
a real focused window live in test_live.py.

    python tests/test_core.py
"""
import os, sys, json, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import snip_core as core


class ShippedDefaults(unittest.TestCase):
    """The built-in examples ship to every new user and to anyone who clones
    the repo, so they must be valid and must not carry personal content."""

    def test_no_duplicate_triggers(self):
        self.assertEqual(core._find_duplicate_trigger(core.DEFAULTS["snippets"]), (None, None))

    def test_every_example_renders(self):
        for s in core.DEFAULTS["snippets"]:
            core._md_to_html(s["text"]); core._plain_from_md(s["text"])

    def test_examples_cover_each_feature(self):
        blob = " ".join(s["text"] for s in core.DEFAULTS["snippets"])
        for feature in ("$|", "{date}", "{{Name}}", "|Not started,", "**", "1. ", "`", "---", "]("):
            self.assertIn(feature, blob, feature)

    def test_dropdown_example_parses(self):
        s = next(x for x in core.DEFAULTS["snippets"] if ":status" in x["triggers"])
        self.assertEqual(core.extract_field_specs(s["text"]),
                         [("Status", ["Not started", "In progress", "Blocked", "Done"])])

    def test_defaults_are_not_shared_between_stores(self):
        import tempfile
        a = core.Store(os.path.join(tempfile.mkdtemp(), "a.json"))
        a.data["snippets"][0]["label"] = "MUTATED"
        b = core.Store(os.path.join(tempfile.mkdtemp(), "b.json"))
        self.assertNotEqual(b.data["snippets"][0]["label"], "MUTATED")
        self.assertNotEqual(core.DEFAULTS["snippets"][0]["label"], "MUTATED")


class Fields(unittest.TestCase):
    def test_extract_is_ordered_and_deduped(self):
        t = "Hi {{Name}}, about {{Topic}} -- thanks {{Name}}"
        self.assertEqual(core.extract_fields(t), ["Name", "Topic"])

    def test_extract_none(self):
        self.assertEqual(core.extract_fields("no placeholders {here}"), [])

    def test_apply_replaces_every_occurrence(self):
        t = "Hi {{Name}}, bye {{Name}}"
        self.assertEqual(core.apply_fields(t, {"Name": "Sam"}), "Hi Sam, bye Sam")

    def test_dropdown_spec_parsed(self):
        specs = core.extract_field_specs("Log in to {{Portal|Alpha,Beta,Gamma}} now")
        self.assertEqual(specs, [("Portal", ["Alpha", "Beta", "Gamma"])])

    def test_plain_field_has_no_choices(self):
        self.assertEqual(core.extract_field_specs("Hi {{Name}}"), [("Name", [])])

    def test_apply_replaces_whole_dropdown_placeholder(self):
        t = "Use {{Portal|Alpha,Beta}} today"
        self.assertEqual(core.apply_fields(t, {"Portal": "Beta"}), "Use Beta today")

    def test_mixed_fields_keep_order(self):
        self.assertEqual(core.extract_fields("{{Name}} on {{Portal|A,B}} re {{Name}}"),
                         ["Name", "Portal"])

    def test_empty_option_list_degrades_to_free_text(self):
        self.assertEqual(core.extract_field_specs("{{Thing|}}"), [("Thing", [])])

    def test_unfilled_placeholder_is_left_intact(self):
        self.assertEqual(core.apply_fields("{{A}} {{B|x,y}}", {"A": "1"}), "1 {{B|x,y}}")

    def test_single_brace_tokens_are_not_fields(self):
        # {date}/{time}/{clip} are a separate mechanism and must not be picked up
        self.assertEqual(core.extract_fields("today is {date}"), [])


class BalanceSplit(unittest.TestCase):
    """Splitting at $| must leave BOTH halves valid markdown, or the pasted
    result shows literal ** characters."""

    def test_bold_split_closes_and_drops_orphan_closer(self):
        b, a = core._balance_split("hey **I know, ", "**\n1. item")
        self.assertTrue(b.endswith("** "), b)          # closed BEFORE trailing space
        self.assertFalse(a.startswith("**"), a)        # orphaned closer removed

    def test_code_span_split(self):
        b, a = core._balance_split("run `npm ", "install`")
        self.assertEqual(b.count("`") % 2, 0)
        self.assertEqual(a.count("`") % 2, 0)

    def test_balanced_input_is_untouched(self):
        b, a = core._balance_split("**bold** then ", " more text")
        self.assertEqual((b, a), ("**bold** then ", " more text"))

    def test_italic_not_confused_by_bold(self):
        b, a = core._balance_split("**done** and *open ", "close* end")
        self.assertEqual(b.replace("**", "").count("*") % 2, 0)


class UndoMatching(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(core.matches_pasted("abcdef", "abcdef"))
    def test_character_missing_from_the_middle(self):
        # the caret sits at $| mid-text, so backspace deletes from the middle
        self.assertTrue(core.matches_pasted("abdef", "abcdef"))
    def test_character_missing_from_the_end(self):
        self.assertTrue(core.matches_pasted("abcde", "abcdef"))
    def test_rejects_different_content(self):
        self.assertFalse(core.matches_pasted("xyzzy1", "abcdef"))
    def test_rejects_too_much_missing(self):
        self.assertFalse(core.matches_pasted("ab", "abcdef"))
    def test_rejects_empty(self):
        self.assertFalse(core.matches_pasted("", "abcdef"))
    def test_squash_ignores_spacing_nbsp_and_punctuation(self):
        self.assertEqual(core._squash("1. Log in.\u00a0"), "1Login")
        # a source bullet '-' and a rendered bullet '\uu2022' must compare equal
        self.assertEqual(core._squash("- Log  in"), core._squash("\u2022 Log in"))


class Markdown(unittest.TestCase):
    def test_inline_forms(self):
        h = core._md_to_html("**b** *i* `c` [L](http://x)")
        for frag in ("<b>b</b>", "<i>i</i>", "<code>c</code>", '<a href="http://x">L</a>'):
            self.assertIn(frag, h)

    def test_lists_and_rule(self):
        h = core._md_to_html("1. one\n2. two\n\n- a\n- b\n\n---")
        self.assertIn("<ol>", h); self.assertIn("<ul>", h); self.assertIn("<hr>", h)
        self.assertEqual(h.count("<li>"), 4)

    def test_list_keeps_its_starting_number(self):
        # splitting at $| can paste items 2..n on their own; without start=
        # the browser renumbers them from 1
        self.assertIn('<ol start="2">', core._md_to_html("2. two\n3. three"))
        self.assertIn("<ol>", core._md_to_html("1. one\n2. two"))

    def test_no_literal_newlines_between_fragments(self):
        # regression: joining with "\n" rendered as extra blank lines and threw
        # off cursor placement
        self.assertNotIn("\n", core._md_to_html("1. one\n2. two"))

    def test_asterisks_inside_code_are_literal(self):
        self.assertIn("<code>a**b</code>", core._md_to_html("`a**b`"))

    def test_html_is_escaped(self):
        self.assertIn("&lt;script&gt;", core._md_to_html("<script>"))

    def test_has_fmt_detects_each_form(self):
        for t in ("**b**", "`c`", "1. x", "- x", "---", "[L](u)"):
            self.assertTrue(core._has_fmt(t), t)
        self.assertFalse(core._has_fmt("just plain words"))

    def test_plain_fallback_strips_markup(self):
        p = core._plain_from_md("**bold** and `code`")
        self.assertNotIn("**", p); self.assertNotIn("`", p)


class VisibleLen(unittest.TestCase):
    """Only used by the keypress fallback, but a wrong count there means a
    wrong cursor position, so pin the rules down."""

    def test_markup_chars_do_not_count(self):
        self.assertEqual(core._visible_len("**hi**", rich=True), 2)

    def test_list_markers_do_not_count(self):
        self.assertEqual(core._visible_len("1. ab", rich=True), 2)

    def test_rich_link_counts_label_only(self):
        self.assertEqual(core._visible_len("[ab](http://long)", rich=True), 2)

    def test_plain_link_counts_label_and_url(self):
        self.assertGreater(core._visible_len("[ab](http://long)", rich=False), 2)


class DuplicateTriggers(unittest.TestCase):
    def test_reports_conflict_with_owning_label(self):
        t, owner = core._find_duplicate_trigger(
            [{"triggers": [":a"], "label": "First"}, {"triggers": [":a"], "label": "Second"}])
        self.assertEqual((t, owner), (":a", "First"))

    def test_clean_set_reports_nothing(self):
        self.assertEqual(core._find_duplicate_trigger(
            [{"triggers": [":a"]}, {"triggers": [":b"]}]), (None, None))

    def test_case_sensitive_like_the_runtime_matcher(self):
        self.assertEqual(core._find_duplicate_trigger(
            [{"triggers": [":a"]}, {"triggers": [":A"]}]), (None, None))


class StoreRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.path = os.path.join(self.dir, "s.json")

    def test_defaults_are_filled_in_for_older_files(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"snippets": [{"id": "x", "triggers": [":t"], "text": "hi"}]}, f)
        s = core.Store(self.path).data["snippets"][0]
        self.assertEqual(s["tags"], [])
        self.assertIs(s["word_only"], False)
        self.assertEqual(s["scope"], "personal")

    def test_theme_defaults_to_dark(self):
        self.assertEqual(core.Store(self.path).data["theme"], "dark")

    def test_existing_theme_choice_is_kept(self):
        st = core.Store(self.path); st.data["theme"] = "light"; st.save()
        self.assertEqual(core.Store(self.path).data["theme"], "light")

    def test_save_then_load_preserves_content(self):
        st = core.Store(self.path)
        st.data["snippets"] = [{"id": "1", "triggers": [":x"], "label": "L",
                                "text": "body", "tags": ["t"], "word_only": True}]
        self.assertTrue(st.save())
        again = core.Store(self.path).data["snippets"][0]
        self.assertEqual(again["text"], "body")
        self.assertIs(again["word_only"], True)

    def test_corrupt_file_does_not_crash(self):
        with open(self.path, "w", encoding="utf-8") as f: f.write("{ not json")
        self.assertIsInstance(core.Store(self.path).data.get("snippets"), list)


class Backups(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.path = os.path.join(self.dir, "s.json")
        self._old_dir, core.BACKUP_DIR = core.BACKUP_DIR, os.path.join(self.dir, "backups")
    def tearDown(self):
        core.BACKUP_DIR = self._old_dir

    def _store_with(self, label):
        st = core.Store(self.path)
        st.data["snippets"] = [{"id": "1", "triggers": [":x"], "label": label, "text": "t"}]
        st.save(); return st

    def test_first_save_has_nothing_to_back_up(self):
        self._store_with("one")
        self.assertEqual(core.list_backups(), [])

    def test_second_save_snapshots_the_previous_contents(self):
        st = self._store_with("one")
        st.data["snippets"][0]["label"] = "two"; st.save()
        b = core.list_backups()
        self.assertEqual(len(b), 1)
        with open(b[0][0], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["snippets"][0]["label"], "one")   # the OLD state

    def test_restore_brings_back_old_content_and_is_itself_undoable(self):
        st = self._store_with("one")
        st.data["snippets"][0]["label"] = "two"; st.save()
        core.restore_backup(st, core.list_backups()[0][0])
        self.assertEqual(st.data["snippets"][0]["label"], "one")
        self.assertEqual(core.Store(self.path).data["snippets"][0]["label"], "one")
        self.assertGreaterEqual(len(core.list_backups()), 2)   # the restore was backed up too

    def test_restore_applies_newer_field_defaults(self):
        st = self._store_with("one")
        st.data["snippets"][0]["label"] = "two"; st.save()
        core.restore_backup(st, core.list_backups()[0][0])
        self.assertIn("word_only", st.data["snippets"][0])
        self.assertIn("tags", st.data["snippets"][0])

    def test_restore_rejects_a_non_snippets_file(self):
        st = self._store_with("one")
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as f: json.dump({"nope": 1}, f)
        with self.assertRaises(ValueError): core.restore_backup(st, bad)

    def test_prunes_to_the_keep_limit(self):
        st = self._store_with("one")
        keep, core.BACKUP_KEEP = core.BACKUP_KEEP, 3
        try:
            for i in range(6):
                st.data["snippets"][0]["label"] = "v%d" % i; st.save()
            self.assertEqual(len(core.list_backups()), 3)
        finally:
            core.BACKUP_KEEP = keep

    def test_unchanged_snippets_do_not_create_a_backup(self):
        st = self._store_with("one")
        st.data["enabled"] = False; st.save()      # the enable/disable toggle path
        st.data["enabled"] = True;  st.save()
        self.assertEqual(core.list_backups(), [])

    def test_two_saves_in_the_same_second_both_snapshot(self):
        st = self._store_with("one")
        st.data["snippets"][0]["label"] = "two";   st.save()
        st.data["snippets"][0]["label"] = "three"; st.save()
        self.assertEqual(len(core.list_backups()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
