import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

SOURCE = Path(__file__).parents[1] / "deploy" / "resultbot" / "daily_health_report_v2.py"
sys.modules["daily_health_notification"] = types.SimpleNamespace(
    broker=lambda: ("CONNECTED", "17/11/2026 20:00"),
    send=lambda message: None,
)
spec = importlib.util.spec_from_file_location("daily_health_report_v2", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
IST = ZoneInfo("Asia/Kolkata")
now = datetime(2026, 11, 17, 8, 5, tzinfo=IST)


class ResultBotHealthTests(unittest.TestCase):
    def test_morning_checks_yesterday_evening(self):
        self.assertEqual(
            m.expected_renewal(now),
            datetime(2026, 11, 16, 20, 0, tzinfo=IST),
        )

    def test_expiry(self):
        self.assertEqual(m.token_expiry("17/11/2026 20:00", now), ("17 Nov, 20:00", "good"))
        self.assertEqual(m.token_expiry("16/11/2026 20:00", now)[1], "bad")
        self.assertEqual(m.token_expiry("UNVERIFIED", now)[1], "unknown")

    def test_green_format_and_removed_sections(self):
        with patch.object(m, "unit_active", return_value=True), \
             patch.object(m, "last_refresh", return_value="SUCCESS"), \
             patch.object(m, "settings", return_value=(
                 ("ON", "good"), ("ON", "good"),
                 ("BOTH", "good"), ("₹19,500", "good"),
             )):
            report = m.report(now)
        for expected in (
            "🟢 DHAN AUTHENTICATION", "🟢 Token Refresh: SUCCESS",
            "🟢 TRADING SETTINGS", "🟢 Per Order: ₹19,500",
            "🟢 SERVICES", "🟢 OCO Guard: RUNNING",
            "🟢 Token Validity: 17 Nov, 08:00 PM",
        ):
            self.assertIn(expected, report)
        for excluded in ("ACKED", "uncertain", "OVERALL", "REVIEW REQUIRED"):
            self.assertNotIn(excluded, report)

    def test_failure_never_masked_as_green(self):
        with patch.object(m, "unit_active", return_value=False), \
             patch.object(m, "last_refresh", return_value="UNKNOWN"), \
             patch.object(m, "broker", return_value=("FAILED", "UNVERIFIED")), \
             patch.object(m, "settings", return_value=(("UNKNOWN", "unknown"),) * 4):
            report = m.report(now)
        self.assertIn("🔴 DHAN AUTHENTICATION", report)
        self.assertIn("🔴 Dhan Connection: FAILED", report)
        self.assertIn("🔴 SERVICES", report)

    def test_off_switch_is_not_error(self):
        with patch.object(m, "unit_active", return_value=True), \
             patch.object(m, "last_refresh", return_value="SUCCESS"), \
             patch.object(m, "settings", return_value=(
                 ("OFF", "good"), ("OFF", "good"),
                 ("TEST", "good"), ("₹1,000", "good"),
             )):
            report = m.report(now)
        self.assertIn("🟢 LIVE: OFF", report)
        self.assertIn("🟢 AMO: OFF", report)


if __name__ == "__main__":
    unittest.main()
