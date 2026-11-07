#!/usr/bin/env bash
# ResultBot strategy selector update. Does not change live order execution.
set -euo pipefail
umask 077

ROOT="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
UI="$ROOT/control_center_v2.py"
STRATEGY="$ROOT/position_strategy.py"
LAUNCH="$ROOT/amo_settings.sh"
PIN="9339684b633a0f7e2663d5144402eb81ac7a945c"
BASE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/$PIN/resultbot"

if [[ ! -x "$PY" || ! -f "$ROOT/equity_amo_live.py" ]]; then
    echo "ERROR: Existing ResultBot Equity AMO backend missing. No changes." >&2
    exit 1
fi
if [[ ! -f "$UI" || -L "$UI" ]]; then
    echo "ERROR: Existing premium Control Center not found. No changes." >&2
    exit 1
fi
if [[ ! -f "$LAUNCH" ]] || ! grep -q 'control_center_v2.py' "$LAUNCH"; then
    echo "ERROR: bot setup launcher is not linked to premium UI. No changes." >&2
    exit 1
fi
if [[ -L "$STRATEGY" ]]; then
    echo "ERROR: Refusing to replace symlinked strategy module." >&2
    exit 1
fi

TMP="$(mktemp -d "$ROOT/.strategy-install-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
curl -fLsS --retry 2 "$BASE/control_center_v2.py" -o "$TMP/control_center_v2.py"
curl -fLsS --retry 2 "$BASE/position_strategy.py" -o "$TMP/position_strategy.py"

"$PY" -m py_compile "$TMP/control_center_v2.py" "$TMP/position_strategy.py"
"$PY" - "$TMP" <<'CHECK'
import importlib.util, pathlib, sys, tempfile
folder=pathlib.Path(sys.argv[1])
def imported(name):
    spec=importlib.util.spec_from_file_location(name,folder/(name + ".py"))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ui=imported("control_center_v2")
ps=imported("position_strategy")
assert len(ui.MENU)==6
assert ui.MENU[2][1]=="STRATEGY PLAN"
assert ui.mouse_hit_test("CONTROL CENTER",24,80,10,15)==("item",5)
assert ui.mouse_hit_test("STRATEGY DOWN",32,100,18,15)==("item",0)
assert ui.mouse_hit_test("STRATEGY UP",32,100,18,17)==("item",1)
with tempfile.TemporaryDirectory() as scratch:
    ps.FILE=pathlib.Path(scratch)/"position_strategy.json"
    for down in ("CUT","AVERAGE","STOP_ONLY"):
        for up in ("TAKE_PROFIT","ADD_UP","TARGET_ONLY"):
            d=ps.choose_modes(down,up)
            assert d["down_mode"]==down and d["up_mode"]==up
            assert d["execution_enabled"] is False
            assert d["execution_status"]=="TEST_ONLY_NOT_CONNECTED_TO_DHAN"
print("PASS: menu mouse targets, nine strategy combinations, TEST-only persistence")
CHECK

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_UI="$UI.before-strategy-$STAMP"
cp -p "$UI" "$BACKUP_UI"
if [[ -f "$STRATEGY" ]]; then
    BACKUP_STRATEGY="$STRATEGY.before-strategy-$STAMP"
    cp -p "$STRATEGY" "$BACKUP_STRATEGY"
else
    BACKUP_STRATEGY=""
fi

install -m 600 "$TMP/position_strategy.py" "$STRATEGY"
install -m 600 "$TMP/control_center_v2.py" "$UI"

echo
echo "SUCCESS: Strategy Plan added to ResultBot Control Center."
echo "Previous UI backup: $BACKUP_UI"
if [[ -n "$BACKUP_STRATEGY" ]]; then
    echo "Previous strategy module backup: $BACKUP_STRATEGY"
fi
echo "Open: bot setup"
echo "Restore previous UI: cp -p '$BACKUP_UI' '$UI'"
echo "Existing position_strategy.json was not modified by the installer."
echo "WARNING: Staged position strategies are TEST ONLY. LIVE stays Equity AMO ENTRY ONLY."
echo "No broker requests, live signals, services, or existing orders changed."
