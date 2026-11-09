#!/usr/bin/env python3
"""Install protection watcher without enabling it or placing orders."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
ROOT=Path(__file__).resolve().parents[2]
HOME=Path.home(); DATA=HOME/'resultbot-data';PY=HOME/'uditbot/venv/bin/python'
EXPECTED_LIVE='8bb08295f8c00f96469ae19afb7cb10e6c248af0814280f0c9c1b5ef4c80a1fe'
EXPECTED_READONLY='f7f509fa4d0fc32a5708a3f8869c10da5b5c1824a395133b460533a6d9b29e39'

def run(*args): subprocess.run(args,check=True)
def atomic(path,data,mode=0o600):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    os.umask(0o077)
    if os.geteuid()==0:raise SystemExit('Run as the ResultBot VM owner, without sudo.')
    originals={}
    for name,digest in [('equity_amo_live.py',EXPECTED_LIVE),('dhan_readonly.py',EXPECTED_READONLY)]:
        dest=DATA/name;incoming=(ROOT/'resultbot_runtime'/name).read_bytes()
        if dest.is_symlink():raise SystemExit('Unexpected runtime path')
        current=dest.read_bytes();originals[name]=current
        if hashlib.sha256(current).hexdigest() not in (digest,hashlib.sha256(incoming).hexdigest()):
            raise SystemExit('Runtime differs from reviewed version: '+name+'; no changes made.')
    if not (DATA/'position_strategy.json').is_file():raise SystemExit('Saved strategy missing')
    # Do not silently apply a different strategy than the one this integration supports.
    import json
    strategy=json.loads((DATA/'position_strategy.json').read_text())
    if strategy.get('down_mode')!='STOP_ONLY' or strategy.get('up_mode')!='TARGET_ONLY':
        raise SystemExit('This watcher supports your STOP_ONLY / TARGET_ONLY configuration only.')
    incoming={n:(ROOT/'resultbot_runtime'/n).read_bytes() for n in ['equity_amo_live.py','dhan_readonly.py','oco_guard.py']}
    for n,b in incoming.items():compile(b,n,'exec')
    run(str(PY),'-c',"import sys;sys.path.insert(0,sys.argv[1]);from src.broker.dhan_client import verify_forever_oco_group",str(HOME/'uditbot'))
    service=HOME/'.config/systemd/user/resultbot-oco-guard.service'
    service.parent.mkdir(parents=True,exist_ok=True)
    if service.exists():originals['service']=service.read_bytes()
    if (DATA/'oco_guard.py').exists():originals['oco_guard.py']=(DATA/'oco_guard.py').read_bytes()
    backup=DATA/('backup-oco-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    for n,b in originals.items():(backup/n).write_bytes(b)
    listener='resultbot-excellent-watch.service'
    active=subprocess.run(['systemctl','--user','is-active',listener],capture_output=True,text=True).stdout.strip()=='active'
    guard_active=subprocess.run(['systemctl','--user','is-active','resultbot-oco-guard.service'],capture_output=True,text=True).stdout.strip()=='active'
    if guard_active:run('systemctl','--user','stop','resultbot-oco-guard.service')
    run('systemctl','--user','stop',listener)
    try:
        for n,b in incoming.items():atomic(DATA/n,b)
        run(str(PY),'-c',"import sys;sys.path.insert(0,sys.argv[1]);import oco_guard;oco_guard.strategy();print('Saved OCO strategy validated')",str(DATA))
        run(str(PY),'-c',"""import sys
sys.path.insert(0,sys.argv[1])
import equity_amo_live as m
with m.managed_db() as c:
 c.execute("UPDATE intents SET state='UNKNOWN',note='Submission interrupted by protection update; reconcile, never retry' WHERE state='SUBMITTING'")
 c.execute("UPDATE intents SET state='BLOCKED',note='Pre-submit work interrupted by protection update; no replay' WHERE state IN ('RESERVED','PREPARING')")
""",str(DATA))
        atomic(service,b'''[Unit]
Description=ResultBot confirmed-fill SELL OCO protection
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/resultbot-data
ExecStart=%h/uditbot/venv/bin/python -u %h/resultbot-data/oco_guard.py run
Restart=on-failure
RestartSec=3
UMask=0077

[Install]
WantedBy=default.target
''')
        run('systemctl','--user','daemon-reload')
    except BaseException:
        for n in incoming:
            if n in originals:atomic(DATA/n,originals[n])
            elif (DATA/n).exists():(DATA/n).unlink()
        if 'service' in originals:atomic(service,originals['service'])
        elif service.exists():service.unlink()
        run('systemctl','--user','daemon-reload')
        raise
    finally:
        if active:run('systemctl','--user','start',listener)
        if guard_active:run('systemctl','--user','start','resultbot-oco-guard.service')
    print('OCO watcher installed; backup: '+str(backup))
    print('No broker orders submitted by installer. Activation is separate.')

if __name__=='__main__':main()
