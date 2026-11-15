---
name: eve-ship-info
description: EVE Online ship lookup by name. Covers how it looks (icon, render images, 3D model SOF data), skill and role bonuses, slots, fitting, defense, resists, speed, targeting and required skills. Works for any item type too. Use when the user names an EVE ship, wants its picture or model, or asks for its stats or bonuses.
compatibility: Requires python3 3.11+ (stdlib only) and HTTPS access to esi.evetech.net, images.evetech.net and ref-data.everef.net.
metadata:
  author: arkezar
  version: "0.1.0"
---

# EVE ship info

## Run

```bash
python3 scripts/ship_info.py "<ship name | type ID>" [--refresh]
```

The script prints a Markdown sheet and its cache folder. The sheet has a header table, description, bonuses, base stats, required skills, image links, 3D model data and links. Answer from it. For details it does not show, read `info.json`.

## Names

- An exact in-game name (case-insensitive) or a type ID works for any item.
- A partial or misspelled ship name ("tempest fleet", "Vexr Navy Isue") is matched against an index of every ship. If the match is safe, the output starts with "matched ship '…'"; tell the user which ship you used. If it is ambiguous, the script exits with "Did you mean: …". Ask the user to pick one, then re-run with the exact name.
- The first loose match builds the ship index, which takes about 50 ESI requests. It is cached for 30 days.
- For a non-ship item, the output starts with "Note: … is a <category>, not a ship".

## Look and model

- Cached files: `icon.png` (128 px) and `render.jpg` (512 px). `info.json` → `images.urls` holds icon and render URLs from 32 to 1024 px.
- `model` in `info.json` holds the Space Object Factory data: graphic ID, SOF DNA, hull, faction and race. CCP has no official web 3D viewer, so offer the SOF DNA for community viewers and the 1024 px render as the visual.

## Output and cache

- Folder: `~/.cache/eve-skills/ships/<type id>-<slug>/`. Override the root with `EVE_SKILLS_CACHE_DIR`.
- Data stays fresh for 30 days, because ship data only changes with game patches. Use `--refresh` after a balance patch.
- Stats are base hull values, with no skills or fittings applied. Bonuses come from EVE Ref; if it is unreachable, the sheet has no bonuses section.
