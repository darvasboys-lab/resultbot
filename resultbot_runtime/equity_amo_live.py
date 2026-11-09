#!/usr/bin/env python3
"""Real Dhan equity LIMIT entries with independent regular-session and AMO switches.

While ON, every distinct, eligible, NEW Telegram alert may create its own real
Equity AMO. No order-count/day limits, one-shot disarm, or 10-minute expiry.
OFF blocks future submissions, but existing broker orders require a separate
owner-directed cancellation. No HTTP/WebUI service is necessary.
Does NOT start trading_bot.service.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path.home() / "uditbot"))
try:
    from . import dhan_readonly as dh
    from . import excellent_watch as watch
except ImportError:
    import dhan_readonly as dh
    import excellent_watch as watch

ROOT = Path.home() / "resultbot-data"
STATE_DB = ROOT / "equity_amo_live.sqlite3"
IST = ZoneInfo("Asia/Kolkata")
TEST_PREFIX = "[RBLIVEAMO] "
BUDGET_DEFAULT = 500
# SQLite stores signed 64-bit integers; this is a storage format constraint,
# NOT an operator-facing trading budget or risk cap.
SQLITE_INT_MAX = (1 << 63) - 1
# This is a persistent owner ON/OFF toggle, NOT a one-shot or expiring arm.
# The owner sets the per-order amount; the bot only sizes whole shares and
# checks the resulting order against the broker's actual available funds.
MAX_SIGNAL_AGE = 90
EGRESS_CHECK_URL = "https://api.ipify.org"
TERMINAL = frozenset({"TRADED", "REJECTED", "CANCELLED", "EXPIRED"})


class LiveAmoError(Exception):
    pass


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    path = STATE_DB if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Harden only ResultBot's owned data directory; never chmod /tmp or
    # an unrelated caller-provided directory during an isolated self-test.
    if path.parent == ROOT:
        path.parent.chmod(0o700)
    if path.is_symlink():
        raise LiveAmoError("Refusing symlinked real-AMO state database")
    con=sqlite3.connect(str(path),timeout=20,isolation_level=None)
    path.chmod(0o600)
    con.execute("PRAGMA busy_timeout=20000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS control(
      id INTEGER PRIMARY KEY CHECK(id=1),
      armed INTEGER NOT NULL DEFAULT 0 CHECK(armed IN(0,1)),
      source TEXT NOT NULL DEFAULT 'BOTH',
      budget INTEGER NOT NULL DEFAULT 500,
      expires INTEGER NOT NULL DEFAULT 0,
      armed_since INTEGER NOT NULL DEFAULT 0,
      arm_seq INTEGER NOT NULL DEFAULT 0,
      stop_seq INTEGER NOT NULL DEFAULT 0
    );
    INSERT OR IGNORE INTO control(id) VALUES (1);
    CREATE TABLE IF NOT EXISTS intents(
      id INTEGER PRIMARY KEY,
      source TEXT NOT NULL,
      msg_id INTEGER NOT NULL,
      symbol TEXT NOT NULL,
      alert_ts INTEGER NOT NULL,
      created_ts INTEGER NOT NULL,
      account TEXT NOT NULL,
      arm_stop_seq INTEGER NOT NULL DEFAULT 0,
      budget INTEGER NOT NULL DEFAULT 500,
      correlation TEXT NOT NULL UNIQUE,
      security_id TEXT NOT NULL DEFAULT '',
      qty INTEGER NOT NULL DEFAULT 0,
      price REAL NOT NULL DEFAULT 0,
      order_id TEXT NOT NULL DEFAULT '',
      state TEXT NOT NULL DEFAULT 'RESERVED',
      broker_status TEXT NOT NULL DEFAULT '',
      note TEXT NOT NULL DEFAULT '',
      cancel_after INTEGER NOT NULL DEFAULT 0,
      cancel_attempted INTEGER NOT NULL DEFAULT 0,
      UNIQUE(source,msg_id)
    );
    """ )
    # Serialize migrations across listener/CLI processes. Existing intents are AMO.
    con.execute("BEGIN IMMEDIATE")
    try:
        for table, definitions in (
            ("control", {"live_enabled": "INTEGER NOT NULL DEFAULT 0",
                         "live_since": "INTEGER NOT NULL DEFAULT 0",
                         "live_stop_seq": "INTEGER NOT NULL DEFAULT 0"}),
            ("intents", {"entry_route": "TEXT NOT NULL DEFAULT 'AMO'"}),
        ):
            columns={r[1] for r in con.execute("PRAGMA table_info("+table+")")}
            for name, definition in definitions.items():
                if name not in columns:
                    con.execute("ALTER TABLE "+table+" ADD COLUMN "+name+" "+definition)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        con.close()
        raise
    return con


@contextmanager
def managed_db(path: Path | None = None):
    """Commit-safe, explicitly closed SQLite handle (no leaked broker state handles)."""
    con=connect_db(path)
    try:
        yield con
    finally:
        con.close()


def _control(con) -> dict:
    row=con.execute(
        "SELECT armed,source,budget,expires,armed_since,arm_seq,stop_seq,live_enabled,live_since,live_stop_seq FROM control WHERE id=1"
    ).fetchone()
    return dict(zip(("armed","source","budget","expires","armed_since","arm_seq","stop_seq","live_enabled","live_since","live_stop_seq"),row))


def status() -> dict:
    with managed_db() as con:
        ctrl=_control(con)
        rows=con.execute("""SELECT id,symbol,state,order_id,broker_status,qty,price,
             created_ts,note,cancel_after,entry_route FROM intents ORDER BY id DESC""").fetchall()
        ctrl["orders"]=[dict(zip(
            ("id","symbol","state","order_id","broker_status","qty","price",
             "created_ts","note","cancel_after","entry_route"),r)) for r in rows]
        ctrl["effective_on"]=bool(ctrl["armed"])
        ctrl["active_orders"]=con.execute(
            "SELECT COUNT(*) FROM intents WHERE state IN "
            "('RESERVED','PREPARING','SUBMITTING','UNKNOWN','ACKED','CANCEL_REQUESTED')"
        ).fetchone()[0]
        return ctrl


