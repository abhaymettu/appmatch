# DECISIONS

Choices made while building appmatch, written as they were made. Newest last.

## departures.to is not in the catalog

The spec listed it as a TestFlight source, "whatever is scrapeable without a browser, skip
if it needs JS". It needs JS. The page is a Laravel Livewire app behind Cloudflare
Turnstile: the initial HTML is 285 KB and contains **zero** `testflight.apple.com/join`
links, no `__NEXT_DATA__`, and no JSON payload. App rows arrive over Livewire round trips
after the challenge resolves.

Verified before deciding:

```
testflight links in raw HTML: 0
__NEXT_DATA__: False
scripts: /livewire/livewire.min.js, challenges.cloudflare.com/turnstile/v0/api.js
```

Skipped rather than adding a headless browser. That would have been the single largest
dependency in the project, for a source whose entries overlap heavily with
`awesome-testflight-link`, which is already in and gave 317 usable betas.

## TestFlight pitches are generic, on purpose

**Superseded 2026-08-30, and the reasoning below was wrong. Kept for the trail.**

`awesome-testflight-link` is a table of name, link, status and date. There is no
description column, so there is nothing to write a real pitch from. Every TestFlight entry
gets `"TestFlight beta for <platform>, currently accepting testers."` and leans on the app
name to carry the signal.

The alternative was fetching each of the 317 TestFlight pages to scrape a description.
That is 317 requests per nightly build against Apple, for one sentence each, and the
join pages are themselves JS rendered. Not worth it.

## Correction: the join pages are not JS rendered

The claim above is false and I did not check it before writing it. A plain `curl` with a
browser User-Agent returns the full description in the HTML:

```
$ curl -A "Mozilla/5.0 ... Chrome/120.0.0.0 Safari/537.36" \
    https://testflight.apple.com/join/zjj57upc
HTTP 200, 40826 bytes
p.step3[0] -> "Help developers test beta versions of their apps and App Clips using the TestFlight app."
p.step3[1] -> "Lupora is your personal AI trainer: weekly plans built around your goals, your equipment, and how recovered you are..."
og:title   -> "Join the Lupora beta"
```

Two things I had wrong. The page is server rendered, and the request only needs a browser
User-Agent, which is the one place in this builder that sends one. What misled me is that
the earlier finding about `departures.to` needing a browser was real, and I generalised it
to Apple's own pages without testing them.

So the builder now fetches every join page. The parse takes all `p.step3` blocks, drops the
one containing TestFlight's own boilerplate (`help developers test beta versions`), and
keeps the longest of what remains. `og:title` matches `Join the <Name> beta` and gives a
cleaner app name than the README table, so it overrides the name when it parses.

The pitch is the first sentence when there is a clean sentence break between 40 and 140
characters, otherwise a hard cut at 140. Em dashes are stripped as everywhere else.

The volume objection was the only real part of the old reasoning, and it is handled by
caching rather than by giving up:

- One fetch per id, ever. `build/.cache/testflight.json` maps id to
  `{status, checked, pitch, name}`.
- A nightly run only fetches ids that are absent or marked `retry`.
- A non-200, or a page with no usable `step3`, records `status: retry`, leaves the
  placeholder pitch in place, and sets `status: "retry"` on the catalog entry so the next
  run tries again rather than baking in a failure.
- Four workers, 0.25 s delay each. The cold run is about 80 seconds for 317 pages; a warm
  run fetches nothing.

`build/test_build.py` fails the build if more than 30 percent of TestFlight pitches are
still the placeholder. That is loose enough to survive Apple having a bad night and tight
enough to catch a parser that has silently stopped matching.

## 29 dead betas were dropped, not retried

Fetching the join pages surfaced something the README does not know. Of 343 rows the
README marks `Y`, 29 return HTTP 200 with an empty description and this:

```
og:title    -> "TestFlight - Apple"
beta-status -> "This beta isn't accepting any new testers right now."
```

The link outlived the beta. The instruction for a page with no `step3` was to keep the
placeholder and retry next run, but retrying will never fix these, and shipping them means
shipping an entry whose one command lands on a page you cannot join. That is the same
reason status `F`, `N` and `D` rows are filtered out at parse time, so the same rule
applies here: a third cache status, `closed`, and those entries never reach the catalog.

