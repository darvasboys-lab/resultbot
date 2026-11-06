#!/usr/bin/env python3
"""ResultBot Dhan read-only integration and explicit alert-to-order linking.

GET /profile, GET /orders, GET /trades and GET /orders/{orderId} only.
No placements, modifications, cancellations, token writes or Telegram sessions.
All Telegram messages are queued into the existing ResultBot Log Group outbox.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from html import escape

try:
    from . import dhan_readonly as dh
    from . import excellent_watch as watch
except ImportError:
    import dhan_readonly as dh
    import excellent_watch as watch

MAX_LINKS_PER_POLL = 40
TERMINAL = frozenset({"TRADED", "REJECTED", "CANCELLED", "EXPIRED"})
STATUSES = frozenset({"TRANSIT", "PENDING", "PART_TRADED"} | TERMINAL)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
      CREATE TABLE IF NOT EXISTS dhan_order_links (
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        telegram_message_id INTEGER NOT NULL,
        dhan_client_id TEXT NOT NULL,
        dhan_order_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        created_utc INTEGER NOT NULL,
        last_fingerprint TEXT NOT NULL DEFAULT '',
        last_seen_utc INTEGER NOT NULL DEFAULT 0,
        last_broker_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        UNIQUE(channel_id, telegram_message_id)
      );
    """)


def snapshot() -> tuple[str, dict, list[dict], list[dict]]:
    """Read a token afresh each time, so the 08:00 auto-refresh is picked up."""
    account, token = dh.load_credentials()
    profile = dh.verified_profile(account, token)
    orders = dh.verified_rows(dh.get_json("/orders", token), account)
    trades = dh.verified_rows(dh.get_json("/trades", token), account)
    return account, profile, orders, trades


def status_line(order: dict) -> tuple[str, int, int, str]:
    status = str(order.get("orderStatus") or "").upper()
    if status not in STATUSES:
        raise dh.DhanReadError("Dhan returned an unknown or missing order status")
    qty = dh.checked_number(order.get("quantity"))
    filled = dh.checked_number(order.get("filledQty"))
    if qty is None or filled is None or qty <= 0 or filled > qty:
        raise dh.DhanReadError("Dhan returned invalid broker quantities")
    if int(qty) != qty or int(filled) != filled:
        raise dh.DhanReadError("Dhan returned non-integral broker quantities")
    if status == "TRADED" and filled != qty:
        raise dh.DhanReadError("Dhan full-trade status conflicts with filled quantity")
    price = dh.checked_number(order.get("averageTradedPrice"))
    avg = "N/A"
    if filled > 0 and price is not None and price > 0:
        avg = f"₹{price:,.2f}"
    return status, int(filled), int(qty), avg


def validate_identity(order: dict, *, account: str, order_id: str, symbol: str,
                      alert_utc: int | None = None) -> None:
    if str(order.get("orderId") or "") != order_id:
        raise dh.DhanReadError("Broker order ID mismatch")
    if str(order.get("dhanClientId") or "") != account:
        raise dh.DhanReadError("Broker account binding mismatch")
    if str(order.get("tradingSymbol") or "").strip().upper() != symbol:
        raise dh.DhanReadError("Broker symbol mismatch; manual link refused")
    if str(order.get("exchangeSegment") or "") != "NSE_EQ":
        raise dh.DhanReadError("Only verified NSE equity orders can link to Excellent equity alerts")
    if str(order.get("transactionType") or "") != "BUY":
        raise dh.DhanReadError("Only verified BUY orders can link to entry alerts")
    status_line(order)
    if alert_utc is not None:
        try:
            created = datetime.strptime(str(order.get("createTime") or ""), "%Y-%m-%d %H:%M:%S")
            created_utc = int(created.replace(tzinfo=watch.IST).timestamp())
        except ValueError:
            raise dh.DhanReadError("Broker order createTime missing or invalid")
        if created_utc < int(alert_utc) - 5:
            raise dh.DhanReadError("Broker order predates the Telegram alert; link refused")
        if created_utc > time.time() + 60:
            raise dh.DhanReadError("Broker order timestamp is in the future; link refused")


