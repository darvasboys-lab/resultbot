#!/usr/bin/env python3
"""Surgically update only bot status, preserving all other ResultBot commands."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

HOME = Path.home()
CURRENT = HOME / ".local/bin/bot"
SCRIPT = HOME / ".local/share/resultbot/status_dashboard.py"
SOURCE = Path(__file__).with_name("status_dashboard.py")
DATA = HOME / "resultbot-data"


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    if os.geteuid() == 0:
        raise SystemExit("Run as the ResultBot VM user, without sudo.")
    if not CURRENT.is_file() or CURRENT.is_symlink():
        raise SystemExit("STOP: expected ~/.local/bin/bot not found as a regular file. Nothing changed.")
    if not SOURCE.is_file():
        raise SystemExit("STOP: dashboard source missing. Nothing changed.")
    original = CURRENT.read_text()
    pattern = re.compile(r"(?ms)^  status\)\s*\n.*?^    ;;[ \t]*$")
    found = list(pattern.finditer(original))
    if len(found) == 0 and '  status|sttaus)' in original and 'status_dashboard.py' in original:
        print("Already installed: bot status and bot sttaus invoke the dashboard.")
        return
    if len(found) != 1:
        raise SystemExit("STOP: cannot locate exactly one existing status command. Nothing changed.")
    new_case = '  status|sttaus)\n    exec "$PY" "$HOME/.local/share/resultbot/status_dashboard.py"\n    ;;'
    updated = original[:found[0].start()] + new_case + original[found[0].end():]
    compile(SOURCE.read_bytes(), str(SOURCE), "exec")
    with tempfile.TemporaryDirectory(prefix="resultbot-status-test-") as d:
        dry = Path(d) / "bot"
        dry.write_text(updated)
        subprocess.run(["bash", "-n", str(dry)], check=True)
    backup = DATA / ("backup-status-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    shutil.copy2(CURRENT, backup / "bot")
    if SCRIPT.exists():
        shutil.copy2(SCRIPT, backup / "status_dashboard.py")
    SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(SCRIPT, SOURCE.read_bytes(), 0o600)
    atomic_write(CURRENT, updated.encode(), CURRENT.stat().st_mode & 0o777)
    print("ResultBot dashboard installed. Original bot saved at:", backup)
    print("All non-status bot commands are preserved. No services restarted, no orders submitted.")
    print("Run: bot status (or bot sttaus)")


if __name__ == "__main__":
    main()
