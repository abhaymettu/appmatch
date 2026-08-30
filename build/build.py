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
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog.json"
CACHE = Path(__file__).resolve().parent / ".cache"
# Committed, unlike the rest of .cache. The Action starts from a clean checkout and
# would otherwise refetch all 317 join pages every single night.
TF_CACHE = CACHE / "testflight.json"

UA = "appmatch-catalog-builder (+https://github.com/abhaymettu/appmatch)"
# testflight.apple.com serves the description only to something that looks like a
# browser. This is the one place a real User-Agent is needed.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CACHE_TTL = 6 * 3600
PER_CATEGORY_CAP = 500
CATEGORIES = ("testflight", "app", "devtool")
TODAY = date.today().isoformat()

TF_WORKERS = 4
TF_DELAY = 0.25
TF_PLACEHOLDER = "TestFlight beta for "
# The first p.step3 on every join page is TestFlight's own boilerplate, not the app.
TF_BOILERPLATE = "help developers test beta versions"
# A join link in the README can outlive the beta it points at. The page still
# returns 200, but says this, and its one command would lead nowhere.
TF_CLOSED = "isn't accepting any new testers"

GH_SEARCH_PAGES = 5
# Five pages is 500 repos, which would fill the whole devtool cap on its own and
# evict Homebrew, the highest signal source in the category. Newest goes first,
# but not at the price of the mix.
GH_SEARCH_CAP = 200
GH_SEARCH_DELAY = 7.0
GH_SEARCH_WINDOW_DAYS = 30

# Within one first_seen date, this decides who survives the per category cap.
SOURCE_PRIORITY = {"github-search": 0, "github-trending": 1, "homebrew": 2}

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


def get(url: str, browser: bool = False, timeout: int = 60) -> tuple[int, str]:
    """GET a URL, returning (status, body). Never raises for an HTTP error."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_UA if browser else UA,
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        return 0, ""


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


NETWORK_ERRORS = (
    urllib.error.URLError,
    # A chunked response can be cut off mid stream. This is not an OSError, so it
    # escaped the handler once and took the whole build down with it.
    http.client.HTTPException,
    TimeoutError,
    OSError,
)


def try_fetch(url: str, use_cache: bool = True, attempts: int = 3) -> str | None:
    for attempt in range(1, attempts + 1):
        try:
            return fetch(url, use_cache)
        except NETWORK_ERRORS as exc:
            if attempt == attempts:
                print(f"  warn: {url} failed after {attempts} tries: {exc}", file=sys.stderr)
                return None
            time.sleep(2 * attempt)
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


def first_sentence(text: str, limit: int = 140) -> str:
    """One sentence if there is a clean break early enough, otherwise a hard cut."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    for bad, good in DASHES.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ", text).strip()

    cut = re.search(r"(?<=[.!?])\s", text)
    if cut and 40 <= cut.start() + 1 <= limit:
        return text[: cut.start() + 1].strip()
    return clean(text, limit)


