import unittest

from helpers import fixture, load_script

br = load_script("eve-battle-report", "battle_report")


def pilot(char, corp=None, ally=None, ship=587):
    return {"character_id": char, "corporation_id": corp, "alliance_id": ally, "ship_type_id": ship}


def km(kid, victim, attackers, value=100.0):
    return {"id": kid, "time": f"2026-01-01T00:{kid:02d}:00Z", "system_id": 30000142, "value": value,
            "victim": victim, "attackers": attackers}


class Urls(unittest.TestCase):
    def test_supported_links(self):
        cases = {
            "https://zkillboard.com/related/30004807/202611150400/":
                ("zkillboard.com", "https://zkillboard.com/api/related/30004807/202611150400/"),
            "https://br.evetools.org/br/65f6f4ba4264cf00d90f50ac":
                ("br.evetools.org", "https://br.evetools.org/newapi/br/composition/65f6f4ba4264cf00d90f50ac"),
            "https://br.evetools.org/related/30000503/1560456000/":
                ("br.evetools.org", "https://br.evetools.org/newapi/br/related/30000503/201906132000"),
            "https://killmail.app/br/gD9PU6zK8i-some-title":
                ("killmail.app", "https://killmail.app/api/battle-reports/gD9PU6zK8i"),
            "https://killmail.app/related/30004807/202611150400":
                ("killmail.app", "https://killmail.app/api/battle-reports/related/30004807/202611150400"),
            "https://warbeacon.net/br/report/98b76a47-5f2d-4217-ad83-bb4181014279":
                ("warbeacon.net", "https://warbeacon.net/api/br/report/98b76a47-5f2d-4217-ad83-bb4181014279"),
            "https://warbeacon.net/br/related/30002489/202604211300":
                ("warbeacon.net", "https://zkillboard.com/api/related/30002489/202604211300/"),
        }
        for url, (source, api) in cases.items():
            with self.subTest(url=url):
                spec = br.parse_url(url)
                self.assertEqual((spec["source"], br.api_url(spec)), (source, api))

    def test_related_time_rounds_to_the_hour(self):
        self.assertEqual(br.related_time("202611150437"), "202611150400")
        self.assertEqual(br.related_time("1560456000000"), "201906132000")

    def test_saved_zkillboard_br_explains_the_gap(self):
        with self.assertRaisesRegex(br.ec.EveError, "no public API"):
            br.parse_url("https://zkillboard.com/br/12345/")

    def test_unsupported_link(self):
        with self.assertRaisesRegex(br.ec.EveError, "Unsupported"):
            br.parse_url("https://example.com/br/1")


class Normalizers(unittest.TestCase):
    def check(self, source, name, kind="saved", expect_teams=True):
        kms, team_of, systems, _ = br.normalize({"source": source, "kind": kind}, fixture(name))
        self.assertTrue(kms)
        self.assertTrue(systems and systems[0]["id"])
        for k in kms:
            self.assertRegex(k["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(set(k["victim"]), set(br.PILOT_KEYS))
        self.assertEqual(team_of is not None, expect_teams)
        return kms, team_of

    def test_zkillboard_related(self):
        kms, team_of = self.check("zkillboard.com", "zkillboard_related.json", "related")
        self.assertEqual(len(kms), 6)
        self.assertEqual(team_of["a:99003214"], 0)  # Brave Collective on team A

    def test_evetools_saved(self):
        _, team_of = self.check("br.evetools.org", "evetools_composition.json")
        self.assertEqual(team_of["a:99003581"], 0)
        self.assertEqual(team_of["c:1000044"], 1)  # "corp:" prefixed members

    def test_killmailapp_needs_inference(self):
        self.check("killmail.app", "killmailapp_br.json", expect_teams=False)

    def test_warbeacon_saved(self):
        kms, team_of = self.check("warbeacon.net", "warbeacon_report.json")
        self.assertEqual(len(kms), 2)
        self.assertEqual(team_of["a:1354830081"], 1)


class Teams(unittest.TestCase):
    def setUp(self):
        # Alliances 1+2 fight 3; 2 and 1 also shoot together.
        self.kms = [
            km(1, pilot(30, ally=3), [pilot(10, ally=1), pilot(20, ally=2)], 500),
            km(2, pilot(31, ally=3), [pilot(10, ally=1)], 300),
            km(3, pilot(11, ally=1), [pilot(30, ally=3), pilot(32, corp=9)], 200),
            km(4, pilot(21, ally=2), [pilot(32, corp=9)], 50),
        ]

    def test_infer_teams_splits_enemies(self):
        teams = br.infer_teams(self.kms)
        self.assertEqual(teams["a:1"], teams["a:2"])
        self.assertEqual(teams["a:3"], teams["c:9"])
        self.assertNotEqual(teams["a:1"], teams["a:3"])

    def test_build_report_totals(self):
        team_of = {"a:1": 0, "a:2": 0, "a:3": 1, "c:9": 1}
        report = br.build_report(self.kms, team_of, {1: "One", 3: "Three"}, top=2)
        a, b = report["teams"]
        self.assertEqual((a["pilots"], a["ships_lost"], a["isk_lost"], a["isk_killed"]), (4, 2, 250, 800))
        self.assertEqual((b["pilots"], b["ships_lost"], b["isk_lost"], b["isk_killed"]), (3, 2, 800, 250))
        self.assertAlmostEqual(a["efficiency"], 800 / 1050, places=4)
        self.assertEqual([k["id"] for k in report["top_losses"]], [1, 2])
        self.assertEqual(report["totals"]["isk_destroyed"], 1050)
        self.assertTrue(a["name"].startswith("Team A: One"))

    def test_render_markdown_smoke(self):
        report = br.build_report(self.kms, br.infer_teams(self.kms), {}, top=3)
        report.update(url="u", api_url="a", systems=[{"id": 30000142, "name": "Jita", "region": "The Forge"}],
                      teams_inferred=True, _cache={"fetched_at": "2026-01-01T00:00:00Z"})
        text = br.render_markdown(report)
        self.assertIn("# Battle report: Jita (The Forge)", text)
        self.assertIn("Teams inferred", text)


if __name__ == "__main__":
    unittest.main()
