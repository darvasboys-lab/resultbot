#!/usr/bin/env python3
"""ResultBot Control Center Premium: card-based terminal frontend (no trading code changes)."""
from __future__ import annotations
import curses
import json
import math
import subprocess
from pathlib import Path
try:
    from . import strategy_stage_wizard as stage_flow
except ImportError:
    import strategy_stage_wizard as stage_flow

ROOT = Path.home() / "resultbot-data"
PY = Path.home() / "uditbot/venv/bin/python"
BACKEND = ROOT / "equity_amo_live.py"
MENU = [
    ("01", "ALERT SOURCE", "Choose Telegram signal groups"),
    ("02", "ORDER BUDGET", "Set investment per order"),
    ("03", "STRATEGY PLAN", "Fall/rise management (TEST only)"),
    ("04", "TRADING SWITCH", "Turn AMO on or off"),
    ("05", "OVERVIEW", "Settings and order records"),
    ("00", "BACK", "Return to terminal"),
]
SOURCES = [
    ("TEST", "Owner-authorized Test Group"),
    ("EARNINGS_PULSE", "Excellent Results channel"),
    ("BOTH", "Both authorized sources"),
]
DOWN_MODES = [("CUT", "Cut losses in stages"), ("AVERAGE", "Average down with shares"), ("STOP_ONLY", "Hold until final stop")]
UP_MODES = [("TAKE_PROFIT", "Book profits in stages"), ("ADD_UP", "Add as price rises"), ("TARGET_ONLY", "Hold until final target")]
STRATEGY_BACKEND = ROOT / "position_strategy.py"
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

