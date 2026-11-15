#!/usr/bin/env python3
"""Check an EVE poster image prompt: length, section order, plain-text form, exact supplied texts.

Exit 1 on errors; warnings are printed but do not fail.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTIONS = ("FACTS TO PRESERVE", "COMIC METAPHOR", "REFERENCE ROLES",
            "COMPOSITION AND STYLE", "EXACT VISIBLE TEXT", "CONSTRAINTS")
QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|„([^”"\n]+)[”"]')


def section_positions(text: str) -> list[int | None]:
    positions = []
    for name in SECTIONS:
        pattern = rf"^[ \t]*{name}:?[ \t]*$"
        m = re.search(pattern, text, re.MULTILINE)
        positions.append(m.start() if m else None)
    return positions


def visible_texts(text: str) -> list[str]:
    """Quoted strings inside the EXACT VISIBLE TEXT section."""
    start = re.search(r"^[ \t]*EXACT VISIBLE TEXT:?[ \t]*$", text, re.MULTILINE)
    end = re.search(r"^[ \t]*CONSTRAINTS:?[ \t]*$", text, re.MULTILINE)
    if not start:
        return []
    section = text[start.end(): end.start() if end and end.start() > start.end() else len(text)]
    return [next(g for g in m.groups() if g) for m in QUOTED.finditer(section)]


def check(text: str, required: list[str], min_words: int = 450, max_words: int = 750,
          hard_max: int = 1000, *, required_visible: list[str] | None = None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    words = len(text.split())
    if words > hard_max:
        errors.append(f"{words} words; the hard limit is {hard_max}")
    elif not min_words <= words <= max_words:
        warnings.append(f"{words} words; aim for {min_words}-{max_words}")
    if "```" in text:
        errors.append("contains a Markdown code fence")
    if text.lstrip().startswith(("{", "[")):
        errors.append("looks like JSON; the prompt must be plain text")
    positions = section_positions(text)
    missing = [name for name, pos in zip(SECTIONS, positions, strict=True) if pos is None]
    if missing:
        errors.append("missing sections: " + ", ".join(missing))
    for name in SECTIONS:
        if len(re.findall(rf"^[ \t]*{name}:?[ \t]*$", text, re.MULTILINE)) > 1:
            errors.append(f"duplicate section: {name}")
    found = [p for p in positions if p is not None]
    if found != sorted(found):
        errors.append("sections out of order; expected: " + ", ".join(SECTIONS))
    if positions[0] is not None and text[:positions[0]].strip():
        errors.append("preamble before FACTS TO PRESERVE; return only the prompt")
    errors += [f"missing exact text: {r!r}" for r in required if r not in text]
    copy = visible_texts(text)
    errors += [f"missing exact visible text: {r!r}" for r in required_visible or [] if r not in copy]
    copy_words = sum(len(t.split()) for t in copy)
    if positions[4] is not None and not copy:
        warnings.append("EXACT VISIBLE TEXT quotes no strings; quote every visible text")
    elif not 12 <= copy_words <= 25:
        warnings.append(f"visible copy is {copy_words} words; target 12-25 for headline, supporting line and "
                        "punchline (more is fine only for a requested bubble, stamp, labels or footer)")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", type=Path, help="prompt text file")
    parser.add_argument("--require", action="append", default=[],
                        help="text that must appear verbatim anywhere (nickname, number…); repeatable")
    parser.add_argument("--require-visible", action="append", default=[],
                        help="complete quoted string required inside EXACT VISIBLE TEXT; repeatable")
    args = parser.parse_args()
    try:
        text = args.prompt.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as err:
        print(f"ERROR: cannot read prompt: {err}", file=sys.stderr)
        return 1
    errors, warnings = check(text, args.require, required_visible=args.require_visible)
    print(f"words: {len(text.split())}")
    for t in visible_texts(text):
        print(f"visible text: {t}")
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print("FAIL" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
