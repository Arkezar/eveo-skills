# eveo-skills

Agent skills for EVE Online, compatible with the [`skills` CLI](https://github.com/vercel-labs/skills) and the [Agent Skills spec](https://agentskills.io/specification).

| Skill | What it does | Data sources |
|---|---|---|
| [`eve-corp-alliance`](skills/eve-corp-alliance/SKILL.md) | Corporation / alliance profile and logo | ESI, image server |
| [`eve-killboard`](skills/eve-killboard/SKILL.md) | Kill/loss stats, activity, top lists, recent killmails | zKillboard API, ESI |
| [`eve-battle-report`](skills/eve-battle-report/SKILL.md) | Battle report from a link: teams, ships, ISK, efficiency | zKillboard, br.evetools.org, killmail.app, warbeacon.net |
| [`eve-ship-info`](skills/eve-ship-info/SKILL.md) | Ship by name: icon, render, 3D model (SOF) data, bonuses, stats | ESI, image server, EVE Ref |

## Install

```bash
npx skills add arkezar/eveo-skills            # pick skills and agents interactively
npx skills add arkezar/eveo-skills --skill eve-battle-report -a claude-code
```

Requirements: `python3` 3.11+ (standard library only) and HTTPS access to the APIs above. The skills do not need API keys or an EVE login.

## Cache

Every skill writes what it fetched as readable files and reuses them on the next call:

```text
~/.cache/eve-skills/              # or $EVE_SKILLS_CACHE_DIR, or $XDG_CACHE_HOME/eve-skills
├── names.json                    # shared ID → name cache (ESI /universe/names)
├── ids.json                      # shared exact name → ID cache (ESI /universe/ids)
├── corporations/<id>-<slug>/     # info.json, README.md, logo.png          (fresh 24 h)
├── alliances/<id>-<slug>/        # info.json, README.md, logo.png          (fresh 24 h)
├── killboard/<kind>-<id>-<slug>/ # stats.json, killmails-*.json, report-*.md (fresh 1 h)
├── battle-reports/<source>-<id>/ # report.json, README.md                  (final once the fight is 6 h old)
└── ships/<id>-<slug>/            # info.json, README.md, icon.png, render.jpg (fresh 30 d)
```

Pass `--refresh` to any script to ignore the cache.

| Variable | Default | Purpose |
|---|---|---|
| `EVE_SKILLS_CACHE_DIR` | `~/.cache/eve-skills` | Cache root |
| `EVE_SKILLS_USER_AGENT` | `eveo-skills/0.1 (+https://github.com/arkezar/eveo-skills)` | User-Agent sent to every API. zKillboard asks for a contact or project URL. |

## API etiquette

- ESI calls pin `X-Compatibility-Date` (see `ESI_COMPAT_DATE` in `shared/eve_common.py`) so response shapes stay stable.
- Requests to zKillboard are spaced at least 1 s apart. All clients retry on 420/429/5xx and honour `Retry-After`.
- zKillboard forbids scraping, so the skills use only its documented API. A saved zKillboard BR (`/br/<id>/`) has no API and is refused with an explanation.
- The br.evetools.org, killmail.app and warbeacon.net endpoints are the JSON APIs that their own web apps call. They are not documented and can change without notice.

## Development

The scripts share one helper module. The canonical copy is `shared/eve_common.py`; each skill gets its own copy, because `npx skills add` installs every skill folder on its own.

```bash
python3 tools/sync_common.py            # copy shared/eve_common.py into skills/*/scripts/
python3 -m unittest discover -s tests   # offline tests (fixtures are trimmed real API responses)
```

A test fails when a skill's copy of the helper module differs from the canonical one.
