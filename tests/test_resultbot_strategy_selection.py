"""Strategy selector tests: no Dhan or Telegram calls, isolated temporary state."""
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from resultbot import position_strategy as ps


class TestStrategySelector(TestCase):
    def test_default_plan_is_test_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "FILE", Path(tmp) / "strategy.json"):
                status=ps.selection_json()
                self.assertEqual(status["down_mode"], "CUT")
                self.assertEqual(status["up_mode"], "TAKE_PROFIT")
                self.assertFalse(status["execution_enabled"])
                self.assertEqual(status["live_amo_strategy"], "ENTRY_ONLY")

    def test_all_nine_combinations_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "FILE", Path(tmp) / "strategy.json"):
                for down in ("CUT", "AVERAGE", "STOP_ONLY"):
                    for up in ("TAKE_PROFIT", "ADD_UP", "TARGET_ONLY"):
                        selection=ps.choose_modes(down,up)
                        self.assertEqual(selection["down_mode"], down)
                        self.assertEqual(selection["up_mode"], up)
                        self.assertFalse(selection["execution_enabled"])
                        self.assertEqual(ps.selection_json(), selection)

    def test_reject_unsafe_mode_without_mutating_saved_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "FILE", Path(tmp) / "strategy.json"):
                ps.choose_modes("AVERAGE","ADD_UP")
                before=ps.FILE.read_bytes()
                with self.assertRaises(ValueError):
                    ps.choose_modes("NOT_A_MODE",None)
                self.assertEqual(ps.FILE.read_bytes(), before)

    def test_saved_executor_flag_never_becomes_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ps, "FILE", Path(tmp) / "strategy.json"):
                ps.choose_modes("CUT","TARGET_ONLY")
                self.assertFalse(ps.load()["execution_enabled"])
                self.assertNotIn("access-token",ps.FILE.read_text())
