#!/usr/bin/env python3
"""ResultBot Excellent Results: read-only Telegram collection + verifiable 1m Yahoo research.

This listener contains no order-entry or trading-engine imports.
An optional independent Dhan GET-only bridge monitors only explicitly linked
orders and queues broker-verification updates through the same Log Group outbox.
It never runs the trading_bot.service and never trades.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from html import escape
import logging
import math
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LOG = logging.getLogger("excellent")
IST = ZoneInfo("Asia/Kolkata")
ROOT = Path.home() / "resultbot-data"
DB = ROOT / "excellent_watch.sqlite3"
SESSION = ROOT / "excellent_telegram"  # independent: no reuse of trading bot's session filename
CHANNEL = "earnings_pulse"
CHANNEL_ID = 2457353254
TEST_MARKER = "[RBTEST]"  # required literal prefix for isolated Test Group messages
EXCELLENT = re.compile(r"\bexcellent\s+results?\b", re.IGNORECASE)
HASH = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9&._-]{0,29})\b")
FIELDS = ("open", "high", "low", "close")
CANDLES_MAX_DAYS = 5  # window queried with 1m chart; not a promise of availability


def parse_alert(text: str) -> tuple[str | None, str]:
    """Only unambiguous #STOCK - Excellent Results; never Great/Good/Weak."""
    if not isinstance(text, str) or not EXCELLENT.search(text):
        return None, "not_excellent"
    # Require a single stock hashtag, not unrelated multi-company posts.
    symbols = [m.upper() for m in HASH.findall(text)]
    unique = sorted(set(symbols))
    if len(unique) != 1:
        return None, "missing_or_multiple_stock_symbols"
    symbol = unique[0]
    if symbol in ("NIFTY", "BANKNIFTY", "SENSEX"):
        return None, "index_not_equity"
    return symbol, "excellent"


def parse_test_alert(text: str) -> tuple[str | None, str]:
    """Parse Excellent-only messages from the verified TEST group.

    The verified Telegram group identity is the security boundary, not a
    manually added text prefix. Old [RBTEST] messages also remain accepted.
    """
    if not isinstance(text, str):
        return None, "not_excellent"
    if text.startswith(TEST_MARKER + " "):
        text = text[len(TEST_MARKER) + 1:]
    return parse_alert(text)