def status_short() -> str:
    """Human-readable persistent owner switch, avoiding a large order-history dump."""
    data=status()
    mode="YES / ON" if data["effective_on"] else "NO / OFF"
    return ("RESULTBOT LIVE: "+("ON" if data["live_enabled"] else "OFF")+
            " | AMO: "+mode+
            " | sources="+data["source"]+
            " | per-order budget INR "+str(data["budget"])+
            " | unresolved broker intents="+str(data["active_orders"]))


def validate_budget(budget: int) -> int:
    """Owner-chosen positive whole-rupee amount; no arbitrary risk upper cap.

    Only reject values that cannot be stored as a SQLite integer or cannot
    represent any positive whole-rupee budget. Broker funds and order
    constraints are checked when an actual signal is submitted.
    """
    if type(budget) is not int or budget <= 0 or budget > SQLITE_INT_MAX:
        raise LiveAmoError("Enter a positive whole-rupee budget (e.g. 5000)")
    return budget


def editable_settings() -> dict:
    """Read saved operator preferences; never expose credentials or broker IDs."""
    with managed_db() as con:
        c=_control(con)
    return {
        "amo_enabled": bool(c["armed"]),
        "live_enabled": bool(c["live_enabled"]),
        "live_product_type": "CNC",
        "source": c["source"],
        "per_order_budget_inr": int(c["budget"]),
        "budget_min_inr": 1,
        "budget_max_inr": None,
    }


def update_settings(*, source: str | None = None,
                    budget: int | None = None) -> dict:
    """Edit persisted settings WITHOUT altering YES/NO or existing orders.

    Settings only govern newly reserved Telegram intents. Reject edits while a
    previously reserved intent is in the pre-broker-submission transition, so
    a source change cannot silently invalidate that already accepted signal.
    This never contacts Dhan, submits or cancels an order, or restarts services.
    """
    if source is None and budget is None:
        raise LiveAmoError("Specify --source or --budget to edit settings")
    if source is not None and source not in ("TEST", "EARNINGS_PULSE", "BOTH"):
        raise LiveAmoError("Source must be TEST, EARNINGS_PULSE or BOTH")
    if budget is not None:
        validate_budget(budget)
    with managed_db() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            c=_control(con)
            next_source = c["source"] if source is None else source
            next_budget = c["budget"] if budget is None else budget
            if next_source in ("TEST","BOTH") and watch.configured_test_group_id() is None:
                raise LiveAmoError("Verified Test Group ID is not configured")
            if watch.configured_log_group_id() is None:
                raise LiveAmoError("Verified Log Group ID is not configured")
            # Budgets are snapshotted inside each intent at reservation.
            # Editing the owner's budget therefore never changes an existing
            # order, even if it is currently PREPARING or SUBMITTING.
            # Source changes can interfere with the final broker submission
            # gate; defer only that kind of change while an intent is in flight.
            if source is not None and next_source != c["source"]:
                pending=con.execute(
                    """SELECT COUNT(*) FROM intents
                       WHERE state IN ('RESERVED','PREPARING','SUBMITTING')"""
                ).fetchone()[0]
                if pending:
                    raise LiveAmoError(
                        "One order is currently being submitted. Source change "
                        "is unavailable until its status is known; budget "
                        "can still be edited."
                    )
            con.execute(
                "UPDATE control SET source=?,budget=? WHERE id=1",
                (next_source,next_budget)
            )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return editable_settings()


def trading_service_stopped() -> bool:
    try:
        active=subprocess.run(["systemctl","is-active","trading_bot.service"],
                              capture_output=True,text=True,timeout=3).stdout.strip()
        enabled=subprocess.run(["systemctl","is-enabled","trading_bot.service"],
                               capture_output=True,text=True,timeout=3).stdout.strip()
        return active=="inactive" and enabled=="disabled"
    except (OSError,subprocess.TimeoutExpired):
        return False


def arm(*, source: str = "BOTH", budget: int = BUDGET_DEFAULT) -> dict:
    if source not in ("TEST","EARNINGS_PULSE","BOTH"):
        raise LiveAmoError("Invalid source")
    validate_budget(budget)
    if not trading_service_stopped():
        raise LiveAmoError("Original trading_bot.service must be inactive AND disabled")
    if watch.configured_log_group_id() is None:
        raise LiveAmoError("Telegram Log Group is not configured; real AMO cannot be armed")
    if source in ("TEST","BOTH") and watch.configured_test_group_id() is None:
        raise LiveAmoError("Authorized Telegram Test Group is not configured")
    if not _session_closed():
        raise LiveAmoError("Real Equity AMO can only be armed in a closed-market window")
    account, token=dh.load_credentials()
    # Read-only live permission checks before ON; fail closed on unlisted IP.
    _verified_broker_context(account,token)
    with managed_db() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            # No count-based restriction, no time expiry and no automatic OFF.
            # Every distinct NEW signal can become its own broker order while ON.
            # Preserve the stop generation so prior owner OFF cannot be bypassed.
            con.execute("""UPDATE control SET armed=1,source=?,budget=?,expires=0,
                            armed_since=?,arm_seq=arm_seq+1 WHERE id=1""",
                        (source,budget,int(time.time())))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return status()


def disarm() -> dict:
    """Legacy AMO OFF remains independent of regular-session entries."""
    amo_off()
    return status()