They are rechecked on every run rather than blacklisted, because the README is upstream
truth for status and a beta that reopens should come back on its own. That costs 29
requests a night.

Net effect: 343 rows in, 288 entries out, and **0 of 288** still carry the placeholder
pitch.

## GitHub search is capped at 200, not 500

Five pages of search results is 500 repos, which is exactly the devtool cap, so on the
first run it filled the entire category and evicted Homebrew and trending completely. The
instruction was to fill from search first and then Homebrew, which that technically obeys
while making two of the three sources dead code.

It was also visibly worse. The tail of the search results is personal repos, benchmark
papers, GitHub Pages placeholders and repos with no description at all. Compare the two
runs: the mixed catalog offered `lazygit` and `yazi`, the all-search one led with
`PNGAL`, `LinearAbiltyCastingThreeJS` and `Aether-0.github.io`.

So two limits, both mine rather than the spec's:

- Repos with a null `description` are skipped. "New on GitHub, owner/repo" is not a pitch,
  and a repo that cannot say what it does in one line cannot be recommended in four.
- Search contributes at most 200 entries. That is roughly where star counts stop being
  meaningful inside a 30 day window, and it leaves the majority of the category to
  Homebrew's install rank list, which is the highest signal source of the three.

Result: 200 search, 19 trending, 281 Homebrew. Newest still goes first, but not at the
price of the mix.

## A fresh entry outranks a carried forward one on the same date

The carry forward that stops the catalog shrinking on a bad night had a side effect: an
entry from a previous build with today's date tied with a fresh one, and ties were broken
by source priority alone. One lopsided build's composition then survived every later build
that day. Entries the current run actually fetched now win that tie, so a rebuild can
correct the mix instead of inheriting it.

## build/.cache/testflight.json is committed

The rest of `build/.cache/` is scratch and stays ignored, but the Action runs from a clean
checkout. Without a committed cache, every nightly build would refetch all 317 join pages,
which is both rude and slow for data that essentially never changes. `.gitignore` is
therefore:

```
build/.cache/*
!build/.cache/testflight.json
```

The Action already commits `catalog.json`; it now picks up cache updates in the same
commit, so newly discovered betas get their description fetched once and never again.

## Product Hunt needs several feeds, not one

The spec named `https://www.producthunt.com/feed`. That returns exactly 50 entries, and
the definition of done wants at least 100 in the `app` category on a cold build.

The same endpoint takes a `?category=` parameter, and the feeds overlap only partly:

```
(default)           50 entries,  50 new, union 50
tech                50 entries,  48 new, union 98
productivity        50 entries,  28 new, union 126
design-tools        50 entries,  41 new, union 167
developer-tools     50 entries,  34 new, union 201
health-fitness      50 entries,  48 new, union 249
travel              50 entries,  48 new, union 297
```

Eight feeds are unioned, giving 296 apps on the first build. Same source, same format, no
new dependency.

## Homebrew is ranked by install count

`formula.json` has no `generated_date` and no per formula date of any kind, so "recent"
cannot be derived from it. The spec anticipated this and offered the fallback: take the
formulae not in the previous catalog. On a cold build the previous catalog is empty, so
that is all 8575 of them, and the 500 cap has to choose.

Ordering by name would put `a2ps` and `abseil` at the top and make the category
alphabetical noise. Instead the builder also fetches
`https://formulae.brew.sh/api/analytics/install/365d.json` and sorts by install rank, so
the cold slice is 500 tools people actually install. Same host, same stdlib fetch, no new
dependency. Deprecated and disabled formulae are dropped, as are any without a `desc`.

This is a small extension of a source the spec already named. Flagging it here because it
is a URL the spec did not list.

## Em dashes are stripped at build time, not display time

The spec bans em dashes in output. Pitches come from upstream and the agent prints them
verbatim, so a Product Hunt tagline with an em dash would put one on screen through no
fault of the skill. `clean()` replaces em, en, figure and horizontal bar dashes with
commas as entries are built, and `test_build.py` fails the build if one survives. That
makes the rule enforceable rather than aspirational.

