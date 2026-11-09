#!/usr/bin/env python3
"""Read-only, colored terminal status for ResultBot. No broker orders or mutations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path.home() / "resultbot-data"
PYTHON = Path.home() / "uditbot/venv/bin/python"
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
GREEN, RED, YELLOW, CYAN, GRAY, BOLD, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[90m", "\033[1m", "\033[0m"
)
C1, C2, C3 = 24, 14, 31


def styled(s: str, color: str) -> str:
    return color + s + RESET if COLOR else s


def fit(s: object, width: int) -> str:
    s = str(s).replace("\n", " ").replace("\r", " ")
    return s if len(s) <= width else s[:width - 1] + "…"


def border(left: str, join: str, right: str, char: str = "─") -> str:
    return left + join.join(char * (n + 2) for n in (C1, C2, C3)) + right


def table_row(name: str, status: str, detail: str, level: str) -> None:
    colors = {"good": GREEN, "bad": RED, "warn": YELLOW, "info": CYAN, "muted": GRAY}
    s = fit("● " + status, C2).ljust(C2)
    left = fit(name, C1).ljust(C1)
    right = fit(detail, C3).ljust(C3)
    print("│ " + left + " │ " + styled(s, colors.get(level, CYAN)) + " │ " + right + " │")


def run(cmd: list[str], timeout: float = 6) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def unit(unit_name: str, user: bool = True) -> tuple[str, str, str]:
    base = ["systemctl"] + (["--user"] if user else [])
    rc, state = run(base + ["is-active", unit_name], 3)
    rc2, enabled = run(base + ["is-enabled", unit_name], 3)
    if rc == 0 and state == "active":
        return "RUNNING", "good", "autostart " + ("yes" if enabled == "enabled" else "no")
    if state == "failed":
        return "FAILED", "bad", "see journalctl for details"
    if state in {"inactive", "deactivating", "activating"}:
        return "STOPPED", "bad", "autostart " + ("yes" if enabled == "enabled" else "no")
    if state == "unknown":
        return "UNKNOWN", "warn", "cannot determine service state"
    if enabled in {"disabled", "masked", "static"} or state in {"not-found", ""}:
        return "NOT RUNNING", "bad", "service unavailable or disabled"
    return "NOT RUNNING", "bad", fit(state or "systemd check failed", 31)


def read_control():
    db = ROOT / "equity_amo_live.sqlite3"
    if not db.is_file():
        return None, "Order state database missing"
    try:
        db_uri = db.as_uri() + "?mode=ro"
        with sqlite3.connect(db_uri, uri=True, timeout=2) as c:
            c.execute("PRAGMA query_only=ON")
            state = c.execute("SELECT live_enabled,armed,source,budget FROM control WHERE id=1").fetchone()
            # ACKED is broker-acknowledged, not an uncertain/unknown intent.
            # Report only genuine submission ambiguity and uncertain cancellations.
            pending = c.execute("""SELECT COUNT(*) FROM intents WHERE state IN
                ('SUBMITTING','UNKNOWN','CANCEL_REQUESTED')""").fetchone()[0]
            last = c.execute("SELECT symbol,state,broker_status,note FROM intents ORDER BY id DESC LIMIT 1").fetchone()
        if not state:
            return None, "Trading settings missing"
        return {"live": bool(state[0]), "amo": bool(state[1]), "source": state[2],
                "budget": state[3], "pending": pending, "last": last}, None
    except (OSError, sqlite3.Error, ValueError) as exc:
        return None, "Trading settings unreadable: " + type(exc).__name__


def read_oco():
    script = ROOT / "oco_guard.py"
    if not script.is_file():
        return None, "OCO script not installed"
    code, output = run([str(PYTHON), str(script), "status"], 6)
    if code != 0:
        return None, "OCO status command failed"
    try:
        status = json.loads(output)
        if not isinstance(status, dict):
            raise ValueError
        return status, None
    except ValueError:
        return None, "OCO status output unreadable"


def quote_policy():
    path = ROOT / "equity_amo_live.py"
    try:
        content = path.read_text()
    except OSError:
        return "UNKNOWN", "warn", "quote module not readable"
    times = re.findall(r"time\.time\(\)\s*-\s*(?:stamp|quote_ts)\s*<=\s*(\d+)", content)
    if len(times) == 2 and times[0] == times[1]:
        return times[0] + " sec", "info", "configured; not a live feed test"
    return "REVIEW", "warn", "quote checks differ or not found"


def broker_open_orders():
    """Read-only broker snapshot, restricted to recorded ResultBot order IDs."""
    try:
        sys.path.insert(0, str(ROOT))
        import oco_guard as guard
        account, token = guard.dh.load_credentials()
        with sqlite3.connect((ROOT / "equity_amo_live.sqlite3").as_uri() + "?mode=ro",
                             uri=True, timeout=2) as c:
            bought = {str(r[0]) for r in c.execute(
                "SELECT order_id FROM intents WHERE order_id != ''"
            )}
        with sqlite3.connect((ROOT / "oco_guard.sqlite3").as_uri() + "?mode=ro",
                             uri=True, timeout=2) as c:
            protected = {str(r[0]) for r in c.execute(
                "SELECT order_id FROM coverage WHERE order_id != ''"
            )}
        orders = guard.api("GET", "/orders", token)
        forever = guard.api("GET", "/forever/orders", token)
        active = {"TRANSIT", "PENDING", "PART_TRADED", "CONFIRM"}
        buy = {str(o.get("orderId")) for o in orders
               if str(o.get("orderId")) in bought and
               str(o.get("orderStatus") or "").upper() in active}
        oco = {str(o.get("orderId")) for o in forever
               if str(o.get("orderId")) in protected and
               str(o.get("orderStatus") or "").upper() in active}
        return len(buy), len(oco), None
    except Exception as exc:
        return 0, 0, type(exc).__name__


def main():
    now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y · %I:%M %p IST")
    print()
    print(styled("  RESULTBOT  /  SYSTEM HEALTH", BOLD))
    print("  " + now)
    print(border("┌", "┬", "┐"))
    print("│ " + f"{'COMPONENT':<{C1}}" + " │ " + f"{'STATUS':<{C2}}" + " │ " + f"{'DETAILS':<{C3}}" + " │")
    print(border("├", "┼", "┤"))
    c, error = read_control()
    if error:
        table_row("Trading configuration", "UNAVAILABLE", error, "bad")
    else:
        table_row("LIVE intraday BUY", "ON" if c["live"] else "OFF", "owner trading switch", "good" if c["live"] else "bad")
        table_row("Alert sources", str(c["source"]), "selected Telegram inputs", "good" if c["source"] in ("TEST","EARNINGS_PULSE","BOTH") else "warn")
        table_row("Per-order budget", "INR " + str(c["budget"]), "configured per-order setting", "good")
    print(border("├", "┼", "┤"))
    s, lev, detail = unit("resultbot-excellent-watch.service")
    table_row("Excellent signal listener", s, detail, lev)
    s, lev, detail = unit("resultbot-oco-guard.service")
    table_row("Automatic OCO guard", s, detail, lev)
    print(border("├", "┼", "┤"))
    oco, oco_error = read_oco()
    if oco_error:
        table_row("Broker OCO records", "UNKNOWN", oco_error, "warn")
    else:
        records = oco.get("protection") or []
        errors = oco.get("unprotected_errors") or []
        active = sum(1 for x in records if isinstance(x, dict) and x.get("state") == "ACTIVE")
        concern = sum(1 for x in records if isinstance(x, dict) and x.get("state") in
                      ("UNKNOWN", "ERROR", "FAILED", "REVIEW", "CANCEL_REQUESTED", "SUBMITTING"))
        if errors or concern:
            table_row("Broker OCO records", "REVIEW", f"{active} active; {len(errors) + concern} warnings", "bad")
        elif not oco.get("enabled", False):
            table_row("Broker OCO records", "DISABLED", f"{active} active on last check", "bad")
        elif active:
            table_row("Broker OCO records", f"{active} ACTIVE", "last stored broker verification", "good")
        else:
            table_row("Broker OCO records", "0 OPEN", "no active protection recorded", "good")
    if c:
        pending = c["pending"]
        table_row("Unresolved intents", "CLEAR" if not pending else str(pending) + " REVIEW",
                  "0 uncertain broker submissions" if not pending else "check UNKNOWN / SUBMITTING", "good" if not pending else "warn")
    pending_buy, pending_oco, broker_error = broker_open_orders()
    if broker_error:
        table_row("Broker open orders", "UNKNOWN", "Dhan check: " + broker_error, "warn")
    else:
        total = pending_buy + pending_oco
        table_row("Broker open orders", str(total) + " OPEN",
                  f"ResultBot BUY {pending_buy}; OCO {pending_oco}",
                  "good" if total == 0 else "info")
    s, lev, detail = quote_policy()
    table_row("Yahoo quote policy", s, detail, "good" if lev == "info" else lev)
    print(border("├", "┼", "┤"))
    if c:
        table_row("After-market BUY (AMO)", "ON" if c["amo"] else "OFF",
                  "independent owner switch", "good" if c["amo"] else "bad")
    else:
        table_row("After-market BUY (AMO)", "UNKNOWN", "trading settings unavailable", "warn")
    print(border("└", "┴", "┘"))
    print(styled("  ● Green = verified ON / running   ", GREEN) + styled("● Red = OFF / stopped   ", RED) + styled("● Yellow = review", YELLOW))
    print("  ACKED orders are not unresolved intents; this view does not query live broker orders.")
    print("  OFF can be intentional. Service RUNNING does not prove broker connectivity.")
    print("  Read-only check: no orders sent, settings changed, or services restarted.")
    print()


if __name__ == "__main__":
    main()
