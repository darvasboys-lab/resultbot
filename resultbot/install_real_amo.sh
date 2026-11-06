#!/usr/bin/env bash
# Install private OFF-by-default REAL Equity AMO panel on the ResultBot VM.
# Does not place orders or modify any service other than the ResultBot listener
# and the newly created resultbot-amo-panel user unit.
set -euo pipefail
umask 077

D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
LISTENER="resultbot-excellent-watch.service"
PANEL="resultbot-amo-panel.service"
BASE_URL="${1:?Provide pinned GitHub raw resultbot directory URL as first argument}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

test -x "$PY"
test -s "$D/dhan_readonly.py"
test -s "$D/excellent_watch.py"
test -s "$D/dhan_bridge.py"
if [[ "$(systemctl is-active trading_bot.service 2>/dev/null || true)" != "inactive" ]]; then
    echo "BLOCKED: Original trading service must be inactive."
    exit 1
fi
if [[ "$(systemctl is-enabled trading_bot.service 2>/dev/null || true)" != "disabled" ]]; then
    echo "BLOCKED: Original trading service must be disabled."
    exit 1
fi
if ! systemctl --user is-active --quiet "$LISTENER"; then
    echo "BLOCKED: Existing ResultBot Telegram listener must be active."
    exit 1
fi

echo "===== DOWNLOAD LIVE AMO CODE ====="
for name in equity_amo_live.py amo_panel.py excellent_watch.py; do
    curl --fail --location --silent --show-error "$BASE_URL/$name" -o "$TMP/$name"
done

echo "===== COMPILE BEFORE INSTALL ====="
"$PY" -m py_compile "$TMP/equity_amo_live.py" "$TMP/amo_panel.py" "$TMP/excellent_watch.py"

echo "===== MAKE RESTORE COPIES ====="
STAMP="$(date -u +%Y%m%d-%H%M%S)"
cp -p "$D/excellent_watch.py" "$D/excellent_watch.py.pre-real-amo-$STAMP"
for f in equity_amo_live.py amo_panel.py; do
    if [[ -f "$D/$f" ]]; then cp -p "$D/$f" "$D/$f.pre-real-amo-$STAMP"; fi
done

echo "===== INSTALL FILES: STILL OFF ====="
for name in equity_amo_live.py amo_panel.py excellent_watch.py; do
    install -m 600 "$TMP/$name" "$D/$name"
done

echo "===== VALIDATE LOCAL OFF-BY-DEFAULT STATE ====="
"$PY" "$D/equity_amo_live.py" self-test
"$PY" "$D/equity_amo_live.py" status

echo "===== VERIFY LIVE DHAN ORDER-PERMISSION PREREQUISITES (GET ONLY) ====="
if ! "$PY" "$D/equity_amo_live.py" preflight; then
    cp -p "$D/excellent_watch.py.pre-real-amo-$STAMP" "$D/excellent_watch.py"
    echo "Preflight blocked: listener kept on previous version."
    echo "The new AMO module remains unarmed and cannot place orders."
    exit 1
fi

echo "===== ENABLE LOCALHOST-ONLY OWNER PANEL ====="
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$PANEL" <<EOF
[Unit]
Description=ResultBot Private Real Dhan Equity AMO ON/OFF Dashboard
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$D
ExecStart=$PY $D/amo_panel.py
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF
chmod 600 "$UNIT_DIR/$PANEL"
systemctl --user daemon-reload
systemctl --user enable --now "$PANEL"
if ! systemctl --user is-active --quiet "$PANEL"; then
    cp -p "$D/excellent_watch.py.pre-real-amo-$STAMP" "$D/excellent_watch.py"
    echo "Owner panel failed. Listener restored; real AMO not activated."
    exit 1
fi

echo "===== RESTART ONLY RESULTBOT LISTENER ====="
if ! systemctl --user restart "$LISTENER" ||
   ! systemctl --user is-active --quiet "$LISTENER"; then
    cp -p "$D/excellent_watch.py.pre-real-amo-$STAMP" "$D/excellent_watch.py"
    systemctl --user restart "$LISTENER" || true
    systemctl --user stop "$PANEL" || true
    echo "Listener failure. Previous version restored."
    exit 1
fi

echo "===== FINAL ====="
systemctl --user is-active "$LISTENER"
systemctl --user is-active "$PANEL"
systemctl --user is-active resultbot-dhan-token-refresh.timer
"$PY" "$D/equity_amo_live.py" status
echo "OWNER PANEL: localhost:8093 (SSH forward required)"
echo "LOGIN USER: resultbot"
echo "LOGIN PASSWORD: private file $D/.amo_panel_key (do not paste into chat)"
echo "REAL AMO is OFF until owner explicitly confirms ON."
