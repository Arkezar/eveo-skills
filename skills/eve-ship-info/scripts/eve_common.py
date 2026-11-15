"""Shared helpers for the eveo-skills scripts: HTTP, cache, ESI name/ID resolution, formatting.

Canonical copy lives in shared/eve_common.py; tools/sync_common.py copies it into every
skills/*/scripts/ directory, because each skill is installed on its own.
"""
from __future__ import annotations

import gzip
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ESI = "https://esi.evetech.net"
ESI_COMPAT_DATE = "2026-08-18"  # pinned so ESI response shapes stay stable
IMAGES = "https://images.evetech.net"
USER_AGENT = os.environ.get(
    "EVE_SKILLS_USER_AGENT", "eveo-skills/0.1 (+https://github.com/arkezar/eveo-skills)"
)
TIMEOUT = 60
MAX_WAIT = 120  # longest Retry-After we sleep through before giving up
MIN_INTERVAL = {"zkillboard.com": 1.0}  # zKillboard asks clients to space requests out
_last_request: dict[str, float] = {}


class EveError(Exception):
    """Expected failure with a message fit for the user (not found, API down, bad input)."""


class HttpError(EveError):
    def __init__(self, status: int, url: str, detail: str = ""):
        self.status = status
        super().__init__(f"HTTP {status} for {url}" + (f": {detail}" if detail else ""))


# ---------------------------------------------------------------- HTTP

