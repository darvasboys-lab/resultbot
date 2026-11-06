"""Run offline: python -m unittest -v tests.test_resultbot_excellent_watch"""
import io
import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
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

    def test_extremes_exclude_reference_bar(self):
        with tempfile.TemporaryDirectory() as d:
            con=ew.setup_db(Path(d)/"db.sqlite3")
            t=datetime(2026,11,6,7,5,30,tzinfo=timezone.utc)
            ew.add_alert(con,42,t,"#ROLEXRINGS Excellent Results")
            start=int(t.timestamp())//60*60+60
            bars=[
                {"symbol":"ROLEXRINGS","ts_utc":start,"open":100,"high":190,"low":10,"close":100,"volume":100,"fetched_utc":123},
                {"symbol":"ROLEXRINGS","ts_utc":start+60,"open":100,"high":105,"low":95,"close":101,"volume":100,"fetched_utc":123},
            ]
            ew.store_bars(con,"ROLEXRINGS",bars)
            summary=ew.summarize(con,1)
            self.assertEqual(summary["observed_high_pct"],5.0)
            self.assertEqual(summary["observed_low_pct"],-5.0)
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


    def test_research_log_card_never_claims_fill(self):
        dt=datetime(2026,11,6,10,0,tzinfo=timezone.utc)
        card=ew.format_excellent_log("ROLEXRINGS",int(dt.timestamp()))
        self.assertIn("EXCELLENT RESULTS", card)
        self.assertIn("#ROLEXRINGS", card)
        self.assertIn("NOT SUBMITTED", card)
        self.assertIn("execution price: N/A", card)
        self.assertNotIn("Order Executed", card)
        self.assertIn("\n",card)

    def test_live_notice_dedup_and_no_non_excellent(self):
        with tempfile.TemporaryDirectory() as d:
            con=ew.setup_db(Path(d)/"db.sqlite3")
            t=datetime(2026,11,6,10,0,tzinfo=timezone.utc)
            self.assertFalse(ew.enqueue_live_log(con,100))
            self.assertEqual(ew.add_alert(con,100,t,"#ROLEXRINGS Excellent Results"),"stored")
            self.assertTrue(ew.enqueue_live_log(con,100))
            self.assertFalse(ew.enqueue_live_log(con,100))
            self.assertEqual(ew.add_alert(con,101,t,"#GOOD Great Results"),"not_excellent")
            self.assertFalse(ew.enqueue_live_log(con,101))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM log_outbox").fetchone()[0],1)
            con.close()

    def test_notification_delivery_once_per_live_alert(self):
        with tempfile.TemporaryDirectory() as d:
            con=ew.setup_db(Path(d)/"db.sqlite3")
            t=datetime(2026,11,6,10,0,tzinfo=timezone.utc)
            ew.add_alert(con,50,t,"#FOO Excellent Results")
            ew.enqueue_live_log(con,50)
            class FakeTelegram:
                sent=[]
                async def send_message(self,destination,text,**kwargs):
                    self.sent.append((destination,text,kwargs))
                    return object()
            fake=FakeTelegram()
            self.assertEqual(asyncio.run(ew.send_queued_logs(con,fake,"verified_group")),1)
            self.assertEqual(asyncio.run(ew.send_queued_logs(con,fake,"verified_group")),0)
            self.assertEqual(len(fake.sent),1)
            self.assertIn("NOT SUBMITTED",fake.sent[0][1])
            self.assertEqual(con.execute("SELECT state FROM log_outbox").fetchone()[0],"SENT")
            con.close()

    def test_test_history_fallback_recent_only(self):
        moment=datetime(2026,11,6,16,20,0,tzinfo=timezone.utc)
        self.assertTrue(ew.recent_test_candidate(moment-timedelta(minutes=5),moment))
        self.assertTrue(ew.recent_test_candidate(moment-timedelta(minutes=14),moment))
        self.assertFalse(ew.recent_test_candidate(moment-timedelta(minutes=16),moment))
        self.assertFalse(ew.recent_test_candidate(moment+timedelta(minutes=3),moment))
        self.assertFalse(ew.recent_test_candidate(moment.replace(tzinfo=None),moment))

    def test_test_group_excellent_only_with_marker(self):
        self.assertEqual(
            ew.parse_test_alert("[RBTEST] #ROLEXRINGS - 🏆🔥 Excellent Results"),
            ("ROLEXRINGS", "excellent"))
        for invalid in (
            "#ROLEXRINGS - Excellent Results",
            "[RBTEST] #ROLEXRINGS - 🌟 Great Results",
            "[RBTEST] #ROLEXRINGS Good Results",
            "[RBTEST] #A #B Excellent Results",
        ):
            self.assertIsNone(ew.parse_test_alert(invalid)[0])

    def test_test_alert_is_never_saved_as_live_research(self):
        with tempfile.TemporaryDirectory() as d:
            con = ew.setup_db(Path(d)/"test.db")
            sent = datetime.now(timezone.utc)
            group_id = -1234567890
            msg = "[RBTEST] #ROLEXRINGS - 🏆🔥 Excellent Results"
            self.assertEqual(ew.enqueue_test_log(con,group_id,41,sent,msg),"test_queued")
            self.assertEqual(ew.enqueue_test_log(con,group_id,41,sent,msg),"duplicate")
            self.assertEqual(
                ew.enqueue_test_log(con,group_id,42,sent,"[RBTEST] #FOO - Great Results"),
                "not_excellent")
            self.assertEqual(
                ew.enqueue_test_log(con,group_id,43,sent,"#ROLEXRINGS - Excellent Results"),
                "not_marked_test")
            self.assertEqual(
                ew.enqueue_test_log(con, -int(f"100{ew.CHANNEL_ID}"),44,sent,msg),
                "invalid_test_source")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM one_minute_bars").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM log_outbox").fetchone()[0],1)
            kind, card = con.execute("SELECT kind,text_html FROM log_outbox").fetchone()
            self.assertEqual(kind,"TEST_EXCELLENT_ALERT")
            self.assertIn("NOT A REAL EARNINGS ALERT",card)
            self.assertIn("NOT SUBMITTED",card)
            self.assertIn("execution price: n/a",card.lower())
            con.close()

    def test_offline_self_test(self):
        ew.self_test()


if __name__=='__main__':
    unittest.main()