def strategy_backend(*args):
    """Read/write the staged TEST-only plan; never Dhan orders."""
    try:
        result = subprocess.run([str(PY), str(STRATEGY_BACKEND), *args],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            return {}, (result.stderr or result.stdout).strip() or "Plan not saved"
        return json.loads(result.stdout), ""
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {}, str(exc)


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


def render(win, state, page, entries, selection, focus, note="", amount="", plan=None, wizard=None, oco_editor=None):
    plan = plan or {}
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
    if page == "CONTROL CENTER":
        summary = "PLAN: {} / {}  [TEST]".format(plan.get("down_mode", "?"), plan.get("up_mode", "?"))
        draw_text(win, top + (9 if compact else 12), left + 25, summary, 8, width - 28)
    row_start = top + (10 if compact else 14)
    row_step = 1 if compact else 2
    if page == "OCO EDIT" and oco_editor:
        phase=oco_editor["phase"]
        label="STOP LOSS" if phase==0 else "TARGET"
        draw_text(win, row_start, left+5,
                  "OCO SETTINGS — {} %  |  STEP {} OF 2".format(label,phase+1),
                  2, width-10)
        draw_text(win, row_start+2, left+5,
                  "Percentage {} from confirmed first BUY executed price".format(
                      "BELOW" if phase==0 else "ABOVE"), 2, width-11)
        field=5 if focus==0 else 4
        line(win,row_start+4,left+5,min(45,width-12),field)
        draw_text(win,row_start+4,left+7,oco_editor["input"]+" % ▏",
                  field,min(40,width-16))
        draw_text(win,row_start+6,left+5,
                  "Dhan averageTradedPrice, not Telegram/Yahoo/limit price",8,width-11)
        draw_text(win,row_start+7,left+5,
                  "Enter: next   Esc: cancel entire OCO settings draft",8,width-11)
    elif page == "OCO CONFIRM" and oco_editor:
        draw_text(win,row_start,left+5,"REVIEW OCO SETTINGS  |  NOT A LIVE BROKER ORDER",2,width-10)
        draw_text(win,row_start+2,left+6,
                  "STOP TRIGGER: -{}% from confirmed Dhan execution VWAP".format(
                      oco_editor["stop_pct"]),2,width-12)
        draw_text(win,row_start+4,left+6,
                  "TARGET: +{}% from confirmed Dhan execution VWAP".format(
                      oco_editor["target_pct"]),2,width-12)
        draw_text(win,row_start+6,left+6,
                  "Tick rounding calculated AFTER a verified full fill",8,width-12)
        draw_text(win,row_start+7,left+6,
                  "No OCO submitted; confirm broker eligibility separately",8,width-12)
        draw_text(win,top+height-6,left+5,
                  "OK: SAVE PERCENTAGES    Cancel: DISCARD",8,width-11)
    elif page == "STRATEGY WIZARD" and wizard:
        q = wizard["questions"][wizard["position"]]
        step = wizard["position"]+1
        total = len(wizard["questions"])
        direction = "FALL" if wizard["side"]=="down" else "RISE"
        draw_text(win, row_start, left + 5,
                  "{} / {}  |  {}: {}  [TEST PLAN]".format(
                      step, total, direction, wizard["mode"]), 8, width - 10)
        draw_text(win, row_start+2, left+5, q["prompt"], 2, width-10)
        field_style = 5 if focus==0 else 4
        line(win, row_start+4, left+5, min(45,width-12), field_style)
        draw_text(win, row_start+4, left+7,
                  wizard["input"] + "▏", field_style, min(39,width-16))
        draw_text(win, row_start+6, left+5,
                  q["hint"], 8, width-10)
        draw_text(win, row_start+7, left+5,
                  "Enter / OK: next    Esc / Cancel: discard draft", 8, width-10)
    elif page == "STRATEGY CONFIRM" and wizard:
        draw_text(win, row_start, left + 5,
                  "REVIEW BEFORE SAVE  |  {}  [TEST PLAN]".format(wizard["mode"]),
                  2, width-10)
        for i, (label, trigger, detail) in enumerate(stage_flow.stage_summary(wizard)[:6]):
            row_y=row_start+2+i
            if row_y >= top+height-5: break
            draw_text(win, row_y, left+6,
                      "{}  {:<18} {}".format(label,trigger,detail),
                      2, width-12)
        draw_text(win, top+height-6, left+5,
                  "OK / Y: SAVE PLAN    Cancel / N: discard", 8, width-11)
    elif page == "ORDER BUDGET":
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
        for i, (code, label, description) in enumerate(entries[:6]):
            row_y = row_start + row_step * i
            selected = 5 if i == selection and focus == 0 else 2
            line(win, row_y, left + 3, width - 6, selected)
            selected_value = (state.get("source") if page == "ALERT SOURCE"
                              else plan.get("down_mode") if page == "STRATEGY DOWN"
                              else plan.get("up_mode") if page == "STRATEGY UP"
                              else None)
            if selected_value is not None:
                marker = "◉" if label == selected_value else "○"
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
              "Mouse Click  ·  ↑↓ Move  ·  Tab Buttons  ·  Enter Select  ·  Esc Back",
              10, width - 28)
    draw_text(win, footer + 2, left + width - 21,
              " OK ", 5 if focus == 1 else 11)
    draw_text(win, footer + 2, left + width - 13,
              " Cancel ", 5 if focus == 2 else 11)
    win.refresh()
    return True



def mouse_hit_test(page, height, width, x, y):
    """Find clickable UI targets; coordinates match render() exactly.

    Returns (action, index). No trading or database operations occur here.
    """
    if height < 24 or width < 78:
        return None, None

    compact = height < 30
    panel_height = 24 if compact else 29
    panel_width = min(width - 4, 98)
    left = (width - panel_width) // 2
    top = (height - panel_height) // 2
    footer_y = top + panel_height - 2

    # Only the visible, unshadowed button rectangles are clickable.
    if y == footer_y:
        if left + panel_width - 21 <= x < left + panel_width - 17:
            return "ok", None
        if left + panel_width - 13 <= x < left + panel_width - 5:
            return "cancel", None

    if page == "CONTROL CENTER":
        # The three status cards are shortcuts to their matching menus.
        card_y = top + (4 if compact else 5)
        card_h = 4 if compact else 5
        card_width = (panel_width - 10) // 3
        if card_y <= y < card_y + card_h:
            for i, target in enumerate((3, 0, 1)):
                card_x = left + 3 + i * (card_width + 2)
                if card_x <= x < card_x + card_width:
                    return "item", target

    if page in ("CONTROL CENTER", "ALERT SOURCE", "STRATEGY",
                "STRATEGY DOWN", "STRATEGY UP", "STRATEGY REVIEW"):
        row_start = top + (10 if compact else 14)
        count = (len(MENU) if page == "CONTROL CENTER" else
                 5 if page == "STRATEGY" else
                 5 if page == "STRATEGY REVIEW" else 3)
        step = 1 if compact else 2
        if left + 3 <= x < left + panel_width - 3:
            for i in range(count):
                if y == row_start + step * i:
                    return "item", i

    if page in ("ORDER BUDGET","STRATEGY WIZARD","OCO EDIT"):
        row_start = top + (10 if compact else 14)
        field_y=row_start + (2 if page=="ORDER BUDGET" else 4)
        if y == field_y and left + 5 <= x < left + min(50,panel_width-7):
            return "input", None

    return None, None


