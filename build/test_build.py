#!/usr/bin/env python3
"""One small check on catalog.json: schema, counts, no duplicate ids.

Run:  python build/test_build.py

No framework. Exits 1 and names the first failure, or prints per category counts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog.json"

REQUIRED = ("id", "name", "pitch", "category", "action", "url", "source", "first_seen")
CATEGORIES = ("testflight", "app", "devtool")
MIN_PER_CATEGORY = 100
CAP_PER_CATEGORY = 500

ACTION = re.compile(r"^(open https?://\S+|brew install [\w@.+-]+|npm i -g [\w@./-]+)$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The spec bans em dashes in output, and the agent prints these fields verbatim.
DASHES = re.compile(r"[‒–—―]")

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    if not CATALOG.exists():
        print("catalog.json is missing, run python build/build.py first")
        return 1

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    check(isinstance(entries, list) and bool(entries), "catalog has no entries")

    seen: dict[str, int] = {}
    for i, e in enumerate(entries):
        where = f"entry {i} ({e.get('id', 'no id')})"
        for field in REQUIRED:
            check(bool(e.get(field)), f"{where}: missing required field {field}")
        check(e.get("category") in CATEGORIES, f"{where}: bad category {e.get('category')!r}")
        check(isinstance(e.get("tags", []), list), f"{where}: tags must be a list")
        check(bool(ACTION.match(e.get("action", ""))), f"{where}: action not runnable: {e.get('action')!r}")
        check(bool(DATE.match(e.get("first_seen", ""))), f"{where}: bad first_seen {e.get('first_seen')!r}")
        check(str(e.get("url", "")).startswith("http"), f"{where}: url is not http")
        check(not DASHES.search(e.get("pitch", "")), f"{where}: pitch contains an em dash")
        check("\n" not in e.get("pitch", ""), f"{where}: pitch spans more than one line")

        ident = e.get("id", "")
        if ident in seen:
            failures.append(f"{where}: duplicate id, first seen at entry {seen[ident]}")
        else:
            seen[ident] = i

    counts = {c: sum(1 for e in entries if e.get("category") == c) for c in CATEGORIES}
    for category, n in counts.items():
        check(n >= MIN_PER_CATEGORY, f"category {category} has {n} entries, need at least {MIN_PER_CATEGORY}")
        check(n <= CAP_PER_CATEGORY, f"category {category} has {n} entries, cap is {CAP_PER_CATEGORY}")

    if failures:
        print(f"FAIL  {len(failures)} problem(s)")
        for line in failures[:20]:
            print("  " + line)
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")
        return 1

    print(f"PASS  {len(entries)} entries, {len(seen)} unique ids, generated {data.get('generated')}")
    for category, n in counts.items():
        print(f"  {category:<11} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
