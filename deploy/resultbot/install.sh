#!/usr/bin/env bash
# ResultBot fresh-VM installation. Run as a normal sudo-enabled Debian/Ubuntu user.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
STATE="$ROOT/.resultbot-install-stage"
D="$HOME/resultbot-data"
PY="$ROOT/venv/bin/python"
fail() { printf 'INSTALL STOPPED: %s\n' "$*" >&2; exit 1; }
[[ $(id -u) != 0 ]] || fail 'Use a normal sudo-enabled user.'
[[ "$ROOT" == "$HOME/uditbot" ]] || fail 'Clone this release into ~/uditbot.'
[[ "$HOME" =~ ^/[a-zA-Z0-9_./-]+$ ]] || fail 'Home path contains unsupported service-unit characters.'
[[ -t 0 ]] || fail 'Run in an interactive VM terminal for credentials and OTP.'
source /etc/os-release
[[ "$ID" == debian || "$ID" == ubuntu ]] || fail 'Debian/Ubuntu required.'
command -v sudo >/dev/null || fail 'sudo required.'
systemctl --user show-environment >/dev/null || fail 'Log in directly as the VM user so user services are available.'

if [[ ! -f "$STATE" ]]; then
    [[ ! -e "$D" && ! -e "$ROOT/.env" && ! -e "$HOME/.dhan_token" ]] || fail 'Fresh VM only: existing bot data/credentials found.'
    for unit in trading_bot.service auditbot-web.service resultbot-listener.service dhan-token-refresh.timer; do
        if systemctl cat "$unit" >/dev/null 2>&1; then fail "Existing $unit found. Do not run on a working VM."; fi
    done
    for unit in resultbot-excellent-watch.service resultbot-dhan-token-refresh.timer; do
        if systemctl --user cat "$unit" >/dev/null 2>&1; then fail "Existing $unit found."; fi
    done
    printf 'preparing\n' > "$STATE"
fi
[[ "$(cat "$STATE")" != complete ]] || { echo 'Already installed. Use bot setup or bot status.'; exit 0; }

echo '1/5 Installing dependencies...'
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git ca-certificates curl gpg gpg-agent whiptail dbus-user-session
[[ -x "$PY" ]] || python3 -m venv "$ROOT/venv"
"$PY" -m pip install -r "$ROOT/requirements.txt"

echo '2/5 Installing captured VM code and terminal menu...'
mkdir -p "$D" "$ROOT/data" "$HOME/.local/bin" "$HOME/.local/share/resultbot" "$HOME/.config/systemd/user"
chmod 700 "$D" "$ROOT/data"
for src in "$ROOT"/resultbot_runtime/*.py "$ROOT"/resultbot_runtime/*.sh; do
    install -m 700 "$src" "$D/$(basename "$src")"
done
install -m 700 "$ROOT/deploy/resultbot/bot" "$HOME/.local/bin/bot"
install -m 600 "$ROOT/deploy/resultbot/menu.sh" "$HOME/.local/share/resultbot/menu.sh"
install -m 600 "$ROOT/deploy/resultbot/status_dashboard.py" "$HOME/.local/share/resultbot/status_dashboard.py"
"$PY" -m compileall -q "$D" "$ROOT/deploy/resultbot"
"$PY" "$D/equity_amo_live.py" self-test

# Keep original service present, inactive and disabled: captured runtime checks it.
# No other original AuditBot service is enabled by this installer.
INSTALL_USER="$(id -un)"
INSTALL_GROUP="$(id -gn)"
sed -e "s|@HOME@|$HOME|g" -e "s|@USER@|$INSTALL_USER|g" -e "s|@GROUP@|$INSTALL_GROUP|g" \
    "$ROOT/deploy/resultbot/trading_bot.service.in" | sudo tee /etc/systemd/system/trading_bot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl disable trading_bot.service >/dev/null 2>&1

echo '3/5 Credentials and Telegram login...'
if [[ "$(cat "$STATE")" != configured ]]; then
    "$PY" "$ROOT/deploy/resultbot/configure.py"
    printf 'configured\n' > "$STATE"
fi

echo '4/5 Provisioning automatic startup...'
for unit in resultbot-excellent-watch.service resultbot-dhan-token-refresh.service resultbot-dhan-token-refresh.timer; do
    install -m 600 "$ROOT/deploy/resultbot/$unit" "$HOME/.config/systemd/user/$unit"
done
sed -e "s|@HOME@|$HOME|g" -e "s|@USER@|$INSTALL_USER|g" -e "s|@GROUP@|$INSTALL_GROUP|g" \
    "$ROOT/deploy/resultbot/resultbot-listener.service.in" | sudo tee /etc/systemd/system/resultbot-listener.service >/dev/null
sudo loginctl enable-linger "$INSTALL_USER"
sudo systemctl daemon-reload
systemctl --user daemon-reload

touch "$HOME/.bashrc"
for line in 'export PATH="$HOME/.local/bin:$PATH"' 'source "$HOME/.local/share/resultbot/menu.sh"'; do
    grep -Fqx "$line" "$HOME/.bashrc" || printf '\n%s\n' "$line" >> "$HOME/.bashrc"
done

echo '5/5 Starting and checking listeners...'
"$PY" "$D/equity_amo_live.py" off >/dev/null
sudo systemctl enable --now resultbot-listener.service
systemctl --user enable --now resultbot-excellent-watch.service
if [[ -f "$D/.dhan_autorenew.json" ]]; then
    systemctl --user enable --now resultbot-dhan-token-refresh.timer
else
    echo 'PIN/TOTP renewal not configured; timer remains disabled. Access-token-only setup needs manual renewal.'
fi
sleep 8
sudo systemctl is-active --quiet resultbot-listener.service || fail 'Collector failed; inspect its journal.'
systemctl --user is-active --quiet resultbot-excellent-watch.service || fail 'Earnings listener failed; use journalctl --user -u resultbot-excellent-watch.service.'
[[ "$(systemctl is-active trading_bot.service || true)" == inactive ]] || fail 'Original trading service must remain inactive.'
printf 'complete\n' > "$STATE"
echo 'RESULTBOT INSTALLED. Listeners start automatically and survive logout/reboot.'
echo 'AMO is OFF. Edit budget/source and enable orders using bot setup.'
echo 'Run: source ~/.bashrc   then: menu'
echo 'Status: bot status | Settings: bot setup | Live logs: bot logs'
