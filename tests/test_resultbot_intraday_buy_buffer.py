"""ResultBot intraday BUY limit-buffer settings and offline pricing tests.

No Dhan API calls, credentials, positions, or actual orders.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resultbot import intraday_buy_plan as plan
from resultbot import control_center_v2 as ui


class TestIntradayBuyBuffer(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        patcher1=patch.object(plan,"ROOT",root)
        patcher2=patch.object(plan,"CONFIG",root/"intraday_buy_settings.json")
        patcher1.start();patcher2.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)

    def test_defaults_do_not_enable_live_trading(self):
        s=plan.settings()
        self.assertEqual(s["buy_buffer_pct"],"1")
        self.assertFalse(s["live_intraday_execution_connected"])
        self.assertEqual(s["planned_broker_product_type"],"INTRADAY")
        self.assertTrue(s["existing_equity_amo_unchanged"])

    def test_owner_picks_all_preset_buffers(self):
        for x in (1,2,3,4):
            saved=plan.save_buffer_pct(x)
            self.assertEqual(saved["buy_buffer_pct"],str(x))
            self.assertEqual(plan.settings()["buy_buffer_pct"],str(x))

    def test_custom_and_zero_are_allowed(self):
        for x in ("0","0.25","1.75","4.5","12.5"):
            saved=plan.save_buffer_pct(x)
            self.assertEqual(saved["buy_buffer_pct"],x)

    def test_buffer_and_share_budget_example(self):
        p=plan.calculate_buy_limit(quote_price="100",budget_inr=1000,
                                   tick_size="0.05",buffer_pct=2)
        self.assertEqual(p["buy_limit_price"],"102")
        self.assertEqual(p["whole_shares_affordable_at_limit"],9)
        self.assertEqual(p["maximum_entry_notional_inr"],"918")
        self.assertEqual(p["product_type"],"INTRADAY")
        self.assertEqual(p["order_type"],"LIMIT")
        self.assertFalse(p["broker_order_submitted"])
        self.assertFalse(p["live_quote_freshness_verified"])

    def test_ceiling_rounds_up_to_exchange_tick(self):
        p=plan.calculate_buy_limit(quote_price="100.03",budget_inr=1000,
                                   tick_size="0.05",buffer_pct=2)
        self.assertEqual(p["buy_limit_price"],"102.05")
        self.assertEqual(p["whole_shares_affordable_at_limit"],9)

    def test_saved_buffer_used_without_overriding_budget_or_amo(self):
        plan.save_buffer_pct("3")
        p=plan.calculate_buy_limit(quote_price=100,budget_inr=500,
                                   tick_size="0.05")
        self.assertEqual(p["buy_buffer_pct"],"3")
        self.assertEqual(p["buy_limit_price"],"103")
        self.assertEqual(p["whole_shares_affordable_at_limit"],4)

    def test_unaffordable_stock_no_buy_suggested(self):
        p=plan.calculate_buy_limit(quote_price=110,budget_inr=100,
                                   tick_size="0.05",buffer_pct=2)
        self.assertFalse(p["can_fit_one_share"])
        self.assertEqual(p["whole_shares_affordable_at_limit"],0)

    def test_bad_values_not_saved(self):
        plan.save_buffer_pct("2")
        before=plan.CONFIG.read_bytes()
        for value in (-1,100,"nan","Infinity","abc",None,True):
            with self.subTest(value=value):
                with self.assertRaises(plan.BuyBufferError):
                    plan.save_buffer_pct(value)
                self.assertEqual(plan.CONFIG.read_bytes(),before)

    def test_reject_non_live_quote_source(self):
        with self.assertRaises(plan.BuyBufferError):
            plan.calculate_buy_limit(quote_price=100,budget_inr=1000,
                                     tick_size="0.05",buffer_pct=2,
                                     reference_kind="YAHOO_OLDER_BAR")

    def test_invalid_price_tick_budget(self):
        for values in (
            dict(quote_price=0,budget_inr=1000,tick_size=".05"),
            dict(quote_price=100,budget_inr=-10,tick_size=".05"),
            dict(quote_price=100,budget_inr=1000,tick_size=0),
            dict(quote_price="inf",budget_inr=1000,tick_size=".05"),
            dict(quote_price=100,budget_inr="10.5",tick_size=".05")
        ):
            with self.subTest(values=values):
                with self.assertRaises(plan.BuyBufferError):
                    plan.calculate_buy_limit(buffer_pct=2,**values)

    def test_save_permissions_and_bad_document(self):
        plan.save_buffer_pct(4)
        self.assertEqual(plan.CONFIG.stat().st_mode & 0o777,0o600)
        plan.CONFIG.write_text("not-json")
        with self.assertRaises(plan.BuyBufferError):
            plan.settings()

    def test_mouse_click_preset_and_custom_fields(self):
        for h,w in ((24,80),(32,100)):
            top = (h-(24 if h<30 else 29))//2
            row = top+(10 if h<30 else 14)
            step = 1 if h<30 else 2
            for i in range(6):
                self.assertEqual(
                    ui.mouse_hit_test("BUY BUFFER",h,w,18,row+i*step),
                    ("item",i)
                )
            self.assertEqual(
                ui.mouse_hit_test("BUFFER CUSTOM",h,w,18,row+4),
                ("input",None)
            )

    def test_buffer_button_in_strategy_menu(self):
        h,w=32,100
        y=(h-29)//2+14+2*4
        self.assertEqual(ui.mouse_hit_test("STRATEGY",h,w,20,y),("item",4))


if __name__=="__main__":
    unittest.main()