def setup_db(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    con = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        sent_utc INTEGER NOT NULL,
        inserted_utc INTEGER NOT NULL,
        text TEXT NOT NULL,
        UNIQUE(channel_id, message_id)
      );
      CREATE INDEX IF NOT EXISTS alert_symbol_time ON alerts(symbol, sent_utc);
      CREATE TABLE IF NOT EXISTS one_minute_bars (
        symbol TEXT NOT NULL,
        ts_utc INTEGER NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume INTEGER,
        fetched_utc INTEGER NOT NULL,
        PRIMARY KEY(symbol, ts_utc)
      );
      CREATE INDEX IF NOT EXISTS bars_by_time ON one_minute_bars(ts_utc);
      CREATE TABLE IF NOT EXISTS fetch_status (
        symbol TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        checked_utc INTEGER NOT NULL,
        details TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS log_outbox (
        id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT 'EXCELLENT_ALERT',
        text_html TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING',
        created_utc INTEGER NOT NULL,
        last_try_utc INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        sent_utc INTEGER,
        UNIQUE(channel_id,message_id,kind)
      );
    """)
    return con


def add_alert(con: sqlite3.Connection, message_id: int, sent_dt: datetime, text: str, channel_id: int = CHANNEL_ID) -> str:
    if abs(int(channel_id)) not in (CHANNEL_ID, int(f"100{CHANNEL_ID}")):
        return "wrong_channel"
    symbol, reason = parse_alert(text)
    if not symbol:
        return reason
    if sent_dt.tzinfo is None:
        raise ValueError("Telegram timestamp must be timezone-aware")
    cur = con.execute("""INSERT OR IGNORE INTO alerts
        (channel_id,message_id,symbol,sent_utc,inserted_utc,text) VALUES (?,?,?,?,?,?)""",
        (CHANNEL_ID, int(message_id), symbol, int(sent_dt.timestamp()), int(time.time()), text[:4000]))
    return "stored" if cur.rowcount else "duplicate"



def format_excellent_log(symbol: str, alert_utc: int) -> str:
    """Only a capture acknowledgement. Never imply Dhan executed an order."""
    if not re.fullmatch(r"[A-Z][A-Z0-9&._-]{0,29}", symbol):
        raise ValueError("Invalid Excellent stock symbol")
    when = datetime.fromtimestamp(int(alert_utc), IST).strftime("%d-%b-%Y %H:%M:%S IST")
    return "\n".join((
        "🏆🔥 <b>RESULTBOT — EXCELLENT RESULTS</b>",
        f"📈 Stock: <b>#{escape(symbol)}</b>",
        "📩 Source: Earnings Pulse",
        f"🕒 Alert received: {when}",
        "✅ Status: Excellent alert captured",
        "🔎 Price study: Tracking verified post-alert candles",
        "",
        "🏦 <b>Dhan order (ResultBot research): NOT SUBMITTED</b>",
        "💰 Dhan execution price: N/A",
        "🟡 Research-only mode — no broker order was attempted",
    ))


def enqueue_live_log(con: sqlite3.Connection, message_id: int) -> bool:
    """Queue ONLY an already-stored live Excellent message; never history/backfill."""
    row = con.execute(
        "SELECT symbol,sent_utc FROM alerts WHERE channel_id=? AND message_id=?",
        (CHANNEL_ID, int(message_id)),
    ).fetchone()
    if row is None:
        return False
    text = format_excellent_log(str(row[0]), int(row[1]))
    cur = con.execute(
        """INSERT OR IGNORE INTO log_outbox
        (channel_id,message_id,kind,text_html,created_utc)
        VALUES (?,?,'EXCELLENT_ALERT',?,?)""",
        (CHANNEL_ID,int(message_id),text,int(time.time())),
    )
    return bool(cur.rowcount)


def format_test_log(symbol: str, alert_utc: int) -> str:
    """A synthetic test must never resemble a broker fill or real source alert."""
    if not re.fullmatch(r"[A-Z][A-Z0-9&._-]{0,29}", symbol):
        raise ValueError("Invalid test symbol")
    when = datetime.fromtimestamp(int(alert_utc), IST).strftime("%d-%b-%Y %H:%M:%S IST")
    return "\n".join((
        "🧪 <b>RESULTBOT TEST — EXCELLENT ONLY</b>",
        f"📈 Test symbol: <b>#{escape(symbol)}</b>",
        "📩 Source: Authorized ResultBot TEST GROUP (not Earnings Pulse)",
        f"🕒 Test message: {when}",
        "✅ Test-group Excellent filter: PASSED",
        "🏦 Dhan order: NOT SUBMITTED",
        "💰 Execution price: N/A",
        "🚫 NOT A REAL EARNINGS ALERT — no orders, no price research",
    ))


def enqueue_test_log(con: sqlite3.Connection, group_id: int, message_id: int,
                     sent_dt: datetime, raw_text: str) -> str:
    """Test-only outbox row, without touching alerts or minute bars."""
    symbol, verdict = parse_test_alert(raw_text)
    if symbol is None:
        return verdict
    if sent_dt.tzinfo is None:
        raise ValueError("Test message timestamp must be timezone-aware")
    if group_id >= 0 or group_id in (-CHANNEL_ID, -int(f"100{CHANNEL_ID}")):
        return "invalid_test_source"
    text = format_test_log(symbol, int(sent_dt.timestamp()))
    cur = con.execute(
        """INSERT OR IGNORE INTO log_outbox
        (channel_id,message_id,kind,text_html,created_utc)
        VALUES (?,?,'TEST_EXCELLENT_ALERT',?,?)""",
        (int(group_id),int(message_id),text,int(time.time())),
    )
    return "test_queued" if cur.rowcount else "duplicate"


def recent_test_candidate(sent_dt: datetime, now_utc: datetime, lookback_seconds: int = 1800) -> bool:
    """Limit startup history scanning to recently posted test-group messages."""
    if sent_dt.tzinfo is None or now_utc.tzinfo is None:
        return False
    age = (now_utc - sent_dt).total_seconds()
    return -120 <= age <= lookback_seconds


def configured_test_group_id() -> int | None:
    """Read the existing authorized Test Group ID; never print or alter secrets."""
    from dotenv import dotenv_values
    values = dotenv_values(Path.home() / "uditbot" / ".env")
    raw = (os.environ.get("RESULTBOT_TEST_GROUP_ID") or
           values.get("RESULTBOT_TEST_GROUP_ID") or
           os.environ.get("TEST_GROUP_ID") or values.get("TEST_GROUP_ID") or "")
    raw = str(raw).strip()
    if not raw or raw == "0":
        return None
    if not re.fullmatch(r"-[0-9]{5,20}",raw):
        raise ValueError("Test Group ID must be a negative signed Telegram group peer ID")
    return int(raw)


async def resolve_test_group(client):
    """Resolve only one existing joined Telegram group, not source or Log Group."""
    wanted = configured_test_group_id()
    if wanted is None:
        LOG.info("TEST_GROUP_ID not configured; test input is off")
        return None, None
    log_id = configured_log_group_id()
    if wanted == log_id or abs(wanted) in (CHANNEL_ID,int(f"100{CHANNEL_ID}")):
        raise RuntimeError("TEST_GROUP_ID cannot be the Log Group or Earnings Pulse source")
    matches=[]
    async for dialog in client.iter_dialogs():
        if int(dialog.id) == wanted and getattr(dialog,"is_group",False):
            matches.append(dialog.entity)
    if len(matches) != 1:
        raise RuntimeError("Test group is not a uniquely joined group; test input disabled")
    LOG.info("Verified ResultBot TEST GROUP (ID not printed)")
    return matches[0], wanted


def configured_log_group_id() -> int | None:
    """Use explicit ResultBot group, otherwise the existing bot log group."""
    from dotenv import dotenv_values
    values = dotenv_values(Path.home() / "uditbot" / ".env")
    raw = (os.environ.get("RESULTBOT_LOG_GROUP_ID") or
           values.get("RESULTBOT_LOG_GROUP_ID") or
           os.environ.get("LOG_GROUP_ID") or values.get("LOG_GROUP_ID") or "")
    raw = str(raw).strip()
    if not raw:
        return None
    if not re.fullmatch(r"-[0-9]{5,20}",raw):
        raise ValueError("Log group must be a signed negative Telegram group ID")
    return int(raw)


async def resolve_log_group(client):
    """Send only to an authenticated account's uniquely matching joined group."""
    wanted = configured_log_group_id()
    if wanted is None:
        LOG.warning("No LOG_GROUP_ID configured; Excellent Telegram cards disabled")
        return None
    matched=[]
    async for dialog in client.iter_dialogs():
        if int(dialog.id) == wanted and getattr(dialog,"is_group",False):
            entity_id = int(getattr(dialog.entity, "id", 0))
            if entity_id == CHANNEL_ID:
                raise RuntimeError("Refusing to post into the signal source channel")
            matched.append(dialog.entity)
    if len(matched) != 1:
        raise RuntimeError(
            "Log Group not found as exactly one joined Telegram group. "
            "No messages will be sent to an unverified destination."
        )
    LOG.info("Verified ResultBot Log Group destination (ID not printed)")
    return matched[0]


async def send_queued_logs(con: sqlite3.Connection, client, group) -> int:
    """Crash-recoverable at-least-once delivery; no historical import notifications."""
    if group is None:
        return 0
    now=int(time.time())
    # A previous process may have crashed after claim. Retrying could deliver a
    # duplicate in an ambiguous network failure; never claim exactly once.
    con.execute(
        "UPDATE log_outbox SET state='PENDING' WHERE state='SENDING' AND last_try_utc<?",
        (now-600,),
    )
    rows=con.execute(
        """SELECT id,text_html,created_utc FROM log_outbox
           WHERE state='PENDING' AND attempts<5 AND last_try_utc<=?
           ORDER BY id LIMIT 10""",
        (now-30,),
    ).fetchall()
    delivered=0
    for rowid, body, created in rows:
        if created < now-24*3600:
            con.execute("UPDATE log_outbox SET state='EXPIRED' WHERE id=?",(rowid,))
            continue
        claimed=con.execute(
            """UPDATE log_outbox SET state='SENDING',attempts=attempts+1,last_try_utc=?
               WHERE id=? AND state='PENDING'""",
            (now,rowid),
        )
        if not claimed.rowcount:
            continue
        try:
            await client.send_message(group,body,parse_mode="html",link_preview=False)
        except Exception as exc:
            con.execute("UPDATE log_outbox SET state='PENDING' WHERE id=?",(rowid,))
            LOG.warning("Excellent Log Group delivery failed: %s",type(exc).__name__)
        else:
            con.execute(
                "UPDATE log_outbox SET state='SENT',sent_utc=? WHERE id=?",
                (int(time.time()),rowid),
            )
            delivered+=1
    return delivered


def request_bars(symbol: str, timeout: int = 12) -> list[dict]:
    """Return *completed*, provider-timestamped NSE 1m candles, or fail closed."""
    if not re.fullmatch(r"[A-Z][A-Z0-9&._-]{0,29}", symbol):
        raise ValueError("Invalid symbol")
    ticker = symbol + ".NS"
    query = urlencode({"interval": "1m", "range": "5d"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}?{query}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 ResultBot-Research/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as reply:
        data = json.load(reply)
    chart = data.get("chart") or {}
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"No Yahoo chart for {ticker}: {str(chart.get('error'))[:180]}")
    result = results[0]
    meta = result.get("meta") or {}
    returned = str(meta.get("symbol", "")).upper()
    if returned != ticker.upper() or meta.get("currency") != "INR":
        raise ValueError(f"Yahoo identity mismatch: asked={ticker}, returned={returned}, currency={meta.get('currency')}")
    quotes = (result.get("indicators") or {}).get("quote") or []
    if not quotes:
        raise ValueError("Yahoo returned no quote arrays")
    quoted = quotes[0]
    out = []
    now = time.time()
    for i, ts in enumerate(result.get("timestamp") or []):
        try:
            values = {k: float(quoted[k][i]) for k in FIELDS}
            if not all(math.isfinite(v) and v > 0 for v in values.values()):
                continue
            ts = int(ts)
            # Exclude not-yet-closed minute to prevent use of moving candles.
            if ts + 60 > now + 1:
                continue
            ist = datetime.fromtimestamp(ts, IST)
            if ist.weekday() >= 5 or not (9 * 60 + 15 <= ist.hour*60 + ist.minute < 15*60 + 30):
                continue
            if values["high"] < values["low"] or not values["low"] <= values["close"] <= values["high"]:
                continue
            v = quoted.get("volume") or []
            vol = v[i] if i < len(v) and isinstance(v[i], (float, int)) and v[i] >= 0 else None
            out.append({"symbol": symbol, "ts_utc": ts, **values,
                        "volume": int(vol) if vol is not None else None, "fetched_utc": int(now)})
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    return out


def store_bars(con: sqlite3.Connection, symbol: str, bars: list[dict]) -> int:
    rows = [(symbol,b["ts_utc"],b["open"],b["high"],b["low"],b["close"],b["volume"],b["fetched_utc"]) for b in bars]
    # Preserve first saved completed candle, never replace historic values with later vendor revisions.
    con.executemany("""INSERT OR IGNORE INTO one_minute_bars
      (symbol, ts_utc, open, high, low, close, volume, fetched_utc) VALUES (?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def track_once(con: sqlite3.Connection, *, now: int | None = None) -> tuple[int, int]:
    """Only request symbols with recent Excellent alerts. Not all market symbols."""
    now = int(now or time.time())
    earliest = now - 5 * 86400
    symbols = [row[0] for row in con.execute("SELECT DISTINCT symbol FROM alerts WHERE sent_utc>=?", (earliest,))]
    worked = 0
    for symbol in symbols:
        # Respect free-data endpoint and give delayed minute bars time to settle.
        last = con.execute("SELECT checked_utc FROM fetch_status WHERE symbol=?", (symbol,)).fetchone()
        if last and now - int(last[0]) < 180:
            continue
        try:
            bars = request_bars(symbol)
            n = store_bars(con, symbol, bars)
            con.execute("REPLACE INTO fetch_status VALUES (?,?,?,?)", (symbol, "OK" if n else "NO_BARS", now, f"candles={n}"))
            worked += 1
        except (HTTPError, URLError, OSError, ValueError, TimeoutError) as e:
            con.execute("REPLACE INTO fetch_status VALUES (?,?,?,?)", (symbol, "UNAVAILABLE", now, str(e)[:250]))
            LOG.warning("Price unavailable for %s: %s", symbol, str(e)[:160])
    return worked, len(symbols)


def poll_new_db_connection() -> tuple[int,int]:
    """SQLite connections must be created inside the polling thread."""
    con=setup_db()
    try:
        return track_once(con)
    finally:
        con.close()


def same_session(ts1: int, ts2: int) -> bool:
    return datetime.fromtimestamp(ts1, IST).date() == datetime.fromtimestamp(ts2, IST).date()


def summarize(con: sqlite3.Connection, alert_id: int) -> dict:
    row = con.execute("SELECT symbol, sent_utc, message_id FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise ValueError("Missing alert")
    sym, alert_ts, message_id = row
    # A 1-minute bar spans [ts, ts+60); require open >= alert time, and
    # strictly later than the alert's minute (pre-alert trades may share that minute).
    first_possible = (alert_ts//60 + 1)*60
    first = con.execute("""SELECT ts_utc,close FROM one_minute_bars WHERE symbol=? AND ts_utc>=?
      AND ts_utc<=? ORDER BY ts_utc LIMIT 1""", (sym, first_possible, alert_ts + 5*86400)).fetchone()
    d = {"symbol": sym, "message_id": message_id, "alert_utc": alert_ts,
         "first_bar_utc": None, "first_price": None, "status": "NO_VERIFIED_POST_ALERT_CANDLE"}
    if first is None:
        return d
    first_ts, first_price = first
    d.update(first_bar_utc=first_ts, first_price=first_price, status="VERIFIED_1M_CANDLE")
    # Only same market session, only first 60 minutes after first price observation.
    rows = [r for r in con.execute("""SELECT ts_utc,low,high,close FROM one_minute_bars
      WHERE symbol=? AND ts_utc>=? AND ts_utc<=? ORDER BY ts_utc""",
      (sym, first_ts, first_ts+3600)) if same_session(first_ts, r[0])]
    # The reference first_price is the CLOSE of first_ts: its own high/low
    # could have happened BEFORE that close and must never be counted as
    # post-reference excursions. Only LATER completed bars are eligible.
    range_rows = [r for r in rows if r[0] > first_ts]
    d["observed_low_pct"] = round((min(r[1] for r in range_rows)/first_price-1)*100, 3) if range_rows else None
    d["observed_high_pct"] = round((max(r[2] for r in range_rows)/first_price-1)*100, 3) if range_rows else None
    for minutes in (5, 15, 30, 60):
        # First completed candle AT/AFTER milestone, at most 2 min late.
        candidates = [r for r in rows if first_ts + minutes*60 <= r[0] <= first_ts+(minutes+2)*60]
        d[f"return_{minutes}m_pct"] = round((candidates[0][3]/first_price-1)*100, 3) if candidates else None
    d["observed_bars"] = len(rows)
    return d


def report(con: sqlite3.Connection, limit: int = 25) -> None:
    total = con.execute("SELECT count(*) FROM alerts").fetchone()[0]
    print(f"RESULTBOT / EXCELLENT ONLY / RESEARCH MODE  |  stored alerts: {total}")
    print("Return is from FIRST verified post-alert 1m CLOSE, not Telegram arrival price.")
    print("Candle timestamp is its OPEN; the reference CLOSE is observed approximately one minute later.")
    print("High/low exclude the reference candle because its extremes could precede that close.")
    print("1m bar containing the alert is excluded. A missing return means NOT VERIFIED.")
    print("Prices are delayed/retrospective research candles, NOT executable entry quotes.")
    for (alert_id,) in con.execute("SELECT id FROM alerts ORDER BY sent_utc DESC LIMIT ?", (limit,)):
        d = summarize(con, alert_id)
        t = datetime.fromtimestamp(d["alert_utc"], IST).strftime("%Y-%m-%d %H:%M")
        first = f'₹{d["first_price"]:.2f}' if d["first_price"] is not None else "MISSING"
        ret = [f"{m}m={'N/A' if d.get(f'return_{m}m_pct') is None else str(d[f'return_{m}m_pct'])+'%'}" for m in (5,15,30,60)]
        if d["first_bar_utc"] is not None:
            first_when=datetime.fromtimestamp(d["first_bar_utc"],IST).strftime("%d-%b %H:%M IST")
            low = "N/A" if d["observed_low_pct"] is None else f'{d["observed_low_pct"]:+.2f}%'
            high = "N/A" if d["observed_high_pct"] is None else f'{d["observed_high_pct"]:+.2f}%'
            range_text=f" | post-reference low={low} high={high}"
        else:
            first_when="NO POST-ALERT BAR"
            range_text=""
        print(f"{t} IST  #{d['symbol']:<14} first={first:<11} [{first_when}] {'  '.join(ret)}{range_text}")


def env_config() -> tuple[int,str]:
    """Only Telegram keys read; no Dhan credentials or trading settings used."""
    try:
        from dotenv import dotenv_values
    except ImportError as e:
        raise RuntimeError("Missing python-dotenv in venv") from e
    # Read Telegram keys only; do not inject broker secrets into this process.
    values = dotenv_values(Path.home()/"uditbot"/".env")
    api_id = os.environ.get("TG_API_ID") or values.get("TG_API_ID") or values.get("API_ID")
    api_hash = os.environ.get("TG_API_HASH") or values.get("TG_API_HASH") or values.get("API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("Missing TG_API_ID/TG_API_HASH in ~/uditbot/.env")
    return int(api_id), api_hash


def existing_telegram_login():
    """Read the already-authorized portable Telegram session, without echoing secrets."""
    from dotenv import dotenv_values
    env = dotenv_values(Path.home() / "uditbot" / ".env")
    value = os.environ.get("TG_SESSION_STRING") or env.get("TG_SESSION_STRING")
    return str(value).strip() if value else ""


def existing_sqlite_login():
    """Convert the original bot's authorized SQLite session to an in-memory
    Telethon StringSession. This function reads SQLite in read-only mode and
    never copies or prints its authentication key or changes .env.
    """
    import base64
    import ipaddress
    import struct
    from urllib.parse import quote

    path = Path.home() / "uditbot" / "data" / "tg_userbot.session"
    if not path.is_file():
        return ""
    try:
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            row = conn.execute(
                "SELECT dc_id, server_address, port, auth_key "
                "FROM sessions LIMIT 1"
            ).fetchone()
        if not row:
            raise ValueError("empty sessions table")
        dc, address, port, key = row
        key_bytes = bytes(key)
        if len(key_bytes) != 256:
            raise ValueError("invalid session authentication key length")
        packed_ip = ipaddress.ip_address(str(address)).packed
        fmt = ">B4sH256s" if len(packed_ip) == 4 else ">B16sH256s"
        encoded = struct.pack(fmt, int(dc), packed_ip, int(port), key_bytes)
        return "1" + base64.urlsafe_b64encode(encoded).decode("ascii")
    except (OSError, sqlite3.Error, ValueError, TypeError, struct.error) as exc:
        raise RuntimeError(
            "Existing ~/uditbot/data/tg_userbot.session could not be read; "
            "credentials were not changed"
        ) from exc


def require_trading_inactive():
    """Fail closed before sharing an existing Telegram authorization."""
    import subprocess
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "trading_bot.service"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError("Cannot verify trading bot state; refusing session reuse") from e
    status = result.stdout.strip()
    if status not in ("inactive", "failed"):
        raise RuntimeError(
            "Cannot reuse Telegram login while trading_bot.service is active or its state is uncertain: "
            + repr(status)
        )


async def telegram_client():
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as e:
        raise RuntimeError("Telethon missing from venv") from e
    api_id, api_hash = env_config()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Prefer an already configured portable session; fall back to the original
    # authenticated SQLite session, never requiring another Telegram login.
    # Read the SQLite file only if the portable session did not authenticate.
    # Only one authorization is connected at a time.
    client = None
    for source, load_token in (
        ("TG_SESSION_STRING", existing_telegram_login),
        ("~/uditbot/data/tg_userbot.session", existing_sqlite_login),
    ):
        token = load_token()
        if not token:
            continue
        require_trading_inactive()
        try:
            candidate = TelegramClient(StringSession(token), api_id, api_hash)
        except (ValueError, TypeError):
            LOG.warning("Stored %s format is invalid; checking next local session", source)
            continue
        await candidate.connect()
        if await candidate.is_user_authorized():
            client = candidate
            print(f"[TELEGRAM] Reusing authenticated {source}; no OTP required", flush=True)
            break
        await candidate.disconnect()
        LOG.warning("Stored %s is not authorized; checking next local session", source)

    if client is None:
        # An independent session may already have been authorized; do not prompt.
        candidate = TelegramClient(str(SESSION), api_id, api_hash)
        await candidate.connect()
        if await candidate.is_user_authorized():
            client = candidate
            print("[TELEGRAM] Using previously authorized independent session",flush=True)
        else:
            await candidate.disconnect()
            raise RuntimeError(
                "No authorized Telegram session was found in the original bot "
                "or the research collector. No OTP was requested. "
                "Check ~/uditbot/data/tg_userbot.session without sharing credentials."
            )
    entity = await client.get_entity(CHANNEL)
    if int(entity.id) != CHANNEL_ID:
        await client.disconnect()
        raise RuntimeError(f"Unexpected Earnings Pulse source identity: {entity.id} != {CHANNEL_ID}")
    return client, entity


async def shared_login_watchdog(client):
    """Disconnect a shared authorization if trading starts after this research listener."""
    while True:
        await asyncio.sleep(5)
        try:
            require_trading_inactive()
        except RuntimeError:
            LOG.error("Trading service started or could not be verified: disconnecting research session")
            await client.disconnect()
            return

async def backfill(con: sqlite3.Connection, limit: int):
    client, entity = await telegram_client()
    counts = {}
    try:
        async for m in client.iter_messages(entity, limit=limit):
            result = add_alert(con, m.id, m.date, m.raw_text or "", channel_id=entity.id)
            counts[result] = counts.get(result,0)+1
    finally:
        await client.disconnect()
    print("Backfill (no broker calls):",counts)


async def listen(con: sqlite3.Connection, *, interval: int = 75):
    from telethon import events
    client, entity = await telegram_client()
    try:
        log_group = await resolve_log_group(client)
    except Exception as exc:
        LOG.warning("Log Group notifications disabled: %s",exc)
        log_group = None
    # Test group is a completely separate synthetic input; never add it to
    # Earnings Pulse backfill, the alerts database, or the Yahoo research feed.
    test_entity, test_id = None, None
    if log_group is not None:
        try:
            test_entity, test_id = await resolve_test_group(client)
        except Exception as exc:
            LOG.warning("ResultBot test input disabled: %s", exc)
    # Backfill a small gap on startup to recover missed messages, deduped by Telegram ID.
    async def handler(event):
        m = event.message
        verdict = add_alert(con, m.id, m.date, m.raw_text or "", channel_id=entity.id)
        if verdict == "stored":
            symbol, _ = parse_alert(m.raw_text or "")
            print(f"[EXCELLENT STORED] #{symbol}  telegram_id={m.id}  {m.date.isoformat()}",flush=True)
            if log_group is not None:
                enqueue_live_log(con,m.id)
        elif verdict == "missing_or_multiple_stock_symbols":
            LOG.warning("Excellent message %s needs manual stock-symbol review", m.id)

    client.add_event_handler(handler, events.NewMessage(chats=entity))
    test_scan_task = None
    if test_entity is not None:
        def record_test_message(message, source: str) -> str:
            verdict = enqueue_test_log(
                con, test_id, message.id, message.date, message.raw_text or ""
            )
            if verdict == "test_queued":
                print(
                    f"[TEST EXCELLENT] {source}: queued for Log Group (no broker actions)",
                    flush=True,
                )
            elif verdict not in ("not_excellent", "duplicate"):
                LOG.info("Test Group message ignored (%s): %s", source, verdict)
            return verdict

        async def test_handler(event):
            record_test_message(event.message, "LIVE EVENT")
        client.add_event_handler(test_handler, events.NewMessage(chats=test_entity))

        async def test_history_poller():
            # Telethon may not emit an incoming NewMessage update for posts sent by
            # the same logged-in Telegram account. Poll *only the verified test
            # group* as a fallback; never touch Earnings Pulse research history.
            # The first scan can recover recent Excellent posts from the last 30 minutes.
            first = True
            while True:
                total = 0
                marked = 0
                accepted = 0
                try:
                    moment = datetime.now(timezone.utc)
                    async for m in client.iter_messages(test_entity, limit=40):
                        total += 1
                        if not recent_test_candidate(m.date, moment):
                            continue
                        text = m.raw_text or ""
                        symbol, reason = parse_test_alert(text)
                        if symbol is None:
                            continue
                        marked += 1
                        if record_test_message(m, "HISTORY POLL") == "test_queued":
                            accepted += 1
                    if first:
                        print(
                            f"[TEST SCAN] Test Group history readable: {total} recent entries checked, "
                            f"{marked} Excellent test posts matched, {accepted} newly queued. "
                            "No [RBTEST] prefix is required.",
                            flush=True,
                        )
                        first = False
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOG.warning("Test Group history poll failed: %s", type(exc).__name__)
                await asyncio.sleep(30)

        test_scan_task = asyncio.create_task(test_history_poller())
        print("[TEST] Send to test group: #YESBANK - 🏆🔥 Excellent Results",flush=True)
        print("[TEST] Also send: #YESBANK - 🌟 Great Results (must stay silent)",flush=True)
    for attempt in range(4):
        try:
            async for m in client.iter_messages(entity, limit=30):
                add_alert(con, m.id, m.date, m.raw_text or "", channel_id=entity.id)
            break
        except OSError as e:
            LOG.warning("Backfill retry %d: %s", attempt+1,e)
            await asyncio.sleep(3)
    print("[LISTEN] @earnings_pulse | ONLY Excellent Results | no order execution",flush=True)
    async def log_poller():
        while True:
            try:
                await send_queued_logs(con,client,log_group)
            except Exception as exc:
                LOG.warning("Excellent Log Group polling error: %s",type(exc).__name__)
            await asyncio.sleep(10)
    log_task = asyncio.create_task(log_poller())
    # Optional Dhan GET-only broker bridge shares THIS Telegram session/outbox;
    # no separate listener, no order placement, no symbol-only trade attribution.
    broker_task = None
    try:
        import dhan_bridge
        dhan_bridge.ensure_schema(con)
        async def dhan_broker_poller():
            while True:
                try:
                    changed = await dhan_bridge.poll_async(con)
                    if changed:
                        LOG.info("Dhan account-verified linked order updates queued: %d",changed)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Never log credential-bearing HTTP URLs or raw response bodies.
                    LOG.warning("Dhan linked-order monitor unavailable: %s",type(exc).__name__)
                await asyncio.sleep(45)
        broker_task = asyncio.create_task(dhan_broker_poller())
        LOG.info("ResultBot Dhan GET-only linked-order monitor enabled")
    except ImportError:
        LOG.info("Optional Dhan GET-only bridge not installed; Excellent collector unchanged")
    async def price_poller():
        while True:
            now = datetime.now(IST)
            # Market days and clock time, NOT a guarantee exchange is open (holiday possible).
            if now.weekday()<5 and (9*60+16)<=now.hour*60+now.minute<15*60+35:
                worked, candidates = await asyncio.to_thread(poll_new_db_connection)
                if candidates:
                    LOG.info("Verified Yahoo NSE candles: %d/%d symbols",worked,candidates)
            await asyncio.sleep(interval)
    poll = asyncio.create_task(price_poller())
    from telethon.sessions import StringSession
    guard = asyncio.create_task(shared_login_watchdog(client)) if isinstance(client.session, StringSession) else None
    try:
        await client.run_until_disconnected()
    finally:
        poll.cancel()
        log_task.cancel()
        if broker_task: broker_task.cancel()
        if test_scan_task: test_scan_task.cancel()
        if guard: guard.cancel()
        await client.disconnect()


def self_test() -> None:
    import tempfile
    examples = {
        "#ROLEXRINGS - 🏆🔥 Excellent Results": ("ROLEXRINGS","excellent"),
        "#SIGMAADV - 🌟 Great Results": (None,"not_excellent"),
        "#TEST - Good Results": (None,"not_excellent"),
        "#AAA #BBB - Excellent Results": (None,"missing_or_multiple_stock_symbols"),
        "Results Out #SGFIN": (None,"not_excellent"),
        "Excellent Results": (None,"missing_or_multiple_stock_symbols"),
    }
    for msg, expected in examples.items():
        assert parse_alert(msg) == expected,(msg,parse_alert(msg))
    with tempfile.TemporaryDirectory() as d:
        con=setup_db(Path(d)/"test.db")
        t=datetime(2026,11,6,8,0,tzinfo=timezone.utc)
        assert add_alert(con,100,t,"#ROLEXRINGS - Excellent Results") == "stored"
        assert add_alert(con,100,t,"#ROLEXRINGS - Excellent Results") == "duplicate"
        assert add_alert(con,101,t,"#SIGMAADV Great Results") == "not_excellent"
        assert add_alert(con,102,t,"#ROLEXRINGS Excellent Results",channel_id=999) == "wrong_channel"
        symbol="ROLEXRINGS"; ts=int(t.timestamp()); start=(ts//60+1)*60
        # Same-minute bar excluded. Exact first eligible 1m bar and post-alert outcomes used.
        bars=[]
        for i in range(64):
            price=100+i*0.1
            bars.append({"symbol":symbol,"ts_utc":start+i*60,"open":price,"high":price+0.2,
                         "low":price-0.2,"close":price,"volume":100,"fetched_utc":int(time.time())})
        store_bars(con,symbol,bars)
        before={"symbol":symbol,"ts_utc":ts,"open":50,"high":80,"low":40,"close":50,
                "volume":100,"fetched_utc":int(time.time())}
        store_bars(con,symbol,[before])
        s=summarize(con,1)
        assert s["first_price"]==100.0,s
        assert s["return_5m_pct"] == 0.5,s
        assert s["observed_high_pct"] < 7,s
        con.close()
    print("PASS: strict Excellent classification; source gate; dedup; first-post-alert exclusion; 1m returns")


def main():
    ap=argparse.ArgumentParser(description="ResultBot Excellent-only research. No Dhan or trading functions.")
    sub=ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")
    sub.add_parser("listen",help="read-only Telegram listener + weekday market-hour Yahoo research")
    sub.add_parser("log-status",help="local Excellent Log Group queue status; no messages sent")
    p=sub.add_parser("backfill",help="read-only Telegram history scan, Excellent only")
    p.add_argument("--limit",type=int,default=2000)
    sub.add_parser("prices",help="one-time Yahoo minute-bar retrieval for recently seen Excellent alerts")
    p=sub.add_parser("report",help="summarize first-post-alert observed prices")
    p.add_argument("--limit",type=int,default=25)
    args=ap.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd=="self-test":
        self_test();return
    con=setup_db()
    if args.cmd=="backfill":
        if not 1<=args.limit<=10000:ap.error("--limit range 1..10000")
        asyncio.run(backfill(con,args.limit))
    elif args.cmd=="listen":
        asyncio.run(listen(con))
    elif args.cmd=="prices":
        done, all_=track_once(con)
        print(f"Yahoo 1m requests: completed={done}; candidates={all_}")
    elif args.cmd=="report":
        report(con,args.limit)
    elif args.cmd=="log-status":
        try:
            configured = configured_log_group_id() is not None
        except ValueError as exc:
            print("Log Group configuration invalid:", exc)
            configured = False
        print("ResultBot Excellent notifications | destination configured:", "YES" if configured else "NO")
        for kind, state, count in con.execute(
            "SELECT kind,state,COUNT(*) FROM log_outbox GROUP BY kind,state ORDER BY kind,state"
        ):
            print("  ",kind,"/",state,":",count)
        try:
            test_configured = configured_test_group_id() is not None
        except ValueError:
            test_configured = False
        print("TEST GROUP configured:", "YES" if test_configured else "NO")
        print("Only new Excellent alerts generate cards; history and all other ratings stay silent.")
        print("Dhan orders are never placed here; only explicitly linked orders receive broker-verified updates.")
    con.close()


if __name__=="__main__":
    main()
