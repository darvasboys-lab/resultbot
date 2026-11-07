import asyncio
import re
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import dotenv_values
from telethon import TelegramClient, events
from telethon.sessions import StringSession

ROOT = Path.home() / "uditbot"
STORE = Path.home() / "resultbot-data"
DB_PATH = STORE / "alerts.sqlite3"
IST = ZoneInfo("Asia/Kolkata")

PATTERN = re.compile(
    r"^\s*#([A-Z][A-Z0-9&_.-]{0,24})\s*[-–—]\s*"
    r"(?:🏆\s*🔥\s*)?Excellent Results\b",
    re.IGNORECASE
)

def save_alert(db, channel_id, msg):
    raw = msg.raw_text or ""
    match = PATTERN.search(raw)
    if not match:
        return False

    cur = db.execute("""
        INSERT OR IGNORE INTO alerts
        (channel_id, message_id, symbol, telegram_time_utc, raw_text)
        VALUES (?, ?, ?, ?, ?)
    """, (
        channel_id, msg.id, match.group(1).upper(),
        msg.date.isoformat(), raw
    ))

    if cur.rowcount:
        print(
            "SAVED", match.group(1).upper(),
            msg.date.astimezone(IST).strftime("%Y-%m-%d %H:%M IST"),
            "message", msg.id,
            flush=True
        )
        return True
    return False

async def main():
    env = dotenv_values(ROOT / ".env")
    api_id = int(env.get("TG_API_ID") or 0)
    api_hash = env.get("TG_API_HASH") or ""

    data_dir = Path(env.get("DATA_DIR") or "data")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir

    session_file = data_dir / "tg_userbot.session"

    if not api_id or not api_hash:
        raise RuntimeError("Missing Telegram API settings")

    if session_file.is_file():
        session = str(session_file)
    elif env.get("TG_SESSION_STRING"):
        session = StringSession(env["TG_SESSION_STRING"])
    else:
        raise RuntimeError("No Telegram session found")

    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")

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

    db.execute("""
        CREATE TABLE IF NOT EXISTS listener_state (
            channel_id INTEGER PRIMARY KEY,
            last_message_id INTEGER NOT NULL
        )
    """)
    db.commit()

    client = TelegramClient(
        session, api_id, api_hash, auto_reconnect=True
    )
    await client.connect()

    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram login not authorized")

        channel = await client.get_entity("earnings_pulse")

        print(
            f"ResultBot connected: {channel.title} ({channel.id})",
            flush=True
        )

        async def catch_up():
            row = db.execute(
                "SELECT last_message_id FROM listener_state "
                "WHERE channel_id=?", (channel.id,)
            ).fetchone()

            if row:
                last_id = row[0]
            else:
                last_id = db.execute(
                    "SELECT COALESCE(MAX(message_id), 0) "
                    "FROM alerts WHERE channel_id=?",
                    (channel.id,)
                ).fetchone()[0]

            scanned = new = 0

            async for msg in client.iter_messages(
                channel, min_id=last_id, reverse=True
            ):
                with db:
                    new += save_alert(db, channel.id, msg)
                    db.execute("""
                        INSERT INTO listener_state
                        (channel_id, last_message_id)
                        VALUES (?, ?)
                        ON CONFLICT(channel_id) DO UPDATE SET
                        last_message_id = MAX(
                            listener_state.last_message_id,
                            excluded.last_message_id
                        )
                    """, (channel.id, msg.id))
                scanned += 1

            print(
                f"Catch-up: {scanned} scanned, "
                f"{new} new alerts",
                flush=True
            )

        @client.on(events.NewMessage(chats=channel))
        async def on_message(event):
            with db:
                save_alert(db, channel.id, event.message)

        async def periodic_catchup():
            while True:
                await asyncio.sleep(300)
                try:
                    await catch_up()
                except Exception as exc:
                    print(
                        f"Catch-up retry: {type(exc).__name__}: {exc}",
                        flush=True
                    )

        await catch_up()
        task = asyncio.create_task(periodic_catchup())

        try:
            print("RESULTBOT LISTENING 24/7", flush=True)
            await client.run_until_disconnected()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        await client.disconnect()
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
