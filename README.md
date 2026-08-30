# appmatch

An agent skill that reads your machine, confirms a taste profile with you, and hands you
five things you would actually want: TestFlight betas, new apps, dev tools.

No server. No account. No telemetry. Your agent does all the work on your machine, and the
only network call is a plain GET for a static catalog file.

## Install

Claude Code:

```
claude plugin marketplace add abhaymettu/appmatch
claude plugin install appmatch@appmatch
```

Any other agent, using the Vercel `skills` convention:

```
npx skills add abhaymettu/appmatch
```

Then run `/appmatch`.

## What it looks like

First run, it shows you what it read and waits:

```
Here is what I found on this machine. Strike anything wrong, add anything missing.

- You live in the terminal: brew, gh, fzf and neovim are all in daily use.
- You write Python and R, mostly analysis rather than application code.
- You keep 34 apps in /Applications and about half are developer tools.
- You have 3 TestFlight betas installed, all productivity.
- You care about local first software: Obsidian, Tailscale and Syncthing are installed.
- You have no music or photo tooling installed at all.

Strike anything wrong, add anything missing, or hit enter.
```

Then five matches, each ending in one command you can run right now:

```
1  Vane                        testflight
   Weather app that learns your comfort range.
   Why you: you have Carrot and Mercury installed and keep trying weather apps.
   → open https://testflight.apple.com/join/XXXX

2  atuin                       devtool
   Replacement for a shell history which records additional commands.
   Why you: you use fzf and zsh, and your history is 40k lines with no search.
   → brew install atuin

Run a number to open it, "more" for five new, or tell me what you want.
```

## Commands

| Command | What it does |
| --- | --- |
| `/appmatch` | Full flow. Skips the scan and confirm if a profile already exists. |
| `/appmatch more` | Five you have not been shown before. |
| `/appmatch rescan` | Redo the scan and the confirm screen, overwrite the profile. |
| `/appmatch something for screenshots` | Match free text, with your profile as context. |

## What it reads, and what it never reads

Reads, locally, once:

- `ls /Applications`
- `brew list --formula` and `brew list --cask`
- `npm ls -g --depth=0`
- `ls ~/.config`
- the top 40 command names from your shell history, names only, never arguments
- file extension counts under `~/code` and `~/Desktop`, two levels deep
- `ls ~/Library/Mobile Documents`

Never reads file contents, dotfile values, environment variables, keys, or browser data.
Never sends any of it anywhere. The confirm screen exists so you can see the whole profile
before anything is matched against it.

Everything it keeps lives in `~/.config/appmatch/`:

| File | What |
| --- | --- |
| `profile.md` | Your confirmed bullets. Plain markdown, edit it by hand. |
| `catalog.json` | Cached catalog, refetched when older than 24 hours. |
| `seen.txt` | Ids already shown, so `more` is always new. |
| `state.json` | Last drip timestamp. |

Delete the directory and appmatch forgets you completely.

## Drip, off by default

One unseen match at session start, at most once every 6 hours. To turn it on, put this in
the `## settings` block of `~/.config/appmatch/profile.md`:

```
drip: on
```

To turn it off, change it to `drip: off` or delete the line. It prints nothing when the
catalog is unreachable, and nothing when it has already fired inside the last 6 hours.

## The catalog

`catalog.json` is rebuilt nightly by a GitHub Action and committed to this repo. It is a
flat list of entries:

```json
{
  "id": "brew-atuin",
  "name": "atuin",
  "pitch": "Replacement for a shell history which records additional commands.",
  "category": "devtool",
  "tags": ["brew", "cli", "core"],
  "action": "brew install atuin",
  "url": "https://atuin.sh",
  "source": "homebrew",
  "first_seen": "2026-08-30"
}
```

Sources:

| Category | Where it comes from |
| --- | --- |
| `testflight` | `pluwen/awesome-testflight-link` for the links, plus each join page for the real description |
| `app` | Product Hunt Atom feeds, eight categories unioned |
| `devtool` | GitHub search for repos created in the last 30 days, GitHub trending, and the Homebrew formulae API ranked by install count |

Capped at 500 entries per category, newest first. `first_seen` is stable across rebuilds.
TestFlight descriptions are fetched once per app and cached in
`build/.cache/testflight.json`, which is committed so the nightly Action does not refetch
them.

Build it yourself:

```
python build/build.py
python build/test_build.py
```

Python 3.10 or newer, standard library only, no dependencies.

## License

MIT.
