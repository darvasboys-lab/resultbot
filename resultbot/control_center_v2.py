#!/usr/bin/env python3
"""ResultBot Control Center V2: presentation-only terminal frontend."""
from __future__ import annotations
import curses
import json
import subprocess
from pathlib import Path

ROOT = Path.home() / "resultbot-data"
PY = Path.home() / "uditbot/venv/bin/python"
BACKEND = ROOT / "equity_amo_live.py"
MENU = [
    ("01", "ALERT SOURCE", "Choose Telegram signal groups"),
    ("02", "ORDER BUDGET", "Set investment per order"),
    ("03", "TRADING SWITCH", "Turn AMO on or off"),
    ("04", "OVERVIEW", "Settings and order records"),
    ("00", "BACK", "Return to terminal"),
]
SOURCES = [
    ("TEST", "Owner-authorized Test Group"),
    ("EARNINGS_PULSE", "Excellent Results channel"),
    ("BOTH", "Both authorized sources"),
]
ENTER = (10, 13, curses.KEY_ENTER)

def backend(*args):
    try:
        result = subprocess.run([str(PY), str(BACKEND), *args],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            return {}, (result.stderr or result.stdout).strip() or "Request rejected"
        return json.loads(result.stdout), ""
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {}, str(error)

def colors():
    curses.start_color()
    if curses.COLORS >= 256:
        scheme = [(255, 18), (235, 253), (255, 19), (235, 255),
                  (235, 221), (28, 255), (160, 255)]
    else:
        scheme = [(curses.COLOR_WHITE, curses.COLOR_BLUE),
                  (curses.COLOR_BLACK, curses.COLOR_WHITE),
                  (curses.COLOR_WHITE, curses.COLOR_BLUE),
                  (curses.COLOR_BLACK, curses.COLOR_WHITE),
                  (curses.COLOR_BLACK, curses.COLOR_YELLOW),
                  (curses.COLOR_GREEN, curses.COLOR_WHITE),
                  (curses.COLOR_RED, curses.COLOR_WHITE)]
    for num, pair in enumerate(scheme, 1):
        curses.init_pair(num, *pair)

def draw_text(win, y, x, value, style=2, limit=999):
    h, w = win.getmaxyx()
    if not (0 <= y < h and 0 <= x < w - 1):
        return
    try:
        win.addnstr(y, x, str(value).replace("\n", " "),
                    min(limit, w - x - 1), curses.color_pair(style))
    except curses.error:
        pass

def line(win, y, x, width, style=2):
    draw_text(win, y, x, " " * max(0, width), style, width)

def render(win, state, page, entries, selection, focus, note="", amount=""):
    h, w = win.getmaxyx()
    win.bkgd(" ", curses.color_pair(1))
    win.erase()
    if h < 23 or w < 74:
        draw_text(win, 2, 2, "Please enlarge terminal to 74 x 23. Esc to exit.", 1)
        win.refresh()
        return False
    width = min(w - 4, 98)
    left = (w - width) // 2
    top = (h - 23) // 2
    for row in range(top, top + 23):
        line(win, row, left, width)
    for row in range(top, top + 3):
        line(win, row, left, width, 3)
    draw_text(win, top + 1, left + 3, "RESULTBOT  /  " + page, 3)
    draw_text(win, top + 3, left + 3, "CURRENT CONFIGURATION", 2)
    enabled = state.get("amo_enabled")
    budget = state.get("per_order_budget_inr")
    source = state.get("source", "UNKNOWN")
    cards = [
        ("TRADING AMO", "ON" if enabled else "OFF", 6 if enabled else 7),
        ("ALERT SOURCE", source, 4),
        ("PER ORDER", "INR {:,}".format(budget) if isinstance(budget, int) else "UNKNOWN", 4)
    ]
    card_w = (width - 12) // 3
    for i, (label, val, style) in enumerate(cards):
        x = left + 3 + i * (card_w + 2)
        for row in range(top + 5, top + 9):
            line(win, row, x, card_w, 4)
        draw_text(win, top + 6, x + 2, label, 4, card_w - 3)
        draw_text(win, top + 7, x + 2, val, style, card_w - 3)
    draw_text(win, top + 10, left + 3, "─" * (width - 6), 2)
    draw_text(win, top + 11, left + 3,
              "CONFIGURATION MENU" if page == "CONTROL CENTER" else page, 2)
    for i, (code, label, description) in enumerate(entries[:5]):
        row = top + 12 + i
        selected = 5 if i == selection and focus == 0 else 2
        line(win, row, left + 3, width - 6, selected)
        draw_text(win, row, left + 5, (code + "  " + label).strip(), selected, 35)
        draw_text(win, row, left + 40, description, selected, width - 44)
    if page == "ORDER BUDGET":
        line(win, top + 15, left + 5, min(38, width - 10), 5)
        draw_text(win, top + 15, left + 7, "INR " + amount + "_", 5, 32)
    if note:
        draw_text(win, top + 18, left + 3, note, 7 if "FAILED" in note or "BLOCKED" in note else 2, width - 6)
    draw_text(win, top + 19, left + 3, "Up/Down: move   Tab: OK/Cancel   Enter: select   Esc: back", 2, width - 6)
    draw_text(win, top + 21, left + 3, "Saved settings • Future signals only", 2)
    draw_text(win, top + 21, left + width - 23, "[ OK ]", 5 if focus == 1 else 4)
    draw_text(win, top + 21, left + width - 13, "[ Cancel ]", 5 if focus == 2 else 4)
    win.refresh()
    return True

def start(win):
    try: curses.curs_set(0)
    except curses.error: pass
    win.keypad(True)
    colors()
    page = "CONTROL CENTER"
    index = 0
    focus = 0
    note = ""
    amount = ""
    fresh_amount = True
    overview_offset = 0
    while True:
        saved, error = backend("settings", "--show")
        if error:
            note = "SETTINGS UNAVAILABLE: " + error
            saved = {}
        if page == "CONTROL CENTER":
            descriptions = [
                str(saved.get("source", "UNKNOWN")),
                "INR {} / order".format(saved.get("per_order_budget_inr", "?")),
                "AMO ON" if saved.get("amo_enabled") else "AMO OFF",
                "View existing orders",
                "Exit",
            ]
            entries = [(code, name, descriptions[i])
                       for i, (code, name, _) in enumerate(MENU)]
            selection = index
        elif page == "ALERT SOURCE":
            entries = [(str(i + 1).zfill(2), name, detail)
                       for i, (name, detail) in enumerate(SOURCES)]
            selection = index
        elif page == "ORDER BUDGET":
            entries = [("INR", "PER-ORDER BUDGET", "Enter any positive whole-rupee amount")]
            selection = -1
        elif page == "TRADING SWITCH":
            desired = "OFF" if saved.get("amo_enabled") else "ON"
            entries = [("!!", "TURN AMO " + desired, "Press Y to confirm; Esc to cancel")]
            selection = -1
        else:
            status, status_error = backend("status")
            orders = status.get("orders", []) if not status_error else []
            entries = [(str(o.get("id", "?")), str(o.get("symbol", "?")),
                        "{}  QTY {}".format(o.get("state", "?"), o.get("qty", 0)))
                       for o in orders[overview_offset:overview_offset + 5]]
            if not entries:
                entries = [("--", "NO ORDER RECORDS", status_error or "No existing orders")]
            selection = -1
        visible = render(win, saved, page, entries, selection,
                         focus, note, amount)
        key = win.getch()
        if not visible:
            if key == 27: return
            continue
        note = ""
        if page == "CONTROL CENTER":
            if key == curses.KEY_UP: index = (index - 1) % len(MENU); focus = 0
            elif key == curses.KEY_DOWN: index = (index + 1) % len(MENU); focus = 0
            elif key == 9: focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB: focus = (focus - 1) % 3
            elif key in ENTER or key == 32:
                if focus == 2 or index == 4: return
                page = ["ALERT SOURCE", "ORDER BUDGET", "TRADING SWITCH", "OVERVIEW"][index]
                focus = 0
                index = 0
                if page == "ORDER BUDGET":
                    amount = str(saved.get("per_order_budget_inr", ""))
                    fresh_amount = True
                if page == "OVERVIEW": overview_offset = 0
            elif key == 27: return
        elif page == "ALERT SOURCE":
            if key == curses.KEY_UP: index = (index - 1) % len(SOURCES); focus = 0
            elif key == curses.KEY_DOWN: index = (index + 1) % len(SOURCES); focus = 0
            elif key == 9: focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB: focus = (focus - 1) % 3
            elif key in ENTER or key == 32:
                if focus == 2:
                    page = "CONTROL CENTER"; index = 0; focus = 0; continue
                value = SOURCES[index][0]
                if value != saved.get("source"):
                    _, problem = backend("settings", "--source", value)
                    note = "NOT SAVED: " + problem if problem else "Saved source: " + value
                page = "CONTROL CENTER"; index = 0; focus = 0
            elif key == 27: page = "CONTROL CENTER"; index = 0; focus = 0
        elif page == "ORDER BUDGET":
            if 48 <= key <= 57:
                amount = chr(key) if fresh_amount else amount + chr(key)
                amount = amount[:19]
                fresh_amount = False
            elif key in (8, 127, curses.KEY_BACKSPACE):
                amount = amount[:-1]; fresh_amount = False
            elif key == 9: focus = (focus + 1) % 3
            elif key in ENTER:
                if focus == 2:
                    page = "CONTROL CENTER"; index = 1; focus = 0; continue
                value = int(amount or "0")
                if 0 < value <= 2**63 - 1:
                    _, problem = backend("settings", "--budget", str(value))
                    note = "NOT SAVED: " + problem if problem else "Saved budget: INR {:,}".format(value)
                    page = "CONTROL CENTER"; index = 1; focus = 0
                else:
                    note = "Enter a positive whole-rupee amount"
            elif key == 27: page = "CONTROL CENTER"; index = 1; focus = 0
        elif page == "TRADING SWITCH":
            if key in (ord("Y"), ord("y")):
                if saved.get("amo_enabled"):
                    _, problem = backend("off")
                else:
                    _, problem = backend("preflight")
                    if not problem: _, problem = backend("arm")
                note = "SWITCH FAILED: " + problem if problem else "AMO switch saved"
                page = "CONTROL CENTER"; index = 2; focus = 0
            elif key in (27, ord("n"), ord("N")):
                page = "CONTROL CENTER"; index = 2; focus = 0
        else:
            if key == curses.KEY_DOWN: overview_offset = min(max(0, len(orders) - 5), overview_offset + 1)
            elif key == curses.KEY_UP: overview_offset = max(0, overview_offset - 1)
            elif key in ENTER or key == 27:
                page = "CONTROL CENTER"; index = 3; focus = 0

if __name__ == "__main__":
    curses.wrapper(start)
