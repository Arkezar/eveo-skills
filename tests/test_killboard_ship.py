import unittest

from helpers import load_script

kb = load_script("eve-killboard", "killboard")
ship = load_script("eve-ship-info", "ship_info")


class KillboardUrls(unittest.TestCase):
    def test_entity_filter_comes_before_dependent_modifiers(self):
        self.assertEqual(kb.killmail_url("corporation", 5), "https://zkillboard.com/api/corporationID/5/")
        self.assertEqual(kb.killmail_url("alliance", 5, "losses", solo=True, past_days=9, page=3),
                         "https://zkillboard.com/api/losses/allianceID/5/solo/pastSeconds/604800/page/3/")
        self.assertEqual(kb.filter_key("losses", True, 9, 3), "losses-solo-7d-p3")

    def test_compact_killmails_marks_kill_or_loss(self):
        raw = [
            {"killmail_id": 1, "killmail_time": "t", "solar_system_id": 3,
             "victim": {"corporation_id": 5, "ship_type_id": 587},
             "attackers": [{"corporation_id": 9, "final_blow": True}], "zkb": {"totalValue": 10}},
            {"killmail_id": 2, "killmail_time": "t", "solar_system_id": 3,
             "victim": {"corporation_id": 9, "ship_type_id": 587},
             "attackers": [{"corporation_id": 5, "final_blow": True}], "zkb": {"totalValue": 20}},
        ]
        rows = kb.compact_killmails(raw, "corporation", 5, {587: "Rifter"})
        self.assertEqual([r["side"] for r in rows], ["loss", "kill"])
        self.assertEqual(rows[0]["victim"]["ship"], {"id": 587, "name": "Rifter"})
        self.assertEqual(rows[1]["url"], "https://zkillboard.com/kill/2/")


class ShipInfo(unittest.TestCase):
    def test_attribute_formatting(self):
        self.assertEqual(ship.fmt_attribute(0.6, "resist"), "40.0%")
        self.assertEqual(ship.fmt_attribute(625000, "ms"), "625 s")
        self.assertEqual(ship.fmt_attribute(45000.0, "m"), "45,000 m")
        sections = ship.key_attributes([{"attribute_id": 14, "value": 3.0}, {"attribute_id": 208, "value": 0}])
        self.assertEqual(sections, {"Fitting": {"High slots": "3"}})

    def test_fuzzy_matching(self):
        index = {"Tempest": 1, "Tempest Fleet Issue": 2, "Vexor Navy Issue": 3, "Omen Navy Issue": 4}
        candidates, confident = ship.fuzzy_matches("tempest fleet", index)
        self.assertEqual((candidates[0], confident), ("Tempest Fleet Issue", True))
        self.assertEqual(ship.fuzzy_matches("Vexr Navy Isue", index)[0][0], "Vexor Navy Issue")
        candidates, confident = ship.fuzzy_matches("navy", index)
        self.assertFalse(confident)
        self.assertEqual(len(candidates), 2)

    def test_bonus_line(self):
        bonus = {"bonus": 10.0, "unit_id": 105, "bonus_text": {"en": "bonus to <a href=showinfo:3302>Turret</a>"}}
        self.assertEqual(ship._bonus_line(bonus), "10% bonus to Turret")


if __name__ == "__main__":
    unittest.main()
