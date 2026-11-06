"""Offline REAL Equity AMO one-shot controls: broker calls are mocked."""
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from resultbot import equity_amo_live as live
from resultbot import dhan_readonly as dh
from resultbot import amo_panel

ACCOUNT = "1105508885"
TOKEN = "T"*50
OID = "112111182198"

def fake_broker_order(correlation="RBA1-9",status="PENDING"):
    return {
       "dhanClientId":ACCOUNT,"orderId":OID,"correlationId":correlation,
       "securityId":"1333","exchangeSegment":"NSE_EQ",
       "productType":"CNC","transactionType":"BUY","orderType":"LIMIT",
       "afterMarketOrder":True,"orderStatus":status,"filledQty":0
    }

class RealEquityAmoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"live.sqlite3"
        self.patcher=patch.object(live,"STATE_DB",self.path)
        self.patcher.start()
        self.con=live.connect_db()
        self.con.close()
        self.patches=[
          patch.object(live,"trading_service_stopped",return_value=True),
          patch.object(live,"_session_closed",return_value=True),
          patch.object(dh,"load_credentials",return_value=(ACCOUNT,TOKEN)),
          patch.object(live,"_verified_broker_context",return_value=5000.0),
          patch.object(live,"_verified_instrument",return_value=("1333",__import__("decimal").Decimal("0.05"))),
          patch.object(live,"_quote_and_size",return_value=(5,20.0,int(time.time())-120)),
          patch.object(live,"_queue",return_value=None),
        ]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.patcher.stop()
        self.tmp.cleanup()

    def armed(self):
        live.arm(source="TEST",budget=500)
        self.assertTrue(live.status()["effective_on"])

    def reserved(self):
        self.armed()
        ident=live.reserve("TEST",9,"YESBANK",int(time.time()))
        self.assertIsInstance(ident,int)
        return ident

    def test_default_off_no_live_broker_request(self):
        self.assertFalse(live.status()["effective_on"])
        with patch.object(live,"_dhan_http") as broker:
            self.assertIsNone(live.reserve("TEST",10,"YESBANK",int(time.time())))
            broker.assert_not_called()

    def test_single_arm_rejects_wrong_budget_or_source(self):
        for bad in (0,99,1001,2000,-1,True,500.1):
            with self.assertRaises(live.LiveAmoError):
                live.arm(source="TEST",budget=bad)
        with self.assertRaises(live.LiveAmoError):
            live.arm(source="ANYTHING",budget=500)

    def test_arm_requires_ip_and_funds_account_preflight(self):
        with patch.object(live,"_verified_broker_context",side_effect=live.LiveAmoError("IP not whitelisted")):
            with self.assertRaises(live.LiveAmoError):
                live.arm(source="TEST",budget=500)
        self.assertFalse(live.status()["effective_on"])

    def test_no_old_alert_and_no_wrong_source(self):
        self.armed()
        self.assertIsNone(live.reserve("EARNINGS_PULSE",9,"YESBANK",int(time.time())))
        self.assertIsNone(live.reserve("TEST",9,"YESBANK",int(time.time())-120))
        self.assertIsNone(live.reserve("TEST",9,"YESBANK",int(time.time())+120))
        self.assertIsNone(live.reserve("TEST",9,"A/B",int(time.time())))
        self.assertTrue(live.status()["effective_on"])

    def test_reserved_intent_disarms_and_is_idempotent(self):
        ident=self.reserved()
        self.assertFalse(live.status()["effective_on"])
        self.assertIsNone(live.reserve("TEST",9,"YESBANK",int(time.time())))
        self.assertEqual(live._intent(ident)["state"],"RESERVED")
        with self.assertRaises(live.LiveAmoError):
            live.arm(source="TEST",budget=500)

    def test_off_before_broker_transition_blocks_post(self):
        ident=self.reserved()
        live.disarm()
        with patch.object(live,"_dhan_http") as broker:
            live.execute(ident)
            broker.assert_not_called()
        self.assertEqual(live._intent(ident)["state"],"BLOCKED")

    def test_verified_real_dhan_order_sent_once_and_id_saved(self):
        ident=self.reserved()
        with patch.object(live,"_dhan_http",return_value={"orderId":OID,"orderStatus":"PENDING"}) as broker, \
             patch.object(live,"_verify_owned_order",return_value=fake_broker_order()):
            live.execute(ident)
            live.execute(ident)
            self.assertEqual(broker.call_count,1)
            args=broker.call_args.args
            self.assertEqual(args[0],"POST")
            self.assertEqual(args[1],"/orders")
            payload=args[3]
            self.assertIs(payload["afterMarketOrder"],True)
            self.assertEqual(payload["amoTime"],"OPEN")
            self.assertEqual(payload["productType"],"CNC")
            self.assertEqual(payload["quantity"],5)
            self.assertEqual(payload["price"],20.0)
            self.assertEqual(payload["dhanClientId"],ACCOUNT)
        item=live._intent(ident)
        self.assertEqual(item["order_id"],OID)
        self.assertEqual(item["state"],"ACKED")
        self.assertFalse(live.status()["effective_on"])

    def test_timeout_goes_unknown_and_never_reposts(self):
        ident=self.reserved()
        with patch.object(live,"_dhan_http",side_effect=live.LiveAmoError("timeout")) as broker:
            live.execute(ident)
            live.execute(ident)
            self.assertEqual(broker.call_count,1)
        self.assertEqual(live._intent(ident)["state"],"UNKNOWN")

    def test_reconcile_uncertain_order_by_correlation_get_only(self):
        ident=self.reserved()
        live._update(ident,"UNKNOWN",security_id="1333",qty=5,price=20.0)
        item=live._intent(ident)
        with patch.object(live,"_order_by_correlation",
                          return_value=fake_broker_order(correlation=item["correlation"])) as get_order:
            self.assertTrue(live.reconcile_unknown(ident))
            get_order.assert_called_once()
        self.assertEqual(live._intent(ident)["order_id"],OID)
        self.assertEqual(live._intent(ident)["state"],"ACKED")

    def test_cancel_only_verified_owned_order_once(self):
        ident=self.reserved()
        live._update(ident,"ACKED",order_id=OID,security_id="1333",qty=5,price=20.0)
        item=live._intent(ident)
        with patch.object(live,"_verify_owned_order",
                          return_value=fake_broker_order(correlation=item["correlation"])), \
             patch.object(live,"_dhan_http",return_value={
                 "orderId":OID,"orderStatus":"CANCELLED"}) as cancel:
            self.assertEqual(live.request_cancel(ident),"CANCELLED")
            self.assertEqual(cancel.call_args.args[0],"DELETE")
            self.assertEqual(cancel.call_args.args[1],"/orders/"+OID)
            with self.assertRaises(live.LiveAmoError):
                live.request_cancel(ident)
        self.assertEqual(live._intent(ident)["cancel_attempted"],1)

    def test_partial_fill_cancel_remainder_is_permitted(self):
        ident=self.reserved()
        live._update(ident,"ACKED",order_id=OID,security_id="1333",qty=5,price=20.0)
        item=live._intent(ident)
        with patch.object(live,"_verify_owned_order",
                          return_value={**fake_broker_order(correlation=item["correlation"],
                                   status="PART_TRADED"),"filledQty":2}), \
             patch.object(live,"_dhan_http",return_value={
                 "orderId":OID,"orderStatus":"TRANSIT"}) as cancel:
            self.assertEqual(live.request_cancel(ident),"TRANSIT")
            cancel.assert_called_once()

    def test_live_dhan_http_is_allowlisted(self):
        with self.assertRaises(live.LiveAmoError):
            live._dhan_http("POST","/positions",TOKEN,{})
        with self.assertRaises(live.LiveAmoError):
            live._dhan_http("DELETE","/orders/abc",TOKEN)
        with self.assertRaises(live.LiveAmoError):
            live._dhan_http("PUT","/orders/"+OID,TOKEN,{})

    def test_panel_secret_file_is_private(self):
        private=Path(self.tmp.name)/"panel-key"
        result=amo_panel.read_secret(private)
        self.assertTrue(len(result)>20)
        self.assertEqual(private.stat().st_mode&0o777,0o600)
        self.assertEqual(amo_panel.read_secret(private),result)

if __name__=="__main__":
    unittest.main()
