"""Read-only ResultBot terminal breadcrumb and directional color regression tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from resultbot import control_center_v2 as ui


class TestBreadcrumbAndDirectionVisuals(unittest.TestCase):
    def test_main_and_strategy_folder_path(self):
        self.assertEqual(ui.breadcrumb_text("CONTROL CENTER"),
                         "RESULTBOT › CONTROL CENTER")
        self.assertEqual(ui.breadcrumb_text("STRATEGY"),
                         "RESULTBOT › STRATEGY PLAN")
        self.assertEqual(ui.breadcrumb_text("STRATEGY DOWN"),
                         "RESULTBOT › STRATEGY PLAN › FALLING PRICE")
        self.assertEqual(ui.breadcrumb_text("STRATEGY UP"),
                         "RESULTBOT › STRATEGY PLAN › RISING PRICE")
        self.assertEqual(ui.breadcrumb_text("STRATEGY REVIEW"),
                         "RESULTBOT › STRATEGY PLAN › REVIEW PLAN")

    def test_path_for_wizard_and_confirmation(self):
        w={"side":"down","mode":"AVERAGE"}
        self.assertEqual(ui.breadcrumb_text("STRATEGY WIZARD",w),
                         "RESULTBOT › STRATEGY PLAN › FALLING PRICE › AVERAGE › SETUP")
        self.assertEqual(ui.breadcrumb_text("STRATEGY CONFIRM",w),
                         "RESULTBOT › STRATEGY PLAN › FALLING PRICE › AVERAGE › REVIEW")
        self.assertEqual(ui.breadcrumb_text("STRATEGY WIZARD",
                                           {"side":"up","mode":"TAKE_PROFIT"}),
                         "RESULTBOT › STRATEGY PLAN › RISING PRICE › TAKE_PROFIT › SETUP")

    def test_additional_strategy_folders_have_correct_parent(self):
        self.assertEqual(ui.breadcrumb_text("BUY BUFFER"),
                         "RESULTBOT › STRATEGY PLAN › BUY BUFFER")
        self.assertEqual(ui.breadcrumb_text("BUFFER CUSTOM"),
                         "RESULTBOT › STRATEGY PLAN › BUY BUFFER › CUSTOM")
        self.assertEqual(ui.breadcrumb_text("OCO EDIT",
                                             oco_editor={"phase":0}),
                         "RESULTBOT › STRATEGY PLAN › OCO SETTINGS › STOP LOSS")
        self.assertEqual(ui.breadcrumb_text("OCO EDIT",
                                             oco_editor={"phase":1}),
                         "RESULTBOT › STRATEGY PLAN › OCO SETTINGS › TARGET")
        self.assertEqual(ui.breadcrumb_text("OCO CONFIRM"),
                         "RESULTBOT › STRATEGY PLAN › OCO SETTINGS › REVIEW")
        self.assertEqual(ui.breadcrumb_text("ALERT SOURCE"),
                         "RESULTBOT › ALERT SOURCE")
        self.assertEqual(ui.breadcrumb_text("ORDER BUDGET"),
                         "RESULTBOT › ORDER BUDGET")

    def test_small_terminals_keep_root_and_current_page(self):
        for leaf in ("STRATEGY WIZARD", "STRATEGY CONFIRM"):
            path=ui.breadcrumb_text(leaf,{"side":"down","mode":"STOP_ONLY"},
                                    max_chars=49)
            self.assertTrue(path.startswith("RESULTBOT › "))
            self.assertTrue(path.endswith("SETUP" if leaf=="STRATEGY WIZARD"
                                          else "REVIEW"))
            self.assertLessEqual(len(path),49)
        path=ui.breadcrumb_text("STRATEGY DOWN",max_chars=49)
        self.assertIn("FALLING PRICE",path)
        self.assertLessEqual(len(path),49)

    def test_arrow_styles_red_down_green_up_in_both_row_states(self):
        self.assertEqual(ui.direction_arrow_style("▼",False),15)
        self.assertEqual(ui.direction_arrow_style("▼",True),17)
        self.assertEqual(ui.direction_arrow_style("▲",False),16)
        self.assertEqual(ui.direction_arrow_style("▲",True),18)
        self.assertIsNone(ui.direction_arrow_style("X",False))

    @staticmethod
    def rendered(page, entries, selected=0, size=(32,100),wizard=None):
        screen=MagicMock()
        screen.getmaxyx.return_value=size
        with patch.object(ui,"draw_text") as draws, \
             patch.object(ui,"card"),patch.object(ui,"line"), \
             patch.object(ui.curses,"color_pair",return_value=0):
            assert ui.render(screen, {"amo_enabled":False,
                        "per_order_budget_inr":1000, "source":"BOTH"},
                        page,entries,selected,0,plan={"down_mode":"CUT",
                        "up_mode":"TARGET_ONLY"},wizard=wizard,
                        listener_running=True)
        return draws.call_args_list

    def test_render_red_selected_down_arrow_and_green_up_arrow(self):
        entries=[("01","▼ FALLING PRICE","CUT"),
                 ("02","▲ RISING PRICE","TARGET_ONLY")]
        calls=self.rendered("STRATEGY",entries,selected=0)
        self.assertTrue(any(c.args[3]=="▼" and c.args[4]==17 for c in calls))
        self.assertTrue(any(c.args[3]=="▲" and c.args[4]==16 for c in calls))
        self.assertTrue(any(c.args[3]=="FALLING PRICE" for c in calls))
        self.assertTrue(any(c.args[3]=="RISING PRICE" for c in calls))
        self.assertTrue(any(c.args[3]=="▣ RESULTBOT › STRATEGY PLAN"
                            for c in calls))
        calls=self.rendered("STRATEGY",entries,selected=1)
        self.assertTrue(any(c.args[3]=="▼" and c.args[4]==15 for c in calls))
        self.assertTrue(any(c.args[3]=="▲" and c.args[4]==18 for c in calls))

    def test_render_submenu_breadcrumb_and_mouse_rows_unchanged(self):
        calls=self.rendered("STRATEGY DOWN",
                            [("01","CUT","Cut losses in stages")],selected=0)
        self.assertTrue(any(c.args[3] ==
                            "▣ RESULTBOT › STRATEGY PLAN › FALLING PRICE"
                            for c in calls))
        for h,w in ((24,80),(32,100)):
            top=(h-(24 if h<30 else 29))//2
            row=top+(10 if h<30 else 14)
            step=1 if h<30 else 2
            self.assertEqual(ui.mouse_hit_test("STRATEGY",h,w,18,row),
                             ("item",0))
            self.assertEqual(ui.mouse_hit_test("STRATEGY",h,w,18,row+step),
                             ("item",1))
            self.assertEqual(ui.mouse_hit_test("STRATEGY DOWN",h,w,18,row),
                             ("item",0))

    def test_256_color_palette_sets_4_distinct_arrow_pairs(self):
        with patch.object(ui.curses,"start_color"), \
             patch.object(ui.curses,"init_pair") as init, \
             patch.object(ui.curses,"COLORS",256):
            ui.colors()
        mapping={c.args[0]:c.args[1:] for c in init.call_args_list}
        self.assertEqual(len(mapping),18)
        self.assertEqual(mapping[15],(196,253))
        self.assertEqual(mapping[16],(46,253))
        self.assertEqual(mapping[17],(196,221))
        self.assertEqual(mapping[18],(46,221))


if __name__=="__main__":
    unittest.main()