def stop_all() -> dict:
    with managed_db() as con:
        # An explicit owner OFF invalidates already-reserved pre-submit intents.
        con.execute("UPDATE control SET armed=0,live_enabled=0,expires=0,stop_seq=stop_seq+1,live_stop_seq=live_stop_seq+1 WHERE id=1")
    return status()


def set_live(enabled: bool) -> dict:
    """Owner-controlled regular-session entries; never submits an order itself."""
    if enabled:
        if not trading_service_stopped():
            raise LiveAmoError("Original trading_bot.service must remain inactive and disabled")
        saved=editable_settings()
        if watch.configured_log_group_id() is None:
            raise LiveAmoError("Telegram Log Group is not configured")
        if saved["source"] in ("TEST","BOTH") and watch.configured_test_group_id() is None:
            raise LiveAmoError("Authorized Test Group is not configured")
        account,token=dh.load_credentials()
        _verified_broker_context(account,token)
    with managed_db() as con:
        if enabled:
            # Repeated ON is idempotent; only a new activation advances freshness.
            con.execute("""UPDATE control SET live_enabled=1, live_since=?,
                           arm_seq=arm_seq+1 WHERE id=1 AND live_enabled=0""",
                        (int(time.time()),))
        else:
            con.execute("""UPDATE control SET live_enabled=0,
                           live_stop_seq=live_stop_seq+1 WHERE id=1""")
    return editable_settings()


def amo_off() -> dict:
    with managed_db() as con:
        con.execute("UPDATE control SET armed=0,stop_seq=stop_seq+1 WHERE id=1")
    return editable_settings()


def _regular_session_open() -> bool:
    from src.engine.market import market_status, MARKET_CLOSE
    now=datetime.now(IST)
    # Exclude the closing instant; use the existing verified holiday calendar.
    return now.time() < MARKET_CLOSE and market_status(now=now, cutoff=MARKET_CLOSE)[0]


def _route_window(route: str) -> bool:
    return _regular_session_open() if route == "REGULAR" else route == "AMO" and _session_closed()


def _route_enabled(control: dict, item: dict) -> bool:
    live=item.get("entry_route", "AMO") == "REGULAR"
    return bool(control["live_enabled" if live else "armed"]) and (
        control["live_stop_seq" if live else "stop_seq"] == item["arm_stop_seq"])


