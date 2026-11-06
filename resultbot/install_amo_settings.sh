#!/usr/bin/env bash
# Install only ResultBot's editable Equity AMO settings.
# Never stop/start services, toggle AMO, create/cancel orders, or modify SQLite.
set -euo pipefail
umask 077

D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
BASE="${1:?Provide pinned raw GitHub URL ending /resultbot}"
LISTENER="resultbot-excellent-watch.service"

if [[ ! -x "$PY" || ! -s "$D/equity_amo_live.py" || ! -s "$D/amo_yes_no.sh" ]]; then
    echo "BLOCKED: ResultBot live AMO and YES/NO must be installed first." >&2
    exit 1
fi
if ! systemctl --user is-active --quiet "$LISTENER"; then
    echo "BLOCKED: Existing ResultBot listener must be active." >&2
    exit 1
fi

echo "===== CURRENT REAL ORDER / SWITCH STATE ====="
"$PY" "$D/equity_amo_live.py" status-short

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "===== GET PINNED SETTINGS-ONLY UPDATE ====="
for file in equity_amo_live.py amo_yes_no.sh amo_settings.sh; do
    curl -fLsS "$BASE/$file" -o "$TMP/$file"
done

echo "===== VALIDATE WITHOUT MODIFICATION ====="
"$PY" -m py_compile "$TMP/equity_amo_live.py"
bash -n "$TMP/amo_yes_no.sh" "$TMP/amo_settings.sh"
grep -q 'def update_settings(' "$TMP/equity_amo_live.py"
grep -q 'arm > /dev/null' "$TMP/amo_yes_no.sh"

echo "===== PRIVATE BACKUP ====="
STAMP="$(date -u +%Y%m%d-%H%M%S)"
cp -p "$D/equity_amo_live.py" "$D/equity_amo_live.py.pre-settings-$STAMP"
cp -p "$D/amo_yes_no.sh" "$D/amo_yes_no.sh.pre-settings-$STAMP"
if [[ -f "$D/amo_settings.sh" ]]; then
    cp -p "$D/amo_settings.sh" "$D/amo_settings.sh.pre-settings-$STAMP"
fi

echo "===== INSTALL SETTINGS, NO RESTART ====="
install -m 600 "$TMP/equity_amo_live.py" "$D/equity_amo_live.py"
install -m 700 "$TMP/amo_yes_no.sh" "$D/amo_yes_no.sh"
install -m 700 "$TMP/amo_settings.sh" "$D/amo_settings.sh"

echo "===== CONFIRM SAVED SETTINGS ====="
"$PY" "$D/equity_amo_live.py" settings --show
"$PY" "$D/equity_amo_live.py" status-short

echo "===== ORIGINAL TELEGRAM LISTENER STILL RUNNING ====="
systemctl --user is-active "$LISTENER"

echo "===== SETTINGS EDITOR READY ====="
echo "bash $D/amo_settings.sh"
echo "No AMO state toggles, Dhan orders, cancellations, or service restarts performed."
