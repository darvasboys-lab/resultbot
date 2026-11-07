#!/usr/bin/env bash
# ResultBot: configurable intraday BUY limit buffer. UI/offline planner only.
set -euo pipefail
umask 077
D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
PIN="ee2d0e4820ab59c304c7051df3f2e7bfccfec049"
BASE="https://raw.githubusercontent.com/darvasboys-lab/resultbot/$PIN/resultbot"

if [[ ! -x "$PY" || ! -f "$D/equity_amo_live.py" || ! -f "$D/control_center_v2.py" ||
      ! -f "$D/strategy_stage_wizard.py" ]]; then
  echo "ERROR: Existing premium ResultBot strategy wizard is required." >&2
  exit 1
fi
if [[ ! -f "$D/amo_settings.sh" ]] || ! grep -q 'control_center_v2.py' "$D/amo_settings.sh"; then
  echo "ERROR: bot setup launcher is not connected to premium Control Center." >&2
  exit 1
fi
for name in control_center_v2.py intraday_buy_plan.py; do
  [[ ! -L "$D/$name" ]] || { echo "Refusing symlinked $name." >&2; exit 1; }
done

TMP="$(mktemp -d "$D/.intraday-buffer-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
for name in control_center_v2.py intraday_buy_plan.py; do
  curl -fLsS --retry 2 "$BASE/$name" -o "$TMP/$name"
done
"$PY" -m py_compile "$TMP/control_center_v2.py" "$TMP/intraday_buy_plan.py"

"$PY" - "$TMP" "$D" <<'CHECK'
import importlib.util,sys,tempfile
from pathlib import Path
src=Path(sys.argv[1])
sys.path[:0]=[str(src),sys.argv[2]]
def imported(name):
    spec=importlib.util.spec_from_file_location(name,src/(name+".py"))
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
ui=imported("control_center_v2")
policy=imported("intraday_buy_plan")
for h,w in ((24,80),(32,100)):
    top=(h-(24 if h<30 else 29))//2
    start=top+(10 if h<30 else 14)
    step=1 if h<30 else 2
    assert ui.mouse_hit_test("STRATEGY",h,w,18,start+step*4)==("item",4)
    assert ui.mouse_hit_test("BUY BUFFER",h,w,18,start+step*5)==("item",5)
    assert ui.mouse_hit_test("BUFFER CUSTOM",h,w,18,start+4)==("input",None)
with tempfile.TemporaryDirectory() as scratch:
    policy.ROOT=Path(scratch)
    policy.CONFIG=Path(scratch)/"settings.json"
    for percent in (1,2,3,4,"2.5","0"):
        assert policy.save_buffer_pct(percent)["buy_buffer_pct"]==str(percent)
    p=policy.calculate_buy_limit(quote_price=100,budget_inr=1000,
                                  tick_size="0.05",buffer_pct=2)
    assert p["buy_limit_price"]=="102"
    assert p["whole_shares_affordable_at_limit"]==9
    assert p["broker_order_submitted"] is False
print("PASS: mouse selection, buffer persistence, limit ceiling and quantity; no Dhan")
CHECK

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$D/intraday-buffer-backup-$STAMP"
mkdir -m 700 "$BACKUP"
for name in control_center_v2.py intraday_buy_plan.py; do
  if [[ -f "$D/$name" ]]; then cp -p "$D/$name" "$BACKUP/$name"; fi
done

completed=0
rollback() {
  if [[ "$completed" != 1 ]]; then
    for name in control_center_v2.py intraday_buy_plan.py; do
      if [[ -f "$BACKUP/$name" ]]; then cp -p "$BACKUP/$name" "$D/$name"
      else rm -f "$D/$name"; fi
    done
    echo "Installer interrupted: previous UI restored." >&2
  fi
}
trap 'rollback; rm -rf "$TMP"' EXIT
for name in control_center_v2.py intraday_buy_plan.py; do
  install -m 600 "$TMP/$name" "$D/$name"
done
completed=1

echo "SUCCESS: Intraday BUY buffer added to ResultBot Control Center."
echo "Backup directory: $BACKUP"
echo "Open: bot setup > Strategy Plan > BUY BUFFER"
echo "Presets: 1% / 2% / 3% / 4% / Custom"
echo "Test only: $PY $D/intraday_buy_plan.py preview --price 100 --budget 1000 --buffer 2"
echo "No trading service restart, Dhan broker call, or Equity AMO changes."
echo "LIVE intraday Super Order execution is NOT connected yet."
