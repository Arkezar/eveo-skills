import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import load_script

refs = load_script("eve-poster-prompt", "collect_refs")
checker = load_script("eve-poster-prompt", "check_prompt")
ec = refs.ec

GOOD = """FACTS TO PRESERVE
Swiezaq flew a Tholos. Do not draw section names or technical instructions.
COMIC METAPHOR
Involuntary travel agency.
REFERENCE ROLES
Image 1 is the pilot portrait.
COMPOSITION AND STYLE
Vertical 2:3 propaganda poster.
EXACT VISIBLE TEXT
Top: "BIURO PODRÓŻY SWIEZAQ" Middle: "Bilety w jedną stronę" Bottom: "Logistyka gratis"
CONSTRAINTS
Exact lettering and correct Polish diacritics.
"""


class CheckPrompt(unittest.TestCase):
    def test_good_prompt_passes_with_length_warning(self):
        errors, warnings = checker.check(GOOD, ["Swiezaq", "BIURO PODRÓŻY SWIEZAQ"])
        self.assertEqual(errors, [])
        self.assertTrue(any("words" in w for w in warnings))

    def test_visible_texts_are_listed(self):
        self.assertEqual(checker.visible_texts(GOOD),
                         ["BIURO PODRÓŻY SWIEZAQ", "Bilety w jedną stronę", "Logistyka gratis"])

    def test_errors(self):
        swapped = GOOD.replace("COMIC METAPHOR", "TEMP").replace("REFERENCE ROLES", "COMIC METAPHOR") \
                      .replace("TEMP", "REFERENCE ROLES")
        cases = {
            "missing exact text": (GOOD, ["Świeżak"]),
            "code fence": ("```\n" + GOOD + "```", []),
            "missing sections": (GOOD.replace("CONSTRAINTS", "LIMITS"), []),
            "out of order": (swapped, []),
            "hard limit": (GOOD + " word" * 1000, []),
            "duplicate section": (GOOD + "\nCONSTRAINTS\nAnother block.", []),
            "preamble": ("Here is your prompt:\n" + GOOD, []),
        }
        for expected, (text, required) in cases.items():
            with self.subTest(expected):
                errors, _ = checker.check(text, required)
                self.assertTrue(any(expected in e for e in errors), errors)

    def test_required_copy_must_be_a_complete_visible_string(self):
        errors, _ = checker.check(GOOD, [], required_visible=["BIURO PODRÓŻY SWIEZAQ"])
        self.assertEqual(errors, [])
        for copy in ("Swiezaq", "BIURO PODRÓŻY", "BIURO PODRÓŻY ŚWIEŻAK"):
            with self.subTest(copy=copy):
                errors, _ = checker.check(GOOD, [], required_visible=[copy])
                self.assertTrue(any("missing exact visible text" in e for e in errors))

    def test_polish_quotes_and_diacritics(self):
        text = GOOD.replace('"Bilety w jedną stronę"', '„Bilety w jedną stronę”')
        self.assertIn("Bilety w jedną stronę", checker.visible_texts(text))

    def test_all_copy_omitted_is_a_warning_not_an_error(self):
        text = GOOD[:GOOD.index("EXACT VISIBLE TEXT")] + "EXACT VISIBLE TEXT\nNone.\nCONSTRAINTS\nNo writing."
        errors, warnings = checker.check(text, [])
        self.assertEqual(errors, [])
        self.assertTrue(any("quotes no strings" in w for w in warnings))