def _throttle(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname or ""
    gap = MIN_INTERVAL.get(host)
    if gap:
        wait = _last_request.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def _decode(raw: bytes, headers) -> bytes:
    if (headers.get("Content-Encoding") or "").lower() == "gzip":
        return gzip.decompress(raw)
    return raw


def _retry_wait(err: urllib.error.HTTPError, attempt: int) -> float:
    for header in ("Retry-After", "X-Esi-Error-Limit-Reset"):
        value = err.headers.get(header)
        if value and value.strip().isdigit():
            return float(value) + 1
    return 2.0 ** attempt


def http(method: str, url: str, body=None, accept: str = "application/json", retries: int = 3) -> bytes:
    """Request `url`; retry 420/429/5xx and network errors, honouring Retry-After."""
    if not url.startswith("https://"):
        raise EveError(f"Refusing non-HTTPS URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if url.startswith(ESI):
        headers["X-Compatibility-Date"] = ESI_COMPAT_DATE
    for attempt in range(retries + 1):
        _throttle(url)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - https-only, checked above
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                return _decode(resp.read(), resp.headers)
        except urllib.error.HTTPError as err:
            if err.code in (420, 429, 500, 502, 503, 504) and attempt < retries:
                wait = _retry_wait(err, attempt)
                if wait <= MAX_WAIT:
                    print(f"[eve] HTTP {err.code} from {url}, retrying in {wait:.0f}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
            try:
                detail = _decode(err.read(), err.headers).decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            raise HttpError(err.code, url, detail) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            if attempt < retries:
                time.sleep(2.0 ** attempt)
                continue
            raise EveError(f"Network error for {url}: {err}") from None
    raise EveError(f"Giving up on {url}")


def _parse(raw: bytes, url: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        raise EveError(f"Expected JSON from {url}, got: {raw[:200]!r}") from None


def get_json(url: str) -> Any:
    return _parse(http("GET", url), url)


def post_json(url: str, body) -> Any:
    return _parse(http("POST", url, body), url)


def esi_get(path: str) -> Any:
    return get_json(ESI + path)


def esi_post(path: str, body) -> Any:
    return post_json(ESI + path, body)


def download(url: str, dest: Path, refresh: bool = False) -> Path:
    """Save a binary file (logo, render) unless it is already cached."""
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest
    raw = http("GET", url, accept="*/*")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------- cache files

def cache_root() -> Path:
    root = os.environ.get("EVE_SKILLS_CACHE_DIR")
    if root:
        return Path(root).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".cache") / "eve-skills"


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)  # mkstemp creates 0600; cache files are meant to be read by people too
    os.replace(tmp, path)


def save_json(path: Path, obj) -> Path:
    _atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    return Path(path)


def save_text(path: Path, text: str) -> Path:
    _atomic_write(path, text if text.endswith("\n") else text + "\n")
    return Path(path)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(doc: dict, sources: list[str]) -> dict:
    doc["_cache"] = {"fetched_at": now_iso(), "sources": sources}
    return doc


def is_fresh(doc, max_age_seconds: float) -> bool:
    try:
        fetched = parse_iso(doc["_cache"]["fetched_at"])
    except (TypeError, KeyError, ValueError):
        return False
    return (datetime.now(UTC) - fetched).total_seconds() < max_age_seconds


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or "unnamed"


def entity_dir(kind: str, entity_id, name: str | None = None) -> Path:
    """<cache>/<kind>/<id>-<slug>; reuses an existing directory for the same id."""
    base = cache_root() / kind
    existing = sorted(base.glob(f"{entity_id}-*")) if base.exists() else []
    if existing:
        return existing[0]
    return base / f"{entity_id}-{slugify(name or str(entity_id))}"


# ---------------------------------------------------------------- names and IDs

NAMES_FILE = "names.json"
IDS_FILE = "ids.json"
NEGATIVE_TTL = 24 * 3600  # re-ask ESI about unknown names after a day


def _names_path() -> Path:
    return cache_root() / NAMES_FILE


def remember_names(entries: dict) -> None:
    """Seed the shared id->name cache with names a source already gave us: {id: (name, category)}."""
    if not entries:
        return
    cache = load_json(_names_path(), {})
    changed = False
    for key, (name, category) in entries.items():
        if key and name and str(key) not in cache:
            cache[str(key)] = {"name": name, "category": category}
            changed = True
    if changed:
        save_json(_names_path(), cache)


def resolve_names(ids) -> dict[int, str]:
    """Map ESI IDs (characters, corps, alliances, types, systems, stations, factions) to names."""
    wanted = {int(i) for i in ids if i and int(i) > 0}
    cache = load_json(_names_path(), {})
    missing = sorted(i for i in wanted if str(i) not in cache)
    fetched = {}

    def fetch(batch: list[int]) -> None:
        try:
            for row in esi_post("/universe/names", batch):
                fetched[str(row["id"])] = {"name": row["name"], "category": row["category"]}
        except HttpError as err:
            if err.status != 404:
                raise
            # One invalid ID fails the whole batch; split until the bad one is isolated.
            if len(batch) > 1:
                fetch(batch[: len(batch) // 2])
                fetch(batch[len(batch) // 2:])

    for start in range(0, len(missing), 1000):
        fetch(missing[start:start + 1000])
    if fetched:
        cache = load_json(_names_path(), {})  # re-read: another skill may have written meanwhile
        cache.update(fetched)
        save_json(_names_path(), cache)
    return {i: cache[str(i)]["name"] for i in wanted if str(i) in cache}


def name_category(entity_id: int) -> str | None:
    resolve_names([entity_id])
    entry = load_json(_names_path(), {}).get(str(entity_id))
    return entry and entry["category"]


def lookup_ids(name: str) -> dict[str, list[dict]]:
    """Exact (case-insensitive) name -> {category: [{id, name}]} via ESI /universe/ids, cached."""
    key = name.strip().casefold()
    path = cache_root() / IDS_FILE
    cache = load_json(path, {})
    hit = cache.get(key)
    if hit and (hit["hits"] or is_fresh({"_cache": {"fetched_at": hit["t"]}}, NEGATIVE_TTL)):
        return hit["hits"]
    result = esi_post("/universe/ids", [name.strip()]) or {}
    hits = {cat: [r for r in rows if r["name"].casefold() == key] for cat, rows in result.items()}
    hits = {cat: rows for cat, rows in hits.items() if rows}
    cache = load_json(path, {})
    cache[key] = {"t": now_iso(), "hits": hits}
    save_json(path, cache)
    remember_names({r["id"]: (r["name"], cat[:-1] if cat.endswith("s") else cat)
                    for cat, rows in hits.items() for r in rows})
    return hits


ENTITY_KINDS = ("corporation", "alliance")
_URL_ID = re.compile(r"/(corporation|alliance)s?/(\d+)")


def resolve_entity(query: str, kind: str | None = None) -> list[dict]:
    """Corporation/alliance from an exact name, numeric ID or zKillboard/EveWho URL -> [{kind, id, name}]."""
    query = query.strip()
    url_match = _URL_ID.search(query)
    if url_match:
        found_kind, entity_id = url_match.group(1), int(url_match.group(2))
        return [{"kind": found_kind, "id": entity_id, "name": resolve_names([entity_id]).get(entity_id)}]
    if query.isdigit():
        entity_id = int(query)
        category = name_category(entity_id)
        if category not in ENTITY_KINDS:
            raise EveError(f"ID {entity_id} is a {category or 'unknown entity'}, not a corporation or alliance.")
        return [{"kind": category, "id": entity_id, "name": resolve_names([entity_id])[entity_id]}]
    hits = lookup_ids(query)
    matches = [{"kind": k, "id": r["id"], "name": r["name"]}
               for k in ENTITY_KINDS for r in hits.get(k + "s", [])
               if kind in (None, k)]
    if not matches:
        raise EveError(
            f"No {kind or 'corporation or alliance'} named '{query}'. ESI matches exact full names only "
            "(tickers need an authenticated search): pass the full name, the numeric ID, or a zKillboard URL."
        )
    return matches


# ---------------------------------------------------------------- formatting

def fmt_isk(value) -> str:
    value = float(value or 0)
    for size, suffix in ((1e12, "t"), (1e9, "b"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= size:
            return f"{value / size:.2f}{suffix}"
    return f"{value:.0f}"


def strip_html(text: str) -> str:
    """EVE descriptions use <font>, <a>, <br> and <loc> markup; keep text and link targets."""
    text = text or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r'<a\s+href="(https?://[^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def md_cell(value) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def md_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def ref(entity_id, names: dict) -> dict | None:
    """{"id", "name"} for an ID, or None when the ID is empty."""
    if not entity_id:
        return None
    return {"id": int(entity_id), "name": names.get(int(entity_id))}


def ref_text(r: dict | None) -> str:
    if not r:
        return "—"
    return f"{r['name'] or 'Unknown'} ({r['id']})"


def run(main) -> None:
    """Run a CLI entry point; print EveError as a one-line message with exit code 1."""
    try:
        sys.exit(main())
    except EveError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
