---
name: appmatch
description: Use when the user runs /appmatch or asks for app, TestFlight beta, or dev tool recommendations tailored to their machine. Profiles the machine locally, confirms the profile with the user, then presents five matches each ending in one runnable command. Also handles "appmatch more", "appmatch rescan", and free text like "appmatch something for screenshots".
---

# appmatch

You are the recommender. Nothing is hosted, no server sees any of this, and every fact
you use comes from this machine or the public catalog.

Work through the steps in order. Skip step 1 and step 2 if
`~/.config/appmatch/profile.md` already exists, unless the user said `rescan`.

## Argument handling

- `/appmatch` with no argument: full flow, steps 1 to 4.
- `/appmatch more`: step 3 and step 4 only, five entries not already in `seen.txt`.
- `/appmatch rescan`: redo steps 1 and 2, overwrite the profile, then 3 and 4.
- `/appmatch <free text>`: steps 3 and 4, matching the text against the catalog with the
  profile as context. The text wins where it disagrees with the profile.

## Step 1, scan

Read only the things in this list. Nothing else. Say so to the user before you start.

| What | How |
| --- | --- |
| Installed Mac apps | `ls /Applications` |
| Homebrew | `brew list --formula` and `brew list --cask` |
| Global npm | `npm ls -g --depth=0` |
| Config directories | `ls ~/.config` |
| Shell habits | top 40 command names from `~/.zsh_history` or `history`, command name only |
| Language mix | file extension counts under `~/code` and `~/Desktop`, two levels deep, skip `node_modules` |
| iOS apps in use | `ls ~/Library/Mobile\ Documents` |

For shell history, take the command name and throw the rest of the line away before it
reaches your context. A one liner that does this:

```
awk -F';' '{print $2}' ~/.zsh_history 2>/dev/null | awk '{print $1}' | sort | uniq -c | sort -rn | head -40
```

Any command that fails is fine, note it and move on. A machine without Homebrew is a
fact about the person, not an error.

**Never** read file contents, dotfile values, environment variables, keys, tokens, or
browser data. **Never** send any of this anywhere. The catalog fetch in step 3 is a plain
GET that carries none of it.

## Step 2, confirm

This step is the product. Do not rush it and do not skip it.

Print 6 to 8 bullets. Each one is a plain sentence about the person, not a list of tools.
Wrong: "Installed: brew, gh, fzf, neovim, ripgrep." Right: "You live in the terminal:
brew, gh, fzf and neovim are all in daily use."

Ground every bullet in something you actually found. Then ask, on its own line:

```
Strike anything wrong, add anything missing, or hit enter.
```

Apply whatever the user says. Then write `~/.config/appmatch/profile.md`:

```markdown
# appmatch profile

Written 2026-08-30. Edit this by hand any time.

- You live in the terminal: brew, gh, fzf and neovim are all in daily use.
- You write Python and R, mostly analysis rather than application code.
- ... the rest of the confirmed bullets ...

## wants

Anything you want more or less of. Free text, one per line.

## settings

drip: off
```

Create `~/.config/appmatch/` if it is missing. If a profile already exists and the user
did not say `rescan`, read it and go straight to step 3.

## Step 3, match

Fetch the catalog:

```
curl -fsSL -o ~/.config/appmatch/catalog.json \
  https://raw.githubusercontent.com/abhaymettu/appmatch/main/catalog.json
```

Cache it at `~/.config/appmatch/catalog.json`. Refetch only if the file is missing or
older than 24 hours. If the fetch fails and no cache exists, say so plainly in one line
and stop. Do not invent entries.

Each catalog entry has `id`, `name`, `pitch`, `category`, `tags`, `action`, `url`,
`source`, `first_seen`.

Rank against the profile. Pick 5, with at least one from each of `testflight`, `app` and
`devtool` unless the profile or the user's free text says otherwise. Prefer:

- an entry whose `tags` or `pitch` line up with a concrete thing on the machine
- an entry that is adjacent to something installed rather than a duplicate of it, since a
  user with three weather apps wants a fourth, but a user with `ripgrep` does not want
  `ripgrep`
- newer `first_seen` when two entries are otherwise equal

Skip any `id` already in `~/.config/appmatch/seen.txt`. After choosing, append the five
ids to that file, one per line, creating it if needed.

## Step 4, present

One block per match, exactly this shape and nothing else:

```
1  Vane                        testflight
   Weather app that learns your comfort range.
   Why you: you have Carrot and Mercury installed and keep trying weather apps.
   → open https://testflight.apple.com/join/XXXX
```

Line 1 is the index, the name, and the category, right aligned to roughly column 30.
Line 2 is the `pitch` from the catalog, unchanged.
Line 3 is `Why you:` and one sentence naming a concrete thing found on the machine.
Line 4 is the arrow and the `action`, verbatim, runnable as typed.

Then one line, exactly:

```
Run a number to open it, "more" for five new, or tell me what you want.
```

If the user answers with a number, run that entry's `action`. If the user says `more`,
run step 3 and step 4 again.

## Rules for what you write

- Never the phrase "based on your interests" or anything like it. Every `Why you` names a
  concrete thing found on the machine. If you cannot name one, that entry is not a match,
  pick another.
- No emoji anywhere. The arrow `→` is the only glyph.
- No em dashes.
- No spinners, no progress bars, no boxes, no tables in the match output. Plain text.
- Do not pad. Five matches, one closing line, done.
