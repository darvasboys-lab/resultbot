"""Offline regression tests for the ResultBot Dhan GET-only Telegram bridge."""
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from resultbot import dhan_bridge as br
from resultbot import dhan_readonly as dh
from resultbot import excellent_watch as watch

ACCOUNT="1105508885"
ORDERID="112233445566"


def order(*, status="PART_TRADED", filled=3, qty=10, average=125.5,
          symbol="YESBANK", account=ACCOUNT, side="BUY", segment="NSE_EQ",
          created=None):
    return {
        "orderId":ORDERID,
        "dhanClientId":account,
        "orderStatus":status,
        "transactionType":side,
        "exchangeSegment":segment,
        "tradingSymbol":symbol,
        "quantity":qty,
        "filledQty":filled,
        "averageTradedPrice":average,
        "createTime":created or datetime.now(watch.IST).strftime("%Y-%m-%d %H:%M:%S"),
    }


class DhanBridgeTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.con=watch.setup_db(Path(self.root.name)/"excellent.sqlite3")
        br.ensure_schema(self.con)
        self.alert_utc=int(time.time())-120
        dt=datetime.fromtimestamp(self.alert_utc,timezone.utc)
        self.assertEqual(watch.add_alert(self.con,300,dt,"#YESBANK - 🏆🔥 Excellent Results"),"stored")

    def tearDown(self):
        self.con.close()
        self.root.cleanup()

    def _linked(self):
        self.con.execute(
            """INSERT INTO dhan_order_links
               (channel_id,telegram_message_id,dhan_client_id,dhan_order_id,symbol,created_utc)
               VALUES (?,?,?,?,?,?)""",
            (watch.CHANNEL_ID,300,ACCOUNT,ORDERID,"YESBANK",int(time.time())),
        )

    def test_broker_verified_connect_card_is_deduplicated(self):
        profile={"dhanClientId":ACCOUNT,"tokenValidity":"07/11/2026 22:40"}
        with patch.object(br,"snapshot",return_value=(ACCOUNT,profile,[order()],[])):
            one=br.connect(self.con)
            two=br.connect(self.con)
        self.assertEqual(one["account"],"***8885")
        self.assertEqual(two["orders_today"],1)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM log_outbox WHERE kind='DHAN_CONNECTED_V1'").fetchone()[0],1)

    def test_unknown_or_mismatched_broker_identity_rejected(self):
        for bad in ({"account":"9999999999"},{"symbol":"ANOTHER"},
                    {"side":"SELL"},{"segment":"NSE_FNO"}):
            with self.assertRaises(dh.DhanReadError):
                br.validate_identity(order(**bad),account=ACCOUNT,order_id=ORDERID,symbol="YESBANK")
        with self.assertRaises(dh.DhanReadError):
            br.validate_identity(order(),account=ACCOUNT,order_id="1234",symbol="YESBANK")

    def test_broker_order_must_not_predate_alert(self):
        with self.assertRaisesRegex(dh.DhanReadError,"predates"):
            br.validate_identity(
                order(created="2020-01-01 09:15:00"),
                account=ACCOUNT,order_id=ORDERID,symbol="YESBANK",alert_utc=self.alert_utc)

    def test_only_verified_real_alert_may_be_linked(self):
        with patch.object(dh,"load_credentials",return_value=(ACCOUNT,"T"*50)), \
             patch.object(dh,"verified_profile",return_value={"dhanClientId":ACCOUNT}), \
             patch.object(dh,"get_json",return_value=order()):
            self.assertEqual(br.link_order(self.con,telegram_id=300,order_id=ORDERID),"LINKED")
            self.assertEqual(br.link_order(self.con,telegram_id=300,order_id=ORDERID),"ALREADY_LINKED")
            with self.assertRaisesRegex(dh.DhanReadError,"not a stored REAL"):
                br.link_order(self.con,telegram_id=301,order_id=ORDERID)

    def test_duplicate_order_other_alert_refused(self):
        self._linked()
        other = datetime.fromtimestamp(self.alert_utc,timezone.utc)
        watch.add_alert(self.con,301,other,"#YESBANK - Excellent Results")
        with patch.object(dh,"load_credentials",return_value=(ACCOUNT,"T"*50)), \
             patch.object(dh,"verified_profile",return_value={"dhanClientId":ACCOUNT}), \
             patch.object(dh,"get_json",return_value=order()):
            with self.assertRaisesRegex(dh.DhanReadError,"already linked"):
                br.link_order(self.con,telegram_id=301,order_id=ORDERID)

    def test_link_rejects_symbol_only_matching_and_wrong_order_id(self):
        with patch.object(dh,"load_credentials",return_value=(ACCOUNT,"T"*50)), \
             patch.object(dh,"verified_profile",return_value={"dhanClientId":ACCOUNT}), \
             patch.object(dh,"get_json",return_value=order()):
            with self.assertRaisesRegex(dh.DhanReadError,"Broker order ID mismatch"):
                br.link_order(self.con,telegram_id=300,order_id="9988776655")
        self.assertEqual(self.con.execute("SELECT count(*) FROM dhan_order_links").fetchone()[0],0)

    def test_unchanged_poll_silent_changed_poll_queues_without_order_id(self):
        self._linked()
        tr=[{"orderId":ORDERID,"tradedQuantity":3,"tradedPrice":125.5}]
        self.assertEqual(br.apply_snapshot(self.con,account=ACCOUNT,orders=[order()],trades=tr),1)
        self.assertEqual(br.apply_snapshot(self.con,account=ACCOUNT,orders=[order()],trades=tr),0)
        first=self.con.execute(
            "SELECT text_html FROM log_outbox WHERE kind LIKE 'DHAN_%'").fetchone()[0]
        self.assertIn("₹125.50",first)
        self.assertIn("manually linked",first)
        self.assertNotIn(ORDERID,first)
        full=order(status="TRADED",filled=10,average=135.5)
        updated_trade=[{"orderId":ORDERID,"tradedQuantity":10,"tradedPrice":135.5}]
        self.assertEqual(br.apply_snapshot(self.con,account=ACCOUNT,orders=[full],trades=updated_trade),1)
        self.assertEqual(self.con.execute("SELECT active FROM dhan_order_links").fetchone()[0],0)
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM log_outbox WHERE kind LIKE 'DHAN_%'").fetchone()[0],2)

    def test_inconsistent_trade_quantities_fail_closed(self):
        self._linked()
        with self.assertRaisesRegex(dh.DhanReadError,"quantities disagree"):
            br.apply_snapshot(self.con,account=ACCOUNT,orders=[order()],
                              trades=[{"orderId":ORDERID,"tradedQuantity":5,"tradedPrice":100}])
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM log_outbox").fetchone()[0],0)

    def test_broker_account_mismatch_no_outbox(self):
        self._linked()
        with self.assertRaisesRegex(dh.DhanReadError,"account"):
            br.apply_snapshot(self.con,account="2222222222",orders=[order()],trades=[])
        self.assertEqual(self.con.execute("SELECT count(*) FROM log_outbox").fetchone()[0],0)

    def test_no_link_no_network_read(self):
        with patch.object(br,"snapshot") as fetch:
            self.assertEqual(br.poll_once(self.con),0)
            fetch.assert_not_called()

    def test_weighted_trade_average_and_no_false_zero_price(self):
        tr=[{"orderId":ORDERID,"tradedQuantity":3,"tradedPrice":100},
            {"orderId":ORDERID,"tradedQuantity":2,"tradedPrice":120}]
        self.assertEqual(br._trade_summary(tr,ORDERID),(5,"₹108.00"))
        self.assertEqual(br.status_line(order(filled=0,average=999))[-1],"N/A")
        with self.assertRaises(dh.DhanReadError):
            br.status_line(order(filled=11,qty=10))
        with self.assertRaisesRegex(dh.DhanReadError,"full-trade status"):
            br.status_line(order(status="TRADED",filled=9,qty=10))

    def test_self_test(self):
        br.self_test()


if __name__=="__main__":
    unittest.main()
