"""Offline installer contracts; no systemd mutations, Telegram or broker calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import hashlib
import subprocess
import sys
from unittest.mock import patch
import asyncio

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fresh_resultbot", ROOT / "deploy/resultbot/configure.py")
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class ReleaseTests(unittest.TestCase):
    def test_exported_snapshot_hashes(self):
        manifest = json.loads((ROOT / "deploy/resultbot/VM_SOURCE_SHA256.json").read_text())
        self.assertEqual(len(manifest), 27)
        for path, digest in manifest.items():
            self.assertEqual(hashlib.sha256((ROOT / "vm_snapshot" / path).read_bytes()).hexdigest(), digest, path)

    def test_live_modules_preserved(self):
        snapshot = ROOT / "vm_snapshot/home/amansingh111111111/resultbot-data"
        for p in snapshot.iterdir():
            if p.name in ("equity_amo_live.py", "dhan_readonly.py"):
                continue  # Regular-session execution supersedes this captured module.
            self.assertEqual((ROOT / "resultbot_runtime" / p.name).read_bytes(), p.read_bytes(), p.name)

    def test_shell_syntax(self):
        paths = list((ROOT / "deploy/resultbot").glob("*.sh")) + list((ROOT / "resultbot_runtime").glob("*.sh"))
        paths.append(ROOT / "deploy/resultbot/bot")
        for path in paths:
            subprocess.run(["bash", "-n", str(path)], check=True)

    def test_credentials_private_and_roundtrip(self):
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            data = {"TG_API_HASH": "example-value", "TG_PHONE": "+1234567890"}
            wizard.private_write(path, wizard.env_text(data))
            self.assertEqual(dict(dotenv_values(path)), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            wizard.private_write(path, wizard.env_text({"TG_API_HASH": "replacement"}))
            self.assertEqual(dotenv_values(path)["TG_API_HASH"], "replacement")

    def test_credentials_refuse_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "original"
            target.write_text("unchanged")
            link = Path(d) / "link"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                wizard.private_write(link, "new")
            self.assertEqual(target.read_text(), "unchanged")

    def test_install_does_not_enable_legacy_trading_or_arm(self):
        script = (ROOT / "deploy/resultbot/install.sh").read_text()
        self.assertIn('gpg-agent whiptail', script)
        self.assertIn('systemctl --user enable --now resultbot-excellent-watch.service', script)
        self.assertNotIn('enable --now trading_bot.service', script)
        self.assertNotIn('"$D/equity_amo_live.py" arm', script)
        self.assertIn('"$D/equity_amo_live.py" off', script)
        self.assertIn('configured', script)

    def test_log_shortcuts_target_execution_listener(self):
        text = (ROOT / "deploy/resultbot/menu.sh").read_text()
        self.assertEqual(text.count('journalctl --user -u resultbot-excellent-watch.service'), 3)
        self.assertNotIn('sudo journalctl', text)

    def test_service_templates_portable(self):
        for p in (ROOT / "deploy/resultbot").glob("*.service*"):
            self.assertNotIn('amansingh111111111', p.read_text())
            self.assertNotIn('Inline values omitted', p.read_text())

    def test_fresh_wizard_with_mocked_external_services(self):
        sys.path.insert(0, str(ROOT / 'resultbot_runtime'))
        import dhan_readonly as dh
        import equity_amo_live as amo
        import excellent_watch as watch
        original_store, original_load = dh.store_credentials, dh.load_credentials
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def start(self, **kwargs): pass
            async def get_dialogs(self): return []
            async def get_entity(self, value):
                return type('Entity', (), {'id': watch.CHANNEL_ID})()
            async def disconnect(self): pass
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / 'uditbot/data').mkdir(parents=True)
            data = home / 'resultbot-data'
            data.mkdir()
            creds = data / '.dhan_readonly.json'
            inputs = ['12345', '+1234567890', '-123456', '-123457', '12345678']
            secrets = ['a' * 32, 'x' * 40, '']
            with patch('pathlib.Path.home', return_value=home), \
                 patch('builtins.input', side_effect=inputs), \
                 patch('getpass.getpass', side_effect=secrets), \
                 patch('telethon.TelegramClient', Client), \
                 patch.object(dh, 'verified_profile', return_value={}) as verify, \
                 patch.object(dh, 'store_credentials', side_effect=lambda a,t: original_store(a,t,creds)), \
                 patch.object(dh, 'load_credentials', side_effect=lambda: original_load(creds)), \
                 patch.object(amo, 'ROOT', data), \
                 patch.object(amo, 'STATE_DB', data / 'equity_amo_live.sqlite3'):
                asyncio.run(wizard.configure())
                self.assertEqual(verify.call_count, 2)
                self.assertEqual(creds.stat().st_mode & 0o777, 0o600)
                saved = amo.editable_settings()
                self.assertEqual(saved['source'], 'TEST')
                self.assertEqual(saved['per_order_budget_inr'], 500)
                with amo.managed_db() as con:
                    self.assertEqual(con.execute('SELECT armed FROM control').fetchone()[0], 0)
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM intents').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