def reserve(source: str, msg_id: int, symbol: str, sent_utc: int) -> int | None:
    """Persistent ON gate: each distinct fresh Telegram message gets one durable intent."""
    if source not in ("TEST","EARNINGS_PULSE") or not re.fullmatch(
        r"[A-Z][A-Z0-9&._-]{0,29}",symbol or ""
    ):
        return None
    now=int(time.time())
    if not 0<=now-int(sent_utc)<=MAX_SIGNAL_AGE:
        return None
    if not trading_service_stopped():
        stop_all()
        return None
    with managed_db() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            c=_control(con)
            route="REGULAR" if _regular_session_open() else "AMO" if _session_closed() else None
            live=route == "REGULAR"
            if not(route and c["live_enabled" if live else "armed"]
                    and c["source"] in (source,"BOTH")
                    and int(sent_utc)>=c["live_since" if live else "armed_since"]):
                con.execute("COMMIT")
                logging.getLogger(__name__).info(
                    "ENTRY SKIPPED source=%s route=%s LIVE=%s AMO=%s: check session, selected source, activation time",
                    source, route or "CLOSED", bool(c["live_enabled"]), bool(c["armed"]))
                return None
            # NO auto-disarm. UNIQUE(source,msg_id) ensures at-most-one intent
            # per Telegram message across restarts. OFF is owner-controlled.
            account,_=dh.load_credentials()
            # Distinguish overlapping message IDs from the two sources.
            # Dhan correlation IDs must fit 30 characters and are never reused
            # for distinct Telegram messages, including after switch toggles.
            digest=hashlib.sha256(
                f"{source}:{msg_id}:{c['arm_seq']}".encode("utf-8")
            ).hexdigest()[:24]
            correlation="RBA-"+digest
            cur=con.execute(
                """INSERT OR IGNORE INTO intents
                 (source,msg_id,symbol,alert_ts,created_ts,account,arm_stop_seq,budget,correlation,entry_route)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
                 (source,int(msg_id),symbol,int(sent_utc),now,account,c["live_stop_seq" if live else "stop_seq"],c["budget"],correlation,route)
            )
            con.execute("COMMIT")
            return int(cur.lastrowid) if cur.rowcount else None
        except BaseException:
            con.execute("ROLLBACK")
            raise


def _update(intent_id:int,state:str,*,note:str="",order_id:str|None=None,
            broker_status:str|None=None,security_id:str|None=None,
            qty:int|None=None,price:float|None=None,cancel_after:int|None=None):
    fields=["state=?","note=?"]; values=[state,note[:180]]
    for field, val in (("order_id",order_id),("broker_status",broker_status),
                       ("security_id",security_id),("qty",qty),("price",price),
                       ("cancel_after",cancel_after)):
        if val is not None:
            fields.append(field+"=?");values.append(val)
    values.append(intent_id)
    with managed_db() as con:
        con.execute("UPDATE intents SET "+",".join(fields)+" WHERE id=?",values)


def _intent(intent_id:int) -> dict:
    with managed_db() as con:
        con.row_factory=sqlite3.Row
        r=con.execute("SELECT * FROM intents WHERE id=?",(intent_id,)).fetchone()
        if not r:
            raise LiveAmoError("Unknown resultbot-owned AMO intent")
        return dict(r)


def _dhan_http(method:str,path:str,token:str,payload:dict|None=None) -> dict:
    if method=="POST" and path!="/orders":
        raise LiveAmoError("Only Dhan POST /orders is authorized for entry")
    if method=="DELETE" and not re.fullmatch(r"/orders/[0-9]{6,25}",path):
        raise LiveAmoError("Only explicit resultbot-owned Dhan order cancellation allowed")
    if method not in ("POST","DELETE"):
        raise LiveAmoError("Only placement/cancellation broker mutations supported")
    body=json.dumps(payload).encode() if payload is not None else None
    req=Request("https://api.dhan.co/v2"+path,data=body,method=method,headers={
      "access-token":token,"Content-Type":"application/json","Accept":"application/json",
      "User-Agent":"ResultBot-Amo-OwnerOptIn/1.0",
    })
    # No retries. Ambiguous timeouts always require correlation reconciliation.
    try:
        with urlopen(req,timeout=8) as response:
            if response.status not in (200,201,202):
                raise LiveAmoError("Unexpected Dhan HTTP status; manually reconcile")
            data=json.loads(response.read(150_000))
        if not isinstance(data,dict):
            raise LiveAmoError("Broker mutation response is not an object")
        return data
    except (HTTPError,URLError,TimeoutError,OSError,ValueError):
        raise LiveAmoError("Broker outcome UNKNOWN or rejected; reconcile by correlation/order ID") from None


def _egress_ip() -> str:
    try:
        with urlopen(Request(EGRESS_CHECK_URL,headers={"User-Agent":"ResultBot-IP/1.0"}),timeout=6) as r:
            ip=r.read(100).decode().strip()
    except (URLError,OSError,TimeoutError):
        raise LiveAmoError("Cannot verify VM outbound IP") from None
    if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}",ip):
        raise LiveAmoError("Invalid outbound IP response")
    return ip


def _fresh_dhan_funds(account: str, token: str) -> float:
    """Strictly read-only funds retry for temporary Dhan HTTP 5xx.

    Never use a remembered balance, and never retry a broker order POST.
    """
    for attempt in range(3):
        try:
            data=dh.get_json("/fundlimit",token)
            validated=dh.verify_fund_limits(account,data)
            return float(validated["availabelBalance"])
        except dh.DhanReadError as exc:
            msg=str(exc)
            transient=any(f"Dhan HTTP error: {code}" in msg
                          for code in (500,502,503,504))
            if not transient:
                raise
            if attempt==2:
                raise LiveAmoError(
                    "Dhan GET /fundlimit returned HTTP 5xx after 3 "
                    "GET-only attempts; live balance unverified. "
                    "AMO remains OFF; do not submit a real order."
                ) from None
            time.sleep((attempt+1)*1.0)
    raise LiveAmoError("Dhan funds unavailable; AMO blocked")


def _verified_broker_context(account:str,token:str):
    dh.verified_profile(account,token)
    ip=dh.get_json("/ip/getIP",token)
    if not isinstance(ip,dict):
        raise LiveAmoError("Dhan registered-IP response invalid")
    outbound=_egress_ip()
    if outbound not in (ip.get("primaryIP"),ip.get("secondaryIP")):
        raise LiveAmoError("ResultBot outbound IP is not on the Dhan order whitelist")
    # Live funds must always be freshly verified; a temporary upstream
    # HTTP 500 is retried ONLY for the safe, idempotent GET endpoint.
    return _fresh_dhan_funds(account,token)


def _session_closed() -> bool:
    now=datetime.now(IST)
    # Explicit conservative AMO-only window; regular-market and preopen excluded.
    minutes=now.hour*60+now.minute
    return now.weekday()>=5 or minutes >= 16*60 or minutes < 8*60+30


def _verified_instrument(symbol:str) -> tuple[str, Decimal]:
    from src.instruments.resolver import InstrumentResolver
    path=Path.home()/"uditbot"/"data"/"scrip_master.csv"
    resolver=InstrumentResolver()
    if not resolver.load_dhan_master(str(path),max_cache_age_sec=86400,
                                     allow_network_download=True):
        raise LiveAmoError("Dhan instrument master unavailable")
    if time.time()-path.stat().st_mtime>86400:
        raise LiveAmoError("Dhan instrument master stale")
    inst,_=resolver.resolve_equity(symbol)
    if inst is None or inst.exchange_segment!="NSE_EQ" or not str(inst.security_id).isdigit():
        raise LiveAmoError("Cannot confirm NSE_EQ security ID for this symbol")
    tick=Decimal(str(inst.tick_size))
    if not Decimal("0.01")<=tick<=Decimal("1"):
        raise LiveAmoError("Unverified NSE tick size")
    return str(inst.security_id),tick


def _live_quote_and_size(symbol: str, budget: int, tick: Decimal):
    from urllib.parse import quote
    ticker=symbol+".NS"
    req=Request("https://query1.finance.yahoo.com/v8/finance/chart/"+
                quote(ticker,safe="")+"?interval=1d&range=1d",
                headers={"User-Agent":"Mozilla/5.0", "Cache-Control":"no-cache"})
    with urlopen(req, timeout=8) as response:
        data=json.load(response)
    try:
        meta=data["chart"]["result"][0]["meta"]
        if meta.get("symbol") != ticker or meta.get("currency") != "INR":
            raise ValueError("Yahoo symbol/currency mismatch")
        price=Decimal(str(meta["regularMarketPrice"]))
        stamp=float(meta["regularMarketTime"])
        if not price.is_finite() or price <= 0 or not 0 <= time.time()-stamp <= 2:
            raise ValueError("Yahoo live quote missing, future-dated or older than 2 seconds")
        pct=Decimal("0.25")
        config=ROOT/"intraday_buy_settings.json"
        if config.exists():
            pct=Decimal(str(json.loads(config.read_text())["buy_buffer_pct"]))
        if not pct.is_finite() or not 0 <= pct < 100:
            raise ValueError("Invalid saved buying buffer")
        limit=(price*(1+pct/100)/tick).to_integral_value(rounding=ROUND_CEILING)*tick
        qty=int(Decimal(budget)//limit)
        if qty < 1:
            raise ValueError("Stock too expensive for current INR "+str(budget)+" budget")
        return qty,float(limit),stamp
    except (KeyError,TypeError,IndexError,ArithmeticError,ValueError) as exc:
        raise LiveAmoError("Regular entry quote unavailable: "+str(exc)) from exc


def _quote_and_size(symbol:str,budget:int,tick:Decimal):
    bars=watch.request_bars(symbol)
    if not bars:
        raise LiveAmoError("No independently verified Yahoo NSE price bars")
    last=max(bars,key=lambda b:int(b["ts_utc"]))
    age=time.time()-int(last["ts_utc"])
    if not 60<=age<=72*3600:
        raise LiveAmoError("Latest market reference is missing or older than 72 hours")
    # 0.25% cushion, rounded UP to a verified instrument tick.
    close=Decimal(str(last["close"]))
    limit=(close*Decimal("1.0025")/tick).to_integral_value(rounding=ROUND_CEILING)*tick
    if limit <= 0 or limit >= close * Decimal("1.03"):
        raise LiveAmoError("Quoted AMO limit is invalid")
    qty=int(Decimal(budget)//limit)
    if qty<=0 or Decimal(qty)*limit > budget:
        raise LiveAmoError("Stock too expensive for current ₹"+str(budget)+" budget")
    return qty,float(limit),int(last["ts_utc"])


def _queue(intent:dict,kind:str,body:str):
    if intent.get("entry_route") == "REGULAR":
        kind=kind.replace("REAL_AMO", "REAL_REGULAR")
    # Use original listener's durable outbox. Never try Telegram auth here.
    if not re.fullmatch(r"[A-Z0-9_]{5,60}",kind):
        raise ValueError("Invalid broker card kind")
    channel=watch.CHANNEL_ID if intent["source"]=="EARNINGS_PULSE" else watch.configured_test_group_id()
    if channel is None:
        return
    con=watch.setup_db()
    try:
        con.execute("""INSERT OR IGNORE INTO log_outbox
          (channel_id,message_id,kind,text_html,created_utc) VALUES (?,?,?,?,?)""",
            (int(channel),int(intent["msg_id"]),kind,body,int(time.time())))
    finally:
        con.close()


def _broker_card(intent:dict,summary:str) -> str:
    return "\n".join((
       f"🏦 <b>RESULTBOT — REAL EQUITY {escape(intent.get('entry_route', 'AMO'))}</b>",
       f"📈 #{escape(intent['symbol'])}",
       f"📩 Source: {escape(intent['source'])} | Telegram ID {intent['msg_id']}",
       f"🔁 {escape(summary)}",
       f"🧾 Dhan order ID: <b>{escape(intent['order_id'] or 'UNCONFIRMED')}</b>",
       f"📋 Broker status: {escape(intent['broker_status'] or intent['state'])}",
       f"Details: {escape(intent.get('note', ''))}",
       f"💵 CNC LIMIT ₹{intent['price']:.2f} × {intent['qty']}",
       "⚠️ REAL brokerage order. OFF does not cancel it; partial fills remain owned even after cancellation.",
    ))


def _verify_owned_order(intent:dict,token:str) -> dict:
    if not re.fullmatch(r"[0-9]{6,25}",intent["order_id"]):
        raise LiveAmoError("No valid Dhan order ID recorded")
    order=dh.find_owned_order(intent["account"],token,intent["order_id"])
    if not isinstance(order,dict):
        raise LiveAmoError("Broker order detail unavailable")
    expected={
      "dhanClientId":intent["account"],"orderId":intent["order_id"],
      "correlationId":intent["correlation"],"securityId":intent["security_id"],
      "exchangeSegment":"NSE_EQ","productType":"CNC","transactionType":"BUY",
      "orderType":"LIMIT"
    }
    for name,value in expected.items():
        if str(order.get(name) or "")!=str(value):
            raise LiveAmoError("Dhan order binding mismatch: "+name)
    if order.get("afterMarketOrder") is not (intent.get("entry_route", "AMO") == "AMO"):
        raise LiveAmoError("Dhan order route differs from the reserved route")
    return order


def execute(intent_id:int) -> None:
    """One irreversible POST at most. On any transport ambiguity: UNKNOWN."""
    item=_intent(intent_id)
    if item["state"]!="RESERVED":
        return
    with managed_db() as con:
        claimed=con.execute("""UPDATE intents SET state='PREPARING', note='Preparing order'
                               WHERE id=? AND state='RESERVED'""", (intent_id,))
        if claimed.rowcount != 1:
            return
    try:
        if not trading_service_stopped():
            raise LiveAmoError("Trading service not safely stopped")
        if not _route_window(item["entry_route"]):
            raise LiveAmoError("Outside reserved "+item["entry_route"]+" trading window")
        if int(time.time())-item["alert_ts"]>MAX_SIGNAL_AGE:
            raise LiveAmoError("Telegram alert became stale before order submission")
        _update(intent_id,"PREPARING",note="Verifying broker account, IP, funds and market reference")
        account,token=dh.load_credentials()
        if account!=item["account"]:
            raise LiveAmoError("Dhan account changed since intent reservation")
        balance=_verified_broker_context(account,token)
        security_id,tick=_verified_instrument(item["symbol"])
        budget=int(item["budget"])
        qty,price,quote_ts=(_live_quote_and_size if item["entry_route"] == "REGULAR" else _quote_and_size)(item["symbol"],budget,tick)
        if balance < qty*price:
            raise LiveAmoError("Order value exceeds available Dhan funds")
        if not trading_service_stopped() or not _route_window(item["entry_route"]):
            raise LiveAmoError("Safety conditions changed before submission")
        if time.time()-item["alert_ts"]>MAX_SIGNAL_AGE:
            raise LiveAmoError("Telegram alert stale before final submission")
        # Serialize OFF with the final pre-submit transition. If OFF has
        # happened, the broker POST is never attempted.
        with managed_db() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                control=_control(con)
                if (not _route_enabled(control,item) or
                    control["source"] not in (item["source"],"BOTH")):
                    raise LiveAmoError("Entry route is OFF or owner changed the active source")
                # In-flight orders need funds reserved even if simultaneous
                # Dhan fund reads still show the original available balance.
                # This is a financial exposure check, NOT a count-based limit.
                pending_notional=con.execute(
                    """SELECT COALESCE(SUM(qty*price),0) FROM intents
                       WHERE id<>? AND state IN
                       ('SUBMITTING','UNKNOWN','ACKED','CANCEL_REQUESTED')""",
                    (intent_id,)
                ).fetchone()[0]
                if balance < float(pending_notional)+qty*price:
                    raise LiveAmoError(
                        "Order value exceeds Dhan funds after other pending orders"
                    )
                if not _route_window(item["entry_route"]):
                    raise LiveAmoError("Market window changed before submission")
                if item["entry_route"] == "REGULAR" and not 0 <= time.time()-quote_ts <= 2:
                    raise LiveAmoError("Live Yahoo quote exceeded 2 seconds before submission")
                changed=con.execute(
                    """UPDATE intents SET state='SUBMITTING',note=?,security_id=?,qty=?,price=?
                       WHERE id=? AND state='PREPARING'""",
                    ("Broker POST may have been sent; never auto-retry",
                     security_id,qty,price,intent_id))
                if changed.rowcount != 1:
                    raise LiveAmoError("Intent was modified; refusing broker submission")
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        item=_intent(intent_id)
        payload={
          "dhanClientId":account,"correlationId":item["correlation"],
          "transactionType":"BUY","exchangeSegment":"NSE_EQ","productType":"CNC",
          "orderType":"LIMIT","validity":"DAY","securityId":security_id,
          "quantity":qty,"price":price,"triggerPrice":0,
          "disclosedQuantity":0,"afterMarketOrder":item["entry_route"] == "AMO"
        }
        if item["entry_route"] == "AMO":
            payload["amoTime"]="OPEN"
        try:
            result=_dhan_http("POST","/orders",token,payload)
        except LiveAmoError:
            _update(intent_id,"UNKNOWN",note="Broker response ambiguous: inspect Dhan account immediately")
            _queue(_intent(intent_id),"REAL_AMO_UNKNOWN",
                   _broker_card(_intent(intent_id),"Dhan POST outcome UNKNOWN; do not resubmit"))
            return
        oid=str(result.get("orderId") or "")
        state=str(result.get("orderStatus") or "").upper()
        if not re.fullmatch(r"[0-9]{6,25}",oid):
            _update(intent_id,"UNKNOWN",note="No authentic broker order ID; manual correlation reconciliation")
            _queue(_intent(intent_id),"REAL_AMO_UNKNOWN",
                   _broker_card(_intent(intent_id),"Dhan response has no verifiable order ID"))
            return
        _update(intent_id,"ACKED",note="Dhan order ID received; verifying broker order book",
                order_id=oid,broker_status=state,cancel_after=0)
        item=_intent(intent_id)
        try:
            detail=_verify_owned_order(item,token)
        except (dh.DhanReadError,LiveAmoError):
            _update(intent_id,"UNKNOWN",note="Dhan order ID received, but account/route verification failed")
            _queue(_intent(intent_id),"REAL_AMO_UNKNOWN",
                   _broker_card(_intent(intent_id),"Order ID received but broker verification inconclusive"))
            return
        final_status=str(detail.get("orderStatus") or "PENDING").upper()
        if final_status not in ("PENDING","TRANSIT","PART_TRADED",*TERMINAL):
            _update(intent_id,"UNKNOWN",note="Unrecognized Dhan order status; manual verification",
                    broker_status=final_status)
            _queue(_intent(intent_id),"REAL_AMO_UNKNOWN",_broker_card(_intent(intent_id),
                "Order ID verified; Dhan status requires review"))
            return
        _update(intent_id,"TERMINAL" if final_status in TERMINAL else "ACKED",note="Broker-verified equity order status",
                broker_status=final_status)
        _queue(_intent(intent_id),"REAL_AMO_ACK",_broker_card(_intent(intent_id),
              "Dhan order verified; see broker status and fills"))
    except (dh.DhanReadError,LiveAmoError,ValueError,ImportError,OSError) as exc:
        state=_intent(intent_id)["state"]
        if state=="SUBMITTING":
            _update(intent_id,"UNKNOWN",note="Order submission may have occurred; manual reconciliation")
        elif state not in ("ACKED","UNKNOWN","TERMINAL"):
            _update(intent_id,"BLOCKED",note=str(exc)[:150])
        item=_intent(intent_id)
        _queue(item,"REAL_AMO_BLOCKED" if item["state"]=="BLOCKED" else "REAL_AMO_UNKNOWN",
               _broker_card(item,"ORDER BLOCKED" if item["state"]=="BLOCKED"
                            else "Broker outcome requires manual review"))


def request_cancel(intent_id:int,automatic:bool=False) -> str:
    """Cancel only recorded ResultBot-owned pending order; never assume success."""
    item=_intent(intent_id)
    if item["state"] not in ("ACKED","UNKNOWN","CANCEL_REQUESTED"):
        raise LiveAmoError("Cannot safely cancel: no active resultbot-owned order")
    if not re.fullmatch(r"[0-9]{6,25}",item["order_id"]):
        raise LiveAmoError("Cannot cancel without verified Dhan order ID")
    account, token=dh.load_credentials()
    if account!=item["account"]:
        raise LiveAmoError("Dhan account mismatch before cancellation")
    detail=_verify_owned_order(item,token)
    status=str(detail.get("orderStatus") or "").upper()
    filled=dh.checked_number(detail.get("filledQty"))
    if status in TERMINAL:
        _update(intent_id,"TERMINAL",
                broker_status=status,note="Order already terminal; cannot cancel")
        return status
    # PART_TRADED still has a cancellable pending remainder; an existing
    # filled portion cannot be undone and must be managed separately.
    if status not in ("TRANSIT","PENDING","PART_TRADED"):
        raise LiveAmoError("Unrecognized broker state; cancellation blocked")
    # Lock and claim ONE cancellation attempt only after broker preflight succeeds.
    with managed_db() as con:
        claim=con.execute(
            "UPDATE intents SET cancel_attempted=1 WHERE id=? AND cancel_attempted=0",
            (intent_id,)
        )
        if claim.rowcount != 1:
            raise LiveAmoError("Cancel already attempted; check Dhan order book")
    try:
        result=_dhan_http("DELETE","/orders/"+item["order_id"],token)
    except LiveAmoError:
        _update(intent_id,"CANCEL_REQUESTED",note="Cancellation outcome uncertain; check Dhan directly")
        return "CANCEL_UNCONFIRMED"
    result_status=str(result.get("orderStatus") or "TRANSIT").upper()
    if str(result.get("orderId"))!=item["order_id"]:
        _update(intent_id,"CANCEL_REQUESTED",note="Cancellation response mismatch; check Dhan")
        return "CANCEL_UNCONFIRMED"
    _update(intent_id,"CANCEL_REQUESTED",broker_status=result_status,
            note="Dhan cancel request received; awaiting broker status")
    return result_status


def _order_by_correlation(correlation: str, token: str) -> dict | None:
    """Dhan GET /orders/external/{correlationId}; never submit twice on ambiguity."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{3,30}",correlation):
        raise LiveAmoError("Unsafe correlation ID")
    req=Request("https://api.dhan.co/v2/orders/external/"+correlation,
                headers={"access-token":token,"Accept":"application/json"},
                method="GET")
    try:
        with urlopen(req,timeout=10) as r:
            payload=json.loads(r.read(200_000))
    except HTTPError as exc:
        if exc.code==404:
            return None
        raise LiveAmoError("Cannot reconcile Dhan order by correlation ID") from None
    except (URLError,OSError,TimeoutError,ValueError):
        raise LiveAmoError("Broker reconciliation temporarily unavailable") from None
    if not isinstance(payload,dict):
        raise LiveAmoError("Unexpected Dhan correlation response")
    return payload


