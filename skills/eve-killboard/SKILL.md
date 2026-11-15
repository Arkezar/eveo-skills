---
name: eve-killboard
description: zKillboard data for an EVE Online corporation or alliance. Covers kill/loss totals, ISK efficiency, danger and gang ratios, monthly activity, top pilots, ships and systems, and recent killmails. Use when the user asks how active or dangerous a corp or alliance is, or wants its recent kills or losses.
compatibility: Requires python3 3.11+ (stdlib only) and HTTPS access to zkillboard.com and esi.evetech.net.
metadata:
  author: arkezar
  version: "0.1.0"
---

# EVE killboard (zKillboard)

## Run

```bash
python3 scripts/killboard.py "<exact name | numeric ID | zKillboard URL>" \
  [--type corporation|alliance] [--side all|kills|losses] [--solo] [--past-days 1-7] \
  [--page N] [--limit 20] [--refresh]
```

The script prints a Markdown report (stats, monthly activity, top lists, a killmail table) and its cache folder. Answer from that output. To analyse further (labels, hashes, all fetched killmails), read `killmails-<filter>.json`.

## Inputs

- Names must be the exact full corporation or alliance name. If the name is not found, ask for the full name, the ID, or a zKillboard link.
- `--side` picks kills, losses or both. `--past-days` is capped at 7 by zKillboard. One page holds up to 200 killmails, newest first, and `--page` goes up to 100. A page is not the whole history, so present it as "recent".
- zKillboard holds back killmails younger than 5 minutes.

## Output and cache

- Folder: `~/.cache/eve-skills/killboard/<corporation|alliance>-<id>-<slug>/`. Override the root with `EVE_SKILLS_CACHE_DIR`.
- Files: `stats.json` (compact zKillboard stats), `killmails-<filter>.json` (compact killmails with resolved names and zKillboard links), `report-<filter>.md` (the printed report).
- Data stays fresh for 1 hour, as zKillboard asks. A re-run within the hour makes no network calls. Use `--refresh` only for live data.

## zKillboard rules

Use only this script's documented API calls. zKillboard forbids scraping its HTML pages, so give the user the zKillboard links from the report instead. The script sends the project User-Agent; override it with `EVE_SKILLS_USER_AGENT` to add a contact address.
