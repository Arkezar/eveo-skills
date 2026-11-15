#!/usr/bin/env python3
"""Collect ordered reference images for an EVE poster prompt: pilot portraits, ship renders, logos, style posters.

Writes NN-<role>-<slug>.<ext> into one job folder plus references.json and references.md (REFERENCE MAP).
The image generator must receive the files in that order.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eve_common as ec

PORTRAIT_TTL = 7 * 24 * 3600  # players change portraits; renders and logos are effectively static
_CHARACTER_URL = re.compile(r"/characters?/(\d+)")
STYLE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

ROLE_TEXT = {
    "portrait": "portrait of pilot {name}. Controls this pilot's face, likeness, hairstyle and armour/clothing; "
                "use it only for {name}.",
    "ship": "{variation} of a {name} ({group}). Controls visible hull silhouette, proportions and materials "
            "of every {name} in the scene; keep its scale consistent with its class.",
    "logo": "logo of {name} ({kind}). This side's emblem: reproduce its shape and colours faithfully on a banner, "
            "badge, hull decal or stamp.",
    "style": "style poster ({name}). Palette, composition, print texture and typography ONLY; ignore its "
             "characters, ships, inscriptions, numbers, dates and battle.",
}


def resolve_character(query: str) -> dict:
    """Pilot from an exact name, character ID or zKillboard/EveWho URL -> {id, name}."""
    query = query.strip()
    m = _CHARACTER_URL.search(query)
    if m or query.isdigit():
        char_id = int(m.group(1) if m else query)
        if ec.name_category(char_id) != "character":
            raise ec.EveError(f"ID {char_id} is not a character")
        return {"id": char_id, "name": ec.resolve_names([char_id])[char_id]}
    hits = ec.lookup_ids(query).get("characters") or []
    if not hits:
        raise ec.EveError("no pilot with this exact name; pass the full character name, ID or a zKillboard URL")
    return hits[0]


def type_summary(type_id: int, refresh: bool = False) -> dict:
    """Ship class and available images, cached 30 days in <cache>/types.json."""
    path = ec.cache_root() / "types.json"
    hit = ec.load_json(path, {}).get(str(type_id))
    if not refresh and ec.is_fresh(hit, ec.SHIP_INDEX_TTL):
        return hit
    t = ec.esi_get(f"/universe/types/{type_id}")
    group = ec.esi_get(f"/universe/groups/{t['group_id']}")
    variations = ec.get_json(f"{ec.IMAGES}/types/{type_id}")
    hit = {"id": type_id, "name": t["name"], "group": group["name"],
           "is_ship": group["category_id"] == ec.SHIP_CATEGORY, "has_render": "render" in variations}
    cache = ec.load_json(path, {})
    cache[str(type_id)] = ec.stamp(hit, [f"{ec.ESI}/universe/types/{type_id}",
                                          f"{ec.ESI}/universe/groups/{t['group_id']}",
                                          f"{ec.IMAGES}/types/{type_id}"])
    ec.save_json(path, cache)
    return hit


def plan(pilots, ships, alliances, corporations, styles, refresh: bool = False) -> tuple[list[dict], list[str]]:
    """Resolve every name first, so one run reports all problems -> (references, notes)."""
    refs, errors, notes = [], [], []
    for query in pilots:
        try:
            c = resolve_character(query)
        except ec.EveError as err:
            errors.append(f"pilot '{query}': {err}")
            continue
        refs.append({"role": "portrait", "kind": "character", "id": c["id"], "name": c["name"],
                     "url": f"{ec.IMAGES}/characters/{c['id']}/portrait?size=1024",
                     "cache": ec.entity_dir("characters", c["id"], c["name"]) / "portrait-1024.jpg",
                     "max_age": PORTRAIT_TTL})
    for query in ships:
        try:
            type_id, note = ec.resolve_type(query, refresh)
            t = type_summary(type_id, refresh)
        except ec.EveError as err:
            errors.append(f"ship '{query}': {err}")
            continue
        notes += [note] if note else []
        if not t["is_ship"]:
            errors.append(f"ship '{query}': '{t['name']}' is a {t['group']}, not a ship")
            continue
        variation = "render" if t["has_render"] else "icon"
        if variation == "icon":
            notes.append(f"'{t['name']}' has only an icon; unseen hull details are not established by this reference")
        refs.append({"role": "ship", "kind": "type", "id": type_id, "name": t["name"], "group": t["group"],
                     "variation": variation,
                     "url": f"{ec.IMAGES}/types/{type_id}/{variation}?size=1024",
                     "cache": ec.entity_dir("ships", type_id, t["name"])
                              / f"{variation}-1024.{'jpg' if variation == 'render' else 'png'}"})
    for kind, queries in (("alliance", alliances), ("corporation", corporations)):
        for query in queries:
            try:
                e = ec.resolve_entity(query, kind)[0]
            except ec.EveError as err:
                errors.append(f"{kind} '{query}': {err}")
                continue
            refs.append({"role": "logo", "kind": kind, "id": e["id"], "name": e["name"],
                         "url": f"{ec.IMAGES}/{kind}s/{e['id']}/logo?size=256",
                         "cache": ec.entity_dir(kind + "s", e["id"], e["name"]) / "logo-256.png"})
    for style in styles:
        path = Path(style).expanduser().resolve()
        if not path.is_file():
            errors.append(f"style image '{style}': file not found")
        elif path.suffix.lower() not in STYLE_EXTENSIONS:
            errors.append(f"style image '{style}': use a PNG, JPEG, WebP or GIF image")
        else:
            refs.append({"role": "style", "kind": "file", "name": path.name, "cache": path})
    if errors:
        raise ec.EveError("Could not resolve:\n- " + "\n- ".join(errors))
    return refs, notes


def render_map(refs: list[dict], notes: list[str]) -> str:
    lines = ["# REFERENCE MAP", "",
             "Attach these images to the image generator in exactly this order.", ""]
    for ref in refs:
        lines.append(f"{ref['n']}. `{ref['file']}`: image {ref['n']} is a "
                     + ROLE_TEXT[ref["role"]].format(**ref))
    if not any(r["role"] == "portrait" for r in refs):
        lines += ["", "No pilot portrait: the scene must be ship-led, with no invented player likeness."]
    if notes:
        lines += ["", "Notes:"] + [f"- {n}" for n in notes]
    return "\n".join(lines)


def build(title: str, out: Path | None, refs: list[dict], notes: list[str], refresh: bool) -> Path:
    if out is None:
        base = ec.cache_root() / "posters"
        base.mkdir(parents=True, exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix=ec.slugify(title) + "-", dir=base))
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ec.EveError(f"Output folder is not empty: {out}. Choose a new --out folder; existing files are kept.")
    refs = [dict(ref) for ref in refs]
    for n, ref in enumerate(refs, 1):
        if ref.get("url"):
            ec.download(ref["url"], ref["cache"], refresh, ref.get("max_age"))
        ref["n"] = n
        name = Path(ref["name"]).stem if ref["role"] == "style" else ref["name"]
        ref["file"] = f"{n:02d}-{ref['role']}-{ec.slugify(name)}{ref['cache'].suffix}"
        shutil.copyfile(ref["cache"], out / ref["file"])
        ref["cache"] = str(ref["cache"])
    ec.save_json(out / "references.json", ec.stamp({"title": title, "references": refs, "notes": notes},
                                                    [r["url"] for r in refs if r.get("url")]))
    ec.save_text(out / "references.md", render_map(refs, notes))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", required=True, help="short topic; names the job folder")
    parser.add_argument("--pilot", action="append", default=[], help="character name, ID or URL (portrait)")
    parser.add_argument("--ship", action="append", default=[], help="ship name or type ID (render)")
    parser.add_argument("--alliance", action="append", default=[], help="alliance name, ID or URL (logo)")
    parser.add_argument("--corporation", action="append", default=[], help="corporation name, ID or URL (logo)")
    parser.add_argument("--style", action="append", default=[], help="style poster image file")
    parser.add_argument("--out", type=Path, help="new or empty job folder (default <cache>/posters/<title-slug>-<id>)")
    parser.add_argument("--refresh", action="store_true", help="refresh ship metadata and re-download every image")
    args = parser.parse_args()
    try:
        refs, notes = plan(args.pilot, args.ship, args.alliance, args.corporation, args.style, args.refresh)
        out = build(args.title, args.out, refs, notes, args.refresh)
    except OSError as err:
        raise ec.EveError(f"Cannot prepare reference files: {err}") from None
    print((out / "references.md").read_text(encoding="utf-8"))
    print(f"Job folder: {out}/ (references.md, references.json, numbered images)")
    return 0


if __name__ == "__main__":
    ec.run(main)
