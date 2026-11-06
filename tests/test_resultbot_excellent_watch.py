"""Run offline: python -m unittest -v tests.test_resultbot_excellent_watch"""
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from resultbot import excellent_watch as ew


class ExcellentOnlyTests(unittest.TestCase):
    def test_only_excellent(self):
        self.assertEqual(ew.parse_alert('#ROLEXRINGS - 🏆🔥 Excellent Results'), ('ROLEXRINGS','excellent'))
        for t in ('#SIGMAADV - 🌟 Great Results','#FOO Good Results','#AAA Weak Results','#ABC Results Out'):
            self.assertEqual(ew.parse_alert(t)[1],'not_excellent')
        self.assertEqual(ew.parse_alert('#FOO #BAR - Excellent Results')[1], 'missing_or_multiple_stock_symbols')
        self.assertEqual(ew.parse_alert('#NIFTY - Excellent Results')[1], 'index_not_equity')

    def test_source_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            con=ew.setup_db(Path(d)/'db.sqlite3')
            t=datetime(2026,11,6,7,5,19,tzinfo=timezone.utc)
            self.assertEqual(ew.add_alert(con,42,t,'#ROLEXRINGS Excellent Results',12345),'wrong_channel')
            self.assertEqual(ew.add_alert(con,42,t,'#ROLEXRINGS Excellent Results',ew.CHANNEL_ID),'stored')
            self.assertEqual(ew.add_alert(con,42,t,'#ROLEXRINGS Excellent Results',ew.CHANNEL_ID),'duplicate')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM alerts').fetchone()[0],1)
            con.close()

    def test_first_post_alert_price_and_no_fake_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            con=ew.setup_db(Path(d)/'db.sqlite3')
            t=datetime(2026,11,6,7,5,30,tzinfo=timezone.utc)
            ew.add_alert(con,42,t,'#ROLEXRINGS Excellent Results')
            def bar(ts,price):
                return {'symbol':'ROLEXRINGS','ts_utc':ts,'open':price,'low':price-1,
                        'high':price+1,'close':price,'volume':100,'fetched_utc':123}
            st=int(t.timestamp())//60*60
            ew.store_bars(con,'ROLEXRINGS',[bar(st,50), bar(st+60,100), bar(st+120,101), bar(st+60*6,105)])
            r=ew.summarize(con,1)
            self.assertEqual(r['first_price'],100)
            self.assertEqual(r['return_5m_pct'],5.0)
            self.assertIsNone(r['return_15m_pct'])
            self.assertLess(r['observed_high_pct'],10)
            con.close()

    def test_no_bar_and_no_price_invention(self):
        with tempfile.TemporaryDirectory() as d:
            con=ew.setup_db(Path(d)/'db.sqlite3')
            ew.add_alert(con,42,datetime(2026,11,6,7,5,tzinfo=timezone.utc),'#FOO Excellent Results')
            r=ew.summarize(con,1)
            self.assertIsNone(r['first_price'])
            self.assertEqual(r['status'],'NO_VERIFIED_POST_ALERT_CANDLE')
            con.close()

    def test_existing_sqlite_login_read_only(self):
        # The collector should reuse the original login without prompting OTP
        # or modifying .env / the .session SQLite database.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "uditbot" / "data" / "tg_userbot.session"
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE sessions (dc_id INTEGER, server_address TEXT, "
                           "port INTEGER, auth_key BLOB)")
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?)",
                    (2, "149.154.167.51", 443, bytes(range(256)))
                )
            before = path.read_bytes()
            with patch.object(ew.Path, "home", return_value=root):
                session_string = ew.existing_sqlite_login()
            self.assertTrue(session_string.startswith("1"))
            from telethon.sessions import StringSession
            session = StringSession(session_string)
            self.assertEqual(session.dc_id, 2)
            self.assertEqual(session.port, 443)
            self.assertEqual(path.read_bytes(), before)

    def test_offline_self_test(self):
        ew.self_test()


if __name__=='__main__':
    unittest.main()
