"""Offline REAL Equity AMO persistent YES/NO controls: broker calls mocked."""
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from resultbot import equity_amo_live as live
from resultbot import dhan_readonly as dh

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
          patch.object(dh,"verified_profile",return_value={"dhanClientId":ACCOUNT}),
          patch.object(live.watch,"configured_log_group_id",return_value=-5514889030),
          patch.object(live.watch,"configured_test_group_id",return_value=-5537189123),
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

    def test_transient_dhan_funds_500_retried_only_as_get(self):
        current={"dhanClientId":ACCOUNT,"availabelBalance":2500.5}
        with (
            patch.object(dh,"get_json",side_effect=[
                dh.DhanReadError("Dhan HTTP error: 500"),current
            ]) as get_request,
            patch.object(live.time,"sleep") as pause,
            patch.object(live,"_dhan_http") as broker_post,
        ):
            self.assertEqual(live._fresh_dhan_funds(ACCOUNT,TOKEN),2500.5)
            self.assertEqual(get_request.call_count,2)
            self.assertEqual([c.args[0] for c in get_request.call_args_list],
                             ["/fundlimit","/fundlimit"])
            pause.assert_called_once()
            broker_post.assert_not_called()

    def test_persistent_dhan_funds_500_fail_closed_after_three_gets(self):
        with (
            patch.object(dh,"get_json",side_effect=dh.DhanReadError(
                "Dhan HTTP error: 500")) as get_request,
            patch.object(live.time,"sleep") as pause,
            patch.object(live,"_dhan_http") as broker_post,
        ):
            with self.assertRaisesRegex(live.LiveAmoError,"live balance unverified"):
                live._fresh_dhan_funds(ACCOUNT,TOKEN)
            self.assertEqual(get_request.call_count,3)
            self.assertEqual(pause.call_count,2)
            broker_post.assert_not_called()

    def test_dhan_auth_401_is_not_retried_or_bypassed(self):
        with (
            patch.object(dh,"get_json",side_effect=dh.DhanReadError(
                "Authentication rejected or token expired (HTTP 401)"
            )) as get_request,
            patch.object(live.time,"sleep") as pause,
        ):
            with self.assertRaises(dh.DhanReadError):
                live._fresh_dhan_funds(ACCOUNT,TOKEN)
            self.assertEqual(get_request.call_count,1)
            pause.assert_not_called()

    def test_edit_saved_budget_and_source_preserves_switch_and_order_state(self):
        self.armed()
        first=live.reserve("TEST",710,"YESBANK",int(time.time()))
        # Existing reserved signal is frozen to its original budget.
        saved_before=live._intent(first)
        old_control=live.status()
        live._update(first,"ACKED",order_id=OID,security_id="1333",
                     qty=5,price=20.0,broker_status="PENDING")
        with patch.object(live,"_dhan_http") as broker:
            edited=live.update_settings(source="BOTH",budget=750)
            broker.assert_not_called()
        self.assertEqual(edited["per_order_budget_inr"],750)
        self.assertEqual(edited["source"],"BOTH")
        self.assertTrue(edited["amo_enabled"])
        after=live.status()
        self.assertTrue(after["effective_on"])
        self.assertEqual(after["stop_seq"],old_control["stop_seq"])
        self.assertEqual(after["arm_seq"],old_control["arm_seq"])
        self.assertEqual(after["armed_since"],old_control["armed_since"])
        self.assertEqual(live._intent(first)["budget"],saved_before["budget"])
        self.assertEqual(live._intent(first)["order_id"],OID)
        second=live.reserve("EARNINGS_PULSE",711,"TRIDENT",int(time.time()))
        self.assertIsInstance(second,int)
        self.assertEqual(live._intent(second)["budget"],750)

    def test_edit_settings_while_off_does_not_implicitly_enable_real_trading(self):
        with patch.object(live,"_verified_broker_context") as funds, \
             patch.object(live,"_dhan_http") as real_post:
            result=live.update_settings(source="TEST",budget=650)
            funds.assert_not_called()
            real_post.assert_not_called()
        self.assertFalse(result["amo_enabled"])
        self.assertFalse(live.status()["effective_on"])
        self.assertEqual(result["source"],"TEST")
        self.assertEqual(result["per_order_budget_inr"],650)

    def test_edit_settings_rejects_invalid_values_without_mutating(self):
        baseline=live.editable_settings()
        for bad in ("", "UNVERIFIED", "ALL", "BANKNIFTY"):
            with self.assertRaises(live.LiveAmoError):
                live.update_settings(source=bad)
        for bad in (-100,0,99,1001,2000,True,500.5,"500"):
            with self.assertRaises(live.LiveAmoError):
                live.update_settings(budget=bad)
        with self.assertRaises(live.LiveAmoError):
            live.update_settings()
        self.assertEqual(live.editable_settings(),baseline)

    def test_no_settings_edits_during_pre_submission_intent(self):
        first=self.reserved()
        before=live.editable_settings()
        for state in ("RESERVED","PREPARING","SUBMITTING"):
            live._update(first,state)
            with self.assertRaisesRegex(live.LiveAmoError,
                                        "preparing/submitting"):
                live.update_settings(source="BOTH",budget=900)
        self.assertEqual(live.editable_settings(),before)
        live._update(first,"ACKED",order_id=OID,security_id="1333",
                     broker_status="PENDING")
        self.assertEqual(live.update_settings(budget=700)["per_order_budget_inr"],700)

    def test_bare_cli_arm_uses_saved_operator_budget_and_source(self):
        from contextlib import redirect_stdout
        import io,sys
        live.update_settings(source="TEST",budget=700)
        capture=io.StringIO()
        with patch.object(sys,"argv",["equity_amo_live.py","arm" ]), \
             redirect_stdout(capture):
            live.main()
        self.assertEqual(live.status()["budget"],700)
        self.assertEqual(live.status()["source"],"TEST")
        self.assertTrue(live.status()["effective_on"])
        self.assertIn('"budget": 700',capture.getvalue())

    def test_no_old_alert_and_no_wrong_source(self):
        self.armed()
        self.assertIsNone(live.reserve("EARNINGS_PULSE",9,"YESBANK",int(time.time())))
        self.assertIsNone(live.reserve("TEST",9,"YESBANK",int(time.time())-120))
        self.assertIsNone(live.reserve("TEST",9,"YESBANK",int(time.time())+120))
        self.assertIsNone(live.reserve("TEST",9,"A/B",int(time.time())))
        self.assertTrue(live.status()["effective_on"])

    def test_reserved_intent_keeps_amo_on_and_is_idempotent(self):
        ident=self.reserved()
        self.assertTrue(live.status()["effective_on"])
        self.assertIsNone(live.reserve("TEST",9,"YESBANK",int(time.time())))
        self.assertEqual(live._intent(ident)["state"],"RESERVED")
        # ON is durable, and the owner need not arm again for every alert.
        again=live.reserve("TEST",10,"TRIDENT",int(time.time()))
        self.assertIsInstance(again,int)
        self.assertNotEqual(ident,again)
        self.assertTrue(live.status()["effective_on"])
        # Even with pending orders and a prior attempt today, YES stays valid.
        self.assertTrue(live.arm(source="TEST",budget=500)["effective_on"])

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
        self.assertTrue(live.status()["effective_on"])

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

    def test_yes_persists_across_restart_and_no_ten_minute_expiry(self):
        self.armed()
        with live.managed_db() as con:
            con.execute("UPDATE control SET armed_since=? WHERE id=1",
                        (int(time.time())-86400,))
            con.execute("UPDATE control SET expires=1 WHERE id=1")
        # Legacy expiry column may exist, but is no longer consulted.
        self.assertTrue(live.status()["effective_on"])
        with live.managed_db() as reopened:
            self.assertEqual(live._control(reopened)["armed"],1)
        order_id=live.reserve("TEST",101,"TRIDENT",int(time.time()))
        self.assertIsInstance(order_id,int)
        self.assertTrue(live.status()["effective_on"])
        self.assertIn("YES / ON",live.status_short())

    def test_no_disables_future_orders_but_preserves_existing_orders(self):
        ident=self.reserved()
        live._update(ident,"ACKED",order_id=OID,security_id="1333",
                     qty=5,price=20.0,broker_status="PENDING")
        live.disarm()
        self.assertFalse(live.status()["effective_on"])
        self.assertIsNone(live.reserve("TEST",102,"TRIDENT",int(time.time())))
        self.assertEqual(live._intent(ident)["state"],"ACKED")
        self.assertEqual(live._intent(ident)["order_id"],OID)

    def test_allows_real_earnings_and_owner_test_messages_when_on(self):
        live.arm(source="BOTH",budget=500)
        first=live.reserve("TEST",201,"YESBANK",int(time.time()))
        second=live.reserve("EARNINGS_PULSE",201,"YESBANK",int(time.time()))
        self.assertIsInstance(first,int)
        self.assertIsInstance(second,int)
        self.assertNotEqual(first,second)
        self.assertNotEqual(live._intent(first)["correlation"],
                            live._intent(second)["correlation"])
        self.assertTrue(live.status()["effective_on"])
        self.assertEqual(live.status()["active_orders"],2)

    def test_multiple_distinct_real_dhan_orders_without_rearming(self):
        self.armed()
        broker_replies=[
            {"orderId":OID,"orderStatus":"PENDING"},
            {"orderId":str(int(OID)+1),"orderStatus":"PENDING"},
        ]
        with (
            patch.object(live,"_dhan_http",side_effect=broker_replies) as broker,
            patch.object(live,"_verify_owned_order",return_value=fake_broker_order()),
        ):
            first=live.reserve("TEST",302,"YESBANK",int(time.time()))
            live.execute(first)
            second=live.reserve("TEST",303,"TRIDENT",int(time.time()))
            live.execute(second)
            self.assertEqual(broker.call_count,2)
        self.assertEqual(live._intent(first)["state"],"ACKED")
        self.assertEqual(live._intent(second)["state"],"ACKED")
        self.assertNotEqual(live._intent(first)["order_id"],
                            live._intent(second)["order_id"])
        self.assertTrue(live.status()["effective_on"])

    def test_ongoing_monitor_never_auto_cancels_pending_amo(self):
        ident=self.reserved()
        live._update(ident,"ACKED",order_id=OID,security_id="1333",
                     qty=5,price=20.0,broker_status="PENDING",cancel_after=1)
        with (
            patch.object(live,"_verify_owned_order",return_value=fake_broker_order()),
            patch.object(live,"request_cancel") as cancel,
        ):
            live.monitor()
            cancel.assert_not_called()
        self.assertEqual(live._intent(ident)["state"],"ACKED")
        self.assertEqual(live._intent(ident)["cancel_attempted"],0)

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


if __name__=="__main__":
    unittest.main()
