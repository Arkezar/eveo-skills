---
name: eve-corp-alliance
description: EVE Online corporation and alliance profiles with logos, from ESI. Covers ticker, CEO, members, home station, alliance history and member corporations. Use when the user asks about an EVE corp or alliance, wants its logo, or needs its ID from a name.
compatibility: Requires python3 3.11+ (stdlib only) and HTTPS access to esi.evetech.net and images.evetech.net.
metadata:
  author: arkezar
  version: "0.1.0"
---

# EVE corporation / alliance info

## Run

```bash
python3 scripts/corp_alliance.py "<exact name | numeric ID | zKillboard/EveWho URL>" [--type corporation|alliance] [--refresh]
```

The script prints a Markdown profile and the path of its cache folder. Answer from that output. To get fields the Markdown leaves out, read `info.json` in the same folder. It holds the full member-corporation list, logo URLs for every size, and the raw tax rates.

## Inputs

- Names must be the **exact full name**. Matching is case-insensitive. ESI cannot search by ticker or partial name without an authenticated character. If the script reports "No corporation or alliance named …", ask the user for the full name, the ID, or a zKillboard link.
- If a corporation and an alliance share the name, the script prints both. Add `--type` to keep one.

## Output and cache

- Folder: `~/.cache/eve-skills/corporations/<id>-<slug>/` or `…/alliances/<id>-<slug>/`. Override the root with `EVE_SKILLS_CACHE_DIR`.
- Files: `info.json` (structured data), `README.md` (the printed profile), `logo.png` (corporations 256 px, alliances 128 px).
- A cached profile stays fresh for 24 h, and a re-run within that time makes no network calls. Use `--refresh` only when the user wants live data.
- To show the logo, give the `logo.png` path or the image-server URL from `info.json`.

## Related

For alliance member totals, activity, or kills and losses, use the `eve-killboard` skill.
