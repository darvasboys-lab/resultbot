#!/usr/bin/env bash
# ResultBot Control Center: premium terminal skin upgrade only.
# Does NOT touch AMO database, Dhan orders, credentials or services.
set -euo pipefail
umask 077

DATA="$HOME/resultbot-data"
PYTHON="$HOME/uditbot/venv/bin/python"
TARGET="$DATA/control_center_v2.py"
LAUNCHER="$DATA/amo_settings.sh"
SOURCE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/73dbcced3753d1538b8d85ce061c1c094f38df95/resultbot/control_center_v2.py"

if [[ ! -x "$PYTHON" || ! -f "$DATA/equity_amo_live.py" ]]; then
    echo "ERROR: Existing ResultBot backend not found. No changes." >&2
    exit 1
fi
if [[ ! -f "$LAUNCHER" ]] || ! grep -q 'control_center_v2.py' "$LAUNCHER"; then
    echo "ERROR: Current bot setup does not point to V2. No changes." >&2
    echo "Run: type -a bot ; head -n 10 '$LAUNCHER'" >&2
    exit 1
fi
if [[ ! -f "$TARGET" || -L "$TARGET" ]]; then
    echo "ERROR: Expected existing V2 interface is not a regular file." >&2
    exit 1
fi

TMP="$(mktemp "$DATA/.premium-ui-XXXXXX.py")"
trap 'rm -f "$TMP"' EXIT
curl -fLsS --retry 2 "$SOURCE" -o "$TMP"
"$PYTHON" -m py_compile "$TMP"
grep -q "card(win, y, x, width" "$TMP" || { echo "ERROR: Wrong UI file." >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET.before-premium-$STAMP"
cp -p "$TARGET" "$BACKUP"
install -m 600 "$TMP" "$TARGET"
echo "SUCCESS: ResultBot premium card-based terminal design installed."
echo "Previous version: $BACKUP"
echo "Open: bot setup"
echo "Rollback: cp -p '$BACKUP' '$TARGET'"
echo "No saved settings, Dhan orders, tokens, or services changed."
