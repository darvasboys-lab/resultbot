"""Offline actual execution-path regressions; all external effects mocked."""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'resultbot_runtime'))
import equity_amo_live as m

class RegularEntryTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.tmp=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.data=Path(self.tmp)
        self.patch('ROOT',self.data); self.patch('STATE_DB',self.data/'state.sqlite3')
        self.patch('trading_service_stopped',return_value=True)
        self.patch('_regular_session_open',return_value=True)
        self.patch('_session_closed',return_value=False)
        self.patch('_verified_broker_context',return_value=100000)
        self.patch('_verified_instrument',return_value=('123',Decimal('.05')))
        self.patch('_live_quote_and_size',return_value=(1,20.,m.time.time()))
        self.patch('_quote_and_size',return_value=(1,20.,m.time.time()))
        self.queue=self.patch('_queue')
        self.stack.enter_context(patch.object(m.dh,'load_credentials',return_value=('12345678','fake-token')))
        self.stack.enter_context(patch.object(m.dh,'verified_profile',return_value={}))
        self.stack.enter_context(patch.object(m.watch,'configured_log_group_id',return_value=10))
        self.stack.enter_context(patch.object(m.watch,'configured_test_group_id',return_value=11))
        self.post=self.patch('_dhan_http',return_value={'orderId':'123456789','orderStatus':'PENDING'})
        self.detail=self.stack.enter_context(patch.object(m.dh,'get_json',side_effect=self.order_detail))
        m.update_settings(source='BOTH',budget=25)
    def patch(self,name,*args,**kw):
        return self.stack.enter_context(patch.object(m,name,*args,**kw))
    def order_detail(self,*args):
        with m.managed_db() as con:
            ident=con.execute('SELECT MAX(id) FROM intents').fetchone()[0]
        i=m._intent(ident)
        return dict(dhanClientId=i['account'],orderId='123456789',correlationId=i['correlation'],securityId='123',exchangeSegment='NSE_EQ',productType='CNC',transactionType='BUY',orderType='LIMIT',afterMarketOrder=i['entry_route']=='AMO',orderStatus='TRADED',filledQty=1)
    def reserve(self,msg=1):
        return m.reserve('TEST',msg,'YESBANK',int(m.time.time()))
    def test_migration_preserves_legacy_amo_and_preferences(self):
        # Reconstruct the actual legacy schema, then migrate it twice.
        db=self.data/'old.sqlite3'
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE control(id INTEGER PRIMARY KEY,armed INTEGER,source TEXT,budget INTEGER,expires INTEGER,armed_since INTEGER,arm_seq INTEGER,stop_seq INTEGER)')
            c.execute("INSERT INTO control VALUES(1,0,'BOTH',25,0,0,0,3)")
            c.execute('CREATE TABLE intents(id INTEGER PRIMARY KEY, source TEXT,msg_id INTEGER)')
            c.execute("INSERT INTO intents VALUES(1,'TEST',100)")
        for _ in range(2):
            with m.managed_db(db) as c:
                ctrl=m._control(c)
                self.assertEqual((ctrl['budget'],ctrl['source'],ctrl['live_enabled']),(25,'BOTH',0))
                self.assertEqual(c.execute('SELECT entry_route FROM intents').fetchone()[0],'AMO')
    def test_default_off_does_not_reserve(self):
        self.assertIsNone(self.reserve())
    def test_regular_with_amo_off_and_immediate_fill(self):
        m.set_live(True); i=self.reserve(); self.assertIsNotNone(i)
        m.execute(i)
        payload=self.post.call_args.args[3]
        self.assertIs(payload['afterMarketOrder'],False)
        self.assertNotIn('amoTime',payload)
        self.assertEqual(payload['productType'],'CNC')
        self.assertEqual(m._intent(i)['state'],'TERMINAL')
        self.assertEqual(m.editable_settings()['per_order_budget_inr'],25)
        self.assertFalse(m.editable_settings()['amo_enabled'])
    def test_amo_still_submits_amo(self):
        with patch.object(m,'_regular_session_open',return_value=False),patch.object(m,'_session_closed',return_value=True):
            m.arm(source='BOTH',budget=25); i=self.reserve(); m.execute(i)
        payload=self.post.call_args.args[3]
        self.assertTrue(payload['afterMarketOrder']); self.assertEqual(payload['amoTime'],'OPEN')
    def test_closed_with_amo_off_no_order(self):
        m.set_live(True)
        with patch.object(m,'_regular_session_open',return_value=False):
            self.assertIsNone(self.reserve())
        self.post.assert_not_called()
    def test_amo_on_alone_does_not_authorize_regular(self):
        with m.managed_db() as c: c.execute('UPDATE control SET armed=1')
        self.assertIsNone(self.reserve())
    def test_live_off_invalidates_reserved_order(self):
        m.set_live(True); i=self.reserve(); m.set_live(False); m.execute(i)
        self.post.assert_not_called(); self.assertEqual(m._intent(i)['state'],'BLOCKED')
    def test_global_off_invalidates_reserved_order(self):
        m.set_live(True); i=self.reserve(); m.stop_all(); m.execute(i)
        self.post.assert_not_called()
    def test_amo_off_leaves_regular_enabled(self):
        m.set_live(True); m.amo_off(); i=self.reserve(); m.execute(i)
        self.post.assert_called_once()
    def test_market_boundary_never_converts_to_amo(self):
        m.set_live(True); i=self.reserve()
        with patch.object(m,'_regular_session_open',return_value=False),patch.object(m,'_session_closed',return_value=True): m.execute(i)
        self.post.assert_not_called()
    def test_duplicate_message_and_execute_not_resubmitted(self):
        m.set_live(True); i=self.reserve(); self.assertIsNone(self.reserve()); m.execute(i); m.execute(i)
        self.post.assert_called_once()
    def test_old_alert_not_replayed(self):
        m.set_live(True)
        self.assertIsNone(m.reserve('TEST',1,'YESBANK',int(m.time.time())-100))
        with m.managed_db() as c: c.execute('UPDATE control SET live_since=?',(int(m.time.time())+1,))
        self.assertIsNone(self.reserve())
    def test_budget_snapshotted(self):
        m.set_live(True); i=self.reserve(); m.update_settings(budget=1000)
        self.assertEqual(m._intent(i)['budget'],25)
    def test_quote_expiring_during_preparation_blocks(self):
        m.set_live(True); i=self.reserve()
        with patch.object(m,'_live_quote_and_size',return_value=(1,20.,m.time.time()-3)): m.execute(i)
        self.post.assert_not_called()
    def test_unknown_transport_never_resubmits(self):
        m.set_live(True); i=self.reserve(); self.post.side_effect=m.LiveAmoError('timeout')
        m.execute(i); m.execute(i)
        self.assertEqual(m._intent(i)['state'],'UNKNOWN'); self.post.assert_called_once()
    def test_reconciliation_accepts_regular_fill(self):
        m.set_live(True); i=self.reserve(); m._update(i,'UNKNOWN',security_id='123',qty=1,price=20.)
        with patch.object(m,'_order_by_correlation',return_value=self.order_detail()): self.assertTrue(m.reconcile_unknown(i))
        self.assertEqual(m._intent(i)['state'],'TERMINAL')
    def test_route_mismatch_rejected(self):
        m.set_live(True); i=self.reserve(); m._update(i,'ACKED',security_id='123',order_id='123456789')
        wrong=self.order_detail(); wrong['afterMarketOrder']=True
        self.detail.side_effect=None; self.detail.return_value=wrong
        with self.assertRaises(m.LiveAmoError): m._verify_owned_order(m._intent(i),'fake')

