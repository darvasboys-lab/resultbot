import hashlib,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oco_install',ROOT/'deploy/resultbot/install_oco_guard.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)

class OcoInstallerTests(unittest.TestCase):
    def test_install_preserves_strategy_and_never_enables_guard(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);data=home/'resultbot-data';data.mkdir()
            (home/'uditbot').symlink_to(ROOT,target_is_directory=True)
            # Reinstall current reviewed versions is supported without hash overrides.
            for n in ['equity_amo_live.py','dhan_readonly.py','excellent_watch.py']:
                (data/n).write_bytes((ROOT/'resultbot_runtime'/n).read_bytes())
            strategy=json.dumps(dict(down_mode='STOP_ONLY',up_mode='TARGET_ONLY',stop_pct=9,target_pct=5))
            (data/'position_strategy.json').write_text(strategy)
            calls=[];real_run=subprocess.run
            def run(args,**kw):
                if args[0]=='systemctl':
                    calls.append(args)
                    return subprocess.CompletedProcess(args,0,stdout='inactive\n',stderr='')
                return real_run(args,env=dict(os.environ,HOME=str(home)),**kw)
            with patch.multiple(u,HOME=home,DATA=data,PY=Path(sys.executable)),patch.object(u.os,'geteuid',return_value=1000),patch.object(u.subprocess,'run',side_effect=run):
                u.main()
            self.assertEqual((data/'position_strategy.json').read_text(),strategy)
            self.assertFalse((data/'oco_guard.json').exists())
            self.assertFalse((data/'oco_guard.sqlite3').exists())
            self.assertTrue((home/'.config/systemd/user/resultbot-oco-guard.service').exists())
            self.assertFalse(any('enable' in c or 'start' in c for c in calls))
            self.assertTrue(list(data.glob('backup-oco-*/equity_amo_live.py')))

if __name__=='__main__':unittest.main()
