import asyncio, csv, json, re, sqlite3, time, sys
from collections import defaultdict
from datetime import datetime, timedelta, time as Clock
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo
from urllib.parse import quote
import requests
from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.sessions import StringSession

ROOT = Path.home() / "uditbot"
sys.path.insert(0, str(ROOT))
from src.tg_login import _encode_sqlite_session_to_string

STORE = Path.home() / "resultbot-data"
CACHE = STORE / "yahoo_cache"
STORE.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)
IST = ZoneInfo("Asia/Kolkata")

REGEX = re.compile(
    r"^\s*#([A-Z][A-Z0-9&_.-]{0,24})\s*[-–—]\s*"
    r"[^A-Za-z0-9\n]{0,24}(Excellent|Great|Good)\s+Results?\b",
    re.I
)

DB = sqlite3.connect(STORE / "closing_study.sqlite3")
DB.execute("""CREATE TABLE IF NOT EXISTS alerts (
    channel INTEGER, msg INTEGER, rating TEXT,
    symbol TEXT, day TEXT, stamp TEXT,
    intraday INTEGER, PRIMARY KEY(channel,msg)
)""")
DB.execute(
    "CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY)"
)
DB.commit()

async def scan():
    env = dotenv_values(ROOT / ".env")
    api_id = int(env.get("TG_API_ID") or 0)
    api_hash = env.get("TG_API_HASH") or ""
    if not api_id or not api_hash:
        raise RuntimeError("Telegram API credentials missing")

    session = env.get("TG_SESSION_STRING")
    if not session:
        data = Path(env.get("DATA_DIR") or "data")
        if not data.is_absolute():
            data = ROOT / data
        session = _encode_sqlite_session_to_string(
            str(data / "tg_userbot.session")
        )

    client = TelegramClient(
        StringSession(session), api_id, api_hash
    )
    await client.connect()
    scanned = positive = intraday = 0

    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session not authorized")

        channel = await client.get_entity("earnings_pulse")

        async for msg in client.iter_messages(
            channel, limit=2000, wait_time=3
        ):
            scanned += 1
            match = REGEX.match(
                (msg.raw_text or "").split("\n", 1)[0]
            )
            if match:
                positive += 1
                dt = msg.date.astimezone(IST)
                inside = int(
                    dt.weekday() < 5 and
                    Clock(9,15) <=
                    dt.time().replace(tzinfo=None) <
                    Clock(15,30)
                )
                intraday += inside
                DB.execute(
                    "INSERT OR IGNORE INTO alerts VALUES "
                    "(?,?,?,?,?,?,?)",
                    (
                        channel.id, msg.id,
                        match.group(2).upper(),
                        match.group(1).upper(),
                        dt.date().isoformat(),
                        dt.isoformat(), inside
                    )
                )

            if scanned % 500 == 0:
                DB.commit()
                print(
                    "Telegram messages scanned:",
                    scanned, flush=True
                )

        DB.execute(
            "INSERT OR IGNORE INTO flags "
            "VALUES ('scan2000')"
        )
        DB.commit()
        print(
            "Scan complete:", scanned,
            "messages,", positive,
            "positive-rated,", intraday,
            "intraday", flush=True
        )
    finally:
        await client.disconnect()

def yahoo(symbol, days):
    first = (
        datetime.fromisoformat(min(days))
        - timedelta(days=21)
    )
    last = (
        datetime.fromisoformat(max(days))
        + timedelta(days=2)
    )

    url = (
        "https://query2.finance.yahoo.com/v8/finance/chart/"
        + quote(symbol + ".NS", safe="")
    )
    res = requests.get(
        url,
        params={
            "period1":int(first.replace(
                tzinfo=IST).timestamp()),
            "period2":int(last.replace(
                tzinfo=IST).timestamp()),
            "interval":"1d"
        },
        headers={
            "User-Agent":"Mozilla/5.0",
            "Accept":"application/json"
        },
        timeout=25
    )
    if res.status_code in (401,403,429):
        raise RuntimeError(
            "RATE_LIMIT_" + str(res.status_code)
        )
    res.raise_for_status()

    data = (
        res.json().get("chart", {}).get("result")
        or [None]
    )[0]
    if not data:
        raise ValueError("No Yahoo price history")

    meta = data.get("meta", {})
    if (
        meta.get("symbol","").upper() !=
        (symbol+".NS").upper()
        or meta.get("currency") != "INR"
    ):
        raise ValueError("NSE symbol or INR mismatch")

    if meta.get("instrumentType") not in ("EQUITY", None):
        raise ValueError("Not NSE equity")

    prices = {
        datetime.fromtimestamp(
            ts, IST
        ).date().isoformat():float(close)
        for ts, close in zip(
            data.get("timestamp") or [],
            data["indicators"]["quote"][0]["close"]
        )
        if close is not None
        and 0 < float(close) < float("inf")
    }

    return {
        "start":first.date().isoformat(),
        "end":last.date().isoformat(),
        "prices":prices
    }

