#!/usr/bin/env python3
"""Install read-only source-name display labels into existing ResultBot setup UI."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

LIVE = Path.home() / "resultbot-data/amo_settings.sh"
ROOT = Path.home() / "resultbot-data"

HELPER = '''source_display_name() {
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

'''

REPLACEMENTS = [
    ('          "1" "ALERT SOURCE     Choose which Telegram groups to follow" \\',
     '          "1" "ALERT SOURCES    $display_sources" \\'),
    ('        echo "1) Edit alert source" >&2',
     '        echo "1) Alert sources: $display_sources" >&2'),
    ('            "BOTH" "Both authorized sources" "$c" \\',
     '            "BOTH" "Earnings Pulse + Owner Test Group" "$c" \\'),
    ('        echo "3) BOTH sources"',
     '        echo "3) BOTH (Earnings Pulse + Owner Test Group)"'),
]


def swap_one(text: str, old: str, new: str) -> str:
    if text.count(old) != 1 or text.count(new) != 0:
        raise ValueError("Expected one unmodified source-label UI field: " + old[:55])
    return text.replace(old, new, 1)


def main():
    if os.geteuid() == 0:
        raise SystemExit("Run as the ResultBot VM owner, without sudo.")
    if not LIVE.is_file() or LIVE.is_symlink():
        raise SystemExit("STOP: existing setup script not found as a regular file.")
    original = LIVE.read_text()
    if "source_display_name() {" in original and '"ALERT SOURCES    $display_sources"' in original:
        print("Source-name UI already installed.")
        return
    if original.count("select_menu() {\n") != 1:
        raise SystemExit("STOP: unknown settings menu layout, no changes made.")
    result = original.replace("select_menu() {\n",
                              HELPER + 'select_menu() {\n    local display_sources\n    display_sources="$(source_display_name)"\n', 1)
    try:
        for old, new in REPLACEMENTS:
            result = swap_one(result, old, new)
    except ValueError as exc:
        raise SystemExit("STOP: " + str(exc) + "; no changes made.") from None
    with tempfile.TemporaryDirectory(prefix="resultbot-source-ui-check-") as tmp:
        script = Path(tmp) / "amo_settings.sh"
        script.write_text(result)
        subprocess.run(["bash", "-n", str(script)], check=True)
    backup = ROOT / ("backup-source-labels-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(mode=0o700)
    shutil.copy2(LIVE, backup / "amo_settings.sh")
    fd, name = tempfile.mkstemp(dir=LIVE.parent, prefix=".amo_settings.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(result)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(name, LIVE.stat().st_mode & 0o777)
        os.replace(name, LIVE)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print("ResultBot setup now shows named Telegram alert sources.")
    print("Backup:", backup)
    print("No trading logic, broker orders, or OCO state changed.")
    print("Open: bot setup")


if __name__ == "__main__":
    main()
