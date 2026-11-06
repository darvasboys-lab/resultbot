"""Offline ResultBot Equity AMO demonstration tests: zero network or real orders."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from resultbot import equity_amo_preview as demo
from resultbot import excellent_watch as watch


class EquityAmoPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = watch.setup_db(Path(self.tmp.name)/"demo.sqlite3")
        self.when = datetime(2026,11,6,15,50,tzinfo=timezone.utc)
        self.message = "[RBAMO] #YESBANK - 🏆🔥 Excellent Results"
        self.test_group_id = -5149263962

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_amo_dhan_fields_are_exact_and_order_cannot_route(self):
        d = demo.envelope("YESBANK")
        self.assertEqual(d["transactionType"],"BUY")
        self.assertEqual(d["exchangeSegment"],"NSE_EQ")
        self.assertEqual(d["productType"],"CNC")
        self.assertEqual(d["orderType"],"LIMIT")
        self.assertEqual(d["validity"],"DAY")
        self.assertEqual(d["amoTime"],"OPEN")
        self.assertIs(d["afterMarketOrder"],True)
        self.assertEqual(d["securityId"],demo.NONROUTABLE_SECURITY_ID)
        self.assertEqual(d["quantity"],5)
        self.assertEqual(d["price"],100.0)
        self.assertEqual(d["quantity"]*d["price"],500.0)
        self.assertFalse(demo.safety_validation(d)[0])

    def test_invalid_price_budget_or_quantity_fails_closed(self):
        for kwargs in ({"reference_price":0},
                       {"reference_price":float("nan")},
                       {"reference_price":float("inf")},
                       {"budget_inr":-1},
                       {"budget_inr":5,"reference_price":100}):
            with self.assertRaises(ValueError):
                demo.envelope("YESBANK",**kwargs)
        with self.assertRaises(ValueError):
            demo.envelope("INVALID/SYMBOL")

    def test_explicit_test_group_trigger_queues_both_cards_only_once(self):
        self.assertEqual(watch.enqueue_test_log(
            self.con,self.test_group_id,77,self.when,self.message),"test_queued")
        self.assertEqual(watch.enqueue_test_log(
            self.con,self.test_group_id,77,self.when,self.message),"duplicate")
        cards=self.con.execute(
            "SELECT kind,text_html FROM log_outbox ORDER BY id").fetchall()
        self.assertEqual([x[0] for x in cards],["TEST_EXCELLENT_ALERT","EQUITY_AMO_DRY_RUN"])
        self.assertIn("SUBMITTED",cards[1][1])
        self.assertIn("BLOCKED",cards[1][1])
        self.assertIn("N/A",cards[1][1])
        self.assertIn("sample inputs only",cards[1][1])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM one_minute_bars").fetchone()[0],0)

    def test_regular_test_excellent_produces_no_amo_card(self):
        self.assertEqual(watch.enqueue_test_log(
            self.con,self.test_group_id,88,self.when,
            "#YESBANK - 🏆🔥 Excellent Results"),"test_queued")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM log_outbox").fetchone()[0],1)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM log_outbox WHERE kind='EQUITY_AMO_DRY_RUN'").fetchone()[0],0)

    def test_invalid_test_group_and_non_excellent_never_create_amo(self):
        wrong = -int(f"100{watch.CHANNEL_ID}")
        self.assertEqual(watch.enqueue_test_log(
            self.con,wrong,88,self.when,self.message),"invalid_test_source")
        self.assertEqual(watch.enqueue_test_log(
            self.con,self.test_group_id,99,self.when,
            "[RBAMO] #YESBANK - 🌟 Great Results"),"not_excellent")
        self.assertEqual(watch.enqueue_test_log(
            self.con,self.test_group_id,100,self.when,
            "[RBAMO] #YESBANK #TRIDENT - Excellent Results"),"missing_or_multiple_stock_symbols")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM log_outbox").fetchone()[0],0)

    def test_live_research_alert_never_auto_queues_amo(self):
        self.assertEqual(watch.add_alert(
            self.con,123,self.when,self.message),"stored")
        self.assertTrue(watch.enqueue_live_log(self.con,123))
        rows=self.con.execute("SELECT kind FROM log_outbox").fetchall()
        self.assertEqual(rows,[("EXCELLENT_ALERT",)])

    def test_pure_preview_has_no_broker_or_network_dependencies(self):
        with patch("urllib.request.urlopen",side_effect=AssertionError("NETWORK")), \
             patch("urllib.request.Request",side_effect=AssertionError("NETWORK")):
            result=demo.envelope("YESBANK")
            body=demo.card("YESBANK",int(self.when.timestamp()))
            self.assertEqual(result["amoTime"],"OPEN")
            self.assertIn("NOT SUBMITTED",body)

    def test_self_test(self):
        demo.self_test()


if __name__=="__main__":
    unittest.main()
