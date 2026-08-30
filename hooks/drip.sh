#!/bin/sh
# appmatch drip: one unseen match at session start, at most once every 6 hours.
#
# Off unless ~/.config/appmatch/profile.md contains a line "drip: on".
# Silent on every failure. A discovery toy must never be the reason a session
# start is noisy or slow.

set -u

CONF="${HOME}/.config/appmatch"
PROFILE="${CONF}/profile.md"
CATALOG="${CONF}/catalog.json"
SEEN="${CONF}/seen.txt"
STATE="${CONF}/state.json"
INTERVAL=21600

grep -qiE '^[[:space:]]*drip:[[:space:]]*on[[:space:]]*$' "$PROFILE" 2>/dev/null || exit 0
[ -r "$CATALOG" ] || exit 0

now=$(date +%s)
last=0
if [ -r "$STATE" ]; then
  last=$(sed -n 's/.*"last_drip"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' "$STATE" 2>/dev/null)
  [ -n "$last" ] || last=0
fi
[ "$((now - last))" -ge "$INTERVAL" ] || exit 0

pick=$(CATALOG="$CATALOG" SEEN="$SEEN" python3 - <<'PY' 2>/dev/null
import json, os, sys

catalog = os.environ["CATALOG"]
seen_path = os.environ["SEEN"]
try:
    entries = json.load(open(catalog, encoding="utf-8")).get("entries", [])
except Exception:
    sys.exit(1)

seen = set()
try:
    seen = {line.strip() for line in open(seen_path, encoding="utf-8") if line.strip()}
except OSError:
    pass

fresh = [e for e in entries if e.get("id") not in seen]
if not fresh:
    sys.exit(1)
fresh.sort(key=lambda e: (e.get("first_seen", ""), e.get("id", "")), reverse=True)
e = fresh[0]
print(e["id"])
print(f"1  {e['name'][:26]:<26}  {e['category']}")
print(f"   {e['pitch']}")
print(f"   Why you: it is the newest thing in the catalog you have not seen.")
print(f"   → {e['action']}")
PY
) || exit 0

[ -n "$pick" ] || exit 0

mkdir -p "$CONF" 2>/dev/null || exit 0
printf '%s\n' "$pick" | sed -n '1p' >> "$SEEN"
printf '{"last_drip": %s}\n' "$now" > "$STATE"
printf '%s\n' "$pick" | sed -n '2,$p'
exit 0
