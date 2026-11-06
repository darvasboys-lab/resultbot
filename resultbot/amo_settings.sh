#!/usr/bin/env bash
# ResultBot live Equity AMO settings: terminal-only, no WebUI or service restart.
# Changing settings never toggles YES/NO or cancels an existing Dhan order.
set -euo pipefail
umask 077

PY="$HOME/uditbot/venv/bin/python"
D="$HOME/resultbot-data"
BOT="$D/equity_amo_live.py"
TOGGLE="$D/amo_yes_no.sh"

if [[ ! -x "$PY" || ! -f "$BOT" ]]; then
    echo "ResultBot AMO not installed." >&2
    exit 1
fi

get_setting() {
    local field="$1"
    "$PY" "$BOT" settings --show | "$PY" -c \
       'import json,sys; data=json.load(sys.stdin); print(data[sys.argv[1]])' "$field"
}

confirm() {
    local message="$1"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        whiptail --title "Confirm REAL trading settings" --defaultno \
          --yesno "$message" 14 80
    else
        local answer="NO"
        read -r -p "$message [type YES to confirm]: " answer
        [[ "$answer" == "YES" ]]
    fi
}

select_menu() {
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        whiptail --title "ResultBot — Edit Real AMO Settings" \
          --menu "Change settings for FUTURE signals only. Current orders are unaffected." \
          19 84 6 \
          "1" "Edit alert source (TEST / EARNINGS_PULSE / BOTH)" \
          "2" "Edit per-order budget in INR" \
          "3" "Turn AMO YES / NO (existing switch)" \
          "4" "View current settings and broker order intents" \
          "0" "Exit without further changes" \
          3>&1 1>&2 2>&3
    else
        echo >&2
        echo "1) Edit alert source" >&2
        echo "2) Edit per-order budget" >&2
        echo "3) Turn AMO YES / NO" >&2
        echo "4) View current settings and order intents" >&2
        echo "0) Exit" >&2
        local choice
        read -r -p "Select an option: " choice
        printf '%s' "$choice"
    fi
}

edit_source() {
    local current wanted
    current="$(get_setting source)"
    echo "Current source: $current"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        local a=OFF b=OFF c=OFF
        [[ "$current" == "TEST" ]] && a=ON
        [[ "$current" == "EARNINGS_PULSE" ]] && b=ON
        [[ "$current" == "BOTH" ]] && c=ON
        if ! wanted="$(whiptail --title "Choose real-order alert source" \
            --radiolist "Selected sources can generate REAL BUY orders while AMO is ON." \
            17 85 3 \
            "TEST" "Owner-only authorized Test Group" "$a" \
            "EARNINGS_PULSE" "Official Excellent Results channel" "$b" \
            "BOTH" "Both authorized sources" "$c" \
            3>&1 1>&2 2>&3)"; then
            echo "Source change cancelled."
            return
        fi
    else
        echo "1) TEST only (owner Test Group)"
        echo "2) EARNINGS_PULSE only (official live alerts)"
        echo "3) BOTH sources"
        local choice
        read -r -p "Select source [1/2/3]: " choice
        case "$choice" in
            1) wanted=TEST ;;
            2) wanted=EARNINGS_PULSE ;;
            3) wanted=BOTH ;;
            *) echo "No change.";return ;;
        esac
    fi
    [[ "$wanted" == "$current" ]] && { echo "Source already $current.";return; }
    if ! confirm "Change source from $current to $wanted? If AMO is ON, future eligible alerts from the newly selected source(s) may immediately place REAL Dhan orders. Existing orders will not be cancelled."; then
        echo "Source unchanged."
        return
    fi
    # A real order may become RESERVED/PREPARING/SUBMITTING between the
    # confirmation and the actual write; handle that safely without exiting.
    local outcome
    if ! outcome="$("$PY" "$BOT" settings --source "$wanted" 2>&1)"; then
        echo "Source unchanged; edit was blocked safely:"
        echo "$outcome"
        echo "Check 'View current settings and order intents' (menu 4)."
        return 0
    fi
    echo "$outcome"
    echo "New source will apply to future eligible alerts. YES/NO unchanged."
}

edit_budget() {
    local current desired min_budget max_budget outcome
    current="$(get_setting per_order_budget_inr)"
    min_budget="$(get_setting budget_min_inr)"
    max_budget="$(get_setting budget_max_inr)"
    echo "Current per-order budget: INR $current"
    echo "Allowed per-order range in the current bot: INR $min_budget–$max_budget"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        if ! desired="$(whiptail --title "Equity AMO budget per order" \
            --inputbox "Enter a whole INR amount ($min_budget–$max_budget). This is PER ORDER; there is no daily order-count limit." \
            13 80 "$current" 3>&1 1>&2 2>&3)"; then
            echo "Budget change cancelled. Saved INR $current remains unchanged."
            return 0
        fi
    else
        read -r -p "New per-order budget (INR $min_budget–$max_budget): " desired
    fi

    # Normalize ordinary input whitespace; reject other formatting.
    desired="${desired#"${desired%%[![:space:]]*}"}"
    desired="${desired%"${desired##*[![:space:]]}"}"
    if [[ ! "$desired" =~ ^[0-9]+$ ]]; then
        echo "INVALID AMOUNT: Please enter whole rupees, e.g. 600."
        echo "No settings were changed; still INR $current. Returning to menu."
        return 0
    fi

    # Python avoids Bash 08 / leading-zero parsing and integer overflow.
    if ! "$PY" -c 'import sys
try:
    n=int(sys.argv[1]); lo=int(sys.argv[2]); hi=int(sys.argv[3])
    sys.exit(0 if lo <= n <= hi else 1)
except ValueError:
    sys.exit(1)' "$desired" "$min_budget" "$max_budget"; then
        echo "OUT OF RANGE: You entered INR $desired."
        echo "The current per-order limit is INR $min_budget–$max_budget."
        echo "Your saved budget remains INR $current. Returning to menu."
        return 0
    fi

    if [[ "$desired" == "$current" ]]; then
        echo "No change: budget already INR $current."
        return 0
    fi
    if ! confirm "Save INR $desired as the PER-ORDER budget for FUTURE eligible real Dhan AMO signals? Existing orders and the YES/NO switch will not be changed."; then
        echo "Budget unchanged at INR $current."
        return 0
    fi

    # The live engine may reject an edit if an in-flight order is between
    # reservation and broker submission. Never terminate the entire menu.
    if ! outcome="$("$PY" "$BOT" settings --budget "$desired" 2>&1)"; then
        echo "Budget unchanged: live settings check blocked this edit."
        echo "$outcome"
        echo "Check menu 4 for any unresolved Dhan order transition."
        return 0
    fi
    echo "$outcome"
    echo "Budget updated for FUTURE signals only; prior orders unaffected."
}

echo "======================================================"
echo " RESULTBOT — EDIT REAL EQUITY AMO SETTINGS "
echo "======================================================"
echo "Settings are saved on the VM. No WebUI and no service restart."
echo "Existing Dhan orders will not be altered or cancelled."
while true; do
    echo
    "$PY" "$BOT" status-short
    choice="$(select_menu)" || break
    case "$choice" in
        1) edit_source ;;
        2) edit_budget ;;
        3) bash "$TOGGLE" ;;
        4) "$PY" "$BOT" settings --show
           echo "===== EXISTING RESULTBOT-OWNED AMO ORDERS ====="
           "$PY" "$BOT" status ;;
        0) echo "Settings editor closed."; break ;;
        *) echo "Invalid menu choice." ;;
    esac
done
