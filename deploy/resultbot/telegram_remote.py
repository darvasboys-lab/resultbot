#!/usr/bin/env python3
"""ResultBot Telegram control panel: allowlisted commands, never a remote shell.

Dedicated BotFather bot, private-owner pairing, then bind to ONE group.
Does not import trading engine or open any inbound network port.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path.home() / "resultbot-data"
APP = ROOT / "telegram_remote.py"
CONF = ROOT / ".telegram_remote.json"
PY = Path.home() / "uditbot/venv/bin/python"
ENTRY = ROOT / "equity_amo_live.py"
READ = ROOT / "dhan_readonly.py"
OCO = ROOT / "oco_guard.py"
SERVICE = Path.home() / ".config/systemd/user/resultbot-telegram-control.service"
PENDING: dict[str,tuple[int,int,float,tuple[str,...],str]] = {}
MAX_MESSAGE_AGE = 120


class RemoteError(Exception):
    pass


def safe_save(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RemoteError("Refusing symlinked remote config")
    fd, name = tempfile.mkstemp(prefix=".telegram-remote-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def config() -> dict:
    if not CONF.is_file() or CONF.is_symlink():
        raise RemoteError("Telegram remote not configured")
    if CONF.stat().st_mode & 0o077:
        raise RemoteError("Private Telegram config must be chmod 600")
    d = json.loads(CONF.read_text())
    return d


def api(token: str, method: str, data: dict | None = None, timeout: int = 15):
    # Never log token, raw HTTP exceptions, or full URLs.
    url = "https://api.telegram.org/bot" + token + "/" + method
    body = json.dumps(data or {}).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"},
                  method="POST")
    try:
        with urlopen(req, timeout=timeout) as r:
            result = json.load(r)
    except HTTPError as exc:
        raise RemoteError("Telegram API HTTP " + str(exc.code)) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RemoteError("Telegram API unavailable") from None
    if not isinstance(result, dict) or not result.get("ok"):
        raise RemoteError("Telegram rejected requested API method")
    return result.get("result")


def send(cfg: dict, chat_id: int, content: str, buttons: list | None = None):
    payload = {"chat_id": chat_id, "text": str(content)[:3900],
               "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = (
            buttons if isinstance(buttons, dict) else {"inline_keyboard": buttons}
        )
    api(cfg["bot_token"], "sendMessage", payload)


def answer(cfg: dict, query_id: str, message: str = ""):
    try:
        api(cfg["bot_token"], "answerCallbackQuery",
            {"callback_query_id": query_id, "text": message[:180]}, timeout=8)
    except RemoteError:
        pass


def row(*pairs):
    return [{"text": title, "callback_data": key} for title,key in pairs]


BUTTON_COMMANDS = {
    "📊 STATUS": "status",
    "🧾 ORDERS": "orders",
    "💰 FUNDS": "funds",
    "🛡️ OCO": "oco",
    "⚙️ SETTINGS": "setup",
    "📋 LOGS": "logs",
    "🟢 LIVE ON": "live on",
    "🔴 LIVE OFF": "live off",
    "🔴 AMO ON": "amo on",
    "🟢 AMO OFF": "amo off",
    "⏹️ STOP BOTH": "stop",
}


def menu(cfg: dict):
    """Large persistent Telegram reply keyboard with concise visible labels.

    Telegram clients that support KeyboardButton.style display colored keys;
    older clients simply show their native key styling.
    """
    def key(label: str, style: str = "primary"):
        return {"text": label, "style": style}
    return {
        "keyboard": [
            [key("📊 STATUS"), key("🧾 ORDERS")],
            [key("💰 FUNDS"), key("🛡️ OCO")],
            [key("⚙️ SETTINGS"), key("📋 LOGS")],
            [key("🟢 LIVE ON", "success"), key("🔴 LIVE OFF", "danger")],
            [key("🔴 AMO ON", "danger"), key("🟢 AMO OFF", "success")],
            [key("⏹️ STOP BOTH", "danger")],
        ],
        "resize_keyboard": False,
        "one_time_keyboard": False,
        "is_persistent": True,
        "input_field_placeholder": "ResultBot • Tap a button",
    }


HELP = (
    "🤖 RESULTBOT / REMOTE CONTROL\n"
    "Read-only: /rb status · /rb orders · /rb funds · /rb oco · /rb logs · /rb setup\n"
    "Trading controls (ask to confirm):\n"
    "/rb budget 1000\n"
    "/rb source both  (or test / earnings)\n"
    "/rb live on    /rb live off\n"
    "/rb amo on     /rb amo off\n"
    "/rb stop  (disable new LIVE and AMO entries)\n\n"
    "No arbitrary terminal commands. Only the paired owner in this group can use controls."
)


def command(args: list[str], timeout: int = 15) -> tuple[bool,str]:
    try:
        proc = subprocess.run([str(PY), *args], capture_output=True,
                              text=True, timeout=timeout, check=False,
                              cwd=str(Path.home() / "uditbot"))
        # Only purpose-built ResultBot commands may be invoked.
        output = proc.stdout.strip() if proc.returncode == 0 else proc.stderr.strip()
        return proc.returncode == 0, output[:50000] or "No output"
    except (OSError, subprocess.TimeoutExpired):
        return False, "Local ResultBot command unavailable or timed out"


def format_status_reply(settings: dict | None, services: dict, protection: dict | None) -> str:
    """Short mobile-first status message; all displayed values are current state.

    OCO counts are last saved guard verification, not a fresh broker order query.
    An unavailable field is marked UNKNOWN rather than silently treated as OFF.
    """
    lines = ["RESULTBOT", ""]

    if isinstance(settings, dict) and isinstance(settings.get("live_enabled"), bool):
        live_on = settings["live_enabled"]
        lines.append(("🟢" if live_on else "🔴") + " LIVE: " + ("ON" if live_on else "OFF"))
    else:
        lines.append("🟡 LIVE: UNKNOWN")

    if isinstance(settings, dict) and isinstance(settings.get("amo_enabled"), bool):
        amo_on = settings["amo_enabled"]
        lines.append(("🔴" if amo_on else "🟢") + " AMO: " + ("ON" if amo_on else "OFF"))
    else:
        lines.append("🟡 AMO: UNKNOWN")

    source = settings.get("source") if isinstance(settings, dict) else None
    if source in ("BOTH", "TEST", "EARNINGS_PULSE"):
        lines.append("🟢 SOURCES: " + source)
    else:
        lines.append("🟡 SOURCES: UNKNOWN")

    budget = settings.get("per_order_budget_inr") if isinstance(settings, dict) else None
    if type(budget) is int and budget > 0:
        lines.append("🟢 PER ORDER: ₹" + str(budget))
    else:
        lines.append("🟡 PER ORDER: UNKNOWN")

    for label in ("Telegram listener", "OCO Guard", "Token renewal "):
        state = services.get(label)
        if state is True:
            lines.append("🟢 " + label + ": RUNNING")
        elif state is False:
            lines.append("🔴 " + label + ": NOT RUNNING")
        else:
            lines.append("🟡 " + label + ": UNKNOWN")

    if isinstance(protection, dict):
        records = protection.get("protection")
        errors = protection.get("unprotected_errors")
        if isinstance(records, list) and isinstance(errors, list) and all(
            isinstance(record, dict) and isinstance(record.get("state"), str)
            for record in records
        ):
            verified_active = sum(record["state"] == "ACTIVE" for record in records)
            unresolved = sum(record["state"] not in ("ACTIVE", "CLOSED")
                             for record in records)
            warnings = unresolved + len(errors)
            lines.append(("🟢" if warnings == 0 else "🟡") +
                         f" OCO: {verified_active} verified active, {warnings} warnings")
        else:
            lines.append("🟡 OCO: UNKNOWN")
    else:
        lines.append("🟡 OCO: UNKNOWN")

    return "\n".join(lines)


def status_text():
    """Read only ResultBot settings, service health, and last OCO guard state."""
    ok, raw = command([str(ENTRY), "settings", "--show"], 7)
    settings = None
    if ok:
        try:
            settings = json.loads(raw)
        except (ValueError, TypeError):
            pass

    services = {}
    for label, service in (
        ("Telegram listener", "resultbot-excellent-watch.service"),
        ("OCO Guard", "resultbot-oco-guard.service"),
        ("Token renewal ", "resultbot-dhan-token-refresh.timer"),
    ):
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", service],
                capture_output=True, text=True, timeout=3
            )
            services[label] = proc.returncode == 0 and proc.stdout.strip() == "active"
        except (OSError, subprocess.TimeoutExpired):
            services[label] = None

    protection = None
    if OCO.is_file():
        ok, raw = command([str(OCO), "status"], 7)
        if ok:
            try:
                protection = json.loads(raw)
            except (ValueError, TypeError):
                pass

    return format_status_reply(settings, services, protection)

def orders_text():
    ok, raw = command([str(ENTRY), "status"], 7)
    if not ok: return "🟡 Order state unavailable"
    try:
        d = json.loads(raw)
        rows = d.get("orders", [])[:8]
        lines = ["🧾 LATEST RESULTBOT BUY INTENTS"]
        for r in rows:
            lines.append(f"#{r.get('id')} {r.get('symbol')}  {r.get('state')}"
                         f"  {r.get('broker_status') or '-'}"
                         f"  {r.get('entry_route') or '-'}")
        return "\n".join(lines) if rows else "No ResultBot BUY intents recorded."
    except (ValueError,TypeError,AttributeError):
        return "🟡 Order record unreadable"


def oco_text():
    ok, raw = command([str(OCO), "status"], 8)
    if not ok: return "🟡 OCO Guard status unavailable"
    try:
        d = json.loads(raw)
        lines=["🛡️ OCO PROTECTION (saved guard state)"]
        for r in d.get("protection", [])[-10:]:
            lines.append(f"Intent {r.get('intent_id')}  {r.get('state')}  qty={r.get('quantity')}")
        problems = d.get("unprotected_errors", [])
        lines.append(f"Unprotected errors: {len(problems)}")
        lines.append("This is not a fresh Dhan order-book verification.")
        return "\n".join(lines)
    except (ValueError,TypeError,AttributeError):
        return "🟡 OCO records unreadable"


def settings_text():
    ok, raw = command([str(ENTRY), "settings", "--show"], 7)
    if not ok: return "🟡 Settings not available"
    try:
        d = json.loads(raw)
        return (f"⚙️ RESULTBOT SETTINGS\n"
                f"Sources: {d.get('source')}\n"
                f"Per order: ₹{d.get('per_order_budget_inr')}\n"
                "Use /rb budget 1000 or /rb source both to propose a change.")
    except (ValueError,TypeError,AttributeError):
        return "🟡 Settings unreadable"


def funds_text():
    ok, raw = command([str(READ), "funds"], 15)
    if not ok:
        return "🟡 Dhan funds unavailable. Check the VM credentials or Dhan connection."
    # Mask broker account, tokens and any unexpected credential-like strings.
    cleaned = []
    for line in raw.splitlines():
        if re.search(r"(?i)(token|secret|totp|pin|authorization|client.id|dhanclientid)", line):
            continue
        cleaned.append(line[:200])
    return "💰 DHAN FUNDS (read-only)\n" + "\n".join(cleaned[:14])[:1200]


def logs_text():
    try:
        p = subprocess.run(
            ["journalctl","--user","-u","resultbot-excellent-watch.service",
             "--since","30 minutes ago","--no-pager","-n","35","-o","cat"],
            capture_output=True,text=True,timeout=5)
        if p.returncode != 0: return "🟡 Logs unavailable"
        lines=[]
        for item in p.stdout.splitlines():
            if "ERROR" not in item and "WARN" not in item and "REAL AMO" not in item:
                continue
            if re.search(r"(?i)(token|secret|password|credential|auth)", item):
                continue
            lines.append(item[-180:])
        return "📋 RECENT ORDER / WARNING LOGS\n" + ("\n".join(lines[-8:]) or "No recent warnings.")
    except (OSError,subprocess.TimeoutExpired):
        return "🟡 Logs unavailable"


READ_ACTIONS = {
    "status": status_text,
    "orders": orders_text,
    "oco": oco_text,
    "setup": settings_text,
    "funds": funds_text,
    "logs": logs_text,
}


def propose(text: str) -> tuple[tuple[str,...],str] | None:
    fields=text.strip().lower().split()
    if fields in (["stop"], ["stop","all"]):
        return ("stop-all",), "Disable NEW LIVE and AMO entries. Existing broker orders remain unchanged."
    if len(fields)==2 and fields[0]=="live" and fields[1] in ("on","off"):
        value=fields[1]
        return ("live-"+value,), (
            "Turn LIVE intraday entries "+value.upper()+
            ". ON may result in real Dhan BUY orders for new eligible alerts."
        )
    if len(fields)==2 and fields[0]=="amo" and fields[1] in ("on","off"):
        value=fields[1]
        return (("arm",) if value=="on" else ("amo-off",)), (
            "Turn after-market BUY AMO "+value.upper()+
            ". ON may result in real Dhan BUY orders for new eligible alerts."
        )
    if len(fields)==2 and fields[0]=="source":
        v={"both":"BOTH","test":"TEST","earnings":"EARNINGS_PULSE"}.get(fields[1])
        if v: return ("settings","--source",v), ("Set eligible sources to "+v+". Future signals can trade.")
    if len(fields)==2 and fields[0]=="budget":
        if re.fullmatch(r"[1-9][0-9]{0,17}", fields[1]) and int(fields[1]) <= (2**63-1):
            return ("settings","--budget",str(int(fields[1]))), (
                "Set FUTURE per-order budget to ₹"+fields[1]+". This can change real order sizes."
            )
    return None


def new_confirmation(cfg:dict, chat_id:int, uid:int, action:tuple[str,...], label:str):
    nonce=secrets.token_hex(7)
    PENDING.clear()  # one live confirmation at a time
    PENDING[nonce]=(chat_id,uid,time.monotonic()+60,action,label)
    send(cfg, chat_id,
         "⚠️ CONFIRM RESULTBOT CHANGE\n"+label+
         "\n\nThis changes saved trading controls. Confirm within 60 seconds.",
         [row(("✅ CONFIRM","c:"+nonce),("❌ CANCEL","x:"+nonce))])


def execute_confirmed(action:tuple[str,...]):
    cmd=action[0]
    if cmd not in ("settings","arm","amo-off","stop-all","live-on","live-off"):
        return "Request blocked."
    ok, raw = command([str(ENTRY), *action], 22)
    if not ok:
        return "🔴 CHANGE NOT APPLIED\n"+raw[-400:]
    # Re-read canonical state; no order IDs or tokens included.
    current = status_text().splitlines()
    return "🟢 COMMAND ACCEPTED\n"+settings_text()+("\n"+current[1] if len(current)>1 else "")


def is_authorized(cfg, chat:int, sender:int) -> bool:
    return bool(cfg.get("chat_id") == chat and cfg.get("owner_id") == sender)


def handle_message(cfg:dict, m:dict):
    text=str(m.get("text") or "").strip()
    match=re.fullmatch(r"/rb(?:@\w+)?(?:\s+(.+))?",text,re.I|re.S)
    button_command = BUTTON_COMMANDS.get(text)
    if not match and button_command is None:
        return
    timestamp=int(m.get("date") or 0)
    if abs(time.time()-timestamp) > MAX_MESSAGE_AGE:
        return
    chat=m.get("chat") or {}
    user=m.get("from") or {}
    chat_id=chat.get("id")
    uid=user.get("id")
    if not isinstance(chat_id,int) or not isinstance(uid,int) or user.get("is_bot"):
        return
    args=(match.group(1) or "").strip() if match else button_command
    items=args.split()
    sub=items[0].lower() if items else "menu"

    if sub=="pair" and chat.get("type")=="private":
        if cfg.get("owner_id") or len(items)!=2:
            return
        key=hashlib.sha256(items[1].encode()).hexdigest()
        if (time.time()<float(cfg.get("pair_expires") or 0) and
            hmac.compare_digest(str(cfg.get("pair_hash") or ""), key)):
            cfg["owner_id"]=uid
            cfg["pair_hash"]=""
            cfg["pair_expires"]=0
            safe_save(CONF,cfg)
            send(cfg,chat_id,"🟢 Private owner paired.\nNow add this bot to your TERMINAL GROUP and send /rb bind there.")
        else:
            send(cfg,chat_id,"Pairing code invalid or expired. Regenerate from your VM.")
        return

    if sub=="bind" and cfg.get("owner_id")==uid and not cfg.get("chat_id"):
        if chat.get("type") in ("group","supergroup"):
            cfg["chat_id"]=chat_id
            safe_save(CONF,cfg)
            send(cfg,chat_id,"🟢 THIS GROUP IS NOW THE RESULTBOT CONTROL CENTER.\n"
                 "Only the paired Telegram account can issue commands.\nType /rb or use the menu.",
                 menu(cfg))
        return

    if not is_authorized(cfg,chat_id,uid):
        return
    if sub in ("help","menu","start"):
        send(cfg,chat_id,HELP,menu(cfg))
    elif sub in READ_ACTIONS and len(items)==1:
        send(cfg,chat_id,READ_ACTIONS[sub](),menu(cfg))
    else:
        proposal=propose(args)
        if proposal:
            new_confirmation(cfg,chat_id,uid,*proposal)
        else:
            send(cfg,chat_id,"Unknown command.\n"+HELP,menu(cfg))


def handle_callback(cfg:dict, q:dict):
    data=str(q.get("data") or "")
    msg=q.get("message") or {}
    chat=(msg.get("chat") or {}).get("id")
    uid=(q.get("from") or {}).get("id")
    if not isinstance(chat,int) or not isinstance(uid,int):
        return
    if not is_authorized(cfg,chat,uid):
        answer(cfg,str(q.get("id") or ""),"Not authorized")
        return
    qid=str(q.get("id") or "")
    if data.startswith("q:"):
        key=data[2:]
        answer(cfg,qid)
        if key in READ_ACTIONS: send(cfg,chat,READ_ACTIONS[key](),menu(cfg))
        return
    if data.startswith("a:"):
        action=data[2:]
        human=action.replace("-"," ")
        proposal=propose(human if action!="stop" else "stop")
        answer(cfg,qid)
        if proposal: new_confirmation(cfg,chat,uid,*proposal)
        return
    if data.startswith(("c:","x:")):
        nonce=data[2:]
        record=PENDING.pop(nonce,None)
        if not record or record[0]!=chat or record[1]!=uid or record[2]<time.monotonic():
            answer(cfg,qid,"Expired or already handled")
            return
        if data.startswith("x:"):
            answer(cfg,qid,"Cancelled")
            send(cfg,chat,"Change cancelled. Nothing modified.",menu(cfg))
        else:
            answer(cfg,qid,"Processing")
            send(cfg,chat,execute_confirmed(record[3]),menu(cfg))


def serve():
    if not CONF.is_file():
        raise RemoteError("Run install first")
    d=config()
    # On first service start, discard previously queued bot messages.
    if d.get("offset") is None:
        updates=api(d["bot_token"],"getUpdates",
                    {"offset":-1,"limit":1,"timeout":0,"allowed_updates":["message","callback_query"]})
        d["offset"]=int(updates[-1]["update_id"])+1 if updates else 0
        safe_save(CONF,d)
    while True:
        try:
            d=config()
            updates=api(d["bot_token"],"getUpdates",
                {"offset":int(d.get("offset") or 0),"timeout":20,"limit":30,
                 "allowed_updates":["message","callback_query"]},timeout=30)
            for u in updates:
                ident=int(u["update_id"])
                current=config()
                if ident<int(current.get("offset") or 0):
                    continue
                # Persist at-most-once consumption before any state-changing command.
                current["offset"]=ident+1
                safe_save(CONF,current)
                if isinstance(u.get("message"),dict):
                    handle_message(current,u["message"])
                elif isinstance(u.get("callback_query"),dict):
                    handle_callback(current,u["callback_query"])
        except Exception as exc:
            # Log error CLASS only; never Telegram token or account credentials.
            print("Telegram remote retry:",type(exc).__name__,flush=True)
            time.sleep(5)


def pair_code():
    d=config()
    if d.get("owner_id"):
        print("Already paired. No new pairing code generated.")
        return
    key=secrets.token_urlsafe(18)
    d["pair_hash"]=hashlib.sha256(key.encode()).hexdigest()
    d["pair_expires"]=int(time.time())+900
    safe_save(CONF,d)
    print("Private Telegram pairing code (valid 15 min):",key)
    print("Send this command PRIVATELY to your BotFather-created bot:")
    print("/rb pair "+key)


def install():
    if os.geteuid()==0:
        raise RemoteError("Run as the normal ResultBot user, not root.")
    if not PY.is_file() or not ENTRY.is_file() or not READ.is_file():
        raise RemoteError("ResultBot entry/read-only scripts are not present")
    d=config() if CONF.is_file() else None
    if d is None:
        token=getpass.getpass("New dedicated Telegram BotFather BOT TOKEN (hidden): ").strip()
        if not re.fullmatch(r"[0-9]{6,15}:[a-zA-Z0-9_-]{20,200}",token):
            raise RemoteError("Invalid BotFather token format")
        ident=api(token,"getMe",{})
        if not isinstance(ident,dict) or not ident.get("is_bot"):
            raise RemoteError("Token does not belong to a Telegram bot")
        webhook=api(token,"getWebhookInfo",{})
        if webhook.get("url"):
            raise RemoteError("This bot already uses a webhook. Create a dedicated bot.")
        d={"bot_token":token,"owner_id":None,"chat_id":None,
           "pair_hash":"","pair_expires":0,"offset":None,
           "bot_username":ident.get("username") or "your_bot"}
        safe_save(CONF,d)
    APP.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if Path(__file__).resolve()!=APP.resolve():
        if APP.exists() and APP.is_symlink():
            raise RemoteError("Refusing symlinked installed control module")
        temp=APP.with_suffix(".new")
        shutil.copyfile(Path(__file__),temp)
        temp.chmod(0o700)
        temp.replace(APP)
    SERVICE.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    unit="[Unit]\nDescription=ResultBot Telegram owner control panel\nAfter=network-online.target\nWants=network-online.target\n\n"
    unit+=("[Service]\nType=simple\nExecStart=%h/uditbot/venv/bin/python "
           "%h/resultbot-data/telegram_remote.py serve\n"
           "Restart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=true\n\n"
           "[Install]\nWantedBy=default.target\n")
    SERVICE.write_text(unit)
    subprocess.run(["systemctl","--user","daemon-reload"],check=True)
    subprocess.run(["systemctl","--user","enable","--now",
                    "resultbot-telegram-control.service"],check=True)
    subprocess.run(["systemctl","--user","restart",
                    "resultbot-telegram-control.service"],check=True)
    print("Telegram control service enabled. Bot: @"+str(d.get("bot_username")))
    if not d.get("owner_id"):
        pair_code()
    elif not d.get("chat_id"):
        print("Paired privately. Bind the terminal group with /rb bind.")
    else:
        print("Already paired and group-bound. Use /rb in Telegram.")
    print("Original trading listener and OCO guard not restarted.")


def self_test():
    assert propose("budget 1000")[0]==("settings","--budget","1000")
    assert propose("source both")[0]==("settings","--source","BOTH")
    assert propose("live on")[0]==("live-on",)
    assert propose("amo off")[0]==("amo-off",)
    assert propose("stop")[0]==("stop-all",)
    assert propose("budget 0") is None
    assert propose("shell ls") is None
    assert propose("cancel 1") is None
    kb = menu({"bot_username": "Terminalresultbot"})
    assert kb["is_persistent"] and kb["resize_keyboard"] is False
    assert len(kb["keyboard"]) == 6 and len(kb["keyboard"][0]) == 2
    assert all(button["text"] in BUTTON_COMMANDS
               for group in kb["keyboard"] for button in group)
    assert kb["keyboard"][4][1]["style"] == "success"
    assert BUTTON_COMMANDS["📊 STATUS"] == "status"
    assert propose(BUTTON_COMMANDS["🔴 AMO ON"])[0] == ("arm",)

    healthy = format_status_reply(
        {"live_enabled": True, "amo_enabled": False,
         "source": "BOTH", "per_order_budget_inr": 25},
        {"Telegram listener": True, "OCO Guard": True, "Token renewal ": True},
        {"protection": [], "unprotected_errors": []}
    )
    assert healthy == (
        "RESULTBOT\\n\\n🟢 LIVE: ON\\n🟢 AMO: OFF\\n🟢 SOURCES: BOTH"
        "\\n🟢 PER ORDER: ₹25\\n🟢 Telegram listener: RUNNING"
        "\\n🟢 OCO Guard: RUNNING\\n🟢 Token renewal : RUNNING"
        "\\n🟢 OCO: 0 verified active, 0 warnings"
    ).replace("\\n", "\n")
    problematic = format_status_reply(
        {"live_enabled": False, "amo_enabled": True,
         "source": "TEST", "per_order_budget_inr": 1000},
        {"Telegram listener": False, "OCO Guard": True, "Token renewal ": None},
        {"protection": [{"state": "UNKNOWN"}],
         "unprotected_errors": [{"message": "Broker check pending"}]}
    )
    assert "🔴 LIVE: OFF" in problematic
    assert "🔴 AMO: ON" in problematic
    assert "🟡 OCO: 0 verified active, 2 warnings" in problematic
    assert "🟡 Token renewal : UNKNOWN" in problematic
    unknown = format_status_reply(None, {}, None)
    assert "🟡 LIVE: UNKNOWN" in unknown and "🟡 OCO: UNKNOWN" in unknown
    print("PASS: command allowlist, keyboard and dynamic status reply format")


def main():
    if len(sys.argv)<2:
        raise RemoteError("Usage: telegram_remote.py install|serve|pair-code|self-test")
    action=sys.argv[1]
    if action=="install": install()
    elif action=="serve": serve()
    elif action=="pair-code": pair_code()
    elif action=="self-test": self_test()
    else: raise RemoteError("Unknown local command")


if __name__=="__main__":
    try: main()
    except (RemoteError, OSError, subprocess.CalledProcessError) as exc:
        print("Telegram remote setup error:", type(exc).__name__,
              (str(exc) if isinstance(exc,RemoteError) else ""),file=sys.stderr)
        sys.exit(1)
