"""Offline tests for ResultBot visual status; no broker orders or data writes."""
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from resultbot import control_center_v2 as ui


class TestResultBotStatusPolish(unittest.TestCase):
    def test_red_amo_off_and_green_amo_on(self):
        self.assertEqual(ui.amo_status_indicator(False), ("● AMO OFF", 7))
        self.assertEqual(ui.amo_status_indicator(True), ("● AMO ON", 6))
        self.assertEqual(ui.amo_status_indicator(None), ("● AMO UNKNOWN", 4))

    def test_green_live_only_when_armed_and_listener_running(self):
        self.assertEqual(ui.bot_status_indicator(True, True), ("BOT LIVE (AMO)", 12))
        self.assertEqual(ui.bot_status_indicator(False, True), ("BOT LISTENING", 12))
        self.assertEqual(ui.bot_status_indicator(None, True), ("BOT LISTENING", 12))
        self.assertEqual(ui.bot_status_indicator(True, False), ("BOT OFFLINE", 13))
        self.assertEqual(ui.bot_status_indicator(True, None), ("BOT STATUS ?", 3))
        self.assertEqual(ui.bot_status_indicator(False, None), ("BOT STATUS ?", 3))

    def test_bright_green_listening_and_green_cash_icon_are_drawn(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (32, 100)
        state = {"amo_enabled":False, "per_order_budget_inr":1000,
                 "source":"BOTH"}
        with patch.object(ui, "draw_text") as drawing, \
             patch.object(ui, "card") as cards, \
             patch.object(ui, "line"), \
             patch.object(ui.curses, "color_pair", return_value=0):
            self.assertTrue(ui.render(screen, state, "CONTROL CENTER",
                                      [], 0, 0, listener_running=True))
        self.assertTrue(any(
            call.args[2] == "BOT LISTENING" and call.args[3] == 12
            for call in drawing.call_args_list))
        self.assertTrue(any(
            call.args[2] == "💵" and call.args[3] == 14
            for call in drawing.call_args_list))
        self.assertTrue(any(
            call.args[3] == "TRADING AMO" and call.args[4] == "● AMO OFF"
            for call in cards.call_args_list))
        self.assertTrue(any(
            call.args[3] == "PER ORDER" and call.args[4] == "   ₹1,000"
            for call in cards.call_args_list))

    def test_unavailable_budget_does_not_draw_cash_icon(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (32, 100)
        with patch.object(ui, "draw_text") as drawing, \
             patch.object(ui, "card"), \
             patch.object(ui, "line"), \
             patch.object(ui.curses, "color_pair", return_value=0):
            ui.render(screen, {"amo_enabled":False}, "CONTROL CENTER",
                      [], 0, 0, listener_running=False)
        self.assertFalse(any(call.args[2] == "💵"
                             for call in drawing.call_args_list))

    def test_listener_status_reads_only_systemctl(self):
        with patch.object(ui.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="active\n"
        )) as command:
            self.assertIs(ui.is_listener_running(), True)
            args,kwargs=command.call_args
            self.assertEqual(args[0],
                ["systemctl","--user","is-active","resultbot-excellent-watch.service"])
            self.assertEqual(kwargs["timeout"],3)

    def test_listener_offline_or_unavailable(self):
        for result,expected in (
            (SimpleNamespace(returncode=3,stdout="inactive\n"),False),
            (SimpleNamespace(returncode=1,stdout="failed\n"),False),
            (SimpleNamespace(returncode=1,stdout="unknown\n"),None)
        ):
            with self.subTest(result=result.stdout):
                with patch.object(ui.subprocess, "run", return_value=result):
                    self.assertIs(ui.is_listener_running(),expected)
        with patch.object(ui.subprocess,"run",side_effect=OSError):
            self.assertIsNone(ui.is_listener_running())

    def test_arrows_in_strategy_menu_source(self):
        from inspect import getsource
        code=getsource(ui.start)
        self.assertIn("▼ FALLING PRICE",code)
        self.assertIn("▲ RISING PRICE",code)

    def test_existing_menu_and_mouse_targets_remain_unchanged(self):
        self.assertEqual(len(ui.MENU),6)
        h,w=32,100
        top=(h-29)//2
        self.assertEqual(ui.mouse_hit_test("STRATEGY",h,w,18,top+14),
                         ("item",0))
        self.assertEqual(ui.mouse_hit_test("STRATEGY",h,w,18,top+16),
                         ("item",1))
        self.assertEqual(ui.mouse_hit_test("STRATEGY",h,w,18,top+22),
                         ("item",4))
        self.assertEqual(ui.mouse_hit_test("BUY BUFFER",h,w,18,top+22),
                         ("item",4))


if __name__=="__main__":
    unittest.main()
