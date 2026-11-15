#!/usr/bin/env python3
"""Load an EVE Online battle report from a zKillboard / br.evetools.org / killmail.app / warbeacon.net link.

Every source is normalized to one model (systems, teams, killmails), cached as report.json + README.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eve_common as ec

ZKB = "https://zkillboard.com"
LIVE_TTL = 600  # a fight that ended < SETTLED ago may still be gaining killmails
SETTLED = timedelta(hours=6)
TEAM_LETTERS = "ABCDEFGHI"
SHIPS_SHOWN = 20

# (pattern, source, kind); first match wins
URL_PATTERNS = [
    (r"zkillboard\.com/related/(?P<system>\d+)/(?P<time>\d+)", "zkillboard.com", "related"),
    (r"zkillboard\.com/br/(?P<id>\d+)", "zkillboard.com", "saved"),
    (r"br\.evetools\.org/related/(?P<system>\d+)/(?P<time>\d+)", "br.evetools.org", "related"),
    (r"br\.evetools\.org/br/(?P<id>[0-9a-fA-F]{24})", "br.evetools.org", "saved"),
    (r"killmail\.app/related/(?P<system>\d+)/(?P<time>\d+)", "killmail.app", "related"),
    (r"killmail\.app/br/(?P<id>[A-Za-z0-9_-]+)", "killmail.app", "saved"),
    (r"warbeacon\.net/br/related/(?P<system>\d+)/(?P<time>\d{12})", "warbeacon.net", "related"),
    (r"warbeacon\.net/br/report/(?P<id>[0-9a-fA-F-]{36})", "warbeacon.net", "saved"),
]


def related_time(raw: str) -> str:
    """YYYYMMDDHHMM, unix seconds or unix ms -> YYYYMMDDHH00 (zKillboard rejects other minutes)."""
    if len(raw) == 12:
        dt = datetime.strptime(raw, "%Y%m%d%H%M").replace(tzinfo=UTC)
    elif len(raw) in (10, 13):
        dt = datetime.fromtimestamp(int(raw) / (1000 if len(raw) == 13 else 1), UTC)
    else:
        raise ec.EveError(f"Unrecognised related-report time '{raw}' (want YYYYMMDDHHMM or a unix timestamp)")
    return dt.strftime("%Y%m%d%H00")


def parse_url(url: str) -> dict:
    for pattern, source, kind in URL_PATTERNS:
        m = re.search(pattern, url)
        if not m:
            continue
        if source == "zkillboard.com" and kind == "saved":
            raise ec.EveError(
                "Saved zKillboard battle reports (/br/<id>/) have no public API. Open the BR, use its "
                "'related' link (zkillboard.com/related/<system>/<time>/), or pass a br.evetools.org, "
                "killmail.app or warbeacon.net link.")
        spec = {"source": source, "kind": kind, **m.groupdict()}
        if kind == "related":
            spec["time"] = related_time(spec["time"])
            spec["key"] = f"{source}-related-{spec['system']}-{spec['time']}"
        else:
            spec["key"] = f"{source}-{spec['id']}"
        return spec
    raise ec.EveError(
        f"Unsupported battle report link: {url}\nSupported: zkillboard.com/related/…, br.evetools.org/br/… "
        "or /related/…, killmail.app/br/… or /related/…, warbeacon.net/br/report/… or /br/related/…")


def api_url(spec: dict) -> str:
    source, kind = spec["source"], spec["kind"]
    if kind == "related" and source in ("zkillboard.com", "warbeacon.net"):  # warbeacon builds these via POST
        return f"{ZKB}/api/related/{spec['system']}/{spec['time']}/"
    if source == "br.evetools.org":
        if kind == "related":
            return f"https://br.evetools.org/newapi/br/related/{spec['system']}/{spec['time']}"
        return f"https://br.evetools.org/newapi/br/composition/{spec['id']}"
    if source == "killmail.app":
        if kind == "related":
            return f"https://killmail.app/api/battle-reports/related/{spec['system']}/{spec['time']}"
        return f"https://killmail.app/api/battle-reports/{spec['id'].split('-')[0]}"
    return f"https://warbeacon.net/api/br/report/{spec['id']}"


# ---------------------------------------------------------------- normalizers
# Each returns (killmails, team_of or None, systems, names). Killmail shape:
#   {id, time, system_id, value, victim: Pilot, attackers: [Pilot]}
#   Pilot = {character_id, corporation_id, alliance_id, ship_type_id}
# team_of maps an entity key ("a:<alliance id>" or "c:<corp id>") to a team index.

PILOT_KEYS = ("character_id", "corporation_id", "alliance_id", "ship_type_id")


def entity_key(p: dict) -> str | None:
    if p.get("alliance_id"):
        return f"a:{p['alliance_id']}"
    if p.get("corporation_id"):
        return f"c:{p['corporation_id']}"
    return None


def _pilot(char=None, corp=None, ally=None, ship=None) -> dict:
    return dict(zip(PILOT_KEYS, (char or None, corp or None, ally or None, ship or None), strict=True))


def _iso_ms(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(text: str) -> str:
    return ec.parse_iso(text).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_zkillboard(data: dict):
    summary = data.get("summary") or {}
    kms, team_of, systems, names = {}, {}, {}, {}
    for index, team_name in enumerate(("teamA", "teamB")):
        team = summary.get(team_name) or {}
        for entity_id, entity in (team.get("entities") or {}).items():
            team_of[f"{'a' if entity.get('type') == 'alliance' else 'c'}:{entity_id}"] = index
            names[entity_id] = (entity.get("name"), entity.get("type"))
        for p in team.get("list") or []:
            pilot = _pilot(p.get("characterID"), p.get("corporationID"), p.get("allianceID"), p.get("shipTypeID"))
            if entity_key(pilot):
                team_of.setdefault(entity_key(pilot), index)
            names.update({p.get("characterID"): (p.get("characterName"), "character"),
                          p.get("shipTypeID"): (p.get("shipName"), "inventory_type")})
        for kill in (team.get("kills") or {}).values():
            v, system = kill.get("victim") or {}, kill.get("system") or {}
            systems[system.get("solarSystemID")] = {"id": system.get("solarSystemID"),
                                                    "name": system.get("solarSystemName"),
                                                    "region": system.get("regionName")}
            kms[kill["killID"]] = {
                "id": kill["killID"],
                "time": _iso_ms(kill["dttm"]["$date"]["$numberLong"]),
                "system_id": system.get("solarSystemID"),
                "value": (kill.get("zkb") or {}).get("totalValue") or 0,
                "victim": _pilot(v.get("characterID"), v.get("corporationID"), v.get("allianceID"),
                                 v.get("shipTypeID")),
                "attackers": [_pilot(a.get("characterID"), a.get("corporationID"), a.get("allianceID"),
                                     a.get("shipTypeID"))
                              for a in kill.get("involved") or [] if not a.get("isVictim")],
            }
    return list(kms.values()), team_of, list(systems.values()), names


def normalize_evetools(data: dict):
    if data.get("status") == "processing":
        raise ec.EveError("br.evetools.org is still building this report; try again in a minute.")
    relateds = data.get("relateds") or [data]  # saved BR has relateds[]; a related report is one block
    kms, systems = {}, {}
    for block in relateds:
        system = block.get("system") or {}
        if system.get("id"):
            systems[system["id"]] = {"id": system["id"], "name": system.get("name"), "region": system.get("region")}
        for km in block.get("kms") or []:
            v = km.get("victim") or {}
            kms[km["id"]] = {
                "id": km["id"],
                "time": _iso_ms(km["time"]),
                "system_id": km.get("system"),
                "value": km.get("totalValue") or v.get("lossValue") or 0,
                "victim": _pilot(v.get("char"), v.get("corp"), v.get("ally"), v.get("ship")),
                "attackers": [_pilot(a.get("char"), a.get("corp"), a.get("ally"), a.get("ship"))
                              for a in km.get("attackers") or []],
            }
    team_of = None
    if data.get("teams"):
        team_of = {(f"c:{m[5:]}" if str(m).startswith("corp:") else f"a:{m}"): index
                   for index, members in enumerate(data["teams"]) for m in members}
    names = {s["id"]: (s["name"], "solar_system") for s in systems.values()}
    return list(kms.values()), team_of, list(systems.values()), names


def normalize_killmailapp(data: dict):
    entities = data.get("entities") or {}
    names = {}
    for group, category in (("types", "inventory_type"), ("characters", "character"),
                            ("corporations", "corporation"), ("alliances", "alliance")):
        for entity_id, entity in (entities.get(group) or {}).items():
            if int(entity_id) > 0:
                names[entity_id] = (entity.get("name"), category)
    systems = [{"id": loc.get("solar_system_id"), "name": loc.get("solar_system_name"),
                "region": loc.get("region_name")}
               for loc in data.get("locations") or [data.get("location") or {}] if loc.get("solar_system_id")]
    kms = [{
        "id": km["id"],
        "time": _iso(km["created_at"]),
        "system_id": km.get("solar_system_id"),
        "value": km.get("isk_value") or 0,
        "victim": _pilot(km.get("character_id"), km.get("corporation_id"), km.get("alliance_id"),
                         km.get("ship_type_id")),
        "attackers": [_pilot(*(a.get(k) for k in PILOT_KEYS)) for a in km.get("attackers") or []],
    } for km in data.get("killmails") or []]
    return kms, None, systems, names  # killmail.app computes sides client-side


def normalize_warbeacon(data: dict):
    report = data.get("data") or {}
    if not data.get("success", True) or not report:
        raise ec.EveError(f"warbeacon.net returned no report: {str(data)[:200]}")
    team_of = {}
    for index, members in enumerate(report.get("teams") or []):
        for member in members:
            m = re.match(r"(alliance|corporation)_(\d+)$", member)
            if m:
                team_of[f"{m.group(1)[0]}:{m.group(2)}"] = index
    systems = [{"id": loc.get("id"), "name": loc.get("name"), "region": None}
               for loc in report.get("locations") or []]
    kms = [{
        "id": km["killmail_id"],
        "time": _iso(km["killmail_time"]),
        "system_id": km.get("solar_system_id"),
        "value": km.get("total_value") or 0,
        "victim": _pilot(*((km.get("victim") or {}).get(k) for k in PILOT_KEYS)),
        "attackers": [_pilot(*(a.get(k) for k in PILOT_KEYS)) for a in km.get("attackers") or []],
    } for km in report.get("killmails") or []]
    names = {s["id"]: (s["name"], "solar_system") for s in systems}
    return kms, team_of or None, systems, names


NORMALIZERS = {
    "zkillboard.com": normalize_zkillboard,
    "br.evetools.org": normalize_evetools,
    "killmail.app": normalize_killmailapp,
    "warbeacon.net": normalize_warbeacon,
}


def normalize(spec: dict, data: dict):
    """warbeacon related links are served from zKillboard, so they take the zKillboard normalizer."""
    via_zkb = spec["kind"] == "related" and spec["source"] == "warbeacon.net"
    return NORMALIZERS["zkillboard.com" if via_zkb else spec["source"]](data)


# ---------------------------------------------------------------- teams and stats

def infer_teams(kms: list[dict]) -> dict[str, int]:
    """Two-side split for sources without teams: put each entity opposite to whoever it shot / was shot by.

    Greedy: seed the most involved entity on team A, then repeatedly place the unassigned entity with the
    strongest ties to placed ones on the side it is least hostile to (shared kills count as friendly).
    """
    edges: dict[str, Counter] = defaultdict(Counter)  # edges[x][y] = hostility minus friendliness
    ties: dict[str, Counter] = defaultdict(Counter)
    involvement: Counter = Counter()
    for km in kms:
        victim = entity_key(km["victim"])
        shooters = sorted({k for k in map(entity_key, km["attackers"]) if k})
        involvement.update(shooters + ([victim] if victim else []))
        for s in shooters:
            if victim and s != victim:
                for x, y in ((s, victim), (victim, s)):
                    edges[x][y] += 1
                    ties[x][y] += 1
        for x, y in combinations(shooters, 2):
            for a, b in ((x, y), (y, x)):
                edges[a][b] -= 1
                ties[a][b] += 1
    team_of: dict[str, int] = {}
    score = {e: [0, 0] for e in involvement}
    strength: Counter = Counter()
    unassigned = set(involvement)
    while unassigned:
        e = max(unassigned, key=lambda k: (strength[k], involvement[k], k))
        team = 0 if score[e][0] <= score[e][1] else 1
        team_of[e] = team
        unassigned.discard(e)
        for other, weight in edges[e].items():
            if other in unassigned:
                score[other][team] += weight
                strength[other] += ties[e][other]
    return team_of


def _entity_ref(key: str, names: dict, pilots: int) -> dict:
    kind, entity_id = key.split(":")
    entity_id = int(entity_id)
    return {"kind": "alliance" if kind == "a" else "corporation", "id": entity_id,
            "name": names.get(entity_id), "pilots": pilots}


def build_report(kms: list[dict], team_of: dict[str, int], names: dict, top: int = 10) -> dict:
    team_count = max(2, max(team_of.values(), default=0) + 1)
    pilots = [defaultdict(set) for _ in range(team_count)]  # per team: entity key -> character ids
    fielded = [set() for _ in range(team_count)]  # per team: (character, ship)
    lost, isk_lost, isk_killed = [0] * team_count, [0.0] * team_count, [0.0] * team_count
    for km in kms:
        km["victim_team"] = team_of.get(entity_key(km["victim"]) or "")
        for p in [km["victim"]] + km["attackers"]:
            team = team_of.get(entity_key(p) or "")
            if team is None or not p.get("character_id"):
                continue
            pilots[team][entity_key(p)].add(p["character_id"])
            if p.get("ship_type_id"):
                fielded[team].add((p["character_id"], p["ship_type_id"]))
        if km["victim_team"] is not None:
            lost[km["victim_team"]] += 1
            isk_lost[km["victim_team"]] += km["value"]
        shooting = {team_of.get(entity_key(a) or "") for a in km["attackers"]}
        for team in shooting:
            if team is not None and team != km["victim_team"]:
                isk_killed[team] += km["value"]

    teams = []
    for t in range(team_count):
        entities = sorted((_entity_ref(k, names, len(chars)) for k, chars in pilots[t].items()),
                          key=lambda e: (-e["pilots"], e["name"] is None, e["name"] or ""))
        ships = Counter(ship for _, ship in fielded[t])
        total = isk_killed[t] + isk_lost[t]
        lead = entities[0]["name"] if entities else None
        teams.append({
            "name": f"Team {TEAM_LETTERS[t]}" + (f": {lead}" if lead else "")
                    + (f" +{len(entities) - 1}" if len(entities) > 1 else ""),
            "pilots": len(set().union(*pilots[t].values())) if pilots[t] else 0,
            "ships_lost": lost[t],
            "isk_lost": round(isk_lost[t], 2),
            "isk_killed": round(isk_killed[t], 2),
            "efficiency": round(isk_killed[t] / total, 4) if total else None,
            "entities": entities,
            "ships": [{"type_id": s, "name": names.get(s), "pilots": n} for s, n in ships.most_common()],
        })
    biggest = sorted(kms, key=lambda k: -k["value"])[:top]
    times = sorted(k["time"] for k in kms)
    return {
        "start": times[0] if times else None,
        "end": times[-1] if times else None,
        "totals": {"killmails": len(kms), "isk_destroyed": round(sum(k["value"] for k in kms), 2),
                   "pilots": sum(t["pilots"] for t in teams)},
        "teams": teams,
        "top_losses": [{
            "id": k["id"], "time": k["time"], "value": k["value"],
            "team": TEAM_LETTERS[k["victim_team"]] if k["victim_team"] is not None else None,
            "ship": ec.ref(k["victim"]["ship_type_id"], names),
            "pilot": ec.ref(k["victim"]["character_id"], names),
            "owner": ec.ref(k["victim"]["alliance_id"] or k["victim"]["corporation_id"], names),
            "url": f"{ZKB}/kill/{k['id']}/",
        } for k in biggest],
        "killmails": sorted(kms, key=lambda k: k["time"]),
    }


def ids_to_name(kms: list[dict], team_of: dict[str, int], top: int) -> set[int]:
    ids = {int(k.split(":")[1]) for k in team_of}
    for km in kms:
        ids.add(km["system_id"])
        ids.update(p["ship_type_id"] for p in [km["victim"]] + km["attackers"])
    for km in sorted(kms, key=lambda k: -k["value"])[:top]:  # pilot names only where shown
        ids.update(km["victim"][k] for k in ("character_id", "corporation_id", "alliance_id"))
    return {i for i in ids if i}


# ---------------------------------------------------------------- output

def _pct(value) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def render_markdown(report: dict) -> str:
    teams, totals = report["teams"], report["totals"]
    where = ", ".join(s["name"] + (f" ({s['region']})" if s.get("region") else "")
                      for s in report["systems"] if s.get("name")) or "unknown system"
    span = (f"{report['start'][:16].replace('T', ' ')} – {report['end'][11:16]} UTC"
            if report["start"] else "no killmails")
    out = [f"# Battle report: {where}", "",
           (f"{span} · {totals['killmails']} killmails · {ec.fmt_isk(totals['isk_destroyed'])} ISK destroyed"
            f" · {totals['pilots']} pilots  "),
           f"Source: {report['url']} (data: {report['api_url']})"]
    if report["teams_inferred"]:
        out.append("\n> Teams inferred from who shot whom; this source has no team split. "
                   "Check the member lists below.")
    out += ["", ec.md_table([""] + [t["name"] for t in teams], [
        ["Pilots"] + [t["pilots"] for t in teams],
        ["Ships lost"] + [t["ships_lost"] for t in teams],
        ["ISK lost"] + [ec.fmt_isk(t["isk_lost"]) for t in teams],
        ["ISK killed"] + [ec.fmt_isk(t["isk_killed"]) for t in teams],
        ["ISK efficiency"] + [_pct(t["efficiency"]) for t in teams],
    ])]
    for t in teams:
        out += ["", f"## {t['name']}", "",
                "Members: " + (", ".join(f"{e['name'] or e['id']} ({e['pilots']})" for e in t["entities"]) or "—"),
                "", "Ships fielded: " + (", ".join(f"{s['name'] or s['type_id']} ×{s['pilots']}"
                                                  for s in t["ships"][:SHIPS_SHOWN]) or "—")]
        if len(t["ships"]) > SHIPS_SHOWN:
            out[-1] += f", … ({len(t['ships']) - SHIPS_SHOWN} more types in report.json)"
    if report["top_losses"]:
        out += ["", "## Biggest losses", "", ec.md_table(
            ["Time (UTC)", "Team", "Ship", "Pilot", "Alliance / corp", "Value", "Link"],
            [[k["time"][11:16], k["team"] or "—", (k["ship"] or {}).get("name"), (k["pilot"] or {}).get("name"),
              (k["owner"] or {}).get("name"), ec.fmt_isk(k["value"]), k["url"]] for k in report["top_losses"]])]
    out += ["", f"_Fetched {report['_cache']['fetched_at']}._"]
    return "\n".join(out)


def load_report(url: str, refresh: bool = False, top: int = 10) -> tuple[dict, Path]:
    spec = parse_url(url)
    folder = ec.cache_root() / "battle-reports" / ec.slugify(spec["key"])
    cached = ec.load_json(folder / "report.json")
    if cached and not refresh and (cached.get("settled") or ec.is_fresh(cached, LIVE_TTL)):
        return cached, folder
    source_url = api_url(spec)
    data: Any = ec.get_json(source_url)
    kms, team_of, systems, names = normalize(spec, data)
    if not kms:
        raise ec.EveError(f"No killmails in this battle report ({source_url}).")
    ec.remember_names({k: v for k, v in names.items() if k})
    inferred = team_of is None
    team_of = infer_teams(kms) if team_of is None else team_of
    resolved = ec.resolve_names(ids_to_name(kms, team_of, top))
    for s in systems:
        s["name"] = s.get("name") or resolved.get(s["id"])
    known = {s["id"] for s in systems}
    systems += [{"id": i, "name": resolved.get(i), "region": None}
                for i in sorted({k["system_id"] for k in kms} - known) if i]
    report = {"source": spec["source"], "url": url, "api_url": source_url, "systems": systems,
              "teams_inferred": inferred, **build_report(kms, team_of, resolved, top)}
    report["settled"] = datetime.now(UTC) - ec.parse_iso(report["end"]) > SETTLED
    ec.stamp(report, [source_url])
    ec.save_json(folder / "report.json", report)
    ec.save_text(folder / "README.md", render_markdown(report))
    return report, folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="battle report or related-kills link")
    parser.add_argument("--top", type=int, default=10, help="rows in the biggest-losses table")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")
    args = parser.parse_args()
    _, folder = load_report(args.url, args.refresh, args.top)
    print((folder / "README.md").read_text(encoding="utf-8"))
    print(f"Cached: {folder}/ (report.json, README.md)")
    return 0


if __name__ == "__main__":
    ec.run(main)
