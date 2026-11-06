#!/usr/bin/env bash
# ResultBot genuine Equity AMO — terminal YES/NO control, no browser/dashboard.
# Installs the production listener integration OFF by default. Never places
# a broker order during installation. Existing Dhan token renewal untouched.
set -euo pipefail
umask 077

D="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
LISTENER="resultbot-excellent-watch.service"
BASE_URL="${1:?Supply pinned raw GitHub URL ending /resultbot}"
MODE="${2:-}"
case "$MODE" in
  ""|"--code-only") ;;
  *) echo "Unsupported mode: $MODE (allowed: --code-only)" >&2; exit 2;;
esac
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

test -x "$PY"
test -s "$D/dhan_readonly.py"
test -s "$D/excellent_watch.py"
test -s "$D/dhan_bridge.py"
if [[ "$(systemctl is-active trading_bot.service 2>/dev/null || true)" != "inactive" ||
      "$(systemctl is-enabled trading_bot.service 2>/dev/null || true)" != "disabled" ]]; then
    echo "BLOCKED: Original trading_bot.service must remain inactive and disabled."
    exit 1
fi
if ! systemctl --user is-active --quiet "$LISTENER"; then
    echo "BLOCKED: Existing ResultBot Telegram listener is not active."
    exit 1
fi

echo "===== DOWNLOAD TERMINAL YES/NO REAL AMO CODE ====="
for name in equity_amo_live.py excellent_watch.py amo_yes_no.sh; do
    curl -fLsS "$BASE_URL/$name" -o "$TMP/$name"
done

echo "===== VALIDATE CODE BEFORE ANY CHANGE ====="
"$PY" -m py_compile "$TMP/equity_amo_live.py" "$TMP/excellent_watch.py"
bash -n "$TMP/amo_yes_no.sh"

echo "===== ENSURE LIVE AMO IS OFF BEFORE ANY RESTART ====="
# This also works when the previous live-AMO module was already installed.
# OFF does not cancel an existing broker order.
if [[ -f "$D/equity_amo_live.py" ]]; then
    "$PY" "$D/equity_amo_live.py" off >/dev/null
fi

if [[ "$MODE" == "--code-only" ]]; then
    echo "===== CODE-ONLY INSTALL: NO LIVE FUNDS VALIDATION ====="
    echo "Updating the corrected Telegram listener while AMO is OFF."
    echo "This mode does not authorize orders. YES still requires fresh Dhan"
    echo "profile, funds, and IP checks and will fail closed on HTTP 500."
else
    echo "===== READ-ONLY DHAN ORDER-PERMISSION PREFLIGHT ====="
    # Install a temporary Python entrypoint beside the private Dhan connector.
    # Retry only broker GET /fundlimit on transient HTTP 5xx, never POST /orders.
    install -m 600 "$TMP/equity_amo_live.py" "$D/.equity_amo_preflight.tmp.py"
    trap 'rm -rf "$TMP"; rm -f "$D/.equity_amo_preflight.tmp.py"' EXIT
    if ! "$PY" "$D/.equity_amo_preflight.tmp.py" preflight; then
        echo "NOT INSTALLED: fresh Dhan balance / static-IP preflight failed."
        echo "Existing ResultBot Telegram listener is untouched and AMO is OFF."
        echo "If Dhan /fundlimit remains HTTP 500, use explicit --code-only"
        echo "to update code OFF, but do not turn YES until Dhan recovers."
        exit 1
    fi
fi

echo "===== BACK UP WORKING LISTENER ====="
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="$D/excellent_watch.py.pre-real-amo-$STAMP"
cp -p "$D/excellent_watch.py" "$BACKUP"
if [[ -f "$D/equity_amo_live.py" ]]; then
    cp -p "$D/equity_amo_live.py" "$D/equity_amo_live.py.pre-real-amo-$STAMP"
fi

echo "===== INSTALL TERMINAL CONTROL (OFF BY DEFAULT) ====="
install -m 600 "$TMP/equity_amo_live.py" "$D/equity_amo_live.py"
install -m 600 "$TMP/excellent_watch.py" "$D/excellent_watch.py"
install -m 700 "$TMP/amo_yes_no.sh" "$D/amo_yes_no.sh"

"$PY" "$D/equity_amo_live.py" self-test
# Explicitly invalidate any previously armed pre-submit intent.
"$PY" "$D/equity_amo_live.py" off >/dev/null

# The old private panel, if previously deployed, should not be running.
if systemctl --user list-unit-files "resultbot-amo-panel.service" \
   --no-legend 2>/dev/null | grep -q 'resultbot-amo-panel.service'; then
    systemctl --user disable --now resultbot-amo-panel.service || true
fi

echo "===== RESTART RESULTBOT TELEGRAM LISTENER ONLY ====="
if ! systemctl --user restart "$LISTENER" ||
   ! systemctl --user is-active --quiet "$LISTENER"; then
    cp -p "$BACKUP" "$D/excellent_watch.py"
    systemctl --user restart "$LISTENER" || true
    "$PY" "$D/equity_amo_live.py" off || true
    echo "FAILED: original listener restored; AMO switch OFF."
    exit 1
fi

echo "===== VERIFIED FINAL STATUS ====="
systemctl --user is-active "$LISTENER"
systemctl --user is-active resultbot-dhan-token-refresh.timer
"$PY" "$D/equity_amo_live.py" status
echo
echo "NO WEBUI. Real AMO OFF until an owner chooses YES."
if [[ "$MODE" == "--code-only" ]]; then
    echo "CAUTION: no fresh funds preflight was completed during installation."
    echo "YES will require a fresh successful Dhan funds/IP check."
fi
echo "OPEN TERMINAL YES/NO CONTROL:"
echo "  bash $D/amo_yes_no.sh"
echo "NO ORDER was submitted during installation."
