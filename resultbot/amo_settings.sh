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
    "$PY" "$BOT" settings --source "$wanted"
    echo "New source will apply to future eligible alerts. YES/NO unchanged."
}

edit_budget() {
    local current desired
    current="$(get_setting per_order_budget_inr)"
    echo "Current per-order budget: INR $current (valid INR 100 to 1000)"
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then
        if ! desired="$(whiptail --title "Equity AMO budget per order" \
            --inputbox "Whole INR amount, 100 to 1000. This is PER ORDER, not a total daily or order-count limit." \
            13 78 "$current" 3>&1 1>&2 2>&3)"; then
            echo "Budget change cancelled."
            return
        fi
    else
        read -r -p "New per-order budget INR (100–1000): " desired
    fi
    if [[ ! "$desired" =~ ^[0-9]+$ ]]; then
        echo "Invalid number; budget unchanged."
        return
    fi
    if [[ "$desired" == "$current" ]]; then
        echo "Budget unchanged."
        return
    fi
    if ! confirm "Set per-order budget to INR $desired? When AMO is ON, every NEW eligible signal may place a REAL order up to this amount. Existing Dhan orders and the YES/NO switch remain unchanged."; then
        echo "Budget unchanged."
        return
    fi
    "$PY" "$BOT" settings --budget "$desired"
    echo "Budget saved for future alerts. Existing order intents are unchanged."
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
