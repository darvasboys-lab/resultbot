"""Exercise VM updater with a temporary home, real local Python, fake systemd."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('regular_installer',ROOT/'deploy/resultbot/install_regular_entry.py')
u=importlib.util.module_from_spec(spec); spec.loader.exec_module(u)

class InstallerTests(unittest.TestCase):
    def exercise(self,fail=False):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); data=home/'resultbot-data'; data.mkdir()
            bot=home/'bot'; bot.write_text('#!/usr/bin/env bash\necho original\n');bot.chmod(0o700)
            (home/'uditbot').symlink_to(ROOT,target_is_directory=True)
            for name in ['dhan_readonly.py','excellent_watch.py']:
                (data/name).write_bytes((ROOT/'resultbot_runtime'/name).read_bytes())
            target=data/'equity_amo_live.py'
            original=(ROOT/'vm_snapshot/home/amansingh111111111/resultbot-data/equity_amo_live.py').read_bytes()
            target.write_bytes(original)
            # A pre-existing state DB with the user's saved preferences.
            initialize="import sys;sys.path.insert(0,sys.argv[1]);import equity_amo_live as m;c=m.connect_db();c.execute(\"UPDATE control SET source='BOTH',budget=25\");c.close()"
            env=dict(os.environ,HOME=str(home))
            subprocess.run([sys.executable,'-c',initialize,str(data)],env=env,check=True)
            calls=[]
            def run(*args,check=True):
                if args[0]=='systemctl':
                    calls.append(args)
                    return subprocess.CompletedProcess(args,0,stdout='active\n',stderr='')
                if fail and args[-1]=='self-test':
                    raise subprocess.CalledProcessError(1,args,stderr='simulated validation failure')
                return subprocess.run(args,check=check,capture_output=True,text=True,env=env)
            with patch.multiple(u,HOME=home,DATA=data,TARGET=target,PY=Path(sys.executable),BOT=bot),patch.object(u,'run',side_effect=run),patch.object(u.os,'geteuid',return_value=1000):
                if fail:
                    with self.assertRaises(subprocess.CalledProcessError): u.main()
                    self.assertEqual(target.read_bytes(),original)
                    self.assertEqual(bot.read_text(),'#!/usr/bin/env bash\necho original\n')
                else:
                    u.main()
                    self.assertEqual(target.read_bytes(),u.SOURCE.read_bytes())
                    self.assertIn('action=stop-all',bot.read_text())
                    subprocess.run(['bash','-n',str(bot)],check=True)
                    with sqlite3.connect(data/'equity_amo_live.sqlite3') as c:
                        self.assertEqual(c.execute('SELECT budget,source,armed,live_enabled FROM control').fetchone(),(25,'BOTH',0,0))
                self.assertEqual([c[2] for c in calls],['is-active','stop','start'])
                self.assertTrue(list(data.glob('backup-regular-entry-*/equity_amo_live.sqlite3')))
    def test_success_preserves_settings_and_adds_commands(self): self.exercise()
    def test_validation_failure_restores_scripts(self): self.exercise(fail=True)
