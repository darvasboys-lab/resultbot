#!/usr/bin/env python3
"""Read-only ResultBot morning Telegram health report.

Uses the already-installed broker verifier and Telegram sender. Does not
refresh tokens, submit orders, alter configuration or access private keys.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path.home() / "resultbot-data"
DB = ROOT / "equity_amo_live.sqlite3"
REFRESH_SERVICE = "resultbot-dhan-token-refresh.service"
REFRESH_TIMER = "resultbot-dhan-token-refresh.timer"

# Preserve the existing ResultBot authentication/account checks and paired
# Telegram destination. The hourly broker monitor keeps importing this module.
sys.path.insert(0, str(ROOT))
from daily_health_notification import broker, send  # noqa: E402


def unit_active(unit: str) -> bool | None:
    try:
        p = subprocess.run(
            ["systemctl", "--user", "is-active", unit], capture_output=True,
            text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.returncode == 0 and p.stdout.strip() == "active"


def expected_renewal(now: datetime) -> datetime:
    """At 08:05, the most recent required run is yesterday at 20:00 IST."""
    now = now.astimezone(IST)
    scheduled = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return scheduled if now >= scheduled else scheduled - timedelta(days=1)


def last_refresh(now: datetime) -> str:
    """Return SUCCESS / FAILED / UNKNOWN for last scheduled 20:00 window."""
    if unit_active(REFRESH_TIMER) is not True:
        return "UNKNOWN"  # inactive timer is not a verified renewal
    scheduled = expected_renewal(now)
    try:
        p = subprocess.run(
            ["journalctl", "--user", "-u", REFRESH_SERVICE,
             "--no-pager", "-o", "json", "-n", "600"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if p.returncode != 0:
            return "UNKNOWN"
        events: list[tuple[datetime, str]] = []
        for line in p.stdout.splitlines():
            try:
                e = json.loads(line)
                stamp = datetime.fromtimestamp(
                    int(e["__REALTIME_TIMESTAMP"]) / 1_000_000, IST
                )
            except (ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
                continue
            if not scheduled - timedelta(seconds=5) <= stamp <= now:
                continue
            msg = str(e.get("MESSAGE", ""))
            if "SUCCESS: ResultBot Dhan token auto-refreshed and account verified." in msg:
                events.append((stamp, "SUCCESS"))
            elif ("DHAN READ-ONLY ERROR:" in msg or
                  "Failed with result" in msg or
                  "Main process exited, code=exited, status=" in msg):
                events.append((stamp, "FAILED"))
        if not events:
            return "UNKNOWN"
        return max(events, key=lambda v: v[0])[1]
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"


def token_expiry(raw: str, now: datetime) -> tuple[str, str]:
    raw = (raw or "").strip()
    value = None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            value = datetime.strptime(raw, fmt)
            break
        except ValueError:
            pass
    if value is None:
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return "UNVERIFIED", "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    value = value.astimezone(IST)
    return value.strftime("%d %b %Y, %I:%M %p IST"), (
        "good" if value > now else "bad"
    )


def settings() -> tuple[tuple[str, str], ...]:
    if not DB.is_file() or DB.is_symlink():
        return (("UNKNOWN", "unknown"),) * 4
    try:
        with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT live_enabled, armed, source, budget FROM control WHERE id=1"
            ).fetchone()
        if row is None:
            raise ValueError("Missing control record")
        live, amo = int(row[0]), int(row[1])
        if live not in (0, 1) or amo not in (0, 1):
            raise ValueError("Invalid switch")
        source = str(row[2] or "").strip().upper()
        if source not in ("TEST", "OWNER", "BOTH"):
            raise ValueError("Invalid alert source")
        amount = Decimal(str(row[3]))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Invalid order budget")
        return (
            ("ON" if live else "OFF", "good"),
            ("ON" if amo else "OFF", "good"),
            (source, "good"),
            (f"₹{amount:,.0f}" if amount == amount.to_integral_value()
             else f"₹{amount:,.2f}", "good"),
        )
    except (OSError, ValueError, TypeError, sqlite3.Error, InvalidOperation):
        return (("UNKNOWN", "unknown"),) * 4


def dot(state: str) -> str:
    return {"good": "🟢", "unknown": "🟡", "bad": "🔴"}[state]


def section(states: list[str]) -> str:
    if "bad" in states:
        return "bad"
    return "good" if all(x == "good" for x in states) else "unknown"


def service_state(unit: str) -> tuple[str, str]:
    active = unit_active(unit)
    if active is True:
        return "RUNNING", "good"
    if active is False:
        return "NOT RUNNING", "bad"
    return "UNVERIFIED", "unknown"


def report(now: datetime | None = None) -> str:
    now = (now or datetime.now(IST)).astimezone(IST)
    try:
        status, expiry_raw = broker()  # verified_profile account check, read-only
    except Exception:
        status, expiry_raw = "FAILED", "UNVERIFIED"
    connected = status == "CONNECTED"
    expiry_display, expiry_state = token_expiry(str(expiry_raw), now)
    refreshed = last_refresh(now)
    timer_running = unit_active(REFRESH_TIMER)
    refresh_state = ("good" if refreshed == "SUCCESS" and timer_running is True
                     else "bad" if refreshed == "FAILED" or timer_running is False
                     else "unknown")
    broker_state = "good" if connected else "bad"
    expiry_state = expiry_state if connected else "unknown"
    auth_states = [refresh_state, broker_state, broker_state, expiry_state]
    trade_labels = ["LIVE", "AMO", "Sources", "Per Order"]
    trade_settings = settings()
    services = [
        ("Listener", "resultbot-excellent-watch.service"),
        ("OCO Guard", "resultbot-oco-guard.service"),
        ("Telegram Control", "resultbot-telegram-control.service"),
    ]
    service_results = [(name, *service_state(unit)) for name, unit in services]
    lines = [
        "🤖 RESULTBOT — DAILY HEALTH",
        now.strftime("%d %b %Y · %I:%M %p IST"),
        "",
        f"{dot(section(auth_states))} DHAN AUTHENTICATION",
        f"{dot(refresh_state)} Token Refresh: {refreshed}",
        f"{dot(broker_state)} Dhan Connection: {'VERIFIED' if connected else 'FAILED'}",
        f"{dot(broker_state)} Account Identity: {'MATCHED' if connected else 'UNVERIFIED'}",
        f"{dot(expiry_state)} Token Valid Until: {expiry_display}",
        "",
        f"{dot(section([state for _, state in trade_settings]))} TRADING SETTINGS",
    ]
    lines.extend(
        f"{dot(state)} {name}: {value}"
        for name, (value, state) in zip(trade_labels, trade_settings)
    )
    lines.extend(["", f"{dot(section([state for _, _, state in service_results]))} SERVICES"])
    lines.extend(f"{dot(state)} {name}: {value}" for name, value, state in service_results)
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="ResultBot read-only 08:05 daily health report")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--send", action="store_true")
    args = p.parse_args()
    message = report()
    if args.preview:
        print(message)
        return 0
    try:
        send(message)  # Existing Telegram control-group binding, no changes
        print("SENT: ResultBot daily health report")
        return 0
    except Exception as exc:
        print(f"NOT SENT: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
