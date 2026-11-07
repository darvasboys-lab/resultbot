"""Offline coordinate tests for the premium ResultBot terminal mouse targets."""
import unittest
from resultbot import control_center_v2 as ui

class TestMouseHitTest(unittest.TestCase):
    def test_full_screen_menu(self):
        # 100x32 terminal => 98-wide cap, 29-high panel
        h, w = 32, 100
        panel_width = min(w - 4, 98)
        left = (w - panel_width) // 2
        top = (h - 29) // 2
        row_start = top + 14
        for index in range(6):
            self.assertEqual(
                ui.mouse_hit_test("CONTROL CENTER", h, w, left + 8,
                                  row_start + 2 * index), ("item", index)
            )

    def test_cards_shortcut_to_correct_menus(self):
        h, w = 32, 100
        panel_width = min(w - 4, 98)
        left = (w - panel_width) // 2
        top = (h - 29) // 2
        card_width = (panel_width - 10) // 3
        for card, target in enumerate((3, 0, 1)):
            x = left + 3 + card * (card_width + 2) + 2
            self.assertEqual(
                ui.mouse_hit_test("CONTROL CENTER", h, w, x, top + 7),
                ("item", target)
            )

    def test_source_options(self):
        h, w = 32, 100
        top = (h - 29) // 2
        for index in range(3):
            self.assertEqual(
                ui.mouse_hit_test("ALERT SOURCE", h, w, 18,
                                  top + 14 + 2 * index), ("item", index)
            )

    def test_strategy_selection_mouse_rows(self):
        h, w = 32, 100
        top = (h - 29) // 2
        for page, count in (("STRATEGY", 4), ("STRATEGY DOWN", 3),
                            ("STRATEGY UP", 3), ("STRATEGY REVIEW", 5)):
            for index in range(count):
                self.assertEqual(ui.mouse_hit_test(page, h, w, 18,
                                                   top + 14 + 2*index),
                                 ("item", index))

    def test_compact_sixth_menu_row(self):
        self.assertEqual(
            ui.mouse_hit_test("CONTROL CENTER", 24, 80, 12, 15),
            ("item", 5)
        )

    def test_ok_cancel(self):
        for h, w, panel_h in ((32, 100, 29), (24, 80, 24)):
            panel_w = min(w - 4, 98)
            left = (w - panel_w) // 2
            top = (h - panel_h) // 2
            y = top + panel_h - 2
            self.assertEqual(
                ui.mouse_hit_test("ALERT SOURCE", h, w,
                                  left + panel_w - 20, y), ("ok", None)
            )
            self.assertEqual(
                ui.mouse_hit_test("ALERT SOURCE", h, w,
                                  left + panel_w - 11, y), ("cancel", None)
            )

    def test_compact_and_budget_input(self):
        self.assertEqual(
            ui.mouse_hit_test("CONTROL CENTER", 24, 80, 10, 10),
            ("item", 0)
        )
        self.assertEqual(
            ui.mouse_hit_test("ORDER BUDGET", 24, 80, 12, 12),
            ("input", None)
        )
        self.assertEqual(
            ui.mouse_hit_test("TRADING SWITCH", 24, 80, 12, 12),
            (None, None)
        )

    def test_ignore_outside_and_tiny_windows(self):
        self.assertEqual(
            ui.mouse_hit_test("CONTROL CENTER", 23, 80, 12, 12),
            (None, None)
        )
        self.assertEqual(
            ui.mouse_hit_test("CONTROL CENTER", 32, 100, 0, 0),
            (None, None)
        )

if __name__ == "__main__":
    unittest.main()
