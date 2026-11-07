#!/usr/bin/env python3
"""Fresh-install credential wizard. No broker order submission."""
import asyncio
import getpass
import json
import os
from pathlib import Path
import re
import sys


def private_write(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing symlinked configuration")
    temp = path.with_name(path.name + ".install-tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def env_text(values):
    return "".join(f"{key}={json.dumps(str(value), ensure_ascii=False)}\n"
                   for key, value in values.items())


def ask(prompt, pattern):
    while True:
        value = input(prompt).strip()
        if re.fullmatch(pattern, value):
            return value
        print("Please enter a valid value.")


async def configure():
    from dotenv import dotenv_values
    from telethon import TelegramClient
    home = Path.home()
    repo = home / "uditbot"
    data = home / "resultbot-data"
    sys.path.insert(0, str(data))
    import dhan_readonly as dh
    import equity_amo_live as amo
    import excellent_watch as watch

    env_path = repo / ".env"
    if env_path.exists():
        values = dict(dotenv_values(env_path))
        print("Resuming with this installation's saved private configuration.")
    else:
        print("RESULTBOT — enter credentials on this VM only.")
        print("Use your new bot's Dhan account. PIN/TOTP renewal generates broker tokens.")
        api_id = ask("Telegram API ID: ", r"[1-9][0-9]*")
        api_hash = getpass.getpass("Telegram API hash (hidden): ").strip()
        if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
            raise ValueError("Invalid Telegram API hash")
        phone = ask("Telegram phone (+country code): ", r"\+[0-9]{7,15}")
        log = ask("Telegram log group ID (negative number): ", r"-[1-9][0-9]*")
        test = ask("Telegram test group ID (negative number): ", r"-[1-9][0-9]*")
        earnings = {-watch.CHANNEL_ID, -int(f"100{watch.CHANNEL_ID}")}
        if log == test or int(log) in earnings or int(test) in earnings:
            raise ValueError("Log, test and earnings groups must be distinct")
        account = ask("Dhan client ID: ", r"[0-9]{8,15}")
        token = getpass.getpass("Dhan access token (blank to generate using PIN/TOTP): ").strip()
        print("Optional with an access token: enter PIN + TOTP setup key for daily renewal.")
        pin = getpass.getpass("Dhan 6-digit PIN (hidden; blank to skip renewal): ").strip()
        seed = getpass.getpass("Dhan TOTP setup key (hidden): ").strip() if pin else ""
        if pin:
            dh._valid_autorenew(account, pin, seed)
        if not token:
            if not pin:
                raise ValueError("Supply an access token or PIN and TOTP setup key")
            token = dh.token_from_pin_totp(account, pin, dh.current_totp_from_secret(seed))
        dh.verified_profile(account, token)
        dh.store_credentials(account, token)
        if pin:
            dh.store_autorenew(account, pin, seed)
        values = {
            "TG_API_ID": api_id, "TG_API_HASH": api_hash, "TG_PHONE": phone,
            "LOG_GROUP_ID": log, "TEST_GROUP_ID": test,
            "RESULTBOT_LOG_GROUP_ID": log, "RESULTBOT_TEST_GROUP_ID": test,
            "DHAN_CLIENT_ID": account, "TRADING_MODE": "TEST", "DATA_DIR": "data",
            "MONITORED_CHANNEL_IDS": str(-watch.CHANNEL_ID),
        }
        private_write(env_path, env_text(values))

    # Verify saved Dhan account on resume; do not regenerate tokens needlessly.
    account, token = dh.load_credentials()
    dh.verified_profile(account, token)
    client = TelegramClient(str(repo / "data/tg_userbot"), int(values["TG_API_ID"]), values["TG_API_HASH"])
    try:
        await client.start(phone=values["TG_PHONE"], password=lambda: getpass.getpass("Telegram 2-step password: "))
        # Populate Telegram entity cache so numeric group IDs resolve reliably.
        await client.get_dialogs()
        earnings = await client.get_entity(watch.CHANNEL)
        if int(earnings.id) != watch.CHANNEL_ID:
            raise ValueError("Unexpected earnings channel identity")
        await client.get_entity(int(values["TEST_GROUP_ID"]))
        await client.get_entity(int(values["LOG_GROUP_ID"]))
    finally:
        await client.disconnect()
    amo.disarm()
    amo.update_settings(source="TEST", budget=500)
    print("Credentials and Telegram groups verified. New history; AMO OFF; TEST source; INR 500 default.")
    print("Set your own amount and switch using bot setup after installation.")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        asyncio.run(configure())
    except KeyboardInterrupt:
        print("Setup paused. Run the installer again to resume.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Authentication exceptions can embed request URLs or secret-bearing input.
        print(f"Setup incomplete ({type(exc).__name__}). Check credentials/group access and rerun. No services started.", file=sys.stderr)
        sys.exit(1)
