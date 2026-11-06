#!/usr/bin/env python3
"""Standalone ResultBot DhanHQ v2 READ-ONLY client.

Capabilities: GET /profile, GET /ip/getIP, GET /orders,
GET /orders/{id}, GET /trades. No POST/PUT/DELETE, SDK order methods,
Telegram notifications, or broker mutations. Not imported by trading_bot.
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode

BASE = "https://api.dhan.co/v2"
ROOT = Path.home() / "resultbot-data"
CREDS = ROOT / ".dhan_readonly.json"
ALLOWED = frozenset({"/profile", "/ip/getIP", "/orders", "/trades"})


class DhanReadError(Exception):
    pass


def valid_account(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{8,15}", value or ""))


def validate_token(token: str) -> bool:
    return bool(isinstance(token, str) and 24 <= len(token) <= 8192 and
                not any(ord(c) < 33 or ord(c) == 127 for c in token))


def safe_path(path: Path) -> None:
    if path.is_symlink():
        raise DhanReadError("Credentials must not be a symlink")


def store_credentials(account: str, token: str, path: Path = CREDS) -> None:
    if not valid_account(account) or not validate_token(token):
        raise DhanReadError("Invalid Dhan client ID or access token format")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    safe_path(path)
    payload = json.dumps({"dhanClientId": account, "accessToken": token})
    fd, temporary = tempfile.mkstemp(prefix=".dhan_ro_", dir=str(path.parent))
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_credentials(path: Path = CREDS) -> tuple[str, str]:
    safe_path(path)
    try:
        mode = path.stat().st_mode
        if mode & 0o077:
            raise DhanReadError("Credentials permissions are unsafe; require chmod 600")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DhanReadError("Dhan not configured. Run: dhan_readonly.py setup")
    except (ValueError, OSError, TypeError):
        raise DhanReadError("Dhan credential file is unreadable or malformed")
    account, token = str(data.get("dhanClientId", "")), data.get("accessToken", "")
    if not valid_account(account) or not validate_token(token):
        raise DhanReadError("Stored Dhan credentials are invalid")
    return account, token


def safe_endpoint(path: str) -> str:
    if path in ALLOWED:
        return BASE + path
    if re.fullmatch(r"/orders/[0-9]{1,25}", path):
        return BASE + path
    raise DhanReadError("Only explicitly allowlisted GET requests are supported")


def get_json(path: str, token: str) -> object:
    if not validate_token(token):
        raise DhanReadError("Access token invalid or missing")
    url = safe_endpoint(path)
    req = Request(url, method="GET", headers={
        "access-token": token, "Accept": "application/json",
        "User-Agent": "ResultBot-ReadOnly/1.0",
    })
    try:
        with urlopen(req, timeout=12) as response:
            if response.status != 200:
                raise DhanReadError("Dhan returned an unexpected status")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise DhanReadError("Dhan response exceeds size limit")
            result = json.loads(raw)
            if isinstance(result, dict) and result.get("errorCode"):
                raise DhanReadError("Dhan returned an API error")
            return result
    except HTTPError as exc:
        if exc.code in (401,403):
            raise DhanReadError("Authentication rejected or token expired (HTTP %d)" % exc.code)
        raise DhanReadError("Dhan HTTP error: %d" % exc.code)
    except (URLError, TimeoutError, OSError):
        raise DhanReadError("Dhan connection failed; check network/API availability")
    except (ValueError, UnicodeDecodeError):
        raise DhanReadError("Dhan returned invalid JSON")


def verified_profile(account: str, token: str) -> dict:
    profile = get_json("/profile", token)
    if not isinstance(profile, dict):
        raise DhanReadError("Unexpected Dhan profile response")
    reported = str(profile.get("dhanClientId") or "")
    if not valid_account(reported) or reported != account:
        raise DhanReadError("Dhan account mismatch; refusing to proceed")
    return profile


def verified_rows(rows: object, account: str) -> list[dict]:
    if not isinstance(rows, list):
        raise DhanReadError("Dhan returned an unexpected collection format")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise DhanReadError("Dhan returned a malformed order/trade record")
        if str(row.get("dhanClientId") or "") != account:
            raise DhanReadError("Dhan order/trade account mismatch or missing account identity")
        result.append(row)
    return result


def checked_number(value: object) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def fill_info(order: dict) -> tuple[str, str, str]:
    """Never report a price from unfilled orders as an actual execution."""
    status = str(order.get("orderStatus") or "UNVERIFIED").upper()
    qty = checked_number(order.get("quantity"))
    filled = checked_number(order.get("filledQty"))
    if qty is None or filled is None or filled > qty or int(filled) != filled:
        return status, "UNVERIFIED", "N/A"
    avg = checked_number(order.get("averageTradedPrice"))
    price = "N/A" if filled == 0 or avg is None or avg <= 0 else f"₹{avg:,.2f}"
    state = f"{int(filled)}/{int(qty)}"
    if filled and price == "N/A":
        state += " (price unverified)"
    return status, state, price


def symbol_name(row: dict) -> str:
    raw = str(row.get("tradingSymbol") or "")
    return raw[:45] if re.fullmatch(r"[a-zA-Z0-9&._+\\-]{1,45}", raw) else "SYMBOL_UNVERIFIED"


def display_account(account: str) -> str:
    return "***" + account[-4:]



class _NeverRedirect(HTTPRedirectHandler):
    """Do not follow auth redirects carrying transient PIN/TOTP query values."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def token_from_pin_totp(account: str, pin: str, totp: str) -> str:
    """Generate a 24-hour Dhan token; do NOT store PIN, TOTP or TOTP seed.

    This is the only non-GET call in this module. Dhan documents these
    parameters in a POST HTTPS query string, so do not log the URL.
    """
    if not valid_account(account):
        raise DhanReadError("Invalid Dhan Client ID")
    if not re.fullmatch(r"[0-9]{6}", pin or ""):
        raise DhanReadError("Dhan PIN must contain exactly 6 digits")
    if not re.fullmatch(r"[0-9]{6}", totp or ""):
        raise DhanReadError("TOTP must contain exactly 6 digits")
    url = "https://auth.dhan.co/app/generateAccessToken?" + urlencode({
        "dhanClientId": account,
        "pin": pin,
        "totp": totp,
    })
    request = Request(url, data=b"", method="POST", headers={
        "Accept":"application/json",
        "User-Agent":"ResultBot-Dhan-Auth/1.0",
    })
    try:
        with build_opener(_NeverRedirect()).open(request, timeout=12) as reply:
            if reply.status != 200:
                raise DhanReadError("Dhan token generation returned an unexpected status")
            raw = reply.read(200_001)
            if len(raw) > 200_000:
                raise DhanReadError("Dhan authentication response exceeded size limit")
            data = json.loads(raw)
    except HTTPError as exc:
        # Never expose exc.url, which contains the PIN and TOTP.
        raise DhanReadError("Dhan TOTP authentication rejected (HTTP %d). Check PIN, current code and clock." % exc.code) from None
    except (URLError, TimeoutError, OSError):
        raise DhanReadError("Dhan authentication connection failed; check connectivity") from None
    except (ValueError, UnicodeDecodeError):
        raise DhanReadError("Dhan authentication returned invalid JSON") from None
    if not isinstance(data, dict):
        raise DhanReadError("Dhan authentication returned an unexpected response")
    if str(data.get("dhanClientId") or "") != account:
        raise DhanReadError("Dhan generated a token for a different client ID; refusing it")
    token = data.get("accessToken")
    if not validate_token(token):
        raise DhanReadError("Dhan authentication did not return a valid access token")
    return token


