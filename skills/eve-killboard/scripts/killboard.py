#!/usr/bin/env python3
"""zKillboard stats + recent kills/losses for an EVE corporation or alliance, cached as JSON + Markdown."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eve_common as ec

ZKB = "https://zkillboard.com"
TTL = 3600  # zKillboard documents a 1-hour client cache for these endpoints
TOP_LIST_ROWS = 5


def killmail_url(kind: str, entity_id: int, side: str = "all", solo: bool = False,
                 past_days: int | None = None, page: int = 1) -> str:
    """zKillboard /api/ path; entity filter first, then the modifiers that require it."""
    parts = [] if side == "all" else [side]
    parts += [f"{kind}ID", str(entity_id)]
    if solo:
        parts.append("solo")
    if past_days:
        parts += ["pastSeconds", str(min(past_days, 7) * 86400)]
    if page > 1:
        parts += ["page", str(page)]
    return f"{ZKB}/api/{'/'.join(parts)}/"


def filter_key(side: str, solo: bool, past_days: int | None, page: int) -> str:
    key = side + ("-solo" if solo else "")
    key += f"-{min(past_days, 7)}d" if past_days else ""
    return key + (f"-p{page}" if page > 1 else "")


def _zkb_json(url: str) -> Any:
    data: Any = ec.get_json(url)
    if isinstance(data, dict) and data.get("error"):  # zKillboard reports some errors with HTTP 200
        raise ec.EveError(f"zKillboard: {data['error']} ({url})")
    return data


def compact_stats(raw: dict) -> dict:
    info = raw.get("info") or {}
    keep = ("shipsDestroyed", "shipsLost", "iskDestroyed", "iskLost", "pointsDestroyed", "pointsLost",
            "soloKills", "soloLosses", "dangerRatio", "gangRatio", "soloRatio", "avgGangSize", "activepvp")
    months = raw.get("months") or {}
    return {
        "info": {k: info.get(k) for k in ("name", "ticker", "memberCount", "corpCount", "allianceID",
                                           "executorCorpID", "ceoID", "factionID") if info.get(k) is not None},
        **{k: raw.get(k) for k in keep if raw.get(k) is not None},
        "months": [months[k] for k in sorted(months)[-12:]],
        "topLists": [{"type": t.get("type"), "title": t.get("title"),
                      "values": [{"id": v.get("id"), "name": v.get("name"), "kills": v.get("kills")}
                                 for v in (t.get("values") or [])[:TOP_LIST_ROWS]]}
                     for t in raw.get("topLists") or []],
        "topIskKillIDs": raw.get("topIskKillIDs") or [],
    }


def _person(p: dict, names: dict) -> dict:
    return {k: ec.ref(p.get(f"{k}_id"), names)
            for k in ("character", "corporation", "alliance") if p.get(f"{k}_id")} | \
           {"ship": ec.ref(p.get("ship_type_id"), names)}


def compact_killmails(raw: list[dict], kind: str, entity_id: int, names: dict) -> list[dict]:
    rows = []
    for km in raw:
        victim, zkb = km.get("victim") or {}, km.get("zkb") or {}
        attackers = km.get("attackers") or []
        final = next((a for a in attackers if a.get("final_blow")), attackers[0] if attackers else {})
        rows.append({
            "id": km["killmail_id"],
            "time": km["killmail_time"],
            "side": "loss" if victim.get(f"{kind}_id") == entity_id else "kill",
            "system": ec.ref(km.get("solar_system_id"), names),
            "value": zkb.get("totalValue"),
            "victim": _person(victim, names),
            "final_blow": _person(final, names),
            "attackers": len(attackers),
            "solo": zkb.get("solo"),
            "npc": zkb.get("npc"),
            "labels": zkb.get("labels"),
            "hash": zkb.get("hash"),
            "url": f"{ZKB}/kill/{km['killmail_id']}/",
        })
    return rows


def _ids_in(raw: list[dict]) -> set[int]:
    """IDs shown per killmail: system, victim and final-blow pilot."""
    ids: set[Any] = set()
    for km in raw:
        ids.add(km.get("solar_system_id"))
        for p in [km.get("victim") or {}] + (km.get("attackers") or []):
            if p is km.get("victim") or p.get("final_blow"):
                ids.update(p.get(k) for k in ("character_id", "corporation_id", "alliance_id", "ship_type_id"))
    return {int(i) for i in ids if i}


def _who(p: dict) -> str:
    owner = p.get("alliance") or p.get("corporation")
    char = (p.get("character") or {}).get("name")
    return " / ".join(x for x in (char, owner and owner["name"]) if x) or "—"


def render_markdown(entity: dict, stats: dict, kms: list[dict], key: str, limit: int, zkb_url: str) -> str:
    info = stats.get("info") or {}
    kills, losses = stats.get("iskDestroyed") or 0, stats.get("iskLost") or 0
    eff = f"{100 * kills / (kills + losses):.1f}%" if kills + losses else "—"
    rows = [
        ["Ships destroyed / lost", f"{stats.get('shipsDestroyed', 0):,} / {stats.get('shipsLost', 0):,}"],
        ["ISK destroyed / lost", f"{ec.fmt_isk(kills)} / {ec.fmt_isk(losses)} (ISK efficiency {eff})"],
        ["Solo kills / losses", f"{stats.get('soloKills', 0):,} / {stats.get('soloLosses', 0):,}"],
        ["Danger ratio", f"{stats.get('dangerRatio', '—')}% dangerous"],
        ["Gang ratio", f"{stats.get('gangRatio', '—')}% in gangs, avg gang {stats.get('avgGangSize', '—')}"],
    ]
    if info.get("memberCount") is not None:
        rows.insert(0, ["Members", f"{info['memberCount']:,}" + (
            f" in {info['corpCount']:,} corporations" if info.get("corpCount") else "")])
    active = stats.get("activepvp") or {}
    if active:
        rows.append(["Active PvP (last 7 days)", ", ".join(
            f"{v.get('count')} {v.get('type', k).lower()}" for k, v in active.items())])
    out = [f"# Killboard: {entity['name']} ({entity['kind']} {entity['id']})", "",
           ec.md_table(["Stat (all time unless noted)", "Value"], rows)]
    if stats.get("months"):
        out += ["", "## Last months", "", ec.md_table(
            ["Month", "Kills", "Losses", "ISK destroyed", "ISK lost"],
            [[f"{m.get('year')}-{m.get('month'):02d}", m.get("shipsDestroyed", 0), m.get("shipsLost", 0),
              ec.fmt_isk(m.get("iskDestroyed")), ec.fmt_isk(m.get("iskLost"))]
             for m in reversed(stats["months"]) if isinstance(m.get("month"), int)])]
    for top in stats.get("topLists") or []:
        if top["values"]:
            out += ["", f"## {top['title']} (last 7 days, by kills)", "",
                    ", ".join(f"{v['name']} ({v['kills']})" for v in top["values"])]
    shown = kms[:limit]
    out += ["", f"## Recent killmails: {key} (showing {len(shown)} of {len(kms)} fetched)", ""]
    out.append(ec.md_table(
        ["Time (UTC)", "", "Victim ship", "Victim", "Final blow", "System", "Value", "Att.", "Link"],
        [[k["time"][:16].replace("T", " "), k["side"].upper(), (k["victim"].get("ship") or {}).get("name"),
          _who(k["victim"]), _who(k["final_blow"]), (k["system"] or {}).get("name"),
          ec.fmt_isk(k["value"]), k["attackers"], k["url"]] for k in shown]) if shown else "_No killmails._")
    out += ["", f"zKillboard page: {ZKB}/{entity['kind']}/{entity['id']}/  ",
            f"_Fetched {stats['_cache']['fetched_at']} from {zkb_url} (zKillboard withholds kills younger than 5 min)._"]
    return "\n".join(out)


def show(entity: dict, args) -> str:
    kind, entity_id = entity["kind"], entity["id"]
    folder = ec.entity_dir("killboard", f"{kind}-{entity_id}", entity.get("name"))
    stats_path = folder / "stats.json"
    stats = ec.load_json(stats_path)
    if args.refresh or not ec.is_fresh(stats, TTL):
        stats_url = f"{ZKB}/api/stats/{kind}ID/{entity_id}/"
        stats = ec.stamp(compact_stats(_zkb_json(stats_url)), [stats_url])
        ec.save_json(stats_path, stats)

    key = filter_key(args.side, args.solo, args.past_days, args.page)
    kms_path = folder / f"killmails-{key}.json"
    url = killmail_url(kind, entity_id, args.side, args.solo, args.past_days, args.page)
    cached = ec.load_json(kms_path)
    if args.refresh or not ec.is_fresh(cached, TTL):
        raw = _zkb_json(url)
        names = ec.resolve_names(_ids_in(raw))
        cached = ec.stamp({"entity": entity, "query": url,
                           "killmails": compact_killmails(raw, kind, entity_id, names)}, [url])
        ec.save_json(kms_path, cached)
    md_path = folder / f"report-{key}.md"
    ec.save_text(md_path, render_markdown(entity, stats, cached["killmails"], key, args.limit, url))
    text = md_path.read_text(encoding="utf-8")
    return f"{text}\nCached: {folder}/ (stats.json, {kms_path.name}, {md_path.name})\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="exact corporation/alliance name, numeric ID, or zKillboard URL")
    parser.add_argument("--type", choices=ec.ENTITY_KINDS, help="only match this kind")
    parser.add_argument("--side", choices=("all", "kills", "losses"), default="all")
    parser.add_argument("--solo", action="store_true", help="solo killmails only")
    parser.add_argument("--past-days", type=int, choices=range(1, 8), metavar="1-7",
                        help="only the last N days (zKillboard caps this at 7)")
    parser.add_argument("--page", type=int, default=1, help="zKillboard page, 200 killmails each (max 100)")
    parser.add_argument("--limit", type=int, default=20, help="rows shown in the Markdown table")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")
    args = parser.parse_args()
    if not 1 <= args.page <= 100:
        parser.error("--page must be 1-100")
    for entity in ec.resolve_entity(args.query, args.type):
        print(show(entity, args))
    return 0


if __name__ == "__main__":
    ec.run(main)