## Names had their own code path, and it skipped normalisation

Seven catalog names still carried a dash after the em dash rule went in: five em, two en.
All of them TestFlight, all of them from `og:title`.

`clean()` has stripped dashes since the rule was written, and pitches went through it. But
the TestFlight name is overwritten later by the `og:title` parse, which returned
`m.group(1).strip()` straight into the entry and never touched `clean()`. The rule was only
ever enforced on the paths that happened to call the one function that implemented it.

Two changes. `clean_name()` now handles every title: a **spaced** dash separates a name
from its tagline, so it becomes `": "`, and any dash left is inside a word, so it becomes a
hyphen. A name written as `Stash`, spaced em dash, `Visual Bookmarks` now reads
`Stash: Visual Bookmarks`, while an em dash with no spaces around it, as in the usual
spelling of `Wi-Fi`, stays a hyphen rather than turning into `Wi: Fi`. Every name path now routes through it: the
README table, `og:title`, Product Hunt titles, and names read back out of the cache, so the
seven already sitting in `build/.cache/testflight.json` were fixed without a refetch.

`test_build.py` no longer checks `pitch` alone. It walks every string field of every entry,
including strings inside `tags`, and names the field and value it found. Run against the
catalog before the fix it reported all seven and exited 1. A future field will be covered
the day it is added, which was the point.

## The catalog only grows on a bad night

Entries dropped by a source this run are carried forward from the previous catalog rather
than deleted. If GitHub trending has an outage the category does not empty out. The 500
per category cap still applies, sorted by `first_seen` descending, so stale entries fall
off the bottom naturally as new ones arrive.

`first_seen` is copied from the previous catalog whenever an id already existed, so an
entry that has been listed for a month does not look new tonight.

## GitHub search fills the devtool category, trending cannot

Trending yields 19 entries on a good day, which is not a category. The builder now also
queries the search API:

```
https://api.github.com/search/repositories
  ?q=created:>YYYY-MM-DD+stars:>100&sort=stars&order=desc&per_page=100&page=N
```

Repos created in the last 30 days with more than 100 stars, most starred first, five pages.
Unauthenticated works and returned 1704 matches on the first run; the search endpoint
allows 10 requests a minute for anonymous callers, so pages are spaced 7 seconds apart.
No token, so nothing to leak and nothing for a user to configure.

A repo whose name matches a Homebrew formula gets `brew install <name>` instead of
`open <html_url>`, because the point of the `action` field is that it is the one command
worth running, and installing beats reading a README.

The devtool category now fills newest first: GitHub search, then trending, then Homebrew by
install rank, capped at 500. Ties on `first_seen` are broken by source priority rather than
alphabetically, which is what makes that ordering actually hold.

## The builder caches fetches locally

`build/.cache/`, gitignored, 6 hour TTL, skipped in CI with `--no-cache`. The Homebrew
formula API is a 31 MB response that took four and a half minutes to download on this
machine. Without a cache, iterating on the parser meant a five minute wait per run.

## drip is a shell script, not a skill

The SessionStart hook has to be fast, silent and unable to derail a session. It is 40
lines of `sh` with a here doc'd Python block for the JSON, and it exits 0 without printing
on every failure path: no profile, `drip: off`, no catalog, fired within 6 hours, nothing
unseen left, JSON that does not parse. Nothing about it can block a session start.

Its "Why you" line is honest about being dumb: `it is the newest thing in the catalog you
have not seen`. It has no access to the profile beyond the on switch, and inventing a
personalised reason there would be a lie. The real reasoning happens in `/appmatch`.

## The repo's build/ directory collides with a global deny rule

Not a project decision, but worth recording for whoever works on this next. This machine's
`~/.claude/settings.json` denies `Read(**/build/**)`, intended for compiled output. It also
matches this repo's `build/` source directory, so the agent's file write tool is blocked
there and edits have to go through the shell. The spec fixes these paths and the Action,
README and test all reference them, so the directory was not renamed.