def do_setup_totp() -> None:
    if not sys.stdin.isatty():
        raise DhanReadError("TOTP setup requires an interactive VM terminal")
    print("DhanHQ PIN + authenticator TOTP: issue a new 24-hour token.")
    print("Dhan supports up to five active tokens. This is not automatic renewal.")
    print("PIN and current TOTP are never saved or printed by this tool.")
    confirm = input("Type GENERATE to request a fresh Dhan token: ").strip()
    if confirm != "GENERATE":
        print("Cancelled: no Dhan request made.")
        return
    account = input("Dhan Client ID: ").strip()
    if not valid_account(account):
        raise DhanReadError("Dhan Client ID must be numeric")
    pin = getpass.getpass("6-digit Dhan PIN (hidden): ").strip()
    totp = getpass.getpass("Current 6-digit authenticator TOTP (hidden): ").strip()
    token = token_from_pin_totp(account, pin, totp)
    # A generated token is never persisted without independent broker
    # profile verification that its account matches user input.
    profile = verified_profile(account, token)
    store_credentials(account, token)
    print("SUCCESS: Dhan TOTP login verified, token saved privately (0600).")
    print("Verified Dhan account:", display_account(account))
    print("Token validity:", str(profile.get("tokenValidity") or "Not supplied"))
    print("PIN/TOTP were NOT saved; trading remains disabled.")


