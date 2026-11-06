#!/usr/bin/env bash
# ResultBot REAL Equity AMO: owner YES/NO selector. The YES choice arms one
# real broker order from the next eligible NEW Telegram Test Group message.
# NO blocks new submissions; it never cancels an existing order.
set -euo pipefail
D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
BOT="$D/equity_amo_live.py"

if [[ ! -f "$BOT" || ! -x "$PY" ]]; then
    echo "ResultBot real Equity AMO component is not installed."
    exit 1
fi

echo "======================================================"
echo " RESULTBOT — REAL EQUITY AMO YES / NO "
echo "======================================================"
"$PY" "$BOT" status
echo
echo "YES: arm ONE real Dhan BUY CNC LIMIT AMO (max INR 500)"
echo "     Trigger only by a NEW owner message in ResultBot TEST GROUP."
echo "     Expires in 10 minutes. No order placed by YES alone."
echo "NO:  block all future AMO submissions until enabled again."
echo "     Existing Dhan orders are NOT cancelled by NO."
echo

decision="NO"
if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
    if whiptail --title "ResultBot: REAL Dhan Equity AMO" \
        --defaultno --yes-button "YES - Arm 1 real order" \
        --no-button "NO - Switch OFF" \
        --yesno "Will the next NEW authorized [RBLIVEAMO] Test Group alert be allowed to submit ONE REAL Dhan Equity AMO (max INR 500, within 10 minutes)?\n\nNO disables future submissions. It does NOT cancel any order already accepted by Dhan." \
        15 80; then
        decision="YES"
    fi
else
    if [[ ! -t 0 ]]; then
        echo "Non-interactive terminal: defaulting to NO (safe)."
    else
        read -r -p "Enter YES to arm one REAL AMO, otherwise NO: " choice
        if [[ "$choice" == "YES" ]]; then decision="YES"; fi
    fi
fi

if [[ "$decision" == "YES" ]]; then
    echo "===== LIVE DHAN ACCOUNT / FUNDS / IP PREFLIGHT ====="
    "$PY" "$BOT" preflight
    "$PY" "$BOT" arm --source TEST --budget 500
    echo
    echo "YES: Armed. You must send a NEW owner Telegram message to the ResultBot TEST GROUP:"
    echo "[RBLIVEAMO] #YESBANK - 🏆🔥 Excellent Results"
    echo "This can submit a REAL broker order. A Dhan order ID is not a fill."
else
    "$PY" "$BOT" off
    echo "NO: New AMO submissions blocked. Existing order cancellation is separate."
fi
echo
echo "Use: $PY $BOT status"
echo "Request cancellation for a verified ResultBot-owned order:"
echo "  $PY $BOT cancel --intent-id <number>"
echo "Then confirm broker CANCELLED status in the Dhan order book."
