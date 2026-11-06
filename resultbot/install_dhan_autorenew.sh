#!/usr/bin/env bash
# Install ONLY ResultBot's isolated Dhan token refresh timer.
# Never touches trading_bot.service or the original dhan-token-refresh.timer.
set -euo pipefail
umask 077

if [[ "${EUID}" -eq 0 ]]; then
  echo "Refusing root installation. Run as the normal ResultBot VM user." >&2
  exit 1
fi

BOT_PY="$HOME/uditbot/venv/bin/python"
SCRIPT="$HOME/resultbot-data/dhan_readonly.py"
USER_DIR="$HOME/.config/systemd/user"
SERVICE="resultbot-dhan-token-refresh.service"
TIMER="resultbot-dhan-token-refresh.timer"

[[ -x "$BOT_PY" && -f "$SCRIPT" ]] || {
  echo "ResultBot Dhan connector missing or Python environment unavailable." >&2
  exit 1
}

echo "===== VERIFY READ-ONLY DHAN CONNECTOR ====="
"$BOT_PY" "$SCRIPT" self-test

echo "===== CHECK EXISTING TOTP/PIN RENEWAL SOURCE ====="
"$BOT_PY" "$SCRIPT" configure-autorenew

echo "===== VERIFY AUTORENEWAL CREDENTIAL SOURCE ====="
"$BOT_PY" - "$SCRIPT" <<'PY'
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("resultbot_dhan_readonly", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
account, _ = mod.load_credentials()
source = mod.renewal_source(account)
if not source:
    print("Auto-renewal not configured. Timer will NOT be enabled.")
    sys.exit(2)
print("READY: Account", mod.display_account(account), "| source:", source[0])
PY

echo "===== ONE-TIME TOKEN RENEWAL VERIFICATION ====="
# The previous working token stays untouched if this refresh fails.
"$BOT_PY" "$SCRIPT" refresh

mkdir -p "$USER_DIR"
chmod 0700 "$USER_DIR"

cat > "$USER_DIR/$SERVICE" <<EOF
[Unit]
Description=ResultBot DhanHQ read-only access token auto-refresh (no order entry)
Wants=network-online.target time-sync.target
After=network-online.target time-sync.target

[Service]
Type=oneshot
ExecStart=$BOT_PY $SCRIPT refresh-scheduled
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
TimeoutStartSec=90s
StandardOutput=journal
StandardError=journal
EOF

cat > "$USER_DIR/$TIMER" <<EOF
[Unit]
Description=ResultBot daily DhanHQ read-only token renewal at 08:00 IST

[Timer]
OnCalendar=*-*-* 08:00:00 Asia/Kolkata
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0
Unit=$SERVICE

[Install]
WantedBy=timers.target
EOF

chmod 0600 "$USER_DIR/$SERVICE" "$USER_DIR/$TIMER"

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER"

echo "===== AUTO-RENEWAL TIMER ====="
systemctl --user is-active "$TIMER"
systemctl --user list-timers --all | grep -F "resultbot-dhan-token-refresh" || true

echo "===== SAFE END STATE ====="
echo "ResultBot Telegram listener has not been restarted."
echo "Original Dhan refresh timer and trading_bot.service have NOT been modified."
echo "Enable user lingering separately if needed for unattended reboots/logouts:"
echo "sudo loginctl enable-linger \"\$(id -un)\""
