#!/usr/bin/env python3
"""ResultBot Control Center Premium: card-based terminal frontend (no trading code changes)."""
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
    """Blue background, soft-grey dashboard, white cards and yellow focus."""
    curses.start_color()
    if curses.COLORS >= 256:
        scheme = [
            (255, 18), (236, 253), (255, 24), (238, 255),
            (235, 221), (28, 255), (160, 255), (241, 253),
            (246, 255), (238, 251), (236, 255),
        ]
    else:
        scheme = [
            (curses.COLOR_WHITE, curses.COLOR_BLUE),
            (curses.COLOR_BLACK, curses.COLOR_WHITE),
            (curses.COLOR_WHITE, curses.COLOR_BLUE),
            (curses.COLOR_BLACK, curses.COLOR_WHITE),
            (curses.COLOR_BLACK, curses.COLOR_YELLOW),
            (curses.COLOR_GREEN, curses.COLOR_WHITE),
            (curses.COLOR_RED, curses.COLOR_WHITE),
            (curses.COLOR_BLACK, curses.COLOR_WHITE),
            (curses.COLOR_BLACK, curses.COLOR_WHITE),
            (curses.COLOR_BLACK, curses.COLOR_WHITE),
            (curses.COLOR_BLACK, curses.COLOR_WHITE),
        ]
    for num, pair in enumerate(scheme, 1):
        curses.init_pair(num, *pair)


def draw_text(win, y, x, value, style=2, limit=999):
    h, w = win.getmaxyx()
    if not (0 <= y < h and 0 <= x < w - 1):
        return
    try:
        win.addnstr(y, x, str(value).replace("\n", " ").replace("\r", " "),
                    min(limit, w - x - 1), curses.color_pair(style))
    except curses.error:
        pass


def line(win, y, x, width, style=2):
    draw_text(win, y, x, " " * max(0, width), style, width)


def card(win, y, x, width, heading, value, description, value_style=4, compact=False):
    height = 4 if compact else 5
    for row in range(y, y + height):
        line(win, row, x, width, 4)
    draw_text(win, y, x, "╭" + "─" * (width - 2) + "╮", 9, width)
    draw_text(win, y + height - 1, x, "╰" + "─" * (width - 2) + "╯", 9, width)
    for row in range(y + 1, y + height - 1):
        draw_text(win, row, x, "│", 9)
        draw_text(win, row, x + width - 1, "│", 9)
    draw_text(win, y + 1, x + 2, heading, 4, width - 4)
    draw_text(win, y + 2, x + 2, value, value_style, width - 4)
    if not compact:
        draw_text(win, y + 3, x + 2, description, 4, width - 4)


def render(win, state, page, entries, selection, focus, note="", amount=""):
    h, w = win.getmaxyx()
    win.bkgd(" ", curses.color_pair(1))
    win.erase()
    if h < 24 or w < 78:
        draw_text(win, 2, 2, "Resize terminal to at least 78 columns x 24 rows.", 1)
        draw_text(win, 4, 2, "Press Esc to exit.", 1)
        win.refresh()
        return False
    compact = h < 30
    height = 24 if compact else 29
    width = min(w - 4, 98)
    left = (w - width) // 2
    top = (h - height) // 2

    for row in range(top, top + height):
        line(win, row, left, width, 2)
    for row in range(top, top + (2 if compact else 3)):
        line(win, row, left, width, 3)
    draw_text(win, top + 1, left + 3, "▣ RESULTBOT  /  " + page, 3, width - 30)
    draw_text(win, top + 1, left + width - 19, "● LIVE SETTINGS", 3, 17)

    draw_text(win, top + (3 if compact else 4), left + 3,
              "CURRENT CONFIGURATION", 8)
    budget = state.get("per_order_budget_inr")
    amount_label = "₹{:,.0f}".format(budget) if isinstance(budget, int) else "Unavailable"
    on = state.get("amo_enabled")
    cards = (
        ("TRADING AMO", "● ON" if on else "○ OFF" if on is False else "UNKNOWN",
         "Trading enabled" if on else "Trading paused", 6 if on else 7 if on is False else 4),
        ("ALERT SOURCE", str(state.get("source", "UNKNOWN")), "Telegram sources", 4),
        ("PER ORDER", amount_label, "Future orders only", 4),
    )
    card_width = (width - 10) // 3
    for i, props in enumerate(cards):
        card(win, top + (4 if compact else 5),
             left + 3 + i * (card_width + 2), card_width,
             *props, compact=compact)

    draw_text(win, top + (8 if compact else 11), left + 3,
              "─" * (width - 6), 8, width - 6)
    draw_text(win, top + (9 if compact else 12), left + 3,
              "CONFIGURATION MENU" if page == "CONTROL CENTER" else page, 2, width - 6)
    row_start = top + (10 if compact else 14)
    if page == "ORDER BUDGET":
        draw_text(win, row_start, left + 5,
                  "Enter the investment amount for EACH new order:", 2)
        field_style = 5 if focus == 0 else 4
        line(win, row_start + 2, left + 5, min(45, width - 12), field_style)
        draw_text(win, row_start + 2, left + 7,
                  "₹ " + amount + "▏", field_style, min(40, width - 16))
        draw_text(win, row_start + 4, left + 5,
                  "Whole rupees  ·  Enter Save  ·  Esc Cancel", 8)
    elif page == "TRADING SWITCH":
        desired = "OFF" if state.get("amo_enabled") else "ON"
        draw_text(win, row_start + 1, left + 5,
                  "CHANGE AMO STATE TO " + desired + "?", 2)
        draw_text(win, row_start + 3, left + 5,
                  "Press Y to confirm, N / Esc to cancel.", 2)
        draw_text(win, row_start + 5, left + 5,
                  "Existing broker orders are not cancelled by switching OFF.", 8)
    else:
        for i, (code, label, description) in enumerate(entries[:5]):
            row_y = row_start + 2 * i
            selected = 5 if i == selection and focus == 0 else 2
            line(win, row_y, left + 3, width - 6, selected)
            if page == "ALERT SOURCE":
                marker = "◉" if label == state.get("source") else "○"
                code = marker + " " + code
            draw_text(win, row_y, left + 5, str(code), selected, 7)
            draw_text(win, row_y, left + 12, label, selected, 27)
            desc_x = left + max(42, width // 2)
            draw_text(win, row_y, desc_x, description, selected,
                      width - (desc_x - left) - 4)

    footer = top + height - 4
    for row in range(footer, footer + 4):
        line(win, row, left, width, 10)
    draw_text(win, footer, left + 3, "─" * (width - 6), 10, width - 6)
    if note:
        is_error = any(word in note for word in
                       ("FAILED", "BLOCKED", "NOT SAVED", "UNAVAILABLE"))
        draw_text(win, footer + 1, left + 3, note,
                  7 if is_error else 10, width - 6)
    else:
        draw_text(win, footer + 1, left + 3,
                  "Changes apply to future signals  ·  Existing orders unchanged",
                  10, width - 6)
    draw_text(win, footer + 2, left + 3,
              "↑↓ Move  ·  Tab Buttons  ·  Enter Select  ·  Esc Back",
              10, width - 28)
    draw_text(win, footer + 2, left + width - 21,
              " OK ", 5 if focus == 1 else 11)
    draw_text(win, footer + 2, left + width - 13,
              " Cancel ", 5 if focus == 2 else 11)
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