def report():
    rows = DB.execute(
        "SELECT channel,msg,rating,symbol,"
        "day,stamp,intraday FROM alerts "
        "ORDER BY stamp,msg"
    ).fetchall()

    by_symbol = defaultdict(list)
    for ch,msg,rating,symbol,day,stamp,inside in rows:
        if inside:
            by_symbol[symbol].append(day)

    print(
        "Unique Yahoo NSE symbol lookups:",
        len(by_symbol), flush=True
    )

    prices_by_symbol = {}
    requests_count = success = 0

    for symbol, days in sorted(by_symbol.items()):
        file = CACHE / (symbol.replace("/", "_") + ".json")
        wanted_start = (
            datetime.fromisoformat(min(days))
            - timedelta(days=21)
        ).date().isoformat()
        wanted_end = (
            datetime.fromisoformat(max(days))
            + timedelta(days=2)
        ).date().isoformat()

        cached = None
        if file.exists():
            try:
                cached = json.loads(file.read_text())
            except (ValueError, OSError):
                pass

        if (
            cached
            and cached["start"] <= wanted_start
            and cached["end"] >= wanted_end
        ):
            prices_by_symbol[symbol] = cached["prices"]
            continue

        requests_count += 1
        try:
            item = yahoo(symbol, days)
            file.with_suffix(".tmp").write_text(
                json.dumps(item)
            )
            file.with_suffix(".tmp").replace(file)
            prices_by_symbol[symbol] = item["prices"]
            success += 1
        except Exception as exc:
            print(
                "Yahoo unavailable:",
                symbol, str(exc), flush=True
            )
            if "RATE_LIMIT_" in str(exc):
                print(
                    "Rate limit detected. Stopping "
                    "Yahoo requests, preserving cache.",
                    flush=True
                )
                break

        if requests_count % 15 == 0:
            print(
                "Yahoo requests:", requests_count,
                "successful:", success, flush=True
            )
        time.sleep(3)

    print(
        "Yahoo requests this run:",
        requests_count, "successful:", success
    )

    seen = set()
    stats = defaultdict(list)
    output = []

    for ch,msg,rating,symbol,day,stamp,inside in rows:
        status, prior, close, pct = (
            "OUTSIDE_HOURS","","",""
        )
        if inside:
            status = "MISSING_YAHOO_PRICE"
            bars = prices_by_symbol.get(symbol, {})
            dates = sorted(bars)

            if day in bars:
                pos = dates.index(day)
                if pos > 0:
                    close = bars[day]
                    prior = bars[dates[pos-1]]
                    pct = round(
                        (close/prior-1)*100, 6
                    )
                    status = "MATCHED"
                    key = (rating,symbol,day)

                    if key in seen:
                        status = "DUPLICATE_DAY"
                    else:
                        seen.add(key)
                        stats[rating].append(
                            (symbol,pct)
                        )
                else:
                    status = "PRIOR_CLOSE_MISSING"

        output.append((
            rating,symbol,day,stamp,msg,
            status,prior,close,pct
        ))

    csv_file = STORE / "intraday_closing_report.csv"
    with csv_file.open(
        "w",newline="",encoding="utf-8"
    ) as f:
        w = csv.writer(f)
        w.writerow([
            "rating","symbol","day_ist",
            "alert_ist","message_id","status",
            "previous_close","day_close",
            "daily_change_pct"
        ])
        w.writerows(output)

    print("\nRESULTBOT SAME-DAY CLOSING STUDY")
    print(
        "09:15-15:30 IST only; one stock "
        "per rating per day"
    )
    print(
        f"{'RATING':10} {'ALL':>5} "
        f"{'INTRADAY':>9} {'MATCHED':>8} "
        f"{'UP':>5} {'DOWN':>5} "
        f"{'MEAN%':>9} {'MEDIAN%':>9} {'UP%':>6}"
    )

    for rating in ("EXCELLENT","GREAT","GOOD"):
        vals = [v for _,v in stats[rating]]
        count = sum(
            x[0] == rating for x in output
        )
        within = sum(
            x[0] == rating
            and x[5] != "OUTSIDE_HOURS"
            for x in output
        )
        ups = sum(v>0 for v in vals)
        downs = sum(v<0 for v in vals)

        m = f"{mean(vals):+.2f}" if vals else "N/A"
        med = f"{median(vals):+.2f}" if vals else "N/A"
        up_pct = (
            f"{100*ups/len(vals):.1f}"
            if vals else "N/A"
        )

        print(
            f"{rating:10} {count:5} {within:9} "
            f"{len(vals):8} {ups:5} {downs:5} "
            f"{m:>9} {med:>9} {up_pct:>6}"
        )

    missing = sum(
        x[5] == "MISSING_YAHOO_PRICE"
        for x in output
    )
    print(
        "Intraday alerts without verified "
        "Yahoo prices:", missing
    )
    print("CSV report:", csv_file)
    print(
        "This is FULL-DAY change, NOT gains "
        "after Telegram alert time."
    )

try:
    if DB.execute(
        "SELECT 1 FROM flags "
        "WHERE name='scan2000'"
    ).fetchone():
        print(
            "Using cached Telegram scan; "
            "no new Telegram requests.",
            flush=True
        )
    else:
        asyncio.run(scan())
    report()
finally:
    DB.close()
