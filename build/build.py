#!/usr/bin/env python3
"""Build catalog.json from public sources. Python stdlib only, no dependencies.

Run:  python build/build.py
      python build/build.py --no-cache     ignore the local fetch cache

Writes catalog.json at the repo root. Merges with the previous catalog so
first_seen stays stable across rebuilds.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog.json"
CACHE = Path(__file__).resolve().parent / ".cache"

UA = "appmatch-catalog-builder (+https://github.com/abhaymettu/appmatch)"
CACHE_TTL = 6 * 3600
PER_CATEGORY_CAP = 500
CATEGORIES = ("testflight", "app", "devtool")
TODAY = date.today().isoformat()

# Product Hunt serves 50 items per feed. Several category feeds overlap only
# partly, so the union clears the 100 per category bar on a cold build.
PH_FEEDS = [
    "",
    "tech",
    "productivity",
    "design-tools",
    "developer-tools",
    "health-fitness",
    "travel",
    "marketing-sales",
]


# --------------------------------------------------------------------------
# fetching


def fetch(url: str, use_cache: bool = True) -> str:
    """GET a URL as text, cached on disk so reruns are cheap."""
    CACHE.mkdir(exist_ok=True)
    key = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:120]
    path = CACHE / f"{key}.txt"
    if use_cache and path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL:
        return path.read_text(encoding="utf-8")

    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    path.write_text(body, encoding="utf-8")
    return body


def try_fetch(url: str, use_cache: bool = True) -> str | None:
    try:
        return fetch(url, use_cache)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  warn: {url} failed: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# text hygiene

# The spec bans em dashes in output, and the agent prints pitches verbatim,
# so upstream punctuation is normalised here rather than at display time.
DASHES = {"—": ",", "–": ",", "‒": ",", "―": ","}


def clean(text: str, limit: int = 110) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    for bad, good in DASHES.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ", text).strip(" ,;:")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "."
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return text


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


# --------------------------------------------------------------------------
# sources


def source_testflight(use_cache: bool) -> list[dict]:
    """pluwen/awesome-testflight-link README, markdown tables, status Y only."""
    url = "https://raw.githubusercontent.com/pluwen/awesome-testflight-link/main/README.md"
    md = try_fetch(url, use_cache)
    if md is None:
        return []

    platform = "ios"
    out = []
    row = re.compile(
        r"^\|\s*(?P<name>[^|]+?)\s*\|\s*\[(?P<link>https://testflight\.apple\.com/join/\w+)\]"
        r"\([^)]*\)\s*\|\s*(?P<status>[YFND])\s*\|"
    )
    for line in md.splitlines():
        heading = re.match(r"^##\s+(\w+)\s+App List", line)
        if heading:
            platform = heading.group(1).lower()
            continue
        m = row.match(line)
        if not m or m.group("status") != "Y":
            continue
        name = clean(m.group("name"), 60).rstrip(".")
        code = m.group("link").rsplit("/", 1)[-1]
        if not name:
            continue
        out.append({
            "id": f"tf-{code}",
            "name": name,
            "pitch": f"TestFlight beta for {platform}, currently accepting testers.",
            "category": "testflight",
            "tags": ["testflight", platform, "beta"],
            "action": f"open {m.group('link')}",
            "url": m.group("link"),
            "source": "awesome-testflight-link",
            "first_seen": TODAY,
        })
    return out


def source_producthunt(use_cache: bool) -> list[dict]:
    """Product Hunt Atom feeds. One feed is 50 items, so several are unioned."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out, seen = [], set()
    for cat in PH_FEEDS:
        url = "https://www.producthunt.com/feed" + (f"?category={cat}" if cat else "")
        body = try_fetch(url, use_cache)
        if body is None:
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            print(f"  warn: {url} is not valid Atom: {exc}", file=sys.stderr)
            continue

        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", "", ns) or "").strip()
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            if not title or not link:
                continue
            ident = f"ph-{slug(link.rsplit('/', 1)[-1] or title)}"
            if ident in seen:
                continue
            seen.add(ident)

            body_html = entry.findtext("a:content", "", ns) or ""
            first_para = re.search(r"<p>(.*?)</p>", body_html, re.S)
            pitch = clean(first_para.group(1) if first_para else title)
            tags = ["app", "new"] + ([cat] if cat else [])
            out.append({
                "id": ident,
                "name": clean(title, 60).rstrip("."),
                "pitch": pitch,
                "category": "app",
                "tags": tags,
                "action": f"open {link}",
                "url": link,
                "source": "producthunt",
                "first_seen": TODAY,
            })
    return out


