#!/usr/bin/env python3
"""Surgical ResultBot manual-exit OCO cleanup installer (working VM safe)."""
from __future__ import annotations
from datetime import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

HOME = Path.home()
ROOT = HOME / "resultbot-data"
LIVE = ROOT / "oco_guard.py"
MODULE = ROOT / "oco_manual_exit.py"
SRC = Path(__file__).resolve().parents[2] / "resultbot_runtime" / "oco_guard.py"
SRC_MOD = Path(__file__).with_name("oco_manual_exit.py")

MODS = [
(
"""    allowed={'GET':{'/orders','/trades','/positions','/forever/orders'},'POST':{'/forever/orders'}}
    if path not in allowed.get(method,set()): raise GuardError('Unsupported OCO endpoint')""",
"""    allowed={'GET':{'/orders','/trades','/positions','/holdings','/forever/orders'},'POST':{'/forever/orders'}}
    if not (path in allowed.get(method,set()) or
            (method=='DELETE' and re.fullmatch(r'/forever/orders/[0-9]{6,25}',path))):
        raise GuardError('Unsupported OCO endpoint')"""
),
(
"""    if method=='POST' and not isinstance(result,dict): raise GuardError('Uncertain OCO response')""",
"""    if method in ('POST','DELETE') and not isinstance(result,dict):
        raise GuardError('Uncertain OCO response')"""
),
(
"""        for row in previous:
            if row['state']!='REVIEW': reconcile(c,row,forever)""",
"""        for row in previous:
            if row['state'] not in ('REVIEW','CLOSED','CANCEL_REQUESTED'):
                reconcile(c,row,forever)"""
),
(
"""        with db() as c:
            for r in records:
                if r['state']!='REVIEW': reconcile(c,r,forever)
        if not candidates:return
        trades=api('GET','/trades',token);positions=api('GET','/positions',token)
        for item in candidates:
            try:
                process(item,orders,trades,positions,forever,token)""",
"""        with db() as c:
            for r in records:
                if r['state'] not in ('REVIEW','CLOSED','CANCEL_REQUESTED'):
                    reconcile(c,r,forever)
        # Manual-exit reconciliation operates only on verified ResultBot-owned OCOs.
        # The cleanup module throttles additional broker GET requests to 30s.
        try:
            import oco_manual_exit
            oco_manual_exit.cycle(sys.modules[__name__],account,token,orders,forever)
        except (GuardError,dh.DhanReadError,ValueError,OSError,ImportError) as exc:
            LOG.warning('OCO manual-exit cleanup deferred: %s',type(exc).__name__)
        if not candidates:return
        trades=api('GET','/trades',token);positions=api('GET','/positions',token)
        for item in candidates:
            # A historical filled BUY with no remaining intraday exposure must
            # never create a fresh OCO after a manually completed square-off.
            if not any(
                str(p.get('securityId'))==str(item['security_id']) and
                p.get('exchangeSegment')=='NSE_EQ' and p.get('productType')=='CNC' and
                str(p.get('dhanClientId'))==account and
                number(p.get('netQty',0))>0 for p in positions
            ):
                continue
            try:
                process(item,orders,trades,positions,forever,token)"""
),
]


def atomic(path: Path, data: bytes, mode: int):
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    if os.geteuid() == 0:
        raise SystemExit("STOP: Run as the ResultBot VM user, without sudo.")
    if not LIVE.is_file() or LIVE.is_symlink() or not SRC.is_file() or not SRC_MOD.is_file():
        raise SystemExit("STOP: Guard or installer sources missing; no changes.")
    current = LIVE.read_text()
    changed = current
    for old, new in MODS:
        if changed.count(new) == 1 and changed.count(old) == 0:
            continue
        if changed.count(old) != 1 or changed.count(new):
            raise SystemExit("STOP: OCO Guard differs from reviewed source. No changes.")
        changed = changed.replace(old, new)
    module_bytes = SRC_MOD.read_bytes()
    compile(changed, str(LIVE), "exec")
    compile(module_bytes, str(MODULE), "exec")

    with tempfile.TemporaryDirectory(prefix="resultbot-oco-test-") as test:
        test_file = Path(test) / "oco_manual_exit.py"
        test_file.write_bytes(module_bytes)
        subprocess.run([str(HOME / "uditbot/venv/bin/python"), str(test_file)],
                       check=True)

    if changed == current and MODULE.is_file() and MODULE.read_bytes() == module_bytes:
        print("OCO manual-exit cleanup already up to date.")
        return

    backup = ROOT / ("backup-manual-oco-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(mode=0o700)
    shutil.copy2(LIVE, backup / "oco_guard.py")
    if MODULE.is_file():
        shutil.copy2(MODULE, backup / "oco_manual_exit.py")

    db = ROOT / "oco_guard.sqlite3"
    if db.is_file():
        with sqlite3.connect(str(db)) as src, sqlite3.connect(str(backup / "oco_guard.sqlite3")) as dst:
            src.backup(dst)

    atomic(MODULE, module_bytes, 0o700)
    if changed != current:
        atomic(LIVE, changed.encode(), LIVE.stat().st_mode & 0o777)

    state = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "resultbot-oco-guard.service"],
        check=False,
    )
    if state.returncode == 0:
        subprocess.run(["systemctl", "--user", "restart", "resultbot-oco-guard.service"],
                       check=True)
        print("OCO Guard service restarted with manual-exit cleanup.")
    else:
        print("OCO Guard service was inactive; left unchanged (not started).")
    print("Backup:", backup)
    print("Saved settings, BUY execution, and existing broker orders were not changed by installation.")
    print("Live broker reconciliation happens on the guard's next scan (no forced reset).")


if __name__ == "__main__":
    main()