class QuoteAndCalendarTests(unittest.TestCase):
    def test_live_quote_contract(self):
        now=1800000000
        meta=dict(symbol='YESBANK.NS',currency='INR',regularMarketPrice=20,regularMarketTime=now-1)
        def check(data):
            response=io.BytesIO(json.dumps({'chart':{'result':[{'meta':data}]}}).encode())
            with patch.object(m,'urlopen',return_value=response),patch.object(m.time,'time',return_value=now),tempfile.TemporaryDirectory() as d,patch.object(m,'ROOT',Path(d)):
                return m._live_quote_and_size('YESBANK',25,Decimal('.05'))
        self.assertEqual(check(meta)[:2],(1,20.05))
        for key,value in [('symbol','OTHER.NS'),('currency','USD'),('regularMarketPrice','NaN'),('regularMarketTime',now-3),('regularMarketTime',now+1)]:
            with self.subTest(key=key,value=value),self.assertRaises(m.LiveAmoError): check(dict(meta,**{key:value}))
    def test_regular_calendar(self):
        # Call real helper despite the other fixture's mocked calendar.
        for date,expected in [('2026-11-09T09:14:59',False),('2026-11-09T09:15:00',True),('2026-11-09T15:29:59',True),('2026-11-09T15:30:00',False),('2026-11-10T10:00:00',False),('2026-11-08T10:00:00',False)]:
            with self.subTest(date=date),patch.object(m,'datetime') as clock:
                clock.now.return_value=datetime.fromisoformat(date).replace(tzinfo=m.IST)
                self.assertEqual(m._regular_session_open(),expected)

if __name__=='__main__': unittest.main()
