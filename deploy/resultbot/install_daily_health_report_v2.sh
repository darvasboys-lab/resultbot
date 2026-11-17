#!/usr/bin/env bash
# Run from a shell on the ResultBot VM as the normal ResultBot user (not root).
set -euo pipefail
umask 077

[[ "$(hostname)" == "resultbot" && "$EUID" -ne 0 ]] || {
  echo 'STOP: Must run as ordinary user on the resultbot VM.' >&2; exit 1;
}

ROOT="$HOME/resultbot-data"
PY="$HOME/uditbot/venv/bin/python"
UNITS="$HOME/.config/systemd/user"
DAILY_SERVICE="resultbot-daily-health.service"
DAILY_TIMER="resultbot-daily-health.timer"
REFRESH_TIMER="resultbot-dhan-token-refresh.timer"
DEST="$ROOT/daily_health_report_v2.py"
DROP_DIR="$UNITS/$DAILY_SERVICE.d"
DROP="$DROP_DIR/80-visual-health.conf"
SOURCE_COMMIT='3907115df85fc5f66898340a4a674f9092719c4b'
SOURCE_SHA256='75184713cc11933d94183cb84e880b1b1806ef5ea33470663530e84fe16870f5'

[[ -x "$PY" && -d "$ROOT" && -d "$UNITS" ]] || {
  echo 'STOP: ResultBot runtime not found.' >&2; exit 1;
}
[[ -f "$ROOT/daily_health_notification.py" && ! -L "$ROOT/daily_health_notification.py" ]] || {
  echo 'STOP: Existing daily health module missing or unsafe.' >&2; exit 1;
}
[[ -f "$ROOT/.telegram_remote.json" && ! -L "$ROOT/.telegram_remote.json" ]] || {
  echo 'STOP: Existing Telegram pairing missing.' >&2; exit 1;
}
systemctl --user cat "$DAILY_SERVICE" >/dev/null
systemctl --user cat "$DAILY_TIMER" | grep -Fq '08:05:00 Asia/Kolkata' || {
  echo 'STOP: Existing daily-report timer is not at 08:05 IST.' >&2; exit 1;
}
systemctl --user cat "$REFRESH_TIMER" | grep -Fq '20:00:00 Asia/Kolkata' || {
  echo 'STOP: Existing refresh timer is not at 20:00 IST.' >&2; exit 1;
}
[[ "$(systemctl --user is-active "$DAILY_TIMER")" == active ]] || {
  echo 'STOP: Daily report timer is inactive; investigate before deployment.' >&2; exit 1;
}
"$PY" - "$ROOT/daily_health_notification.py" <<'PY_CHECK'
import ast, sys
from pathlib import Path
src=Path(sys.argv[1]).read_text()
funcs={x.name for x in ast.parse(src).body if isinstance(x,(ast.FunctionDef, ast.AsyncFunctionDef))}
if not {'broker','send'}.issubset(funcs):
    raise SystemExit('STOP: Existing broker() and send() are not compatible')
print('Existing verified broker() and Telegram send() found.')
PY_CHECK

TMP="$(mktemp "$ROOT/.daily-health-v2.XXXXXXXX.py")"
trap 'rm -f -- "$TMP"' EXIT
URL="https://raw.githubusercontent.com/darvasboys-lab/resultbot/$SOURCE_COMMIT/deploy/resultbot/daily_health_report_v2.py"
curl --fail --silent --show-error --location --retry 2 --proto '=https' --tlsv1.2 \
  "$URL" --output "$TMP"
printf '%s  %s\n' "$SOURCE_SHA256" "$TMP" | sha256sum --check --status || {
  echo 'STOP: Source checksum verification failed.' >&2; exit 1;
}
"$PY" -m py_compile "$TMP"

echo '===== NEW REPORT PREVIEW (NOT SENT) ====='
"$PY" "$TMP" --preview

echo '===== INSTALL REPORT-ONLY OVERRIDE ====='
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -f "$DEST" ]]; then
  cp -p -- "$DEST" "$DEST.backup-$STAMP"
fi
mkdir -p "$DROP_DIR"
if [[ -f "$DROP" ]]; then
  cp -p -- "$DROP" "$DROP.backup-$STAMP"
fi
install -m 700 -- "$TMP" "$DEST"
TMP_DROP="$(mktemp "$DROP_DIR/.80-visual-health.XXXXXXXX")"
cat > "$TMP_DROP" <<EOF
[Service]
ExecStart=
ExecStart=$PY $DEST --send
EOF
chmod 600 "$TMP_DROP"
mv -f -- "$TMP_DROP" "$DROP"
systemctl --user daemon-reload

systemctl --user show "$DAILY_SERVICE" -p ExecStart --no-pager | grep -F "$DEST" >/dev/null || {
  echo 'STOP: New service override not active; examine the drop-in.' >&2; exit 1;
}
echo '===== SCHEDULE VERIFICATION ====='
systemctl --user list-timers --all --no-pager | grep -E 'resultbot-(daily-health|dhan-token-refresh).timer' || true

echo '===== SEND ONE REPORT TO EXISTING TELEGRAM GROUP ====='
systemctl --user start "$DAILY_SERVICE"

journalctl --user -u "$DAILY_SERVICE" -n 8 -o short-iso --no-pager
printf '\nINSTALLED: Daily health report v2; 08:05 timer retained.\n'
printf 'Previous report script and hourly monitor were NOT changed.\n'
printf 'Rollback: remove %s and run systemctl --user daemon-reload\n' "$DROP"
