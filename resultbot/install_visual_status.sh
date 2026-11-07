#!/usr/bin/env bash
# ResultBot tiny visual polish: directional arrows + truthful bot/AMO status dots.
# UI only: no change to orders, configuration, balances or services.
set -euo pipefail
umask 077
DIR="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
TARGET="$DIR/control_center_v2.py"
SOURCE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/aad322e21ff5d2c56cec4a75b8daf9742fd84b4a/resultbot/control_center_v2.py"

if [[ ! -x "$PY" || ! -f "$TARGET" || -L "$TARGET" ]]; then
  echo "ERROR: Existing premium ResultBot Control Center not found. No changes." >&2
  exit 1
fi
if [[ ! -f "$DIR/amo_settings.sh" ]] ||
   ! grep -q 'control_center_v2.py' "$DIR/amo_settings.sh"; then
  echo "ERROR: bot setup launcher does not use this screen. No changes." >&2
  exit 1
fi
if [[ ! -f "$DIR/strategy_stage_wizard.py" ]]; then
  echo "ERROR: Guided strategy UI is not installed. No changes." >&2
  exit 1
fi

TMP="$(mktemp "$DIR/.status-ui-XXXXXX.py")"
trap 'rm -f "$TMP"' EXIT
curl -fLsS --retry 2 "$SOURCE" -o "$TMP"
"$PY" -m py_compile "$TMP"

"$PY" - "$TMP" "$DIR" <<'CHECK'
import sys, importlib.util
from pathlib import Path
source=Path(sys.argv[1])
sys.path.insert(0,sys.argv[2])
spec=importlib.util.spec_from_file_location("resultbot_status_ui",source)
ui=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
assert ui.amo_status_indicator(False)==("● AMO OFF",7)
assert ui.amo_status_indicator(True)==("● AMO ON",6)
assert ui.bot_status_indicator(True,True)==("BOT LIVE (AMO)",12)
assert ui.bot_status_indicator(False,True)==("BOT LISTENING",12)
assert ui.bot_status_indicator(True,False)==("BOT OFFLINE",13)
assert ui.mouse_hit_test("STRATEGY",32,100,18,15)==("item",0)
assert ui.mouse_hit_test("STRATEGY",32,100,18,17)==("item",1)
from unittest.mock import MagicMock,patch
screen=MagicMock()
screen.getmaxyx.return_value=(32,100)
with patch.object(ui,"draw_text") as drawing, patch.object(ui,"card") as cards, \
     patch.object(ui,"line"),patch.object(ui.curses,"color_pair",return_value=0):
    assert ui.render(screen,{"amo_enabled":False,"per_order_budget_inr":1000},
                     "CONTROL CENTER",[],0,0,listener_running=True)
assert any(x.args[2]=="BOT LISTENING" and x.args[3]==12
           for x in drawing.call_args_list)
assert any(x.args[2]=="💵" and x.args[3]==14
           for x in drawing.call_args_list)
assert any(x.args[3]=="TRADING AMO" and x.args[4]=="● AMO OFF"
           for x in cards.call_args_list)
print("PASS: bright-green BOT LISTENING, red AMO OFF, green cash icon and mouse rows")
CHECK

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET.before-visual-status-$STAMP"
cp -p "$TARGET" "$BACKUP"
install -m 600 "$TMP" "$TARGET"

echo "SUCCESS: Visual status patch installed. Run: bot setup"
echo "Backup: $BACKUP"
echo "Restore: cp -p '$BACKUP' '$TARGET'"
echo "Arrows: ▼ FALLING PRICE / ▲ RISING PRICE"
echo "Red dot: AMO OFF | Bright green: BOT LISTENING when listener active"
echo "Green cash icon: PER ORDER 💵 ₹1,000 (reflects your actual saved budget)"
echo "Green BOT LIVE (AMO) only when AMO is armed and listener active"
echo "No service restart, saved-setting modification or Dhan broker order."
