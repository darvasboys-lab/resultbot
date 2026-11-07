import asyncio
import argparse
import re
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.sessions import StringSession

ROOT = Path.home() / "uditbot"
STORE = Path.home() / "resultbot-data"
DB = STORE / "alerts.sqlite3"
IST = ZoneInfo("Asia/Kolkata")

PATTERN = re.compile(
    r"^\s*#([A-Z][A-Z0-9&_.-]{0,24})\s*[-–—]\s*"
    r"(?:🏆\s*🔥\s*)?Excellent Results\b",
    re.IGNORECASE
)

async def main(limit):
    env = dotenv_values(ROOT / ".env")
    api_id = int(env.get("TG_API_ID") or 0)
    api_hash = env.get("TG_API_HASH") or ""

    if not api_id or not api_hash:
        raise RuntimeError("Telegram API credentials missing")

    data_dir = Path(env.get("DATA_DIR") or "data")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir

    session_file = data_dir / "tg_userbot.session"

    if session_file.exists():
        session = str(session_file)
    elif env.get("TG_SESSION_STRING"):
        session = StringSession(env["TG_SESSION_STRING"])
    else:
        raise RuntimeError("Telegram session missing")

    db = sqlite3.connect(DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            telegram_time_utc TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            PRIMARY KEY(channel_id, message_id)
        )
    """)
    db.commit()

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()

    scanned = 0
    matched = 0
    inserted = 0

    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram authorization missing")

        channel = await client.get_entity("earnings_pulse")
        print("RESULTBOT - HISTORICAL IMPORT", flush=True)
        print("Channel:", channel.title, flush=True)

        async for msg in client.iter_messages(
            channel, limit=limit
        ):
            scanned += 1
            text = msg.raw_text or ""
            match = PATTERN.search(text)

            if match:
                matched += 1
                symbol = match.group(1).upper()
                cursor = db.execute(
                    """INSERT OR IGNORE INTO alerts
                    (channel_id, message_id, symbol,
                     telegram_time_utc, raw_text)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        channel.id,
                        msg.id,
                        symbol,
                        msg.date.isoformat(),
                        text,
                    ),
                )
                inserted += cursor.rowcount

            if scanned % 500 == 0:
                db.commit()
                print("Scanned:", scanned, flush=True)

        db.commit()

        total = db.execute(
            "SELECT COUNT(*) FROM alerts"
        ).fetchone()[0]

        print("\nRESULTBOT IMPORT COMPLETE")
        print("Messages scanned:", scanned)
        print("Excellent Results matched:", matched)
        print("New alerts saved:", inserted)
        print("Total alerts stored:", total)
        print("\nLATEST SAVED ALERTS")

        rows = db.execute("""
            SELECT symbol, telegram_time_utc
            FROM alerts
            ORDER BY telegram_time_utc DESC
            LIMIT 8
        """).fetchall()

        for symbol, timestamp in rows:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp)
            print(symbol, dt.astimezone(IST).strftime(
                "%Y-%m-%d %H:%M IST"
            ))

        print("Database:", DB)

    finally:
        await client.disconnect()
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("Limit must be 0 or greater")

    asyncio.run(main(args.limit or None))
