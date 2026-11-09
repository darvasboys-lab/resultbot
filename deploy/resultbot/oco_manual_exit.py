#!/usr/bin/env python3
"""ResultBot-owned OCO cleanup after broker-confirmed manual equity square-offs.

Only the original OCO order ID can be cancelled. No BUY/SELL orders placed.
Cancellation attempts are recorded before DELETE and never auto-retried.
"""
from __future__ import annotations

import json
import logging
import time

LOG = logging.getLogger("resultbot.oco.manual_exit")
OPEN = frozenset({"PENDING", "TRANSIT", "CONFIRM", "PART_TRADED"})
TERMINAL_FOREVER = frozenset({"CANCELLED", "EXPIRED", "REJECTED", "CLOSED", "TRADED", "TRIGGERED"})
CANCELLED_FOREVER = frozenset({"CANCELLED", "EXPIRED", "REJECTED", "CLOSED"})
LAST_RUN = 0.0
INTERVAL = 30.0


def positive_int(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a quantity")
    number = int(str(value))
    if number < 0 or str(number) != str(value).split(".")[0]:
        raise ValueError("Invalid broker quantity")
    return number


def order_rows(order_id, forever):
    return [r for r in forever if str(r.get("orderId") or "") == str(order_id)]


def row_owned(coverage, broker_rows):
    """Do not act on an OCO solely because a security ID or order ID matches."""
    try:
        p = json.loads(coverage["payload"])
    except (ValueError, KeyError, TypeError):
        return False
    fields = {
        "dhanClientId": p.get("dhanClientId"),
        "securityId": p.get("securityId"),
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "transactionType": "SELL",
    }
    return bool(broker_rows) and all(
        all(str(row.get(k) or "") == str(expected) for k, expected in fields.items())
        for row in broker_rows
    )


def is_flat(security_id, positions, holdings, account):
    """Use portfolio holdings plus today's net CNC movement.

    Dhan holdings are from prior sessions; positions include today's buy/sell
    movement. If either feed is invalid, fail closed (never cancel).
    """
    try:
        net = 0
        held = 0
        for p in positions:
            if str(p.get("securityId")) != str(security_id):
                continue
            if p.get("exchangeSegment") != "NSE_EQ" or p.get("productType") != "CNC":
                return False
            if str(p.get("dhanClientId")) != str(account):
                return False
            net += int(p["netQty"])
        for h in holdings:
            if str(h.get("securityId")) == str(security_id):
                held += positive_int(h["totalQty"])
        return held + net <= 0
    except (TypeError, KeyError, ValueError, OverflowError):
        return False


def sold_manually(security_id, trades, needed, account):
    """Demand filled CNC SELL trades, not an unfilled/pending SELL order."""
    total = 0
    for t in trades:
        if str(t.get("securityId")) != str(security_id):
            continue
        if t.get("transactionType") != "SELL":
            continue
        if t.get("exchangeSegment") != "NSE_EQ" or t.get("productType") != "CNC":
            continue
        if str(t.get("dhanClientId")) != str(account):
            continue
        try:
            total += positive_int(t["tradedQuantity"])
        except (ValueError, TypeError, KeyError):
            return False
    return total >= needed


def open_buys(security_id, orders):
    return any(
        str(o.get("securityId")) == str(security_id)
        and o.get("exchangeSegment") == "NSE_EQ"
        and o.get("productType") == "CNC"
        and o.get("transactionType") == "BUY"
        and str(o.get("orderStatus") or "").upper() in OPEN
        for o in orders
    )


def open_oco_for_security(security_id, forever):
    return any(
        str(o.get("securityId")) == str(security_id)
        and o.get("transactionType") == "SELL"
        and str(o.get("orderStatus") or "").upper() in OPEN
        for o in forever
    )


def archive_problem(c, intent_id, message, reason):
    c.execute("""CREATE TABLE IF NOT EXISTS resolved_oco_problems(
        intent_id INTEGER PRIMARY KEY, message TEXT, reason TEXT, resolved_ts INTEGER
    )""")
    c.execute(
        "INSERT OR REPLACE INTO resolved_oco_problems VALUES(?,?,?,?)",
        (intent_id, message, reason, int(time.time())),
    )
    c.execute("DELETE FROM problems WHERE intent_id=?", (intent_id,))


def clean_once(g, account, token, orders, forever):
    positions = g.api("GET", "/positions", token)
    holdings = g.api("GET", "/holdings", token)
    trades = g.api("GET", "/trades", token)

    if not all(isinstance(group, list) for group in (positions, holdings, trades)):
        raise ValueError("One broker evidence feed is not a list")

    with g.live.managed_db() as c:
        c.row_factory = g.sqlite3.Row
        intents = {
            r["id"]: dict(r) for r in c.execute(
                "SELECT id,account,symbol,security_id,order_id FROM intents"
            )
        }

    with g.db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS manual_exit_cancel_attempts(
            coverage_id INTEGER PRIMARY KEY, order_id TEXT,
            attempted_ts INTEGER NOT NULL, note TEXT NOT NULL
        )""")
        coverage = [dict(r) for r in c.execute("SELECT * FROM coverage")]
        problems = [dict(r) for r in c.execute("SELECT * FROM problems")]

    # First, retire old warnings only when the account is confirmed flat AND
    # the broker no longer reports an active OCO or pending owned BUY.
    for row in coverage:
        intent = intents.get(row["intent_id"])
        if not intent or str(intent["account"]) != str(account):
            continue
        p = json.loads(row["payload"])
        security_id = str(p.get("securityId") or "")
        if not security_id or security_id != str(intent["security_id"]):
            continue
        if not is_flat(security_id, positions, holdings, account):
            continue
        if open_buys(security_id, orders):
            continue

        matching = order_rows(row["order_id"], forever) if row["order_id"] else []
        terminal = (
            row_owned(row, matching)
            and all(str(r.get("orderStatus") or "").upper() in TERMINAL_FOREVER
                    for r in matching)
        )
        absent = bool(row["order_id"]) and not matching
        exit_confirmed = sold_manually(
            security_id, trades, positive_int(p["quantity"]), account
        )
        if row["state"] in ("REVIEW", "UNKNOWN", "CANCEL_REQUESTED", "ACTIVE"):
            if not open_oco_for_security(security_id, forever) and (
                terminal or (absent and exit_confirmed)
            ):
                with g.db() as c:
                    g.record_state(
                        c, row["id"], "CLOSED",
                        "Broker flat; OCO ended or not active; historical record retained",
                        row["order_id"],
                    )

    # Resolve old error entries, but only for confirmed flat securities with
    # no broker-reported active OCO or outstanding BUY.
    for error in problems:
        intent = intents.get(error["intent_id"])
        if not intent or str(intent["account"]) != str(account):
            continue
        sec = str(intent.get("security_id") or "")
        if not sec or not is_flat(sec, positions, holdings, account):
            continue
        if open_oco_for_security(sec, forever) or open_buys(sec, orders):
            continue
        with g.db() as c:
            archive_problem(c, error["intent_id"], error["message"],
                            "Broker confirms no current exposure or open protection")
        LOG.info("Archived resolved OCO problem for intent %s", error["intent_id"])

    # Last, cancel only actively broker-verified ResultBot-owned protection
    # against a manual full exit. Never create any new order.
    with g.db() as c:
        current = [dict(r) for r in c.execute(
            "SELECT * FROM coverage WHERE state='ACTIVE'"
        )]

    for row in current:
        intent = intents.get(row["intent_id"])
        if not intent or str(intent["account"]) != str(account):
            continue
        p = json.loads(row["payload"])
        sec = str(p.get("securityId") or "")
        if not sec or sec != str(intent.get("security_id")):
            continue
        if not is_flat(sec, positions, holdings, account):
            continue
        if open_buys(sec, orders):
            continue
        if not sold_manually(sec, trades, positive_int(p["quantity"]), account):
            continue
        oid = str(row["order_id"])
        if not oid.isdecimal() or not 6 <= len(oid) <= 25:
            continue
        matched = order_rows(oid, forever)
        if not row_owned(row, matched):
            continue
        # The guard's own complete both-SELL-leg broker verification.
        if not g.verify_oco(p, oid, forever, row["tick"]):
            continue

        # A durable claim is recorded BEFORE DELETE; transport ambiguity cannot
        # cause a second cancellation automatically.
        with g.db() as c:
            taken = c.execute(
                """INSERT OR IGNORE INTO manual_exit_cancel_attempts
                    (coverage_id,order_id,attempted_ts,note)
                    VALUES(?,?,?,?)""",
                (row["id"], oid, int(time.time()),
                 "Manual CNC SELL + flat portfolio confirmed"),
            )
            if taken.rowcount != 1:
                continue
            g.record_state(
                c, row["id"], "CANCEL_REQUESTED",
                "Manual exit confirmed; one broker OCO cancel attempted", oid
            )

        try:
            answer = g.api("DELETE", "/forever/orders/" + oid, token)
            if str(answer.get("orderId")) != oid:
                raise ValueError("Dhan cancelled a different order ID")
            updated = g.api("GET", "/forever/orders", token)
            matched = order_rows(oid, updated)
            if row_owned(row, matched) and all(
                str(x.get("orderStatus") or "").upper() in TERMINAL_FOREVER
                for x in matched
            ):
                with g.db() as c:
                    g.record_state(
                        c, row["id"], "CLOSED",
                        "OCO ended after manually verified full equity exit", oid
                    )
            else:
                LOG.warning("OCO cancel request needs broker verification: intent=%s", row["intent_id"])
        except (g.GuardError, ValueError, OSError) as exc:
            LOG.warning(
                "Manual OCO cancellation uncertain: intent=%s %s",
                row["intent_id"], type(exc).__name__
            )


def cycle(g, account, token, orders, forever):
    global LAST_RUN
    now = time.monotonic()
    if LAST_RUN and now - LAST_RUN < INTERVAL:
        return
    LAST_RUN = now
    clean_once(g, account, token, orders, forever)


def offline_self_test():
    account = "12345678"
    p = [{"securityId": "111", "exchangeSegment": "NSE_EQ",
          "productType": "CNC", "dhanClientId": account, "netQty": -1}]
    h = [{"securityId": "111", "totalQty": 1}]
    assert is_flat("111", p, h, account)
    assert not is_flat("111", [], [{"securityId": "111", "totalQty": 1}], account)
    assert not is_flat("111", [{"securityId": "111", "netQty": 0,
                                "exchangeSegment": "NSE_EQ", "productType": "CNC",
                                "dhanClientId": "wrong"}], [], account)
    assert sold_manually("111", [{"securityId": "111", "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ", "productType": "CNC",
        "dhanClientId": account, "tradedQuantity": 1}], 1, account)
    assert not sold_manually("111", [], 1, account)
    assert not open_buys("111", [])
    print("PASS: broker-flat and confirmed-manual-SELL cleanup predicates")


if __name__ == "__main__":
    offline_self_test()