def do_setup() -> None:
    if not sys.stdin.isatty():
        raise DhanReadError("Credential setup requires an interactive terminal")
    print("DhanHQ read-only ResultBot setup. Do not paste secrets into chat.")
    account = input("Dhan Client ID: ").strip()
    if not valid_account(account):
        raise DhanReadError("Dhan Client ID must be numeric")
    token = getpass.getpass("Dhan access token (hidden input): ").strip()
    if not validate_token(token):
        raise DhanReadError("Invalid access token format")
    profile = verified_profile(account, token)
    store_credentials(account, token)
    print("SUCCESS: Verified Dhan profile and saved separate ResultBot credentials (0600).")
    print("Dhan account:", display_account(account))
    print("Token validity:", str(profile.get("tokenValidity") or "Not supplied"))
    print("No orders placed; trading service unchanged.")


def do_check(account: str, token: str) -> None:
    profile = verified_profile(account, token)
    print("CONNECTED: DhanHQ v2 READ-ONLY")
    print("Verified account:", display_account(account))
    print("Token validity:", str(profile.get("tokenValidity") or "Not supplied"))
    print("Active segments:", str(profile.get("activeSegment") or "Not supplied")[:160])
    ipdata = get_json("/ip/getIP", token)
    if not isinstance(ipdata, dict):
        raise DhanReadError("Dhan returned unexpected IP configuration")
    primary = str(ipdata.get("primaryIP") or "Not set")
    secondary = str(ipdata.get("secondaryIP") or "Not set")
    print("Dhan whitelisted public IPs:", primary, "|", secondary)
    print("Order submission: DISABLED (this module contains GET requests only)")


def do_orders(account: str, token: str, limit: int) -> None:
    verified_profile(account, token)
    rows = verified_rows(get_json("/orders", token), account)
    print("DHAN ORDER BOOK (current day) | total:", len(rows))
    if not rows:
        print("No orders returned for the current day.")
    for row in rows[-limit:]:
        status, filled, avg = fill_info(row)
        print(f"{symbol_name(row):<21} {status:<14} filled={filled:<26} avg={avg}")
    print("NOTE: Orders shown are not linked to Excellent alerts without a verified ID correlation.")


def do_trades(account: str, token: str, limit: int) -> None:
    verified_profile(account, token)
    rows = verified_rows(get_json("/trades", token), account)
    print("DHAN TRADE BOOK (current day) | total:", len(rows))
    if not rows:
        print("No trades returned for the current day.")
    for row in rows[-limit:]:
        qty, price = checked_number(row.get("tradedQuantity")), checked_number(row.get("tradedPrice"))
        execution = "N/A" if qty is None or qty <= 0 or price is None or price <= 0 else f"₹{price:,.2f}"
        side = str(row.get("transactionType") or "")[:5]
        print(f"{symbol_name(row):<21} {side:<5} qty={int(qty) if qty is not None else 'N/A'} price={execution}")
    print("NOTE: These are real Dhan trades, but not automatically ResultBot trades.")