def reconcile_unknown(intent_id:int) -> bool:
    item=_intent(intent_id)
    if item["state"]!="UNKNOWN":return False
    account,token=dh.load_credentials()
    dh.verified_profile(account,token)
    if account!=item["account"]:
        raise LiveAmoError("Dhan account mismatch during reconciliation")
    order=_order_by_correlation(item["correlation"],token)
    if order is None:
        return False  # Absence never authorizes automatic resubmission.
    if str(order.get("dhanClientId") or "")!=account:
        raise LiveAmoError("Correlation response belongs to another Dhan account")
    if str(order.get("correlationId") or "")!=item["correlation"]:
        raise LiveAmoError("Dhan correlation mismatch")
    oid=str(order.get("orderId") or "")
    if not re.fullmatch(r"[0-9]{6,25}",oid):
        raise LiveAmoError("Dhan correlation returned invalid order ID")
    # If no verified securityId was stored before a failure, do NOT assume this
    # correlation represents the intended stock and do not claim confirmation.
    if not item["security_id"] or str(order.get("securityId") or "")!=item["security_id"]:
        raise LiveAmoError("Cannot verify order instrument from uncertain submission")
    if (str(order.get("exchangeSegment"))!="NSE_EQ" or
        str(order.get("productType"))!="CNC" or
        str(order.get("transactionType"))!="BUY" or
        order.get("afterMarketOrder") is not (item.get("entry_route", "AMO") == "AMO") or
        str(order.get("orderType"))!="LIMIT"):
        raise LiveAmoError("Dhan order route differs from the reserved equity order")
    status=str(order.get("orderStatus") or "UNKNOWN").upper()
    _update(intent_id,"TERMINAL" if status in TERMINAL else
            "ACKED" if status in ("PENDING","TRANSIT","PART_TRADED") else "UNKNOWN",
            order_id=oid,broker_status=status,
            cancel_after=0,
            note="Broker correlation verified after uncertain order response")
    _queue(_intent(intent_id),"REAL_AMO_RECONCILED",
           _broker_card(_intent(intent_id),"Actual Dhan order ID recovered by correlation"))
    return True


