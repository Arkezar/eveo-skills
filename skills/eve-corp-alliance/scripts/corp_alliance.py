#!/usr/bin/env python3
"""EVE Online corporation / alliance profile + logo from ESI, cached as info.json + README.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eve_common as ec  # noqa: E402

TTL = 24 * 3600
LOGO_SIZES = {"corporation": (64, 128, 256), "alliance": (64, 128)}


def _links(kind: str, entity_id: int, name: str) -> dict:
    dotlan = "corp" if kind == "corporation" else "alliance"
    return {
        "zkillboard": f"https://zkillboard.com/{kind}/{entity_id}/",
        "evewho": f"https://evewho.com/{kind}/{entity_id}",
        "dotlan": f"https://evemaps.dotlan.net/{dotlan}/{name.replace(' ', '_')}",
    }


def _logo(kind: str, entity_id: int) -> dict:
    urls = {str(s): f"{ec.IMAGES}/{kind}s/{entity_id}/logo?size={s}" for s in LOGO_SIZES[kind]}
    return {"file": "logo.png", "urls": urls}


def fetch_corporation(cid: int) -> tuple[dict, list[str]]:
    c = ec.esi_get(f"/corporations/{cid}")
    history = ec.esi_get(f"/corporations/{cid}/alliancehistory")
    names = ec.resolve_names(
        [c.get(k) for k in ("ceo_id", "creator_id", "alliance_id", "home_station_id", "faction_id")]
        + [h.get("alliance_id") for h in history]
    )
    doc = {
        "kind": "corporation",
        "id": cid,
        "name": c["name"],
        "ticker": c["ticker"],
        "alliance": ec.ref(c.get("alliance_id"), names),
        "faction": ec.ref(c.get("faction_id"), names),
        "ceo": ec.ref(c.get("ceo_id"), names),
        "creator": ec.ref(c.get("creator_id"), names),
        "home_station": ec.ref(c.get("home_station_id"), names),
        "member_count": c.get("member_count"),
        "date_founded": c.get("date_founded"),
        "tax_rates_percent": c.get("tax_rates"),
        "war_eligible": c.get("war_eligible"),
        "friendly_fire": c.get("friendly_fire"),
        "state": c.get("state"),
        "type": c.get("type"),
        "url": c.get("url") or None,
        "description": ec.strip_html(c.get("description", "")),
        "alliance_history": [
            {"start_date": h["start_date"], "alliance": ec.ref(h.get("alliance_id"), names)}
            for h in sorted(history, key=lambda h: h["start_date"], reverse=True)
        ],
        "logo": _logo("corporation", cid),
        "links": _links("corporation", cid, c["name"]),
    }
    return doc, [f"{ec.ESI}/corporations/{cid}", f"{ec.ESI}/corporations/{cid}/alliancehistory"]


def fetch_alliance(aid: int) -> tuple[dict, list[str]]:
    a = ec.esi_get(f"/alliances/{aid}")
    corp_ids = ec.esi_get(f"/alliances/{aid}/corporations")
    names = ec.resolve_names(
        [a.get(k) for k in ("executor_corporation_id", "creator_corporation_id", "creator_id", "faction_id")]
        + corp_ids
    )
    corps = sorted(({"id": i, "name": names.get(i)} for i in corp_ids),
                   key=lambda r: (r["name"] or "").casefold())
    doc = {
        "kind": "alliance",
        "id": aid,
        "name": a["name"],
        "ticker": a["ticker"],
        "date_founded": a.get("date_founded"),
        "executor_corporation": ec.ref(a.get("executor_corporation_id"), names),
        "creator_corporation": ec.ref(a.get("creator_corporation_id"), names),
        "creator": ec.ref(a.get("creator_id"), names),
        "faction": ec.ref(a.get("faction_id"), names),
        "corporation_count": len(corps),
        "corporations": corps,
        "logo": _logo("alliance", aid),
        "links": _links("alliance", aid, a["name"]),
    }
    return doc, [f"{ec.ESI}/alliances/{aid}", f"{ec.ESI}/alliances/{aid}/corporations"]


def render_markdown(doc: dict) -> str:
    rows: list[list] = [[f"{doc['kind'].title()} ID", doc["id"]]]
    if doc["kind"] == "corporation":
        tax = doc.get("tax_rates_percent") or {}
        rows += [
            ["Alliance", ec.ref_text(doc["alliance"])],
            ["Faction", ec.ref_text(doc["faction"])] if doc.get("faction") else None,
            ["CEO", ec.ref_text(doc["ceo"])],
            ["Members", doc["member_count"]],
            ["Founded", (doc["date_founded"] or "")[:10]],
            ["Home station", ec.ref_text(doc["home_station"])],
            ["Tax (ISK / LP)", f"{tax.get('isk', '?')}% / {tax.get('loyalty_point', '?')}%"],
            ["War eligible", "yes" if doc["war_eligible"] else "no"],
            ["URL", doc["url"] or "—"],
        ]
    else:
        rows += [
            ["Executor", ec.ref_text(doc["executor_corporation"])],
            ["Founded", (doc["date_founded"] or "")[:10]],
            ["Creator corporation", ec.ref_text(doc["creator_corporation"])],
            ["Faction", ec.ref_text(doc["faction"])] if doc.get("faction") else None,
            ["Member corporations", doc["corporation_count"]],
        ]
    out = [f"# {doc['name']} [{doc['ticker']}]", "", "![logo](logo.png)", "",
           ec.md_table(["Field", "Value"], [r for r in rows if r])]
    if doc["kind"] == "corporation":
        if doc["description"]:
            out += ["", "## Description", "", doc["description"]]
        if doc["alliance_history"]:
            out += ["", "## Alliance history", "", ec.md_table(
                ["Since", "Alliance"],
                [[h["start_date"][:10], ec.ref_text(h["alliance"]) if h["alliance"] else "(no alliance)"]
                 for h in doc["alliance_history"]])]
    else:
        shown = doc["corporations"][:100]
        out += ["", f"## Member corporations ({doc['corporation_count']})", "",
                ", ".join(f"{c['name']} ({c['id']})" for c in shown)]
        if doc["corporation_count"] > len(shown):
            out.append(f"\n…and {doc['corporation_count'] - len(shown)} more in info.json.")
    out += ["", "## Links", ""] + [f"- {k}: {v}" for k, v in doc["links"].items()]
    out += ["", f"_Logo URLs: {', '.join(f'{s}px {u}' for s, u in doc['logo']['urls'].items())}_",
            f"_Fetched {doc['_cache']['fetched_at']} from ESI._"]
    return "\n".join(out)


def show(entity: dict, refresh: bool) -> str:
    kind, entity_id = entity["kind"], entity["id"]
    folder = ec.entity_dir(kind + "s", entity_id, entity.get("name"))
    doc = ec.load_json(folder / "info.json")
    if refresh or not ec.is_fresh(doc, TTL):
        fetch = fetch_corporation if kind == "corporation" else fetch_alliance
        doc, sources = fetch(entity_id)
        ec.stamp(doc, sources)
        ec.save_json(folder / "info.json", doc)
        ec.save_text(folder / "README.md", render_markdown(doc))
    logo_size = max(LOGO_SIZES[kind])
    try:
        ec.download(doc["logo"]["urls"][str(logo_size)], folder / "logo.png", refresh)
    except ec.EveError as err:
        print(f"[eve] logo download failed: {err}", file=sys.stderr)
    text = (folder / "README.md").read_text(encoding="utf-8")
    return f"{text}\nCached: {folder}/ (info.json, README.md, logo.png)\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="exact corporation/alliance name, numeric ID, or zKillboard/EveWho URL")
    parser.add_argument("--type", choices=ec.ENTITY_KINDS, help="only match this kind")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")
    args = parser.parse_args()
    for entity in ec.resolve_entity(args.query, args.type):
        print(show(entity, args.refresh))
    return 0


if __name__ == "__main__":
    ec.run(main)
