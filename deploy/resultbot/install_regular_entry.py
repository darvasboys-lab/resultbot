#!/usr/bin/env python3
"""Install the reviewed regular-entry module; never enable trading or submit orders."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time

REPO=Path(__file__).resolve().parents[2]
HOME=Path.home()
DATA=HOME/'resultbot-data'
TARGET=DATA/'equity_amo_live.py'
SOURCE=REPO/'resultbot_runtime/equity_amo_live.py'
PY=HOME/'uditbot/venv/bin/python'
BOT=HOME/'.local/bin/bot'
SERVICE='resultbot-excellent-watch.service'
# Filled from the exact module uploaded by the owner.
BASE_SHA='0668b1cb0f2e3aa465c9b0aace5b65dc5dbda86ea24100be122b15b2241c8f30'
MARKER='# RESULTBOT_REGULAR_ENTRY_COMMANDS_V1'
COMMANDS='''# RESULTBOT_REGULAR_ENTRY_COMMANDS_V1
case "${1:-}" in
  live-on|live-off|amo-off|stop)
    action="$1"
    [[ "$action" == stop ]] && action=stop-all
    exec "$HOME/uditbot/venv/bin/python" "$HOME/resultbot-data/equity_amo_live.py" "$action"
    ;;
esac
'''

def run(*args,check=True):
    return subprocess.run(args,check=check,text=True,capture_output=True)

def atomic(path,content,mode):
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.chmod(name,mode)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def main():
    os.umask(0o077)
    if os.geteuid()==0:
        raise SystemExit('Run as the normal ResultBot user, without sudo.')
    for p in (TARGET,PY,BOT,SOURCE):
        if not p.is_file() or (p != PY and p.is_symlink()):
            raise SystemExit('Missing or unexpected path: '+str(p))
    original=TARGET.read_bytes(); replacement=SOURCE.read_bytes()
    oldsha=hashlib.sha256(original).hexdigest()
    if oldsha not in (BASE_SHA,hashlib.sha256(replacement).hexdigest()):
        raise SystemExit('Current order module differs from the reviewed upload. No files changed. Send the current equity_amo_live.py before applying this version.')
    compile(replacement,str(SOURCE),'exec')
    # Ensure the market calendar code is available without modifying that repo.
    run(str(PY),'-c',"import sys;sys.path.insert(0,sys.argv[1]);from src.engine.market import market_status,MARKET_CLOSE",str(HOME/'uditbot'))
    bot_old=BOT.read_bytes(); bot_text=bot_old.decode()
    if not bot_text.startswith('#!') or '\n' not in bot_text:
        raise SystemExit('Unexpected bot launcher format; no files changed.')
    if MARKER not in bot_text:
        first,rest=bot_text.split('\n',1)
        bot_new=(first+'\n'+COMMANDS+rest).encode()
    else:
        bot_new=bot_old
    backup=DATA/('backup-regular-entry-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
    backup.mkdir(mode=0o700)
    (backup/'equity_amo_live.py').write_bytes(original)
    (backup/'bot').write_bytes(bot_old)
    active=run('systemctl','--user','is-active',SERVICE,check=False).stdout.strip()=='active'
    run('systemctl','--user','stop',SERVICE)
    try:
        db=DATA/'equity_amo_live.sqlite3'
        if db.exists():
            if db.is_symlink(): raise RuntimeError('Refusing symlinked state database')
            with sqlite3.connect(db) as src,sqlite3.connect(backup/db.name) as dest:
                src.backup(dest)
        atomic(TARGET,replacement,TARGET.stat().st_mode & 0o777)
        atomic(BOT,bot_new,BOT.stat().st_mode & 0o777)
        run('bash','-n',str(BOT))
        run(str(PY),str(TARGET),'self-test')
        # The listener is stopped: incomplete submissions must be reconciled,
        # and pre-submit work must never replay after this code replacement.
        recovery=run(str(PY),'-c',"""import sys
sys.path.insert(0,sys.argv[1])
import equity_amo_live as m
with m.managed_db() as c:
 c.execute("UPDATE intents SET state='UNKNOWN',note='Interrupted submission; reconcile by correlation, never retry' WHERE state='SUBMITTING'")
 c.execute("UPDATE intents SET state='BLOCKED',note='Pre-submit work interrupted by update; no replay' WHERE state IN ('RESERVED','PREPARING')")
print(m.status_short())
""",str(DATA))
        print(recovery.stdout.strip())
    except BaseException:
        atomic(TARGET,original,TARGET.stat().st_mode & 0o777)
        atomic(BOT,bot_old,BOT.stat().st_mode & 0o777)
        print('Installation failed; previous scripts restored. Backup: '+str(backup))
        raise
    finally:
        if active: run('systemctl','--user','start',SERVICE)
    print('Installed regular-entry fix. Backup: '+str(backup))
    print('Saved budget, sources and AMO switch preserved. LIVE is OFF on first upgrade.')
    print('Commands: bot live-on | bot live-off | bot amo-off | bot stop | bot status')
    print('This installer did not enable trading or submit an order.')

if __name__=='__main__':
    try: main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit('Update failed: '+(exc.stderr or exc.stdout or str(exc)))
