"""Offline tests for the ResultBot strategy stage setup wizard."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resultbot import position_strategy as ps
from resultbot import strategy_stage_wizard as flow


def finish(w, answers):
    for value in answers:
        done=flow.advance(w,str(value))
    return done


class StrategyStageWizardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file=Path(self.temp.name)/"position_strategy.json"
        self.patch=patch.object(ps,"FILE",self.file)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_averaging_three_buy_stages(self):
        plan=ps.selection_json()
        w=flow.begin(plan,"down","AVERAGE")
        self.assertTrue(finish(w,[3,2,1000,4.5,1500,7,2000,9]))
        result=ps.apply_side_configuration(flow.request(w))
        self.assertEqual(result["down_mode"],"AVERAGE")
        self.assertEqual(result["average_down"],[2,4.5,7])
        self.assertEqual(result["average_down_amounts_inr"],[1000,1500,2000])
        self.assertFalse(result["execution_enabled"])
        self.assertGreaterEqual(result["total_buy_cap_inr"],500+1000+1500+2000)

    def test_cut_loss_uses_original_share_per_stage_and_cumulative_storage(self):
        w=flow.begin(ps.selection_json(),"down","CUT")
        self.assertTrue(finish(w,[2,3,25,6,30,9]))
        result=ps.apply_side_configuration(flow.request(w))
        self.assertEqual(result["cut_down"],[[3.0,25.0],[6.0,55.0]])
        self.assertEqual(result["down_mode"],"CUT")

    def test_profit_booking_and_add_up_are_independent(self):
        w=flow.begin(ps.selection_json(),"up","TAKE_PROFIT")
        self.assertTrue(finish(w,[2,2,20,4,35,6]))
        ps.apply_side_configuration(flow.request(w))
        w=flow.begin(ps.selection_json(),"down","AVERAGE")
        self.assertTrue(finish(w,[1,3,999,9]))
        ps.apply_side_configuration(flow.request(w))
        saved=ps.selection_json()
        self.assertEqual(saved["up_mode"],"TAKE_PROFIT")
        self.assertEqual(saved["take_profit"],[[2.0,20.0],[4.0,55.0]])
        self.assertEqual(saved["average_down_amounts_inr"],[999])

    def test_adding_up_with_a_custom_amount(self):
        w=flow.begin(ps.selection_json(),"up","ADD_UP")
        self.assertTrue(finish(w,[1,3,12500,8]))
        saved=ps.apply_side_configuration(flow.request(w))
        self.assertEqual(saved["add_up"],[3.0])
        self.assertEqual(saved["add_up_amounts_inr"],[12500])
        self.assertGreaterEqual(saved["total_buy_cap_inr"],13000)

    def test_hold_modes_ask_only_terminal_exit(self):
        for side,mode,value in (("down","STOP_ONLY",12),("up","TARGET_ONLY",15)):
            w=flow.begin(ps.selection_json(),side,mode)
            self.assertEqual(w["questions"][0]["kind"],"exit")
            self.assertTrue(finish(w,[value]))
            saved=ps.apply_side_configuration(flow.request(w))
            self.assertEqual(saved[side+"_mode"],mode)
            self.assertEqual(saved["stop_pct" if side=="down" else "target_pct"],value)

    def test_invalid_trigger_is_rejected_without_writing_file(self):
        w=flow.begin(ps.selection_json(),"down","AVERAGE")
        self.assertFalse(flow.advance(w,"2"))
        self.assertFalse(flow.advance(w,"4"))
        self.assertFalse(flow.advance(w,"500"))
        with self.assertRaisesRegex(ValueError,"greater"):
            flow.advance(w,"3")
        self.assertFalse(self.file.exists())

    def test_partial_sales_leave_remainder(self):
        w=flow.begin(ps.selection_json(),"up","TAKE_PROFIT")
        for v in (2,2,60,4):
            flow.advance(w,str(v))
        with self.assertRaisesRegex(ValueError,"leave shares"):
            flow.advance(w,"40")
        self.assertFalse(self.file.exists())

    def test_review_never_saves_until_explicit_confirmation(self):
        w=flow.begin(ps.selection_json(),"down","CUT")
        self.assertTrue(finish(w,[1,3,25,9]))
        self.assertFalse(self.file.exists())
        self.assertEqual(flow.request(w)["stages"][0]["sell_pct"],25)
        ps.apply_side_configuration(flow.request(w))
        self.assertTrue(self.file.exists())

    def test_suspicious_payload_never_enables_execution(self):
        with self.assertRaises(ValueError):
            ps.apply_side_configuration({
                "side":"down","mode":"CUT","stages":[],
                "terminal_pct":9,"execution_enabled":True
            })
        self.assertFalse(self.file.exists())

    def test_no_buying_cap_imposed_on_user_staged_amounts(self):
        w=flow.begin(ps.selection_json(),"down","AVERAGE")
        self.assertTrue(finish(w,[1,3,100000,9]))
        saved=ps.apply_side_configuration(flow.request(w))
        self.assertEqual(saved["average_down_amounts_inr"],[100000])
        self.assertGreaterEqual(saved["total_buy_cap_inr"],100500)


if __name__=="__main__":
    unittest.main()