def queue_message(con: sqlite3.Connection, *, telegram_id: int, kind: str,
                  body: str) -> bool:
    if not re.fullmatch(r"[A-Z0-9_]{3,60}", kind):
        raise ValueError("Unsafe notification kind")
    cur = con.execute(
        """INSERT OR IGNORE INTO log_outbox
        (channel_id,message_id,kind,text_html,created_utc)
        VALUES (?,?,?,?,?)""",
        (watch.CHANNEL_ID, int(telegram_id), kind, body, int(time.time())),
    )
    return bool(cur.rowcount)


def connect(con: sqlite3.Connection, *, notify: bool = True) -> dict:
    """Live API verification; queue exactly one non-execution confirmation card."""
    ensure_schema(con)
    account, profile, orders, trades = snapshot()
    if notify:
        card = "\n".join((
            "🏦 <b>RESULTBOT — DHAN CONNECTED</b>",
            f"✅ Verified Dhan account: <b>{escape(dh.display_account(account))}</b>",
            "🔐 Token refresh: separate daily systemd timer",
            f"📋 Today's Dhan orders: {len(orders)}",
            f"📒 Today's Dhan trades: {len(trades)}",
            "🟡 Mode: READ-ONLY — order placement DISABLED",
            "🔗 No trade is attributed to an Excellent alert without a verified order link",
        ))
        queue_message(con, telegram_id=0, kind="DHAN_CONNECTED_V1", body=card)
    return {"account": dh.display_account(account),
            "token_validity": str(profile.get("tokenValidity") or "Not supplied"),
            "orders_today": len(orders), "trades_today": len(trades)}


def recent_alerts(con: sqlite3.Connection, limit: int = 10) -> None:
    if not 1 <= limit <= 30:
        raise dh.DhanReadError("Limit must be 1-30")
    print("Verified Earnings Pulse Excellent alerts (real source only):")
    for message_id, symbol, sent in con.execute(
        """SELECT message_id,symbol,sent_utc FROM alerts WHERE channel_id=?
           ORDER BY sent_utc DESC LIMIT ?""",(watch.CHANNEL_ID,limit)
    ):
        when = datetime.fromtimestamp(int(sent),watch.IST).strftime("%d-%b %H:%M IST")
        print(f"  Telegram message {message_id} | #{symbol} | {when}")
    print("These are captured alerts, not broker orders.")


def link_order(con: sqlite3.Connection, *, telegram_id: int, order_id: str) -> str:
    """Only explicit owner-supplied broker order ID can create a mapping."""
    if not re.fullmatch(r"[0-9]{1,25}", order_id or ""):
        raise dh.DhanReadError("Broker order ID must be numeric")
    if not isinstance(telegram_id,int) or telegram_id <= 0:
        raise dh.DhanReadError("Telegram alert message ID must be positive")
    ensure_schema(con)
    alert = con.execute(
        """SELECT symbol,sent_utc FROM alerts
           WHERE channel_id=? AND message_id=?""",
        (watch.CHANNEL_ID,telegram_id)
    ).fetchone()
    if not alert:
        raise dh.DhanReadError("Telegram ID is not a stored REAL Earnings Pulse Excellent alert")
    symbol, sent_utc = str(alert[0]), int(alert[1])
    account, token = dh.load_credentials()
    dh.verified_profile(account, token)
    order = dh.get_json("/orders/"+order_id, token)
    if not isinstance(order, dict):
        raise dh.DhanReadError("Dhan order response must be an object")
    validate_identity(order, account=account,order_id=order_id,
                      symbol=symbol,alert_utc=sent_utc)
    try:
        con.execute(
            """INSERT INTO dhan_order_links
               (channel_id,telegram_message_id,dhan_client_id,dhan_order_id,symbol,created_utc)
               VALUES (?,?,?,?,?,?)""",
            (watch.CHANNEL_ID,telegram_id,account,order_id,symbol,int(time.time()))
        )
    except sqlite3.IntegrityError:
        existing = con.execute(
            """SELECT telegram_message_id,dhan_order_id FROM dhan_order_links
               WHERE dhan_order_id=? OR (channel_id=? AND telegram_message_id=?)""",
            (order_id,watch.CHANNEL_ID,telegram_id)
        ).fetchone()
        if existing == (telegram_id,order_id):
            return "ALREADY_LINKED"
        raise dh.DhanReadError("Order ID or alert already linked; refusing ambiguous attribution")
    return "LINKED"


