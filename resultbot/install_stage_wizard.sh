#!/usr/bin/env bash
# Guided multi-stage Strategy Plan editor. UI + TEST-only configuration ONLY.
set -euo pipefail
umask 077
D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
LAUNCH="$D/amo_settings.sh"
PIN="f56e1bc3f13d7a928e45bb135342d1a246074c05"
BASE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/$PIN/resultbot"

if [[ ! -x "$PY" || ! -f "$D/equity_amo_live.py" || ! -f "$D/control_center_v2.py" ]]; then
    echo "ERROR: Existing ResultBot premium UI and Equity AMO required." >&2
    exit 1
fi
if [[ ! -f "$LAUNCH" ]] || ! grep -q 'control_center_v2.py' "$LAUNCH"; then
    echo "ERROR: bot setup is not pointing to premium UI." >&2
    exit 1
fi
for name in control_center_v2.py position_strategy.py strategy_stage_wizard.py; do
    if [[ -L "$D/$name" ]]; then
        echo "ERROR: Refusing symlinked component $name" >&2
        exit 1
    fi
done

TMP="$(mktemp -d "$D/.stage-wizard-download-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
echo "===== DOWNLOAD PINNED TEST-ONLY STRATEGY UI ====="
for name in control_center_v2.py position_strategy.py strategy_stage_wizard.py; do
    curl -fLsS --retry 2 "$BASE/$name" -o "$TMP/$name"
done
"$PY" -m py_compile "$TMP/control_center_v2.py" "$TMP/position_strategy.py" "$TMP/strategy_stage_wizard.py"

echo "===== VALIDATE COMPLETE WIZARD OFFLINE ====="
"$PY" - "$TMP" <<'CHECK'
import importlib.util,sys,tempfile
from pathlib import Path
folder=Path(sys.argv[1])
sys.path.insert(0,str(folder))
def load(name):
    spec=importlib.util.spec_from_file_location(name,folder/(name+".py"))
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

flow=load("strategy_stage_wizard")
ps=load("position_strategy")
ui=load("control_center_v2")
assert "STRATEGY PLAN" in str(ui.MENU)
assert ui.mouse_hit_test("STRATEGY DOWN",32,100,18,15)==("item",0)
assert ui.mouse_hit_test("CONTROL CENTER",24,80,12,15)==("item",5)
assert ui.mouse_hit_test("STRATEGY WIZARD",32,100,12,19)==("input",None)
with tempfile.TemporaryDirectory() as temp:
    ps.FILE=Path(temp)/"plan.json"
    plan=ps.selection_json()
    w=flow.begin(plan,"down","AVERAGE")
    for answer in (2,3,1000,6,1500,9):
        complete=flow.advance(w,str(answer))
    assert complete
    assert not ps.FILE.exists(), "Wizard wrote before confirmation"
    saved=ps.apply_side_configuration(flow.request(w))
    assert saved["average_down"]==[3,6]
    assert saved["average_down_amounts_inr"]==[1000,1500]
    assert saved["execution_enabled"] is False
    w=flow.begin(saved,"up","TAKE_PROFIT")
    for answer in (2,2,20,4,30,6):
        complete=flow.advance(w,str(answer))
    assert complete
    result=ps.apply_side_configuration(flow.request(w))
    assert result["take_profit"]==[[2,20],[4,50]]
    assert result["execution_status"]=="TEST_ONLY_NOT_CONNECTED_TO_DHAN"
print("PASS: wizard, mouse, atomic save, sale percentages, TEST-only state")
CHECK

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$D/stage-wizard-backup-$STAMP"
mkdir -m 700 "$BACKUP"
for name in control_center_v2.py position_strategy.py strategy_stage_wizard.py; do
    if [[ -f "$D/$name" ]]; then
        cp -p "$D/$name" "$BACKUP/$name"
    fi
done

completed=0
restore_on_failure() {
    if [[ "$completed" != 1 ]]; then
        echo "ERROR: Interrupted; restoring prior UI files." >&2
        for name in control_center_v2.py position_strategy.py strategy_stage_wizard.py; do
            if [[ -f "$BACKUP/$name" ]]; then
                cp -p "$BACKUP/$name" "$D/$name"
            else
                rm -f "$D/$name"
            fi
        done
    fi
}
trap 'restore_on_failure; rm -rf "$TMP"' EXIT

echo "===== INSTALL FRONTEND AND TEST-ONLY PLAN EDITOR ====="
for name in control_center_v2.py position_strategy.py strategy_stage_wizard.py; do
    install -m 600 "$TMP/$name" "$D/$name"
done
completed=1
echo "SUCCESS: Guided ResultBot strategy setup installed."
echo "Backup: $BACKUP"
echo "Open: bot setup"
echo "Choose Strategy Plan > Falling/Rising Price > Mode > Stages > Review > Save"
echo "Existing position_strategy.json not changed by this installer."
echo "LIVE Equity AMO stays ENTRY-ONLY. No Dhan orders or services changed."
