#!/usr/bin/env bash
# ResultBot OCO: settings and broker-executed-price PREVIEW only.
set -euo pipefail
umask 077
D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
PIN="6d755daef1dad26aaee6eca7c372ad9f53df32f2"
BASE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/$PIN/resultbot"
FILES=(control_center_v2.py position_strategy.py oco_price_plan.py oco_preview.py)
[[ -x "$PY" && -f "$D/strategy_stage_wizard.py" && -f "$D/equity_amo_live.py" && -f "$D/control_center_v2.py" ]] || { echo "Existing premium strategy wizard required." >&2; exit 1; }
[[ -f "$D/amo_settings.sh" ]] && grep -q control_center_v2.py "$D/amo_settings.sh" || { echo "bot setup does not use premium UI." >&2; exit 1; }
for f in "${FILES[@]}"; do [[ ! -L "$D/$f" ]] || { echo "Refusing symlinked component $f" >&2; exit 1; }; done
TMP="$(mktemp -d "$D/.oco-patch-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
for f in "${FILES[@]}"; do curl -fLsS --retry 2 "$BASE/$f" -o "$TMP/$f"; done
"$PY" -m py_compile "$TMP/control_center_v2.py" "$TMP/position_strategy.py" "$TMP/oco_price_plan.py" "$TMP/oco_preview.py"
"$PY" - "$TMP" "$D" <<'TEST'
import sys, importlib.util, tempfile
from pathlib import Path
tmp=Path(sys.argv[1]);sys.path[:0]=[str(tmp),sys.argv[2]]
def get(n):
    spec=importlib.util.spec_from_file_location(n,tmp/(n+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
oco=get('oco_price_plan');ps=get('position_strategy');ui=get('control_center_v2')
assert ui.mouse_hit_test('STRATEGY',32,100,18,21)==('item',3)
assert ui.mouse_hit_test('OCO EDIT',32,100,12,19)==('input',None)
intent=dict(account='DEMO',order_id='123456789',correlation='OWN123',security_id='111',qty=5)
order=dict(dhanClientId='DEMO',orderId='123456789',correlationId='OWN123',securityId='111',transactionType='BUY',exchangeSegment='NSE_EQ',productType='CNC',afterMarketOrder=True,orderStatus='TRADED',quantity=5,filledQty=5,averageTradedPrice='102.40',price='200')
def plan(order):
    return oco.build_oco_price_plan(intent=intent,order=order,stop_loss_pct=3,target_pct=6,tick_size='0.05')
p=plan(order)
assert (p['stop_loss_trigger'],p['stop_loss_sell_limit'],p['target_sell_limit'])==('99.30','99.25','108.55')
assert p['confirmed_execution_vwap']=='102.40' and p['broker_oco_created']==False
for changes in ({'orderStatus':'PENDING'},{'averageTradedPrice':0},{'filledQty':4},{'dhanClientId':'WRONG'}):
    try: plan({**order,**changes});raise AssertionError('Unsafe OCO plan accepted')
    except oco.OcoPlanError:pass
with tempfile.TemporaryDirectory() as t:
    ps.FILE=Path(t)/'plan.json'
    d=ps.set_oco_percentages(9,6)
    assert d['stop_pct']==9 and d['target_pct']==6 and not d['execution_enabled']
print('PASS: ownership, executed VWAP, rounding, full fills, no broker placement')
TEST
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$D/oco-settings-backup-$STAMP"
mkdir -m 700 "$BACKUP"
for f in "${FILES[@]}"; do [[ ! -f "$D/$f" ]] || cp -p "$D/$f" "$BACKUP/$f"; done
completed=0
restore() { if [[ "$completed" != 1 ]]; then for f in "${FILES[@]}"; do if [[ -f "$BACKUP/$f" ]]; then cp -p "$BACKUP/$f" "$D/$f"; else rm -f "$D/$f"; fi; done; fi; }
trap 'restore; rm -rf "$TMP"' EXIT
for f in "${FILES[@]}"; do install -m 600 "$TMP/$f" "$D/$f"; done
completed=1
echo "SUCCESS: OCO settings and executed-price preview installed."
echo "Backup: $BACKUP"
echo "Edit: bot setup > Strategy Plan > OCO Settings"
echo "Sample: $PY $D/oco_preview.py --example-price 102.40"
echo "Real filled entry preview: $PY $D/oco_preview.py --intent-id REAL_INTENT_ID"
echo "LIVE OCO placement remains DISABLED. Existing AMO entry and broker orders unchanged."