def _trade_summary(trades: list[dict], order_id: str) -> tuple[int, str]:
    """Weighted actual fill price from trade records with matching order ID."""
    qty=0
    total=0.0
    for trade in trades:
        if str(trade.get("orderId") or "")!=order_id:
            continue
        count=dh.checked_number(trade.get("tradedQuantity"))
        price=dh.checked_number(trade.get("tradedPrice"))
        if count is None or int(count)!=count or count<=0 or price is None or price<=0:
            raise dh.DhanReadError("Malformed broker trade quantity or price")
        qty+=int(count)
        total+=int(count)*price
    return qty, f"₹{total/qty:,.2f}" if qty else "N/A"


def apply_snapshot(con: sqlite3.Connection, *, account: str, orders: list[dict],
                   trades: list[dict]) -> int:
    """Queue only changed statuses of explicitly linked real-source orders."""
    ensure_schema(con)
    # Account mismatch from the cached link is a hard stop, never a symbol match.
    links = con.execute(
        """SELECT id,telegram_message_id,dhan_client_id,dhan_order_id,symbol,last_fingerprint
           FROM dhan_order_links WHERE active=1 ORDER BY id LIMIT ?""",
        (MAX_LINKS_PER_POLL,)
    ).fetchall()
    if not links:
        return 0
    order_map={str(x.get("orderId")):x for x in orders}
    changed=0
    for link_id, message_id, bound_account, order_id, symbol, old_fingerprint in links:
        if bound_account!=account:
            raise dh.DhanReadError("Saved broker account differs from current Dhan account")
        order=order_map.get(order_id)
        if order is None:
            # Dhan order API is current-day only. Do not claim order rejection.
            continue
        validate_identity(order,account=account,order_id=order_id,symbol=symbol)
        status, filled, qty, avg = status_line(order)
        trade_qty, trade_avg = _trade_summary(trades,order_id)
        if trade_qty>qty or trade_qty>filled:
            raise dh.DhanReadError("Broker trade quantities disagree with order")
        if filled and trade_qty == filled and trade_avg != "N/A":
            reported_price = trade_avg
            source = "Dhan trade book"
        elif filled and avg != "N/A":
            reported_price = avg
            source = "Dhan order book"
        else:
            reported_price = "N/A"
            source = "not confirmed"
        fingerprint = json.dumps([status,filled,qty,reported_price,source])
        if fingerprint==old_fingerprint:
            continue
        digest=hashlib.sha256(fingerprint.encode()).hexdigest()[:20].upper()
        body="\n".join((
            "🏦 <b>RESULTBOT — DHAN BROKER UPDATE</b>",
            f"📈 Excellent alert: <b>#{escape(symbol)}</b>",
            f"📩 Telegram message: {int(message_id)}",
            f"📌 Dhan order status: <b>{escape(status)}</b>",
            f"✅ Filled quantity: {filled}/{qty}",
            f"💰 Verified average execution: <b>{escape(reported_price)}</b>",
            f"🔎 Price source: {escape(source)}",
            "🔗 Order manually linked and broker-verified",
            "🟡 Order NOT submitted by ResultBot; monitoring only",
        ))
        queue_message(con,telegram_id=int(message_id),kind="DHAN_"+digest,body=body)
        con.execute(
            """UPDATE dhan_order_links
               SET last_fingerprint=?,last_seen_utc=?,last_broker_status=?,active=?
               WHERE id=?""",
            (fingerprint,int(time.time()),status,0 if status in TERMINAL else 1,link_id)
        )
        changed+=1
    return changed


def poll_once(con: sqlite3.Connection) -> int:
    """Synchronous manual poll of matched orders, no action when none linked."""
    ensure_schema(con)
    count=con.execute("SELECT COUNT(*) FROM dhan_order_links WHERE active=1").fetchone()[0]
    if not count:
        return 0
    if count>MAX_LINKS_PER_POLL:
        raise dh.DhanReadError("Too many active broker links; manual review required")
    account, _, orders, trades = snapshot()
    return apply_snapshot(con,account=account,orders=orders,trades=trades)


