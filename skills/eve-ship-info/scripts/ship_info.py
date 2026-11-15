#!/usr/bin/env python3
"""EVE Online ship (or any item type) by name: stats, bonuses, icon, render and 3D model (SOF) data.

ESI is the source of truth; EVE Ref adds skill bonuses (traits). Cached as info.json + README.md + images.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eve_common as ec  # noqa: E402

TTL = 30 * 24 * 3600  # static data; changes only with game patches
SHIP_CATEGORY = 6
IMAGE_SIZES = (32, 64, 128, 256, 512, 1024)
EVEREF = "https://ref-data.everef.net"

# Dogma attribute ID -> (section, label, kind). IDs are stable SDE values, checked against ESI /dogma/attributes.
ATTRIBUTES = {
    14: ("Fitting", "High slots", "int"), 13: ("Fitting", "Medium slots", "int"),
    12: ("Fitting", "Low slots", "int"), 1137: ("Fitting", "Rig slots", "int"),
    1547: ("Fitting", "Rig size", "int"), 1132: ("Fitting", "Calibration", "int"),
    102: ("Fitting", "Turret hardpoints", "int"), 101: ("Fitting", "Launcher hardpoints", "int"),
    48: ("Fitting", "CPU", "tf"), 11: ("Fitting", "Powergrid", "MW"),
    283: ("Drones", "Drone bay", "m3"), 1271: ("Drones", "Drone bandwidth", "Mbit/s"),
    263: ("Defense", "Shield HP", "HP"), 265: ("Defense", "Armor HP", "HP"), 9: ("Defense", "Structure HP", "HP"),
    479: ("Defense", "Shield recharge", "ms"),
    271: ("Resists", "Shield EM", "resist"), 274: ("Resists", "Shield thermal", "resist"),
    273: ("Resists", "Shield kinetic", "resist"), 272: ("Resists", "Shield explosive", "resist"),
    267: ("Resists", "Armor EM", "resist"), 270: ("Resists", "Armor thermal", "resist"),
    269: ("Resists", "Armor kinetic", "resist"), 268: ("Resists", "Armor explosive", "resist"),
    113: ("Resists", "Hull EM", "resist"), 110: ("Resists", "Hull thermal", "resist"),
    109: ("Resists", "Hull kinetic", "resist"), 111: ("Resists", "Hull explosive", "resist"),
    482: ("Capacitor", "Capacity", "GJ"), 55: ("Capacitor", "Recharge time", "ms"),
    37: ("Navigation", "Max velocity", "m/s"), 70: ("Navigation", "Inertia modifier", "x"),
    1281: ("Navigation", "Warp speed", "AU/s"), 552: ("Navigation", "Signature radius", "m"),
    76: ("Targeting", "Max targeting range", "m"), 192: ("Targeting", "Max locked targets", "int"),
    564: ("Targeting", "Scan resolution", "mm"),
    208: ("Targeting", "Radar strength", "points"), 209: ("Targeting", "Ladar strength", "points"),
    210: ("Targeting", "Magnetometric strength", "points"), 211: ("Targeting", "Gravimetric strength", "points"),
}
META_GROUPS: dict[Any, str] = {1: "Tech I", 2: "Tech II", 3: "Storyline", 4: "Faction", 5: "Officer", 6: "Deadspace",
               14: "Tech III"}
BONUS_UNITS: dict[Any, str] = {105: "%", 104: "x", 1: " m", 139: "+"}
HIDE_WHEN_ZERO = ("points", "m3", "Mbit/s")  # sensor types a hull lacks, empty drone bays


# ---------------------------------------------------------------- lookup

def ship_index(refresh: bool = False) -> dict[str, int]:
    """name -> type ID for every type in published ship groups; built once from ESI, cached for TTL."""
    path = ec.cache_root() / "ships" / "_index.json"
    cached = ec.load_json(path)
    if cached and not refresh and ec.is_fresh(cached, TTL):
        return cached["ships"]
    print("[eve] building ship name index (one-time, ~50 ESI requests)…", file=sys.stderr)
    type_ids: list[int] = []
    for group_id in ec.esi_get(f"/universe/categories/{SHIP_CATEGORY}")["groups"]:
        group = ec.esi_get(f"/universe/groups/{group_id}")
        if group.get("published"):
            type_ids += group.get("types", [])
    names = ec.resolve_names(type_ids)
    ships = {name: type_id for type_id, name in sorted(names.items())}
    ec.save_json(path, ec.stamp({"ships": ships}, [f"{ec.ESI}/universe/categories/{SHIP_CATEGORY}"]))
    return ships


def fuzzy_matches(query: str, index: dict[str, int], n: int = 5) -> tuple[list[str], bool]:
    """-> (candidates, confident). Confident: exactly one name contains the query, or a near-identical spelling."""
    q = query.casefold()
    by_fold = {name.casefold(): name for name in index}
    contains = sorted((name for fold, name in by_fold.items() if q in fold), key=len)
    close = [by_fold[f] for f in difflib.get_close_matches(q, list(by_fold), n=n, cutoff=0.6)]
    candidates = list(dict.fromkeys(contains + close))[:n]
    confident = len(contains) == 1 or (not contains and bool(close) and
                                       difflib.SequenceMatcher(None, q, close[0].casefold()).ratio() >= 0.85)
    return candidates, confident


def resolve_type(query: str, refresh: bool) -> tuple[int, str | None]:
    """-> (type_id, note); note says when the name was matched loosely."""
    query = query.strip()
    if query.isdigit():
        return int(query), None
    hits = ec.lookup_ids(query).get("inventory_types") or []
    if hits:
        return hits[0]["id"], None
    index = ship_index(refresh)
    candidates, confident = fuzzy_matches(query, index)
    if not candidates:
        raise ec.EveError(f"No item or ship named '{query}'. Pass the exact in-game name or a type ID.")
    if confident:
        return index[candidates[0]], f"'{query}' matched ship '{candidates[0]}'"
    raise ec.EveError(f"'{query}' is ambiguous. Did you mean: {', '.join(candidates)}? Re-run with the exact name.")


# ---------------------------------------------------------------- fetch

def fmt_attribute(value: float, kind: str) -> str:
    if kind == "resist":
        return f"{(1 - value) * 100:.1f}%"
    if kind == "ms":
        return f"{value / 1000:g} s"
    if kind == "int":
        return f"{value:.0f}"
    if kind == "x":
        return f"{value:g}x"
    return f"{value:,.2f}".rstrip("0").rstrip(".") + f" {kind}"


def key_attributes(dogma: list[dict]) -> dict[str, dict[str, str]]:
    values = {a["attribute_id"]: a["value"] for a in dogma or []}
    sections: dict[str, dict[str, str]] = {}
    for attr_id, (section, label, kind) in ATTRIBUTES.items():
        if attr_id in values and not (kind in HIDE_WHEN_ZERO and values[attr_id] == 0):
            sections.setdefault(section, {})[label] = fmt_attribute(values[attr_id], kind)
    return sections


def _bonus_line(bonus: dict) -> str:
    text = ec.strip_html((bonus.get("bonus_text") or {}).get("en", ""))
    if bonus.get("bonus") is None:
        return text
    return f"{bonus['bonus']:g}{BONUS_UNITS.get(bonus.get('unit_id'), '')} {text}"


def traits(ref: dict) -> dict[str, list[str]]:
    """EVE Ref traits -> {"Per level of <skill>": [...], "Role bonus": [...]}."""
    raw = ref.get("traits") or {}
    skill_names = ec.resolve_names(int(s) for s in (raw.get("types") or {}))
    out: dict[str, list[str]] = {}
    for skill_id, bonuses in (raw.get("types") or {}).items():
        out[f"Per level of {skill_names.get(int(skill_id), skill_id)}"] = [
            _bonus_line(b) for _, b in sorted(bonuses.items(), key=lambda kv: int(kv[0]))]
    for key, label in (("role_bonuses", "Role bonus"), ("misc_bonuses", "Other bonus")):
        items = raw.get(key) or {}
        items = list(items.values()) if isinstance(items, dict) else items
        if items:
            out[label] = [_bonus_line(b) for b in items]
    return out


def image_urls(type_id: int, variations: list[str]) -> dict[str, dict[str, str]]:
    return {v: {str(s): f"{ec.IMAGES}/types/{type_id}/{v}?size={s}" for s in IMAGE_SIZES} for v in variations}


def fetch_type(type_id: int) -> tuple[dict, list[str]]:
    t = ec.esi_get(f"/universe/types/{type_id}")
    group = ec.esi_get(f"/universe/groups/{t['group_id']}")
    category = ec.esi_get(f"/universe/categories/{group['category_id']}")
    sources = [f"{ec.ESI}/universe/types/{type_id}"]
    model = None
    if t.get("graphic_id"):
        try:
            g = ec.esi_get(f"/universe/graphics/{t['graphic_id']}")
            model = {"graphic_id": t["graphic_id"], "sof_dna": g.get("sof_dna"), "sof_hull": g.get("sof_hull_name"),
                     "sof_faction": g.get("sof_fation_name"), "sof_race": g.get("sof_race_name"),
                     "graphic_file": g.get("graphic_file")}
        except ec.HttpError as err:
            if err.status != 404:
                raise
    try:
        ref: dict[str, Any] = ec.get_json(f"{EVEREF}/types/{type_id}")
        sources.append(f"{EVEREF}/types/{type_id}")
    except ec.EveError as err:
        print(f"[eve] EVE Ref unavailable, skipping bonuses: {err}", file=sys.stderr)
        ref = {}
    variations: list[str] = ec.get_json(f"{ec.IMAGES}/types/{type_id}")
    names = ec.resolve_names([ref.get("faction_id")] + [int(s) for s in ref.get("required_skills") or {}])
    wiki = "https://wiki.eveuniversity.org/" + t["name"].replace(" ", "_")
    doc = {
        "type_id": type_id,
        "name": t["name"],
        "group": {"id": group["group_id"], "name": group["name"]},
        "category": {"id": category["category_id"], "name": category["name"]},
        "is_ship": category["category_id"] == SHIP_CATEGORY,
        "published": t.get("published"),
        "faction": ec.ref(ref.get("faction_id"), names),
        "tech_level": ref.get("tech_level"),
        "meta_group": META_GROUPS.get(ref.get("meta_group_id"), ref.get("meta_group_id")),
        "description": ec.strip_html(t.get("description", "")),
        "physical": {k: t.get(k) for k in ("mass", "volume", "packaged_volume", "capacity", "radius")},
        "attributes": key_attributes(t.get("dogma_attributes")),
        "traits": traits(ref),
        "required_skills": [{"skill": ec.ref(int(s), names), "level": lvl}
                            for s, lvl in (ref.get("required_skills") or {}).items()],
        "model": model,
        "images": {"files": {}, "urls": image_urls(type_id, variations)},
        "links": {"everef": f"https://everef.net/types/{type_id}",
                  "zkillboard": f"https://zkillboard.com/ship/{type_id}/",
                  "eve_university_wiki": wiki},
    }
    return doc, sources


# ---------------------------------------------------------------- output

def render_markdown(doc: dict) -> str:
    files = doc["images"]["files"]
    out = [f"# {doc['name']}", ""]
    if files.get("render"):
        out += [f"![render]({files['render']})", ""]
    rows = [["Type ID", doc["type_id"]], ["Group", f"{doc['group']['name']} ({doc['category']['name']})"]]
    if doc.get("faction"):
        rows.append(["Faction", doc["faction"]["name"]])
    if doc.get("meta_group"):
        rows.append(["Meta group", doc["meta_group"]])
    phys = doc["physical"]
    rows += [["Mass", f"{phys['mass']:,.0f} kg" if phys.get("mass") else "—"],
             ["Volume (packaged)", f"{phys.get('volume') or 0:,.0f} m3 ({phys.get('packaged_volume') or 0:,.0f} m3)"],
             ["Cargo", f"{phys.get('capacity') or 0:,.0f} m3"]]
    out.append(ec.md_table(["Field", "Value"], rows))
    if doc["description"]:
        out += ["", doc["description"]]
    if doc["traits"]:
        out += ["", "## Bonuses"]
        for heading, lines in doc["traits"].items():
            out += ["", f"**{heading}**", ""] + [f"- {line}" for line in lines]
    if doc["attributes"]:
        out += ["", "## Stats (base, no skills)"]
        for section, values in doc["attributes"].items():
            out += ["", f"**{section}:** " + ", ".join(f"{k} {v}" for k, v in values.items())]
    if doc["required_skills"]:
        out += ["", "## Required skills", "",
                ", ".join(f"{(r['skill'] or {}).get('name')} {r['level']}" for r in doc["required_skills"])]
    out += ["", "## Look", ""]
    urls = doc["images"]["urls"]
    for variation in urls:
        out.append(f"- {variation}: " + ", ".join(f"[{s}px]({u})" for s, u in urls[variation].items()))
    if files:
        out.append("- cached files: " + ", ".join(files.values()))
    model = doc.get("model")
    if model:
        out += ["", f"3D model (Space Object Factory): DNA `{model['sof_dna']}` · hull `{model['sof_hull']}` · "
                    f"faction `{model['sof_faction']}` · race `{model['sof_race']}` · graphic ID {model['graphic_id']}",
                "No official web 3D viewer exists; community viewers accept the SOF DNA string."]
    out += ["", "## Links", ""] + [f"- {k}: {v}" for k, v in doc["links"].items()]
    out += ["", f"_Fetched {doc['_cache']['fetched_at']} from ESI"
                + (" + EVE Ref" if any("everef" in s for s in doc["_cache"]["sources"]) else "") + "._"]
    return "\n".join(out)


def download_images(doc: dict, folder: Path, refresh: bool) -> None:
    wanted = {"icon": ("128", "icon.png"), "render": ("512", "render.jpg")}
    for variation, (size, filename) in wanted.items():
        url = doc["images"]["urls"].get(variation, {}).get(size)
        if not url:
            continue
        try:
            ec.download(url, folder / filename, refresh)
            doc["images"]["files"][variation] = filename
        except ec.EveError as err:
            print(f"[eve] {variation} download failed: {err}", file=sys.stderr)


def show(query: str, refresh: bool) -> str:
    type_id, note = resolve_type(query, refresh)
    folder = ec.entity_dir("ships", type_id, None)
    doc = ec.load_json(folder / "info.json")
    if refresh or not ec.is_fresh(doc, TTL):
        doc, sources = fetch_type(type_id)
        folder = ec.entity_dir("ships", type_id, doc["name"])
        download_images(doc, folder, refresh)
        ec.stamp(doc, sources)
        ec.save_json(folder / "info.json", doc)
        ec.save_text(folder / "README.md", render_markdown(doc))
    text = (folder / "README.md").read_text(encoding="utf-8")
    header = f"> {note}\n\n" if note else ""
    if not doc["is_ship"]:
        header += f"> Note: {doc['name']} is a {doc['category']['name']}, not a ship.\n\n"
    return f"{header}{text}\nCached: {folder}/ (info.json, README.md, " \
           f"{', '.join(doc['images']['files'].values()) or 'no images'})\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help="ship name (exact or close), or numeric type ID")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")
    args = parser.parse_args()
    if re.fullmatch(r"\s*", args.query):
        parser.error("empty ship name")
    print(show(args.query, args.refresh))
    return 0


if __name__ == "__main__":
    ec.run(main)