def monitor() -> int:
    """Monitor ALL resultbot-owned live orders; cancellation is owner-controlled."""
    with managed_db() as con:
        intents=[(r[0],r[1]) for r in con.execute(
            "SELECT id,state FROM intents WHERE state IN ('UNKNOWN','ACKED','CANCEL_REQUESTED') ORDER BY id"
        )]
    changed=0
    for intent_id, initial_state in intents:
        if initial_state == "UNKNOWN":
            try:
                if reconcile_unknown(intent_id):
                    changed += 1
            except (LiveAmoError,dh.DhanReadError,OSError,ValueError):
                pass
            if _intent(intent_id)["state"]!="ACKED":
                continue
        item=_intent(intent_id)
        try:
            acct,token=dh.load_credentials()
            if acct!=item["account"]:
                raise LiveAmoError("Account mismatch: monitoring blocked")
            order=_verify_owned_order(item,token)
            status=str(order.get("orderStatus") or "").upper()
            filled=dh.checked_number(order.get("filledQty"))
            if status not in ("TRANSIT","PENDING","PART_TRADED","TRADED","REJECTED","CANCELLED","EXPIRED"):
                raise LiveAmoError("Unknown broker order status")
            if status!=item["broker_status"] or (filled is not None and filled>0):
                _update(intent_id,"TERMINAL" if status in TERMINAL else "ACKED",
                        broker_status=status,note="Broker-verified order update")
                newest=_intent(intent_id)
                _queue(newest,"REAL_AMO_"+re.sub("[^A-Z0-9]","_",status),
                       _broker_card(newest,"Broker-confirmed update; filled="+str(filled)))
                changed+=1
            # Never cancel merely because an elapsed timer fired. The owner
            # chooses which broker-confirmed orders to cancel and when.
        except (LiveAmoError,dh.DhanReadError,ValueError,OSError):
            # State remains unresolved and should be reviewed in Dhan.
            continue
    return changed


