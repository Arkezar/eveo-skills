#!/usr/bin/env python3
"""Copy shared/eve_common.py into every skills/*/scripts/ dir (each skill installs standalone).

--check: exit 1 if any copy differs, without writing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "shared" / "eve_common.py"


def targets() -> list[Path]:
    return sorted(p / "eve_common.py" for p in ROOT.glob("skills/*/scripts") if p.is_dir())


def main() -> int:
    check = "--check" in sys.argv[1:]
    source = SOURCE.read_bytes()
    stale = [t for t in targets() if not t.exists() or t.read_bytes() != source]
    if check:
        for t in stale:
            print(f"out of sync: {t.relative_to(ROOT)}")
        return 1 if stale else 0
    for t in stale:
        t.write_bytes(source)
        print(f"synced {t.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
