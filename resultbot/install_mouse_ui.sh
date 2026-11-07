#!/usr/bin/env bash
# ResultBot Premium Control Center mouse upgrade. UI ONLY; never touches trading settings.
set -euo pipefail
umask 077

D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
TARGET="$D/control_center_v2.py"
LAUNCH="$D/amo_settings.sh"
SOURCE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/b283ff1d289b39e61ebddb6060b6ba9d2e05a98b/resultbot/control_center_v2.py"

if [[ ! -x "$PY" || ! -f "$D/equity_amo_live.py" ]]; then
  echo "ERROR: ResultBot backend not found; nothing changed." >&2
  exit 1
fi
if [[ ! -f "$TARGET" || -L "$TARGET" ]]; then
  echo "ERROR: Premium UI file missing or symlinked; nothing changed." >&2
  exit 1
fi
if [[ ! -f "$LAUNCH" ]] || ! grep -q "control_center_v2.py" "$LAUNCH"; then
  echo "ERROR: bot setup launcher does not reference the V2 screen." >&2
  echo "Nothing changed. Check: type -a bot" >&2
  exit 1
fi

TMP="$(mktemp "$D/.mouse-ui-XXXXXX.py")"
trap 'rm -f "$TMP"' EXIT
curl --fail --location --silent --show-error --retry 2 "$SOURCE" -o "$TMP"
"$PY" -m py_compile "$TMP"

"$PY" - "$TMP" <<'VERIFY'
import importlib.util, sys
spec=importlib.util.spec_from_file_location("resultbot_mouse_ui",sys.argv[1])
ui=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
assert ui.mouse_hit_test("CONTROL CENTER",32,100,10,15)==("item",0)
assert ui.mouse_hit_test("CONTROL CENTER",24,80,10,10)==("item",0)
assert ui.mouse_hit_test("ORDER BUDGET",24,80,12,12)==("input",None)
assert ui.mouse_hit_test("ALERT SOURCE",24,80,61,22)==("ok",None)
assert ui.mouse_hit_test("ALERT SOURCE",24,80,70,22)==("cancel",None)
print("PASS: Mouse menu, compact layout, text field, OK and Cancel")
VERIFY

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET.before-mouse-$STAMP"
cp -p "$TARGET" "$BACKUP"
install -m 600 "$TMP" "$TARGET"

echo
echo "SUCCESS: Premium ResultBot mouse-click UI installed."
echo "Backup: $BACKUP"
echo "Open: bot setup"
echo "Restore: cp -p '$BACKUP' '$TARGET'"
echo "Mouse: click rows/cards/OK/Cancel. Wheel scrolls orders."
echo "Keyboard still works. Terminal must forward mouse events."
echo "No settings, live orders or services changed."
