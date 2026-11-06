#!/usr/bin/env bash
# ResultBot Equity AMO persistent owner YES / NO. NO order-count limit.
# YES authorizes each distinct, NEW eligible Telegram signal while enabled.
# NO blocks all new submissions, but never cancels existing Dhan orders.
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
"$PY" "$BOT" status-short
echo
echo "YES: Keep AMO ON for ALL new eligible Excellent Results alerts."
echo "     New official Earnings Pulse alerts and owner-authored normal"
echo "     Test Group Excellent messages can submit REAL Dhan BUY CNC LIMIT AMOs."
echo "     There is NO single-order limit, daily order-count cap or expiry."
echo "     Saved source/budget settings apply to all future eligible alerts."
echo "NO:  Keep AMO OFF until you manually turn it ON again."
echo "     NO does NOT cancel already submitted Dhan AMOs."
echo "     Use the separate cancel command for selected broker orders."
echo "EDIT: bash $D/amo_settings.sh"
echo

decision="NO"
if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
    if whiptail --title "ResultBot: REAL Equity AMO" \
        --defaultno --yes-button "YES - AMO ON" \
        --no-button "NO - AMO OFF" \
        --yesno "YES: Keep real Dhan Equity AMO ON until you press NO, using your SAVED alert source and per-order budget settings. No order-count limit or automatic expiry.\n\nNO: Stop NEW submissions. Existing broker orders must be cancelled separately by you." \
        16 82; then
        decision="YES"
    fi
else
    if [[ ! -t 0 ]]; then
        echo "Non-interactive input: choosing NO for safety."
    else
        read -r -p "Turn REAL Equity AMO ON? Type YES or NO: " choice
        if [[ "$choice" == "YES" ]]; then decision="YES"; fi
    fi
fi

if [[ "$decision" == "YES" ]]; then
    echo "===== REAL DHAN ACCOUNT / FUNDS / STATIC-IP PREFLIGHT ====="
    "$PY" "$BOT" preflight
    "$PY" "$BOT" arm > /dev/null
    echo
    echo "YES / ON: Real Equity AMO stays ON until you choose NO."
    echo "Only the saved, authorized source(s) can create new real orders."
    echo "To trigger an owner-only TEST GROUP real order, post this NEW message:"
    echo "#YESBANK - 🏆🔥 Excellent Results"
    echo "(The optional [RBLIVEAMO] prefix is also accepted.)"
    echo "Each signal is separately deduplicated and broker-verified."
    echo "Legacy [RBAMO]/[RBTEST] preview messages NEVER place real orders."
else
    "$PY" "$BOT" off > /dev/null
    echo "NO / OFF: New Equity AMOs blocked until you choose YES."
    echo "Any orders already accepted by Dhan remain active."
fi
echo
"$PY" "$BOT" status-short
echo "Edit saved settings: bash $D/amo_settings.sh"
echo "Full order list: $PY $BOT status"
echo "Request cancellation for an existing resultbot-owned order:"
echo "  $PY $BOT cancel --intent-id <intent_number>"
echo "Confirm CANCELLED in Dhan. NO does not cancel live orders."
