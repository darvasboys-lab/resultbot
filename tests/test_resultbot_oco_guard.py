import importlib.util
import json
from decimal import Decimal
from pathlib import Path
import sys,tempfile,unittest
from contextlib import ExitStack
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'resultbot_runtime'))
import oco_guard as g
import dhan_readonly as dh

class OcoTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.tmp=self.stack.enter_context(tempfile.TemporaryDirectory());self.root=Path(self.tmp)
        self.stack.enter_context(patch.multiple(g,ROOT=self.root,DB=self.root/'oco.sqlite',CONFIG=self.root/'config.json'))
        (self.root/'position_strategy.json').write_text(json.dumps(dict(down_mode='STOP_ONLY',up_mode='TARGET_ONLY',stop_pct=9,target_pct=5)))
        self.item=dict(id=3,account='12345678',order_id='321261109238209',correlation='RBA-test',security_id='11915',qty=1,symbol='YESBANK',entry_route='REGULAR')
        self.order=dict(dhanClientId='12345678',orderId=self.item['order_id'],correlationId='RBA-test',securityId='11915',quantity=1,filledQty=1,averageTradedPrice=16.98,transactionType='BUY',exchangeSegment='NSE_EQ',productType='CNC',afterMarketOrder=False,orderStatus='TRADED')
        self.trade=dict(self.order,tradedQuantity=1,tradedPrice=16.98)
        self.pos=dict(dhanClientId='12345678',securityId='11915',exchangeSegment='NSE_EQ',productType='CNC',netQty=1)
        self.calls=[];self.book=[]
        def api(method,path,token,payload=None):
            if method=='POST':
                self.calls.append(payload)
                oid=str(123456789+len(self.calls))
                self.book.append(dict(payload,orderId=oid,orderStatus='CONFIRM'))
                return dict(orderId=oid,orderStatus='PENDING')
            return self.book
        self.api=self.stack.enter_context(patch.object(g,'api',side_effect=api))
        self.stack.enter_context(patch.object(g.live,'_verified_instrument',return_value=('11915',Decimal('.01'))))
    def process(self):g.process(self.item,[self.order],[self.trade],[self.pos],list(self.book),'fake')
    def rows(self):
        with g.db() as c:return [dict(r) for r in c.execute('SELECT * FROM coverage')]
    def test_actual_fill_levels_and_two_verified_legs(self):
        self.process();p=self.calls[0]
        self.assertEqual((p['triggerPrice'],p['price'],p['price1']),(15.45,15.44,17.83))
        self.assertEqual((p['quantity'],p['quantity1'],p['transactionType']),(1,1,'SELL'))
        self.assertEqual(self.rows()[0]['state'],'ACTIVE')
    def test_restart_and_duplicate_dont_resubmit(self):
        self.process();self.process();self.assertEqual(len(self.calls),1)
    def test_unknown_submission_not_retried(self):
        self.api.side_effect=g.GuardError('timeout')
        self.process();self.process()
        self.assertEqual(self.api.call_count,1);self.assertEqual(self.rows()[0]['state'],'UNKNOWN')
    def test_unknown_recovered_by_correlation(self):
        self.process()
        with g.db() as c:c.execute("UPDATE coverage SET state='UNKNOWN',order_id=''")
        self.process();self.assertEqual(self.rows()[0]['state'],'ACTIVE');self.assertEqual(len(self.calls),1)
    def test_filled_quantity_only_and_first_fill_anchor(self):
        self.item['qty']=3;self.order['quantity']=3;self.order['orderStatus']='PART_TRADED';self.process()
        self.order.update(filledQty=3,averageTradedPrice=17.32,orderStatus='TRADED')
        self.trade.update(tradedQuantity=3,tradedPrice=17.32);self.pos['netQty']=3
        self.process();self.assertEqual([p['quantity'] for p in self.calls],[1,2])
        self.assertEqual(self.calls[0]['triggerPrice'],self.calls[1]['triggerPrice'])
    def test_no_oco_before_fill(self):
        self.order['filledQty']=0
        with self.assertRaises(g.GuardError):self.process()
        self.assertFalse(self.calls)
    def test_wrong_account_or_trade_binding_rejected(self):
        self.trade['dhanClientId']='wrong'
        with self.assertRaises(g.GuardError):self.process()
        self.assertFalse(self.calls)
    def test_mismatched_trade_quantities_rejected(self):
        self.trade['tradedQuantity']=2
        with self.assertRaises(g.GuardError):self.process()
    def test_position_closed_no_new_sell(self):
        self.pos['netQty']=0
        with self.assertRaises(g.GuardError):self.process()
        self.assertFalse(self.calls)
    def test_existing_foreign_oco_no_duplicate(self):
        self.book.append(dict(securityId='11915',transactionType='SELL',orderId='999999',orderStatus='CONFIRM'))
        with self.assertRaises(g.GuardError):self.process()
    def test_missing_strategy_never_defaults(self):
        (self.root/'position_strategy.json').unlink()
        with self.assertRaises(g.GuardError):self.process()
    def test_staged_strategy_not_silently_ignored(self):
        (self.root/'position_strategy.json').write_text(json.dumps(dict(down_mode='AVERAGE',up_mode='TARGET_ONLY',stop_pct=9,target_pct=5)))
        with self.assertRaises(g.GuardError):self.process()
    def test_triggered_oco_never_recreated(self):
        self.process();self.book[0]['orderStatus']='TRIGGERED';self.process()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.rows()[0]['state'],'REVIEW')
    def test_only_one_leg_never_marked_active(self):
        self.process();self.book[0].pop('price1');self.book[0].pop('triggerPrice1');self.process()
        self.assertEqual(self.rows()[0]['state'],'UNKNOWN')
    def test_order_book_lookup_ownership_and_duplicates(self):
        with patch.object(dh,'get_json',return_value=[self.order]):
            self.assertEqual(dh.find_owned_order('12345678','fake',self.item['order_id']),self.order)
            with self.assertRaises(dh.DhanReadError):dh.find_owned_order('bad','fake',self.item['order_id'])
        with patch.object(dh,'get_json',return_value=[self.order,self.order]):
            with self.assertRaises(dh.DhanReadError):dh.find_owned_order('12345678','fake',self.item['order_id'])

if __name__=='__main__':unittest.main()
