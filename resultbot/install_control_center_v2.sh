#!/usr/bin/env bash
# ResultBot Control Center V2: frontend-only installer. No broker/service operations.
set -euo pipefail
umask 077

D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
FRONTEND="$D/control_center_v2.py"
URL="https://raw.githubusercontent.com/darvasboys-lab/resultbot/fb332ea3051646019a9ffcc212ad529f5fbf1181/resultbot/control_center_v2.py"

if [[ ! -x "$PY" || ! -f "$D/equity_amo_live.py" ]]; then
  echo "ERROR: Expected ResultBot installation was not found." >&2
  exit 1
fi

"$PY" "$D/equity_amo_live.py" settings --show > /dev/null
"$PY" -c 'import curses' || { echo 'ERROR: Terminal curses unavailable.' >&2; exit 1; }

mkdir -p "$D"
TEMP="$(mktemp "$D/.control-center-XXXXXX.py")"
trap 'rm -f "$TEMP"' EXIT

echo "Downloading pinned Control Center V2 UI..."
curl --fail --location --silent --show-error --retry 2 "$URL" --output "$TEMP"
"$PY" -m py_compile "$TEMP"
grep -q 'RESULTBOT  /' "$TEMP" || { echo "ERROR: UI validation failed"; exit 1; }

echo "Locating the active Whiptail settings screen..."
TARGET="$("$PY" - <<'FIND'
from pathlib import Path
import os, sys
home = Path.home()
candidates=[]
skip = {'.git','venv','.venv','__pycache__','node_modules','backups','site-packages'}
for root in (home/'resultbot-data',home/'uditbot'):
    if not root.is_dir(): continue
    for base,dirs,files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip and 'backup' not in d.lower()]
        for name in files:
            if not name.endswith('.sh') or 'backup' in name.lower() or '.before-' in name:
                continue
            path=Path(base)/name
            if path.is_symlink(): continue
            try: text=path.read_text()
            except (OSError, UnicodeError): continue
            if 'Choose a setting below' in text and 'whiptail' in text:
                candidates.append(path)
if len(candidates) == 1:
    print(candidates[0])
elif not candidates:
    fallback = home/'resultbot-data'/'amo_settings.sh'
    if fallback.is_file() and not fallback.is_symlink():
        print(fallback)
    else:
        print('ERROR: Could not locate a safe settings launcher',file=sys.stderr)
        raise SystemExit(3)
else:
    print('ERROR: More than one possible menu launcher:',file=sys.stderr)
    for p in candidates: print(p,file=sys.stderr)
    raise SystemExit(4)
FIND
)" || exit 1

STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="$TARGET.before-control-center-v2-$STAMP"
cp -p "$TARGET" "$BACKUP"
if [[ -e "$FRONTEND" ]]; then
   cp -p "$FRONTEND" "$FRONTEND.before-control-center-v2-$STAMP"
fi

install -m 600 "$TEMP" "$FRONTEND"
LAUNCH_TMP="$(mktemp "$(dirname "$TARGET")/.rb-launcher-XXXXXX")"
trap 'rm -f "$TEMP" "$LAUNCH_TMP"' EXIT
cat > "$LAUNCH_TMP" <<'LAUNCH'
#!/usr/bin/env bash
# RESULTBOT_CONTROL_CENTER_V2_ACTIVE
set -euo pipefail
exec "$HOME/uditbot/venv/bin/python" "$HOME/resultbot-data/control_center_v2.py"
LAUNCH
bash -n "$LAUNCH_TMP"
install -m 700 "$LAUNCH_TMP" "$TARGET"
grep -q 'RESULTBOT_CONTROL_CENTER_V2_ACTIVE' "$TARGET"
echo
echo "SUCCESS: V2 dashboard installed."
echo "Settings script patched: $TARGET"
echo "Previous script preserved: $BACKUP"
echo "Run the UI directly (guaranteed):"
echo "  \"$PY\" \"$FRONTEND\""
echo "Then check your normal shortcut:"
echo "  bot setup"
echo
echo "To restore the previous settings screen:"
echo "  cp -p \"$BACKUP\" \"$TARGET\""
echo "No AMO switch, budget, source, order, listener, or broker API was changed."
