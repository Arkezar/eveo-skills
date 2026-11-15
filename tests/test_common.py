import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import ROOT, load_script

ec = load_script("eve-corp-alliance", "corp_alliance").ec


class TempCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"EVE_SKILLS_CACHE_DIR": self._tmp.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class Formatting(unittest.TestCase):
    def test_fmt_isk(self):
        self.assertEqual(ec.fmt_isk(145003901701), "145.00b")
        self.assertEqual(ec.fmt_isk(13086479), "13.09m")
        self.assertEqual(ec.fmt_isk(None), "0")

    def test_strip_html_keeps_text_and_links(self):
        raw = '<font size="14">Hi<br>there</font> <a href="https://x.example/a">site</a> &amp; more'
        self.assertEqual(ec.strip_html(raw), "Hi\nthere site (https://x.example/a) & more")

    def test_md_table_escapes_pipes(self):
        self.assertIn("a\\|b", ec.md_table(["h"], [["a|b"]]))

    def test_slugify(self):
        self.assertEqual(ec.slugify("Brave Newbies Inc."), "brave-newbies-inc")
        self.assertEqual(ec.slugify("..."), "unnamed")


class Cache(TempCache):
    def test_freshness(self):
        doc = ec.stamp({}, ["x"])
        self.assertTrue(ec.is_fresh(doc, 60))
        doc["_cache"]["fetched_at"] = "2000-01-01T00:00:00Z"
        self.assertFalse(ec.is_fresh(doc, 60))
        self.assertFalse(ec.is_fresh(None, 60))

    def test_entity_dir_reuses_existing_id_folder(self):
        first = ec.entity_dir("corporations", 1, "Old Name")
        first.mkdir(parents=True)
        self.assertEqual(ec.entity_dir("corporations", 1, "New Name"), first)

    def test_resolve_names_caches_and_isolates_bad_ids(self):
        calls = []

        def fake_post(path, ids):
            calls.append(list(ids))
            if 666 in ids:
                raise ec.HttpError(404, path)
            return [{"id": i, "name": f"n{i}", "category": "character"} for i in ids]

        with mock.patch.object(ec, "esi_post", fake_post):
            self.assertEqual(ec.resolve_names([1, 2, 666]), {1: "n1", 2: "n2"})
            calls.clear()
            self.assertEqual(ec.resolve_names([1, 2]), {1: "n1", 2: "n2"})
        self.assertEqual(calls, [], "second lookup must come from names.json")

    def test_lookup_ids_exact_match_and_cache(self):
        reply = {"corporations": [{"id": 7, "name": "Hole Lotta Trouble"}]}
        with mock.patch.object(ec, "esi_post", return_value=reply) as post:
            self.assertEqual(ec.resolve_entity("hole lotta trouble")[0]["id"], 7)
            ec.resolve_entity("Hole Lotta Trouble")
        post.assert_called_once()

    def test_resolve_entity_from_url(self):
        with mock.patch.object(ec, "resolve_names", return_value={98798418: "HLT"}):
            hit = ec.resolve_entity("https://zkillboard.com/corporation/98798418/")
        self.assertEqual(hit, [{"kind": "corporation", "id": 98798418, "name": "HLT"}])

    def test_unknown_name_raises(self):
        with mock.patch.object(ec, "esi_post", return_value={}), self.assertRaises(ec.EveError):
            ec.resolve_entity("No Such Corp")


class SharedModuleSync(unittest.TestCase):
    def test_every_skill_has_the_canonical_copy(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_common.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + " run: python3 tools/sync_common.py")


if __name__ == "__main__":
    unittest.main()
