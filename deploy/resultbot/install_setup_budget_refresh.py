#!/usr/bin/env python3
"""Patch only ResultBot's saved-budget display refresh; no trading changes."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HOME = Path.home()
LIVE = HOME / "resultbot-data/amo_settings.sh"
REFERENCE = Path(__file__).resolve().parents[2] / "resultbot_runtime/amo_settings.sh"

OLD_HEADER = '''whiptail() {
    command whiptail \\
        --backtitle "RESULTBOT  |  CONTROL CENTER  |  Arrow keys: move   Enter: select   Esc: back" \\
        "$@"
}'''
OLD_MENU_START = '''select_menu() {
    if [[ -t 0 ]] && command -v whiptail >/dev/null 2>&1; then'''
OLD_MENU_PROMPT = '''--menu "Choose a setting below. Changes apply to future signals.

Existing orders remain unchanged."'''
OLD_BUDGET_TAIL = '''    echo "$outcome"
    echo "Saved. Every FUTURE eligible signal uses the new INR $desired budget."
}'''


def between(source: str, left: str, right: str) -> str:
    start = source.index(left)
    end = source.index(right, start)
    return source[start:end].rstrip("\n")


def change_one(source: str, old: str, new: str, name: str) -> str:
    if source.count(new) == 1 and source.count(old) == 0:
        return source
    if source.count(old) != 1 or source.count(new) != 0:
        raise SystemExit("STOP: cannot safely patch " + name +
                         ". VM version differs from reviewed setup script. Nothing changed.")
    return source.replace(old, new, 1)


def main() -> None:
    if os.geteuid() == 0:
        raise SystemExit("Run as the normal ResultBot user, without sudo.")
    if not LIVE.is_file() or LIVE.is_symlink() or not REFERENCE.is_file():
        raise SystemExit("STOP: installed or reference settings script missing/invalid.")
    original = LIVE.read_text()
    reviewed = REFERENCE.read_text()
    new_header = between(reviewed, "whiptail() {", "\n# END RESULTBOT RETRO THEME")
    new_menu_start = between(reviewed, "select_menu() {", "        whiptail --title")
    new_menu_prompt = between(reviewed, '--menu "SAVED PER-ORDER AMOUNT:', '" \\\n          19 84 6') + '"'
    new_budget_tail = between(reviewed, '    echo "$outcome"\n    # Verify the canonical saved amount', '\n}\n\necho "======================================================"') + '\n}'
    new_menu_start = new_menu_start.rstrip("\n")
    updated = original
    updated = change_one(updated, OLD_HEADER, new_header, "dynamic header")
    updated = change_one(updated, OLD_MENU_START, new_menu_start, "menu budget refresh")
    updated = change_one(updated, OLD_MENU_PROMPT, new_menu_prompt, "menu budget text")
    updated = change_one(updated, OLD_BUDGET_TAIL, new_budget_tail, "post-save verification")

    if updated == original:
        print("ResultBot setup budget display already updated.")
        return
    with tempfile.TemporaryDirectory(prefix="resultbot-setup-check-") as d:
        test_path = Path(d) / "amo_settings.sh"
        test_path.write_text(updated)
        subprocess.run(["bash", "-n", str(test_path)], check=True)

    backup = HOME / "resultbot-data" / ("backup-setup-ui-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(mode=0o700)
    shutil.copy2(LIVE, backup / LIVE.name)
    fd, temp = tempfile.mkstemp(dir=LIVE.parent, prefix=".amo_settings.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, LIVE.stat().st_mode & 0o777)
        os.replace(temp, LIVE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print("ResultBot per-order header/menu refresh installed.")
    print("Original settings script backed up:", backup)
    print("No trading data changed, no service restarted, no broker orders sent.")
    print("Open: bot setup")


if __name__ == "__main__":
    main()