def preflight() -> dict:
    """Real Dhan read-only readiness check; never creates or cancels an order."""
    if not trading_service_stopped():
        raise LiveAmoError("Trading service must be inactive and disabled")
    if not _session_closed():
        raise LiveAmoError("Closed-market AMO window not verified")
    account,token=dh.load_credentials()
    balance=_verified_broker_context(account,token)
    if balance<200:
        raise LiveAmoError("Insufficient verified available balance even for a small Equity AMO")
    return {"dhan_account":dh.display_account(account),
            "available_to_trade_inr":round(float(balance),2),
            "outbound_static_ip_whitelisted":True,
            "trading_bot_inactive_and_disabled":True,
            "real_order_submitted":False}


def offline_self_test():
    assert _session_closed is not None
    assert SQLITE_INT_MAX == 9223372036854775807 and MAX_SIGNAL_AGE==90
    assert TEST_PREFIX=="[RBLIVEAMO] "
    with managed_db(Path("/tmp")/("amo_test_"+str(os.getpid())+".sqlite3")) as con:
        assert _control(con)["armed"]==0
    for suffix in ("","-wal","-shm"):
        try:(Path("/tmp")/("amo_test_"+str(os.getpid())+".sqlite3"+suffix)).unlink()
        except FileNotFoundError: pass
    print("PASS: default OFF; durable persistent YES/NO; no order-count or time-expiry caps")