def do_order(account: str, token: str, order_id: str) -> None:
    verified_profile(account, token)
    row = get_json("/orders/" + order_id, token)
    if not isinstance(row, dict) or str(row.get("dhanClientId") or "") != account:
        raise DhanReadError("Order data malformed or belongs to another account")
    status, filled, avg = fill_info(row)
    print("Dhan order status (read-only)")
    print("Symbol:", symbol_name(row))
    print("Broker order status:", status)
    print("Filled / total:", filled)
    print("Broker average traded price:", avg)
    print("Do not equate this order to a Telegram alert without a verified correlation.")


def self_test() -> None:
    assert safe_endpoint("/profile") == BASE + "/profile"
    assert safe_endpoint("/trades") == BASE + "/trades"
    assert safe_endpoint("/orders/123456789") == BASE + "/orders/123456789"
    for invalid in ("/orders/1/modify", "/orders/slicing", "/forever/orders", "/orders/123?x=1", "/RenewToken"):
        try:
            safe_endpoint(invalid)
        except DhanReadError:
            pass
        else:
            raise AssertionError("Non-read-only endpoint accepted")
    assert fill_info({"orderStatus":"PENDING","quantity":10,"filledQty":0,"averageTradedPrice":0}) == ("PENDING","0/10","N/A")
    assert fill_info({"orderStatus":"TRADED","quantity":10,"filledQty":10,"averageTradedPrice":123.45}) == ("TRADED","10/10","₹123.45")
    assert fill_info({"orderStatus":"PART_TRADED","quantity":10,"filledQty":3,"averageTradedPrice":123.45}) == ("PART_TRADED","3/10","₹123.45")
    assert fill_info({"orderStatus":"TRADED","quantity":10,"filledQty":10,"averageTradedPrice":0})[2] == "N/A"
    assert display_account("1100008885") == "***8885"
    for malformed_pin, malformed_totp in (("12345","123456"),("123456",""),("PINCODE","123456")):
        try:
            token_from_pin_totp("1100008885",malformed_pin,malformed_totp)
        except DhanReadError:
            pass
        else:
            raise AssertionError("Unsafe authentication credential accepted")
    print("PASS: Dhan GET-only allowlist, broker fill verification, account masking")


def main() -> None:
    ap = argparse.ArgumentParser(description="ResultBot standalone DhanHQ v2 GET-only client (no trading)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test")
    sub.add_parser("setup", help="privately enter a pre-generated Dhan access token")
    sub.add_parser("setup-totp", help="generate new access token using hidden Dhan PIN plus 6-digit TOTP")
    sub.add_parser("check", help="verify Dhan profile, account identity and registered IPs")
    p=sub.add_parser("orders",help="list today\\'s order statuses with verified fill prices")
    p.add_argument("--limit",type=int,default=10,choices=range(1,21),metavar="1-20")
    p=sub.add_parser("trades",help="list today\\'s exchange trades")
    p.add_argument("--limit",type=int,default=10,choices=range(1,21),metavar="1-20")
    p=sub.add_parser("order",help="inspect one broker order by its confirmed ID")
    p.add_argument("--id",required=True)
    args=ap.parse_args()
    if args.command=="self-test":
        self_test()
        return
    if args.command=="setup":
        do_setup()
        return
    if args.command=="setup-totp":
        do_setup_totp()
        return
    account, token = load_credentials()
    if args.command=="check":
        do_check(account,token)
    elif args.command=="orders":
        do_orders(account,token,args.limit)
    elif args.command=="trades":
        do_trades(account,token,args.limit)
    elif args.command=="order":
        if not re.fullmatch(r"[0-9]{1,25}",args.id):
            raise DhanReadError("Order ID format must be numeric")
        do_order(account,token,args.id)


if __name__=="__main__":
    try:
        main()
    except DhanReadError as exc:
        print("DHAN READ-ONLY ERROR:", exc, file=sys.stderr)
        sys.exit(1)