def start(win):
    try: curses.curs_set(0)
    except curses.error: pass
    win.keypad(True)
    colors()
    # Terminals that support xterm mouse reporting send click events here.
    # Unsupported SSH terminals continue working with the keyboard.
    mouse_mask = (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED |
                  getattr(curses, "BUTTON4_PRESSED", 0) |
                  getattr(curses, "BUTTON5_PRESSED", 0))
    try:
        curses.mousemask(mouse_mask)
        curses.mouseinterval(400)
    except curses.error:
        pass
    page = "CONTROL CENTER"
    index = 0
    focus = 0
    note = ""
    amount = ""
    fresh_amount = True
    overview_offset = 0
    wizard = None
    oco_editor = None
    while True:
        plan, plan_error = strategy_backend("settings-json")
        if plan_error:
            plan = {"down_mode": "UNAVAILABLE", "up_mode": "UNAVAILABLE"}
        saved, error = backend("settings", "--show")
        if error:
            note = "SETTINGS UNAVAILABLE: " + error
            saved = {}
        if page == "CONTROL CENTER":
            descriptions = [
                str(saved.get("source", "UNKNOWN")),
                "INR {} / order".format(saved.get("per_order_budget_inr", "?")),
                "{} / {} (TEST)".format(plan.get("down_mode", "?"), plan.get("up_mode", "?")),
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
        elif page == "STRATEGY":
            entries = [
                ("01", "FALLING PRICE", str(plan.get("down_mode", "Unavailable"))),
                ("02", "RISING PRICE", str(plan.get("up_mode", "Unavailable"))),
                ("03", "REVIEW PLAN", "View saved strategy stages"),
                ("04", "OCO SETTINGS", "SL/TP from Dhan executed price"),
                ("00", "BACK", "Return to main menu"),
            ]
            selection = index
        elif page == "STRATEGY DOWN":
            entries = [(str(i+1).zfill(2), code, desc)
                       for i, (code, desc) in enumerate(DOWN_MODES)]
            selection = index
        elif page == "STRATEGY UP":
            entries = [(str(i+1).zfill(2), code, desc)
                       for i, (code, desc) in enumerate(UP_MODES)]
            selection = index
        elif page == "STRATEGY REVIEW":
            entries = [
                ("DN", "FALL: "+str(plan.get("down_mode", "?")),
                 stage_flow.saved_summary(plan,"down")),
                ("UP", "RISE: "+str(plan.get("up_mode", "?")),
                 stage_flow.saved_summary(plan,"up")),
                ("SL", "FINAL STOP", str(plan.get("stop_pct", "?"))+"% below first buy"),
                ("TP", "FINAL TARGET", str(plan.get("target_pct", "?"))+"% above first buy"),
                ("!!", "TEST PLAN", "LIVE stays entry-only Equity AMO"),
            ]
            selection = -1
        elif page in ("STRATEGY WIZARD", "STRATEGY CONFIRM",
                       "OCO EDIT", "OCO CONFIRM"):
            entries=[]
            selection=-1
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
                         focus, note, amount, plan, wizard, oco_editor)
        key = win.getch()
        if key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, buttons = curses.getmouse()
            except curses.error:
                continue
            if buttons & getattr(curses, "BUTTON4_PRESSED", 0):
                key = curses.KEY_UP
            elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
                key = curses.KEY_DOWN
            elif buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                action, clicked = mouse_hit_test(page, *win.getmaxyx(), mx, my)
                if action == "item":
                    index = clicked
                    focus = 0
                    key = 10  # Clicking an entry is equivalent to selecting it.
                elif action == "ok":
                    focus = 1
                    key = ord("y") if page == "TRADING SWITCH" else 10
                elif action == "cancel":
                    focus = 2
                    key = ord("n") if page == "TRADING SWITCH" else 10
                elif action == "input":
                    focus = 0
                    continue
                else:
                    continue
            else:
                continue
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
                if focus == 2 or index == 5: return
                page = ["ALERT SOURCE", "ORDER BUDGET", "STRATEGY", "TRADING SWITCH", "OVERVIEW"][index]
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
        elif page == "STRATEGY":
            if key == curses.KEY_UP: index = (index - 1) % 5; focus = 0
            elif key == curses.KEY_DOWN: index = (index + 1) % 5; focus = 0
            elif key == 9: focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB: focus = (focus - 1) % 3
            elif key in ENTER or key == 32:
                if focus == 2 or index == 4:
                    page = "CONTROL CENTER"; index = 2; focus = 0
                elif index == 3:
                    if plan_error:
                        note = "OCO SETTINGS UNAVAILABLE: " + plan_error
                    else:
                        oco_editor = {
                            "phase": 0,
                            "stop_pct": str(plan["stop_pct"]),
                            "target_pct": str(plan["target_pct"]),
                            "input": str(plan["stop_pct"]),
                            "fresh": True
                        }
                        page = "OCO EDIT"
                        focus = 0
                else:
                    page = ["STRATEGY DOWN", "STRATEGY UP", "STRATEGY REVIEW"][index]
                    if page == "STRATEGY DOWN":
                        index = next((i for i,(v,_) in enumerate(DOWN_MODES)
                                      if v == plan.get("down_mode")), 0)
                    elif page == "STRATEGY UP":
                        index = next((i for i,(v,_) in enumerate(UP_MODES)
                                      if v == plan.get("up_mode")), 0)
                    else:
                        index = 0
                    focus = 0
            elif key == 27: page = "CONTROL CENTER"; index = 2; focus = 0
        elif page in ("STRATEGY DOWN", "STRATEGY UP"):
            modes = DOWN_MODES if page == "STRATEGY DOWN" else UP_MODES
            side = "down" if page == "STRATEGY DOWN" else "up"
            return_index = 0 if side == "down" else 1
            if key == curses.KEY_UP: index = (index - 1) % len(modes); focus = 0
            elif key == curses.KEY_DOWN: index = (index + 1) % len(modes); focus = 0
            elif key == 9: focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB: focus = (focus - 1) % 3
            elif key in ENTER or key == 32:
                if focus == 2:
                    page = "STRATEGY"; index = return_index; focus = 0
                else:
                    wizard = stage_flow.begin(plan, side, modes[index][0])
                    wizard["fresh"] = True
                    page = "STRATEGY WIZARD"
                    focus = 0
            elif key == 27: page = "STRATEGY"; index = return_index; focus = 0
        elif page == "STRATEGY WIZARD":
            if key == 27 or (key in ENTER and focus == 2):
                page = "STRATEGY"
                index = 0 if wizard["side"]=="down" else 1
                wizard = None
                focus = 0
            elif key == 9:
                focus = (focus+1)%3
            elif key == curses.KEY_BTAB:
                focus = (focus-1)%3
            elif 48 <= key <= 57 or key == ord("."):
                char = chr(key)
                if char != "." or "." not in wizard["input"] or wizard.get("fresh"):
                    wizard["input"] = char if wizard.get("fresh") else wizard["input"]+char
                    wizard["fresh"] = False
                    wizard["input"] = wizard["input"][:19]
                    focus = 0
            elif key in (8, 127, curses.KEY_BACKSPACE):
                wizard["input"]=wizard["input"][:-1]
                wizard["fresh"]=False
                focus=0
            elif key in ENTER:
                try:
                    completed = stage_flow.advance(wizard, wizard["input"])
                    wizard["fresh"] = True
                    focus = 0
                    if completed:
                        page = "STRATEGY CONFIRM"
                except ValueError as exc:
                    note = "INVALID: "+str(exc)
        elif page == "STRATEGY CONFIRM":
            if key == 27 or (key in ENTER and focus == 2) or key in (ord("n"),ord("N")):
                page = "STRATEGY"
                index = 0 if wizard["side"]=="down" else 1
                wizard = None
                focus = 0
            elif key == 9:
                focus=(focus+1)%3
            elif key == curses.KEY_BTAB:
                focus=(focus-1)%3
            elif key in ENTER or key in (ord("y"),ord("Y")):
                data = stage_flow.request(wizard)
                _, problem = strategy_backend("apply-side-json",
                                              json.dumps(data,separators=(",",":")))
                if problem:
                    note = "PLAN NOT SAVED: " + problem
                else:
                    note = "Saved {} {} plan (TEST only)".format(
                        data["side"],data["mode"])
                    page = "STRATEGY"
                    index = 0 if wizard["side"]=="down" else 1
                    wizard = None
                    focus = 0
        elif page == "OCO EDIT":
            if key == 27 or (key in ENTER and focus == 2):
                page = "STRATEGY"
                index = 3
                focus = 0
                oco_editor = None
            elif key == 9:
                focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB:
                focus = (focus - 1) % 3
            elif 48 <= key <= 57 or key == ord("."):
                char = chr(key)
                if char != "." or "." not in oco_editor["input"] or oco_editor["fresh"]:
                    oco_editor["input"] = char if oco_editor["fresh"] else oco_editor["input"] + char
                    oco_editor["input"] = oco_editor["input"][:19]
                    oco_editor["fresh"] = False
                    focus = 0
            elif key in (8, 127, curses.KEY_BACKSPACE):
                oco_editor["input"] = oco_editor["input"][:-1]
                oco_editor["fresh"] = False
                focus = 0
            elif key in ENTER:
                try:
                    value = float(oco_editor["input"])
                    ceiling = 50 if oco_editor["phase"] == 0 else 100
                    if not math.isfinite(value) or not 0 < value < ceiling:
                        raise ValueError("Percentage must be greater than zero and below "+str(ceiling))
                    slot = "stop_pct" if oco_editor["phase"] == 0 else "target_pct"
                    oco_editor[slot] = str(value)
                    if oco_editor["phase"] == 0:
                        oco_editor["phase"] = 1
                        oco_editor["input"] = oco_editor["target_pct"]
                        oco_editor["fresh"] = True
                    else:
                        page = "OCO CONFIRM"
                    focus = 0
                except ValueError as exc:
                    note = "INVALID PERCENTAGE: " + str(exc)
        elif page == "OCO CONFIRM":
            if key == 27 or (key in ENTER and focus == 2) or key in (ord("n"), ord("N")):
                page = "STRATEGY"
                index = 3
                focus = 0
                oco_editor = None
            elif key == 9:
                focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB:
                focus = (focus - 1) % 3
            elif key in ENTER or key in (ord("y"), ord("Y")):
                _, problem = strategy_backend(
                    "set-oco", "--sl-pct", oco_editor["stop_pct"],
                    "--target-pct", oco_editor["target_pct"]
                )
                if problem:
                    note = "OCO SETTINGS NOT SAVED: " + problem
                else:
                    note = "OCO % settings saved (TEST PLAN, no broker OCO placed)"
                    page = "STRATEGY"
                    index = 3
                    focus = 0
                    oco_editor = None
        elif page == "STRATEGY REVIEW":
            if key in ENTER or key == 27:
                page = "STRATEGY"; index = 2; focus = 0
        elif page == "TRADING SWITCH":
            if key == 9: focus = (focus + 1) % 3
            elif key == curses.KEY_BTAB: focus = (focus - 1) % 3
            elif key in ENTER and focus == 1: key = ord("y")
            elif key in ENTER and focus == 2: key = ord("n")
            if key in (ord("Y"), ord("y")):
                if saved.get("amo_enabled"):
                    _, problem = backend("off")
                else:
                    _, problem = backend("preflight")
                    if not problem: _, problem = backend("arm")
                note = "SWITCH FAILED: " + problem if problem else "AMO switch saved"
                page = "CONTROL CENTER"; index = 3; focus = 0
            elif key in (27, ord("n"), ord("N")):
                page = "CONTROL CENTER"; index = 3; focus = 0
        else:
            if key == curses.KEY_DOWN: overview_offset = min(max(0, len(orders) - 5), overview_offset + 1)
            elif key == curses.KEY_UP: overview_offset = max(0, overview_offset - 1)
            elif key in ENTER or key == 27:
                page = "CONTROL CENTER"; index = 4; focus = 0

if __name__ == "__main__":
    curses.wrapper(start)