class CollectRefs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"EVE_SKILLS_CACHE_DIR": str(self.root / "cache")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_plan_reports_every_unresolved_name_at_once(self):
        with (mock.patch.object(ec, "lookup_ids", return_value={}),
              mock.patch.object(ec, "resolve_type", side_effect=ec.EveError("ambiguous")),
              self.assertRaises(ec.EveError) as ctx):
            refs.plan(["Nobody"], ["navy"], ["No Alliance"], [], [str(self.root / "missing.png")])
        message = str(ctx.exception)
        for part in ("pilot 'Nobody'", "ship 'navy'", "alliance 'No Alliance'", "style image"):
            self.assertIn(part, message)

    def test_build_orders_files_and_preserves_sources(self):
        style = self.root / "old poster.png"
        style.write_bytes(b"style")
        out = self.root / "job"
        out.mkdir()
        plan = [
            {"role": "portrait", "kind": "character", "id": 1, "name": "Swiezaq", "url": "https://x/p",
             "cache": self.root / "cache" / "p.jpg"},
            {"role": "logo", "kind": "alliance", "id": 2, "name": "Test Alliance", "url": "https://x/l",
             "cache": self.root / "cache" / "l.png"},
            {"role": "style", "kind": "file", "name": style.name, "cache": style},
        ]

        def fake_download(url, dest, refresh=False, max_age=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(url.encode())
            return dest

        with mock.patch.object(ec, "download", fake_download):
            refs.build("t", out, plan, [], refresh=False)
        files = sorted(p.name for p in out.iterdir())
        self.assertEqual(files, ["01-portrait-swiezaq.jpg", "02-logo-test-alliance.png",
                                 "03-style-old-poster.png", "references.json", "references.md"])
        self.assertEqual((out / "03-style-old-poster.png").read_bytes(), b"style")
        self.assertEqual(style.read_bytes(), b"style")
        self.assertIsInstance(plan[0]["cache"], Path, "building must not mutate the input plan")
        reference_map = (out / "references.md").read_text(encoding="utf-8")
        self.assertTrue(reference_map.startswith("# REFERENCE MAP\n"))
        self.assertIn("1. `01-portrait-swiezaq.jpg`: image 1 is a portrait of pilot Swiezaq", reference_map)
        self.assertIn("ONLY", reference_map)

    def test_build_refuses_to_overwrite_files(self):
        out = self.root / "job"
        out.mkdir()
        for name in ("09-ship-old.jpg", "references.md", "prompt.txt"):
            (out / name).write_bytes(b"keep")
        with self.assertRaisesRegex(ec.EveError, "not empty"):
            refs.build("t", out, [], [], False)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()},
                         dict.fromkeys(("09-ship-old.jpg", "references.md", "prompt.txt"), b"keep"))

    def test_default_job_folders_are_unique_even_with_no_references(self):
        first = refs.build("Same title", None, [], [], False)
        second = refs.build("Same title", None, [], [], False)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root / "cache" / "posters")
        self.assertEqual(ec.load_json(first / "references.json")["references"], [])

    def test_map_without_portrait_asks_for_ship_led_scene(self):
        ship = {"role": "ship", "name": "Tholos", "group": "Destroyer", "variation": "render",
                "n": 1, "file": "01-ship-tholos.jpg"}
        self.assertIn("ship-led", refs.render_map([ship], []))

    def test_character_id_is_validated_before_using_image_service(self):
        with (mock.patch.object(ec, "name_category", return_value=None),
              self.assertRaises(ec.EveError)):
            refs.resolve_character("1")
        with (mock.patch.object(ec, "name_category", return_value="character"),
              mock.patch.object(ec, "resolve_names", return_value={1: "Pilot"})):
            self.assertEqual(refs.resolve_character("https://zkillboard.com/character/1/"),
                             {"id": 1, "name": "Pilot"})

    def test_plan_orders_assets_and_labels_icon_fallback(self):
        style = self.root / "poster.png"
        style.write_bytes(b"style")
        with (mock.patch.object(refs, "resolve_character", return_value={"id": 1, "name": "Pilot"}),
              mock.patch.object(ec, "resolve_type", return_value=(2, None)),
              mock.patch.object(refs, "type_summary", return_value={"name": "Rifter", "group": "Frigate",
                                                                  "is_ship": True, "has_render": False}),
              mock.patch.object(ec, "resolve_entity", side_effect=[
                  [{"id": 3, "name": "Alliance", "kind": "alliance"}],
                  [{"id": 4, "name": "Corp", "kind": "corporation"}]])):
            plan, notes = refs.plan(["Pilot"], ["Rifter"], ["Alliance"], ["Corp"], [str(style)])
        self.assertEqual([r["role"] for r in plan], ["portrait", "ship", "logo", "logo", "style"])
        self.assertEqual(plan[1]["variation"], "icon")
        self.assertIn("/types/2/icon?size=1024", plan[1]["url"])
        self.assertTrue(any("only an icon" in n for n in notes))
        self.assertEqual(plan[0]["max_age"], refs.PORTRAIT_TTL)

    def test_plan_refuses_non_ship_types(self):
        with (mock.patch.object(ec, "resolve_type", return_value=(2, None)),
              mock.patch.object(refs, "type_summary", return_value={"name": "Module", "group": "Module",
                                                                  "is_ship": False}),
              self.assertRaisesRegex(ec.EveError, "not a ship")):
            refs.plan([], ["Module"], [], [], [])

    def test_type_summary_cache_and_refresh(self):
        with (mock.patch.object(ec, "esi_get", side_effect=[
                  {"name": "Rifter", "group_id": 25}, {"name": "Frigate", "category_id": 6},
                  {"name": "Rifter", "group_id": 25}, {"name": "Frigate", "category_id": 6}]) as esi,
              mock.patch.object(ec, "get_json", return_value=["icon", "render"])):
            first = refs.type_summary(2)
            self.assertTrue(first["has_render"])
            self.assertTrue(first["is_ship"])
            self.assertEqual(refs.type_summary(2), first)
            self.assertEqual(esi.call_count, 2)
            refs.type_summary(2, refresh=True)
            self.assertEqual(esi.call_count, 4)

    def test_non_image_style_extension_is_rejected(self):
        style = self.root / "poster.txt"
        style.write_text("not an image")
        with self.assertRaisesRegex(ec.EveError, "use a PNG"):
            refs.plan([], [], [], [], [str(style)])


if __name__ == "__main__":
    unittest.main()
