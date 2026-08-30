# appmatch: design spec

Date: 2026-08-30. Owner: Abhay Mettu (github: abhaymettu). Status: approved for build.

## One line

An agent skill that reads your machine, confirms a taste profile with you, and hands you
five things you would actually want: TestFlight betas, new apps, dev tools. No server.
The user's own agent does all the work.

## Why no server

Abhay does not want to host anything. Every existing TestFlight directory (Departures,
Findbeta, BetaHub, awesome-testflight-link) is a static list with no personalization, and
Apple has already built and pulled a "Tester Matching" feature (TestFlight 4.0, Oct 2025,
removed in 4.0.1). The defensible piece is personalization that runs on the user's side.
A static catalog on GitHub plus the user's agent is that.

## Organizing idea

The agent is the recommender. The skill teaches it three things: how to profile the
machine honestly, how to read the catalog, and how to present matches so each one ends in
a single command the user can run right now.

## Signature move

**The confirm screen.** Before any matching, the agent prints what it inferred about you
as 6 to 8 plain bullets ("You live in the terminal: brew, gh, fzf, neovim." "You write
Python and R, mostly analysis." "You have 3 TestFlight apps installed, all productivity.")
and asks you to strike or add. That moment is the product. It is where "holy shit it knows
me" happens, and it is also the privacy gate: the user sees exactly what was read.

## Components

### 1. Catalog builder (`build/`)

One script, Python stdlib plus `requests` only if stdlib urllib is genuinely painful.
Runs in a GitHub Action nightly (`.github/workflows/catalog.yml`), commits `catalog.json`.

Sources, v1:
- TestFlight: `pluwen/awesome-testflight-link` README (parse the markdown tables) and
  `departures.to` (whatever is scrapeable without a browser; skip if it needs JS).
- New apps: Product Hunt RSS (`https://www.producthunt.com/feed`).
- Dev tools: GitHub trending (scrape the HTML for daily, all languages) and Homebrew new
  formulae (`https://formulae.brew.sh/api/formula.json`, filter by recent `generated_date`
  or just take those not in the previous catalog).

Entry schema, all fields required except `tags`:

```json
{
  "id": "stable-slug",
  "name": "Vane",
  "pitch": "Weather app that learns your comfort range.",
  "category": "testflight | app | devtool",
  "tags": ["weather", "ios"],
  "action": "open https://testflight.apple.com/join/XXXX",
  "url": "https://departures.to/vane",
  "source": "departures",
  "first_seen": "2026-08-30"
}
```

`action` is the one command the user runs: `open <testflight link>`, `brew install x`,
`npm i -g x`, `open <product hunt url>`. Cap the catalog at the most recent 500 entries
per category. Keep `first_seen` stable across rebuilds (merge with the previous file).

Definition of done: `python build/build.py` produces `catalog.json` with at least 100
entries in each of the three categories, validated against the schema, no duplicates by
`id`.

### 2. Skill (`skills/appmatch/SKILL.md`)

Installable two ways, both documented in README:
- Claude Code: repo is a marketplace. `.claude-plugin/marketplace.json` and
  `.claude-plugin/plugin.json` at the right paths so
  `claude plugin marketplace add abhaymettu/appmatch && claude plugin install appmatch@appmatch`
  works. Verify with `claude plugin validate` or whatever the CLI offers.
- Any agent: `npx skills add abhaymettu/appmatch` (Vercel `skills` CLI convention:
  `skills/<name>/SKILL.md`).

The skill body, in order:

**Step 1, scan.** Read only these, nothing else, and say so in the skill:
`ls /Applications`, `brew list --formula` and `--cask`, `npm ls -g --depth=0`,
`ls ~/.config`, top 40 command names from shell history (`history` or
`~/.zsh_history`, command name only, never arguments), language mix from
`~/code` and `~/Desktop` (count file extensions, two levels deep, skip node_modules),
`ls ~/Library/Mobile\ Documents` (iCloud app containers reveal iOS apps in use).
Never read file contents, dotfile values, env vars, or keys. Never send any of it anywhere.

**Step 2, confirm.** Print 6 to 8 bullets, each a plain sentence about the person, not a
list of tools. Ask: "Strike anything wrong, add anything missing, or hit enter." Apply
edits. Save to `~/.config/appmatch/profile.md` as those bullets plus a short "wants"
section the user can edit by hand later.

**Step 3, match.** Fetch
`https://raw.githubusercontent.com/abhaymettu/appmatch/main/catalog.json` (curl, cache to
`~/.config/appmatch/catalog.json`, refetch if older than 24h). Rank against the profile.
Pick 5: at least one from each category unless the profile says otherwise. Append the ids
shown to `~/.config/appmatch/seen.txt`; never show a seen id again unless asked.

**Step 4, present.** One block per match, exactly this shape, nothing else:

```
1  Vane                        testflight
   Weather app that learns your comfort range.
   Why you: you have Carrot and Mercury installed and keep trying weather apps.
   → open https://testflight.apple.com/join/XXXX
```

Then one line: `Run a number to open it, "more" for five new, or tell me what you want.`

**Commands.** `/appmatch` runs the flow (skips scan and confirm if profile exists).
`/appmatch more` gives five unseen. `/appmatch rescan` redoes steps 1 and 2.
`/appmatch <free text>` matches the text against the catalog with the profile as context.

### 3. Drip (optional, off by default)

A SessionStart hook in the plugin (`hooks/hooks.json`) that, when enabled in
`~/.config/appmatch/profile.md` with `drip: on`, prints exactly one unseen match in the
four-line format above at session start, at most once per 6 hours (store last-drip
timestamp in `~/.config/appmatch/state.json`). Nothing when the catalog is unreachable.
Document how to turn it on in README. Ship it, but do not make it the default.

## Banned

- No TUI framework, no ink, no rich, no spinners. The agent renders text.
- No accounts, no telemetry, no analytics, no "share your profile" feature.
- No em dashes anywhere in output or docs.
- No emoji in the match block. The arrow `→` is the only glyph.
- No "based on your interests" phrasing. Every "Why you" names a concrete thing found
  on the machine.
- No new runtime dependency for the builder beyond Python stdlib unless a source
  genuinely cannot be fetched without one; if so, name it in DECISIONS.md.

## Repo layout

```
appmatch/
  README.md              install line first, then a real example of the output
  DECISIONS.md           choices and why, written as they are made
  catalog.json           generated, committed by the Action
  build/build.py
  build/test_build.py    one small check: schema, counts, no dup ids
  .github/workflows/catalog.yml
  .claude-plugin/marketplace.json
  .claude-plugin/plugin.json
  skills/appmatch/SKILL.md
  hooks/hooks.json
  hooks/drip.sh
  docs/superpowers/specs/2026-08-30-appmatch-design.md   this file
```

## Verification the lane must run and paste

1. `python build/build.py` then `python build/test_build.py`: pass, with per-category counts.
2. `claude plugin validate .` (or the closest equivalent) passes.
3. Install locally from path, open a fresh `claude` session in `~/scratch`, run
   `/appmatch`, paste the confirm screen and the five matches verbatim.
4. `git status --short` clean, no screenshots, no scratch dirs.

## Not in v1

Pushing to GitHub (Abhay approves the public repo first), a website, ratings, feedback
loops, Android, Windows. Do not build any of these.