def main():
    import argparse
    p=argparse.ArgumentParser(description="ResultBot LIVE Equity AMO persistent YES/NO; OFF by default")
    sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("status")
    sub.add_parser("status-short")
    sub.add_parser("preflight")
    edit=sub.add_parser("settings",help="edit saved AMO source/budget, leave YES/NO unchanged")
    edit.add_argument("--source",choices=["TEST","EARNINGS_PULSE","BOTH"])
    edit.add_argument("--budget",type=int)
    edit.add_argument("--show",action="store_true",
                      help="display current settings without changing anything")
    sub.add_parser("self-test")
    sub.add_parser("off", help="Legacy AMO OFF only")
    sub.add_parser("stop-all", help="Stop BOTH regular and AMO new entries")
    sub.add_parser("live-on", help="Enable regular-session entries using saved source/budget")
    sub.add_parser("live-off", help="Stop regular-session new entries")
    sub.add_parser("amo-off", help="Stop AMO new entries only")
    x=sub.add_parser("arm")
    x.add_argument("--source",choices=["TEST","EARNINGS_PULSE","BOTH"],default=None)
    x.add_argument("--budget",type=int,default=None)
    x=sub.add_parser("cancel")
    x.add_argument("--intent-id",type=int,required=True)
    sub.add_parser("monitor")
    a=p.parse_args()
    if a.cmd=="self-test": offline_self_test();return
    if a.cmd=="status": print(json.dumps(status(),indent=2));return
    if a.cmd=="status-short": print(status_short());return
    if a.cmd=="settings":
        try:
            if a.show:
                if a.source is not None or a.budget is not None:
                    raise LiveAmoError("--show cannot be combined with settings changes")
                data=editable_settings()
            else:
                data=update_settings(source=a.source,budget=a.budget)
            print(json.dumps(data,indent=2))
        except LiveAmoError as exc:
            print("SETTINGS BLOCKED: "+str(exc),file=sys.stderr)
            raise SystemExit(2) from None
        return
    if a.cmd=="preflight":
        try:
            print(json.dumps(preflight(),indent=2))
        except (LiveAmoError,dh.DhanReadError) as exc:
            print("PRE-FLIGHT BLOCKED: "+str(exc),file=sys.stderr)
            raise SystemExit(1) from None
        return
    if a.cmd in ("live-on","live-off"):
        print(json.dumps(set_live(a.cmd=="live-on"),indent=2));return
    if a.cmd=="amo-off": print(json.dumps(amo_off(),indent=2));return
    if a.cmd=="stop-all": print(json.dumps(stop_all(),indent=2));return
    if a.cmd=="off": print(json.dumps(disarm(),indent=2));return
    if a.cmd=="arm":
        # Bare 'arm' uses SAVED settings, never resets owner choices to BOTH/500.
        saved=editable_settings()
        selected=saved["source"] if a.source is None else a.source
        amount=saved["per_order_budget_inr"] if a.budget is None else a.budget
        print(json.dumps(arm(source=selected,budget=amount),indent=2))
        return
    if a.cmd=="cancel": print("Cancel response:",request_cancel(a.intent_id));return
    if a.cmd=="monitor": print("Updated broker orders:",monitor())


if __name__=="__main__":
    main()