def clean_name(text: str, limit: int = 60) -> str:
    """Normalise a title. Names skip clean(), which is how em dashes got through.

    A spaced dash in a title separates the app from its tagline, and a colon says
    the same thing without the banned character. A dash inside a word is just a
    hyphen.
    """
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+[‒–—―]\s+", ": ", text, count=1)
    text = re.sub(r"[‒–—―]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip(" ,;:-")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


# --------------------------------------------------------------------------
# testflight


def parse_join_page(body: str) -> tuple[str | None, str | None, bool]:
    """Return (description, name, closed) scraped from a TestFlight join page."""
    blocks = re.findall(r'<p[^>]*class="[^"]*\bstep3\b[^"]*"[^>]*>(.*?)</p>', body, re.S)
    best = ""
    for raw in blocks:
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        if TF_BOILERPLATE in text.lower():
            continue
        if len(text) > len(best):
            best = text

    name = None
    og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', body)
    if og:
        m = re.match(r"^Join the (.+?) beta$", html.unescape(og.group(1)).strip(), re.I)
        if m:
            name = clean_name(m.group(1))

    status = re.search(r'<div class="beta-status">(.*?)</div>', body, re.S)
    status_text = html.unescape(re.sub(r"<[^>]+>", " ", status.group(1))) if status else ""
    closed = TF_CLOSED in status_text.lower()

    return (best or None), name, closed


def load_tf_cache() -> dict[str, dict]:
    if not TF_CACHE.exists():
        return {}
    try:
        return json.loads(TF_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def enrich_testflight(entries: list[dict]) -> dict[str, int]:
    """Replace placeholder pitches with the real description from each join page.

    One fetch per id, ever. The result is cached in a committed file keyed by id,
    so a nightly run only touches ids it has never seen plus anything marked for
    retry. Entries that fail keep the placeholder and are marked so the next run
    tries again.
    """
    cache = load_tf_cache()
    todo = [
        e for e in entries
        if cache.get(e["id"], {}).get("status") != "ok"
    ]
    stats = {"cached": len(entries) - len(todo), "fetched": 0, "failed": 0, "closed": 0}

    if todo:
        print(f"  {len(todo)} join pages to fetch, {stats['cached']} already cached")

    def work(entry: dict) -> tuple[str, dict]:
        time.sleep(TF_DELAY)
        status, body = get(entry["url"], browser=True, timeout=30)
        if status != 200 or not body:
            return entry["id"], {"status": "retry", "checked": TODAY, "http": status}
        desc, name, closed = parse_join_page(body)
        if closed:
            return entry["id"], {"status": "closed", "checked": TODAY, "http": status}
        if not desc:
            return entry["id"], {"status": "retry", "checked": TODAY, "http": status}
        record = {"status": "ok", "checked": TODAY, "pitch": first_sentence(desc)}
        if name:
            record["name"] = name
        return entry["id"], record

    if todo:
        with ThreadPoolExecutor(max_workers=TF_WORKERS) as pool:
            for ident, record in pool.map(work, todo):
                cache[ident] = record
                if record["status"] == "ok":
                    stats["fetched"] += 1
                elif record["status"] == "closed":
                    stats["closed"] += 1
                else:
                    stats["failed"] += 1

        CACHE.mkdir(exist_ok=True)
        TF_CACHE.write_text(
            json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    for entry in entries:
        record = cache.get(entry["id"], {})
        if record.get("status") == "ok":
            entry["pitch"] = record["pitch"]
            if record.get("name"):
                entry["name"] = clean_name(record["name"])
        elif record.get("status") == "closed":
            entry["status"] = "closed"
        else:
            entry["status"] = "retry"
    return stats


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
        name = clean_name(m.group("name"))
        code = m.group("link").rsplit("/", 1)[-1]
        if not name:
            continue
        out.append({
            "id": f"tf-{code}",
            "name": name,
            "pitch": f"{TF_PLACEHOLDER}{platform}, currently accepting testers.",
            "category": "testflight",
            "tags": ["testflight", platform, "beta"],
            "action": f"open {m.group('link')}",
            "url": m.group("link"),
            "source": "awesome-testflight-link",
            "first_seen": TODAY,
        })
    return out


# --------------------------------------------------------------------------
# apps


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
                "name": clean_name(title),
                "pitch": pitch,
                "category": "app",
                "tags": tags,
                "action": f"open {link}",
                "url": link,
                "source": "producthunt",
                "first_seen": TODAY,
            })
    return out


# --------------------------------------------------------------------------
# devtools


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


# A repo whose name matches a Homebrew formula is better installed than opened.
def source_github_search(use_cache: bool, brew_names: set[str]) -> list[dict]:
    """Repos created in the last 30 days with over 100 stars, most starred first.

    Unauthenticated search allows 10 requests a minute, so pages are spaced out.
    Trending alone yields 19 entries a day, which does not fill a category.
    """
    since = (date.today() - timedelta(days=GH_SEARCH_WINDOW_DAYS)).isoformat()
    out, seen = [], set()

    for page in range(1, GH_SEARCH_PAGES + 1):
        url = (
            "https://api.github.com/search/repositories"
            f"?q=created:>{since}+stars:>100&sort=stars&order=desc&per_page=100&page={page}"
        )
        body = try_fetch(url, use_cache)
        if body is None:
            break
        try:
            items = json.loads(body).get("items", [])
        except json.JSONDecodeError as exc:
            print(f"  warn: github search page {page} did not parse: {exc}", file=sys.stderr)
            break
        if not items:
            break

        for repo in items:
            full = repo.get("full_name") or ""
            name = repo.get("name") or ""
            if not full or full in seen:
                continue
            seen.add(full)

            description = (repo.get("description") or "").strip()
            # "New on GitHub, owner/repo" is not a pitch. A repo that cannot say
            # what it does in one line cannot be recommended in four.
            if not description:
                continue

            lang = repo.get("language") or ""
            # If Homebrew already ships it, the one command should install it.
            action = (
                f"brew install {name}" if name in brew_names
                else f"open {repo.get('html_url')}"
            )
            out.append({
                "id": f"gh-{slug(full)}",
                "name": name,
                "pitch": clean(description, 140),
                "category": "devtool",
                "tags": ["github", "new"] + ([slug(lang)] if lang else []),
                "action": action,
                "url": repo.get("html_url") or f"https://github.com/{full}",
                "source": "github-search",
                "first_seen": TODAY,
            })

        if page < GH_SEARCH_PAGES:
            time.sleep(GH_SEARCH_DELAY)

    return out[:GH_SEARCH_CAP]


def brew_formulae(use_cache: bool) -> list[dict]:
    body = try_fetch("https://formulae.brew.sh/api/formula.json", use_cache)
    if body is None:
        return []
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        print(f"  warn: brew formula.json did not parse: {exc}", file=sys.stderr)
        return []


def source_homebrew(use_cache: bool, formulae: list[dict], want: int) -> list[dict]:
    """Homebrew formulae, ranked by 365 day install count.

    formula.json carries no per formula date, so "recent" cannot be derived from
    it. Anything absent from the previous catalog is new, which on a cold build
    is everything. Popularity is the only defensible ordering for that first
    slice, otherwise the category is alphabetical noise.
    """
    if want <= 0 or not formulae:
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

    print("fetching testflight ...")
    testflight = source_testflight(use_cache)
    tf_stats = enrich_testflight(testflight)
    print(
        f"  descriptions: {tf_stats['cached']} cached, {tf_stats['fetched']} fetched, "
        f"{tf_stats['closed']} closed betas dropped, {tf_stats['failed']} will retry"
    )
    # An entry whose one command leads to "not accepting testers" is worse than
    # no entry, which is the same reason status F, N and D rows never get built.
    testflight = [e for e in testflight if e.get("status") != "closed"]
    # ... and keep the carry forward below from resurrecting one it dropped.
    closed_ids = {
        ident for ident, record in load_tf_cache().items()
        if record.get("status") == "closed"
    }

    print("fetching product hunt ...")
    apps = source_producthunt(use_cache)

    print("fetching homebrew ...")
    formulae = brew_formulae(use_cache)
    brew_names = {f["name"] for f in formulae}

    print("fetching github search ...")
    search = source_github_search(use_cache, brew_names)
    print(f"  {len(search)} repos created in the last {GH_SEARCH_WINDOW_DAYS} days")

    print("fetching github trending ...")
    trending = source_github_trending(use_cache)

    # devtool fills newest first: github search, then trending, then brew by rank.
    devtool_seen = {e["id"] for e in search} | {e["id"] for e in trending}
    room = PER_CATEGORY_CAP - len(devtool_seen)
    brew = [e for e in source_homebrew(use_cache, formulae, room) if e["id"] not in devtool_seen]

    entries = testflight + apps + search + trending + brew

    # Dedupe by id, first writer wins, and carry first_seen forward so an entry
    # that has been in the catalog for a month does not look new tonight.
    merged: dict[str, dict] = {}
    for entry in entries:
        if entry["id"] in merged:
            continue
        was = previous.get(entry["id"])
        if was and was.get("first_seen"):
            entry["first_seen"] = was["first_seen"]
        entry["_fresh"] = 1
        merged[entry["id"]] = entry

    # Keep anything the sources dropped this run, so the catalog does not shrink
    # when an upstream page has a bad night.
    for ident, entry in previous.items():
        if ident in closed_ids:
            continue
        entry.setdefault("_fresh", 0)
        merged.setdefault(ident, entry)

    kept: list[dict] = []
    for category in CATEGORIES:
        in_cat = [e for e in merged.values() if e.get("category") == category]
        in_cat.sort(
            key=lambda e: (
                e["first_seen"],
                e.get("_fresh", 0),
                -SOURCE_PRIORITY.get(e["source"], 9),
            ),
            reverse=True,
        )
        kept += in_cat[:PER_CATEGORY_CAP]

    kept.sort(key=lambda e: (e["category"], e["id"]))
    for entry in kept:
        entry.pop("_fresh", None)
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
        rows = [e for e in catalog["entries"] if e["category"] == category]
        print(f"  {category:<11} {len(rows)}")
        by_source: dict[str, int] = {}
        for e in rows:
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        for source, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
            print(f"    {source:<24} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
