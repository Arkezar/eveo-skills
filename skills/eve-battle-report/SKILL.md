---
name: eve-battle-report
description: Load an EVE Online battle report (BR) from a link and summarise it. Covers teams, pilots, ships fielded, ISK lost and killed, efficiency and the biggest losses. Use when the user pastes a zkillboard.com/related, br.evetools.org, killmail.app or warbeacon.net link, or asks who won a fight.
compatibility: Requires python3 3.11+ (stdlib only) and HTTPS access to the BR site, zkillboard.com and esi.evetech.net.
metadata:
  author: arkezar
  version: "0.1.0"
---

# EVE battle report

## Run

```bash
python3 scripts/battle_report.py "<battle report URL>" [--top 10] [--refresh]
```

The script prints a Markdown summary and its cache folder. The summary has a team comparison table, the members and ships fielded per team, and the biggest losses. Answer from it. For per-killmail detail, read `report.json`, which has every killmail with its victim, attackers and team.

## Supported links

| Link | Data comes from |
|---|---|
| `zkillboard.com/related/<system>/<YYYYMMDDHHMM>/` | zKillboard related API (teams included) |
| `br.evetools.org/br/<id>` | br.evetools.org saved report (teams included) |
| `br.evetools.org/related/<system>/<time>` | br.evetools.org (teams inferred) |
| `killmail.app/br/<id>`, `killmail.app/related/<system>/<time>` | killmail.app (teams inferred) |
| `warbeacon.net/br/report/<uuid>` | warbeacon.net saved report (teams included) |
| `warbeacon.net/br/related/<system>/<YYYYMMDDHHMM>` | zKillboard related API |

Saved zKillboard BRs (`zkillboard.com/br/<id>/`) have no public API, and the script exits with an explanation. Ask the user for the BR's "related" link or a link from another BR site.

## Teams

- If the source has no team split, the script infers two sides from who shot whom. The report then says "Teams inferred". Tell the user the split is a heuristic, and check the member lists for an entity that looks misplaced.
- ISK killed for a team is the value of every killmail where that team had at least one attacker. ISK efficiency is killed / (killed + lost).

## Output and cache

- Folder: `~/.cache/eve-skills/battle-reports/<source>-<id>/` with `report.json` and `README.md`. Override the root with `EVE_SKILLS_CACHE_DIR`.
- When the last kill is more than 6 h old, the cached report is final and is never refetched. A fight that is still running is refetched after 10 min. Use `--refresh` to force a refetch.