def source_github_trending(use_cache: bool) -> list[dict]:
    """GitHub trending, daily, all languages. Scraped HTML, no API token needed."""
    body = try_fetch("https://github.com/trending?since=daily", use_cache)
    if body is None:
        return []

    out = []
    for article in re.findall(r'<article class="Box-row".*?</article>', body, re.S):
        head = re.search(r"<h2\b.*?</h2>", article, re.S)
        if not head:
            continue
        repo_match = re.search(r'href="/([^/"]+/[^/"]+)"', head.group(0))
        if not repo_match:
            continue
        repo = repo_match.group(1)
        desc_match = re.search(r'<p[^>]*class="col-9[^"]*"[^>]*>(.*?)</p>', article, re.S)
        lang_match = re.search(r'itemprop="programmingLanguage">\s*([^<]+?)\s*<', article)
        lang = lang_match.group(1).strip() if lang_match else ""

        out.append({
            "id": f"gh-{slug(repo)}",
            "name": repo.split("/")[-1],
            "pitch": clean(desc_match.group(1)) if desc_match else f"Trending on GitHub today, {repo}.",
            "category": "devtool",
            "tags": ["github", "trending"] + ([slug(lang)] if lang else []),
            "action": f"open https://github.com/{repo}",
            "url": f"https://github.com/{repo}",
            "source": "github-trending",
            "first_seen": TODAY,
        })
    return out


def source_homebrew(use_cache: bool, want: int) -> list[dict]:
    """Homebrew formulae, ranked by 365 day install count.

    formula.json carries no per formula date, so "recent" cannot be derived from
    it. Anything absent from the previous catalog is new, which on a cold build
    is everything. Popularity is the only defensible ordering for that first
    slice, otherwise the category is alphabetical noise.
    """
    formulae_body = try_fetch("https://formulae.brew.sh/api/formula.json", use_cache)
    if formulae_body is None:
        return []
    try:
        formulae = json.loads(formulae_body)
    except json.JSONDecodeError as exc:
        print(f"  warn: brew formula.json did not parse: {exc}", file=sys.stderr)
        return []

    rank: dict[str, int] = {}
    analytics_body = try_fetch(
        "https://formulae.brew.sh/api/analytics/install/365d.json", use_cache
    )
    if analytics_body:
        try:
            for item in json.loads(analytics_body).get("items", []):
                rank.setdefault(item["formula"].split()[0], item["number"])
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"  warn: brew analytics did not parse: {exc}", file=sys.stderr)

    usable = [
        f for f in formulae
        if f.get("desc") and not f.get("deprecated") and not f.get("disabled")
    ]
    usable.sort(key=lambda f: rank.get(f["name"], 10**9))

    out = []
    for f in usable[:want]:
        out.append({
            "id": f"brew-{slug(f['name'])}",
            "name": f["name"],
            "pitch": clean(f["desc"]),
            "category": "devtool",
            "tags": ["brew", "cli"] + ([slug(f["tap"].split("/")[-1])] if f.get("tap") else []),
            "action": f"brew install {f['name']}",
            "url": f.get("homepage") or f"https://formulae.brew.sh/formula/{f['name']}",
            "source": "homebrew",
            "first_seen": TODAY,
        })
    return out


# --------------------------------------------------------------------------
# assembly


def load_previous() -> dict[str, dict]:
    if not CATALOG.exists():
        return {}
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {e["id"]: e for e in data.get("entries", []) if "id" in e}


def build(use_cache: bool = True) -> dict:
    previous = load_previous()
    print(f"previous catalog: {len(previous)} entries")

    entries: list[dict] = []
    print("fetching testflight ...")
    entries += source_testflight(use_cache)
    print("fetching product hunt ...")
    entries += source_producthunt(use_cache)
    print("fetching github trending ...")
    trending = source_github_trending(use_cache)
    entries += trending
    print("fetching homebrew ...")
    entries += source_homebrew(use_cache, want=PER_CATEGORY_CAP - len(trending))

    # Dedupe by id, first writer wins, and carry first_seen forward so an entry
    # that has been in the catalog for a month does not look new tonight.
    merged: dict[str, dict] = {}
    for entry in entries:
        if entry["id"] in merged:
            continue
        was = previous.get(entry["id"])
        if was and was.get("first_seen"):
            entry["first_seen"] = was["first_seen"]
        merged[entry["id"]] = entry

    # Keep anything the sources dropped this run, so the catalog does not shrink
    # when an upstream page has a bad night.
    for ident, entry in previous.items():
        merged.setdefault(ident, entry)

    kept: list[dict] = []
    for category in CATEGORIES:
        in_cat = [e for e in merged.values() if e.get("category") == category]
        in_cat.sort(key=lambda e: (e["first_seen"], e["id"]), reverse=True)
        kept += in_cat[:PER_CATEGORY_CAP]

    kept.sort(key=lambda e: (e["category"], e["id"]))
    return {
        "generated": TODAY,
        "count": len(kept),
        "entries": kept,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true", help="ignore the local fetch cache")
    args = parser.parse_args()

    catalog = build(use_cache=not args.no_cache)
    CATALOG.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nwrote {CATALOG.relative_to(ROOT)}  {catalog['count']} entries")
    for category in CATEGORIES:
        n = sum(1 for e in catalog["entries"] if e["category"] == category)
        print(f"  {category:<11} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