async def poll_async(con: sqlite3.Connection) -> int:
    """Perform slow HTTP in worker, SQLite writes on Telegram listener thread."""
    ensure_schema(con)
    count=con.execute("SELECT COUNT(*) FROM dhan_order_links WHERE active=1").fetchone()[0]
    if not count:
        return 0
    if count>MAX_LINKS_PER_POLL:
        raise dh.DhanReadError("Too many active broker links; manual review required")
    account,_,orders,trades = await asyncio.to_thread(snapshot)
    return apply_snapshot(con,account=account,orders=orders,trades=trades)


def list_links(con: sqlite3.Connection):
    ensure_schema(con)
    rows=con.execute(
        """SELECT telegram_message_id,symbol,last_broker_status,active,last_seen_utc
           FROM dhan_order_links ORDER BY id DESC LIMIT 30"""
    ).fetchall()
    if not rows:
        print("No explicitly linked Dhan orders. No broker executions claimed.")
    for telegram_id,symbol,status,active,last in rows:
        when=datetime.fromtimestamp(last,watch.IST).strftime("%d-%b %H:%M IST") if last else "Never"
        print(f"Telegram {telegram_id} | #{symbol} | Dhan {status} | "
              f"{'monitoring' if active else 'terminal'} | checked {when}")


def self_test() -> None:
    from tempfile import TemporaryDirectory
    assert _trade_summary([{"orderId":"1","tradedQuantity":2,"tradedPrice":100},
                           {"orderId":"1","tradedQuantity":3,"tradedPrice":110}], "1")==(5,"₹106.00")
    with TemporaryDirectory() as d:
        from pathlib import Path
        con=watch.setup_db(Path(d)/"excellent.sqlite3")
        ensure_schema(con)
        assert con.execute("SELECT COUNT(*) FROM dhan_order_links").fetchone()[0]==0
        assert queue_message(con,telegram_id=0,kind="DHAN_CONNECTED_V1",body="Connected")
        assert not queue_message(con,telegram_id=0,kind="DHAN_CONNECTED_V1",body="Connected")
        con.close()
    print("PASS: Dhan bridge GET-only mapping, trade math, durable Log Group outbox")


def main():
    parser=argparse.ArgumentParser(description="ResultBot verified Dhan GET-only bridge (no broker mutations)")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("self-test")
    sub.add_parser("check",help="verify Dhan live API without sending or queuing notifications")
    sub.add_parser("connect",help="verify Dhan live API and enqueue one connection card")
    sub.add_parser("recent-alerts",help="list captured Excellent Telegram IDs (not broker orders)")
    sub.add_parser("links",help="list only explicitly paired broker order status")
    p=sub.add_parser("link-order",help="manually link a real Excellent Telegram ID and verified Dhan BUY order ID")
    p.add_argument("--telegram-id",required=True,type=int)
    p.add_argument("--order-id",required=True)
    sub.add_parser("poll",help="fetch broker status for explicitly linked orders, then queue Log Group cards")
    args=parser.parse_args()
    if args.command=="self-test":
        self_test()
        return
    con=watch.setup_db()
    try:
        ensure_schema(con)
        if args.command in ("check","connect"):
            result=connect(con,notify=args.command=="connect")
            print("CONNECTED: DhanHQ v2 READ-ONLY | Account:",result["account"])
            print("Token validity:",result["token_validity"])
            print("Today's broker orders:",result["orders_today"])
            print("Today's broker trades:",result["trades_today"])
            if args.command=="connect":
                print("Dhan connection card queued for existing ResultBot Log Group.")
            else:
                print("Dry connection check only; no Log Group card queued.")
            print("No broker orders were submitted or attributed.")
        elif args.command=="recent-alerts":
            recent_alerts(con)
        elif args.command=="links":
            list_links(con)
        elif args.command=="link-order":
            result=link_order(con,telegram_id=args.telegram_id,order_id=args.order_id)
            print(result+": Dhan BUY equity order verified and linked for manual monitoring.")
            print("No orders submitted. Next status poll will queue Log Group updates.")
        elif args.command=="poll":
            print("Broker order changes queued:",poll_once(con))
    finally:
        con.close()


if __name__=="__main__":
    try:
        main()
    except (dh.DhanReadError,sqlite3.Error) as exc:
        # No raw broker HTTP responses or token-bearing URLs in user-facing errors.
        print("RESULTBOT DHAN BRIDGE ERROR:",str(exc)[:160],file=sys.stderr)
        sys.exit(1)
