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


# BEGIN RESULTBOT RETRO THEME
export NEWT_COLORS='
root=white,blue
window=black,lightgray
border=blue,lightgray
title=blue,lightgray
textbox=black,lightgray
acttextbox=black,lightgray
entry=black,white
disentry=gray,lightgray
label=blue,lightgray
button=black,cyan
actbutton=white,blue
checkbox=black,lightgray
actcheckbox=white,blue
listbox=black,lightgray
actlistbox=white,blue
actsellistbox=white,blue
sellistbox=blue,lightgray
compactbutton=white,blue
shadow=black,black
'
whiptail() {
    # Refresh the header from the persisted owner settings on EVERY dialog,
    # including immediately after returning from a budget edit.
    local amount cols left right pad backtitle
    amount="$(get_setting per_order_budget_inr 2>/dev/null)" || amount="UNAVAILABLE"
    left="RESULTBOT  |  CONTROL CENTER"
    right="PER ORDER: INR ${amount}"
    cols="$(tput cols 2>/dev/null || printf '100')"
    [[ "$cols" =~ ^[0-9]+$ ]] || cols=100
    pad=$((cols - ${#left} - ${#right} - 3))
    (( pad >= 2 )) || pad=2
    printf -v backtitle '%s%*s%s' "$left" "$pad" '' "$right"
    command whiptail --backtitle "$backtitle" "$@"
}
# END RESULTBOT RETRO THEME

get_setting() {
    local field="$1"
    "$PY" "$BOT" settings --show | "$PY" -c \
       'import json,sys; data=json.load(sys.stdin); print(data[sys.argv[1]])' "$field"
}

confirm() {
    local message="$1"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        whiptail --title "RESULTBOT  |  CONFIRM CHANGE" --defaultno \
          --yesno "$message" 14 80
    else
        local answer="NO"
        read -r -p "$message [type YES to confirm]: " answer
        [[ "$answer" == "YES" ]]
    fi
}

notice() {
    local message="$1"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        whiptail --title "RESULTBOT  |  NOTICE" \
          --msgbox "$message" 13 80 || true
    else
        printf '%s\n' "$message"
    fi
}

source_display_name() {
    # Display-only labels for verified Telegram sources; routing remains unchanged.
    local current
    current="$(get_setting source 2>/dev/null)" || current="UNKNOWN"
    case "$current" in
        BOTH) printf 'Earnings Pulse + Owner Test Group' ;;
        EARNINGS_PULSE) printf 'Earnings Pulse' ;;
        TEST) printf 'Owner Test Group' ;;
        *) printf 'Source unavailable' ;;
    esac
}

select_menu() {
    # Refresh from saved settings each time, including after source/budget edits.
    local display_amount display_sources
    display_amount="$(get_setting per_order_budget_inr 2>/dev/null)" || display_amount="UNAVAILABLE"
    display_sources="$(source_display_name)"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        whiptail --title "RESULTBOT  |  SETTINGS" \
          --menu "SAVED PER-ORDER AMOUNT: INR $display_amount

Choose a setting below. Changes apply to future signals.
Existing orders remain unchanged." \
          19 84 6 \
          "1" "ALERT SOURCES    $display_sources" \
          "2" "ORDER BUDGET     Set your investment per order" \
          "3" "TRADING SWITCH   Turn AMO on or off" \
          "4" "OVERVIEW         View settings and order records" \
          "0" "BACK             Return to terminal" \
          3>&1 1>&2 2>&3
    else
        echo >&2
        echo "1) Alert sources: $display_sources" >&2
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
        if ! wanted="$(whiptail --title "RESULTBOT  |  ALERT SOURCE" \
            --radiolist "Selected sources can generate REAL BUY orders while AMO is ON." \
            17 85 3 \
            "TEST" "Owner-only authorized Test Group" "$a" \
            "EARNINGS_PULSE" "Official Excellent Results channel" "$b" \
            "BOTH" "Earnings Pulse + Owner Test Group" "$c" \
            3>&1 1>&2 2>&3)"; then
            echo "Source change cancelled."
            return
        fi
    else
        echo "1) TEST only (owner Test Group)"
        echo "2) EARNINGS_PULSE only (official live alerts)"
        echo "3) BOTH (Earnings Pulse + Owner Test Group)"
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
        notice "Source unchanged; edit was blocked safely. $outcome. Check menu 4 for existing order states."
        return 0
    fi
    echo "$outcome"
    echo "New source will apply to future eligible alerts. YES/NO unchanged."
}

edit_budget() {
    local current desired outcome
    current="$(get_setting per_order_budget_inr)"
    echo "Saved budget: INR $current per new order"
    echo "Enter YOUR chosen amount in whole rupees. No arbitrary budget cap."
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        if ! desired="$(whiptail --title "RESULTBOT  |  ORDER BUDGET" \
            --inputbox "Enter your total INR budget for EACH new order. Quantity = whole shares affordable at Yahoo-based LIMIT price. Broker funds must cover actual order." \
            13 80 "$current" 3>&1 1>&2 2>&3)"; then
            echo "Budget unchanged."
            return 0
        fi
    else
        read -r -p "New budget per order (INR): " desired
    fi
    desired="${desired#"${desired%%[![:space:]]*}"}"
    desired="${desired%"${desired##*[![:space:]]}"}"
    if [[ ! "$desired" =~ ^[0-9]+$ ]]; then
        notice "Enter the amount as whole rupees, e.g. 5000, without currency symbols. Budget unchanged at INR $current."
        return 0
    fi
    if ! "$PY" -c 'import sys
try:
 n=int(sys.argv[1]); sys.exit(0 if 0<n<=2**63-1 else 1)
except ValueError: sys.exit(1)' "$desired"; then
        notice "Enter a positive whole-rupee amount. Budget unchanged at INR $current."
        return 0
    fi
    # An unchanged budget is not an error even with leading zeros.
    if [[ "$("$PY" -c 'import sys; print(int(sys.argv[1]))' "$desired")" == "$current" ]]; then
        echo "Already set: INR $current per order."
        return 0
    fi
    if ! confirm "Save INR $desired PER NEW ORDER? The bot will calculate whole-share quantity from a valid Yahoo-based limit price. This does not modify existing orders or change YES/NO."; then
        echo "Budget unchanged."
        return 0
    fi
    if ! outcome="$("$PY" "$BOT" settings --budget "$desired" 2>&1)"; then
        notice "Budget unchanged. $outcome"
        return 0
    fi
    echo "$outcome"
    # Verify the canonical saved amount, rather than showing the unsaved input.
    local verified expected
    verified="$(get_setting per_order_budget_inr)" || {
        notice "The save command returned successfully, but the saved amount could not be verified. Reopen settings to inspect it."
        return 0
    }
    expected="$("$PY" -c 'import sys; print(int(sys.argv[1]))' "$desired")"
    if [[ "$verified" != "$expected" ]]; then
        notice "Saved value differs from the submitted amount. Current per-order amount: INR $verified. Review before the next order."
        return 0
    fi
    notice "PER-ORDER AMOUNT SAVED: INR $verified. The settings screen will now display this amount. Future orders use the saved amount."
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
