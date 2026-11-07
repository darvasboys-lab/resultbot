"""Offline tests: ResultBot OCO prices use Dhan averageTradedPrice, never CMP."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resultbot import oco_price_plan as op
from resultbot import position_strategy as ps


def order_factory(price="102.40",qty=5,status="TRADED"):
    own={"account":"ACC99","order_id":"123456789",
         "correlation":"RBENTRY123","security_id":"111","qty":qty}
    broker={"dhanClientId":"ACC99","orderId":"123456789",
            "correlationId":"RBENTRY123","securityId":"111",
            "exchangeSegment":"NSE_EQ","productType":"CNC",
            "transactionType":"BUY","orderType":"LIMIT",
            "afterMarketOrder":True,"orderStatus":status,
            "quantity":qty,"filledQty":qty,"averageTradedPrice":price,
            "price":"200.00"}
    return own,broker


class ConfirmedFillOcoPlanTest(unittest.TestCase):
    def sample(self,**changes):
        intent,order=order_factory()
        order.update(changes)
        return op.build_oco_price_plan(intent=intent,order=order,
                 stop_loss_pct=3,target_pct=6,tick_size="0.05")

    def test_uses_broker_execution_vwap_not_limit_price(self):
        plan=self.sample()
        self.assertEqual(plan["confirmed_execution_vwap"],"102.40")
        self.assertEqual(plan["stop_loss_trigger"],"99.30")
        self.assertEqual(plan["stop_loss_sell_limit"],"99.25")
        self.assertEqual(plan["target_sell_limit"],"108.55")
        self.assertEqual(plan["target_trigger"],"108.55")
        self.assertNotEqual(plan["confirmed_execution_vwap"],"200.00")
        self.assertFalse(plan["broker_oco_created"])
        self.assertFalse(plan["holding_quantity_verified"])
        self.assertFalse(plan["eligibility_verified"])

    def test_partial_or_pending_fill_refused(self):
        for status in ("PART_TRADED","PENDING","TRANSIT","REJECTED",""):
            with self.subTest(status=status):
                with self.assertRaises(op.OcoPlanError):
                    self.sample(orderStatus=status)
        with self.assertRaisesRegex(op.OcoPlanError,"quantity"):
            self.sample(filledQty=4)

    def test_missing_or_zero_execution_price_never_falls_back_to_limit(self):
        for price in (None,0,"0","nan","inf","-1","not available"):
            with self.subTest(price=price):
                with self.assertRaises(op.OcoPlanError):
                    self.sample(averageTradedPrice=price)

    def test_cross_account_and_security_mismatch_rejected(self):
        for key,value in (
            ("dhanClientId","DIFFERENT"),("securityId","222"),
            ("correlationId","UNRELATED"),("orderId","999999999")
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(op.OcoPlanError,"binding"):
                    self.sample(**{key:value})

    def test_dhan_route_must_be_real_equity_buy_amo(self):
        for key,value in (
            ("transactionType","SELL"),("exchangeSegment","NSE_FNO"),
            ("productType","MARGIN"),("afterMarketOrder",False)
        ):
            with self.subTest(key=key):
                with self.assertRaises(op.OcoPlanError):
                    self.sample(**{key:value})

    def test_missing_qty_and_incorrect_tick_rejected(self):
        with self.assertRaises(op.OcoPlanError):
            self.sample(filledQty=None)
        own,broker=order_factory()
        for tick in ("0","-0.05","NaN","100"):
            with self.subTest(tick=tick):
                with self.assertRaises(op.OcoPlanError):
                    op.build_oco_price_plan(intent=own,order=broker,
                        stop_loss_pct=3,target_pct=6,tick_size=tick)

    def test_tick_rounding_is_conservative(self):
        own,broker=order_factory(price="50.13")
        p=op.build_oco_price_plan(intent=own,order=broker,stop_loss_pct=3,
                                  target_pct=6,tick_size="0.05",
                                  stop_limit_buffer_ticks=2)
        self.assertEqual(p["stop_loss_trigger"],"48.60")
        self.assertEqual(p["stop_loss_sell_limit"],"48.50")
        self.assertEqual(p["target_sell_limit"],"53.15")

    def test_saved_oco_percentage_edit_never_enables_live_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(ps,"FILE",Path(temp)/"plan.json"):
                saved=ps.set_oco_percentages(9.0,6.0)
                self.assertEqual(saved["stop_pct"],9.0)
                self.assertEqual(saved["target_pct"],6.0)
                self.assertFalse(saved["execution_enabled"])
                self.assertEqual(saved["execution_status"],
                                 "TEST_ONLY_NOT_CONNECTED_TO_DHAN")
                self.assertEqual(ps.load()["down_mode"],"CUT")

    def test_invalid_revised_stop_is_not_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(ps,"FILE",Path(temp)/"plan.json"):
                ps.set_oco_percentages(9,6)
                original=ps.FILE.read_bytes()
                with self.assertRaises(ValueError):
                    ps.set_oco_percentages(2,6)  # below active CUT level
                self.assertEqual(ps.FILE.read_bytes(),original)


if __name__=="__main__":
    unittest.main()
