#!/usr/bin/env python3
"""ResultBot Equity AMO envelope/pipeline demonstration (NO network, NO broker).

This is an isolated, non-routable NSE_EQ CNC LIMIT AMO fixture for
test-group visualization. It does NOT import or invoke the trading engine.
It demonstrates the fields of the AMO /orders route retained in the copied bot.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TRIGGER = "[RBAMO] "
FIXTURE_REFERENCE_PRICE = 100.0    # purely illustrative, not a live/verified quote
FIXTURE_BUDGET_INR = 500.0         # purely illustrative, not a live trade budget
NONROUTABLE_SECURITY_ID = "NO_VERIFIED_SECURITY_ID"


def envelope(symbol: str, *, reference_price: float = FIXTURE_REFERENCE_PRICE,
             budget_inr: float = FIXTURE_BUDGET_INR) -> dict:
    """Build a deliberately NON-SUBMITTABLE template of the documented AMO payload."""
    if not re.fullmatch(r"[A-Z][A-Z0-9&._-]{0,29}", symbol):
        raise ValueError("Invalid equity symbol")
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("Invalid illustrative reference price")
    if not math.isfinite(budget_inr) or budget_inr <= 0:
        raise ValueError("Invalid illustrative budget")
    qty = int(budget_inr // reference_price)
    if qty < 1 or qty * reference_price > budget_inr + 1e-8:
        raise ValueError("Illustrative order exceeds budget or quantity is zero")
    return {
        "dhanClientId": "NOT_AUTHORIZED_FOR_DEMO",
        "correlationId": "RBAMO-DEMO-NO-ORDER",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": NONROUTABLE_SECURITY_ID,
        "quantity": qty,
        "price": float(reference_price),
        "triggerPrice": 0.0,
        "disclosedQuantity": 0,
        "afterMarketOrder": True,
        "amoTime": "OPEN",
    }


def safety_validation(template: dict) -> tuple[bool, tuple[str, ...]]:
    """Never treat fixtures as broker-ready: reject absent verified data/approval."""
    reasons = []
    if template.get("dhanClientId") == "NOT_AUTHORIZED_FOR_DEMO":
        reasons.append("Demo is not authorized to submit live orders")
    if template.get("securityId") == NONROUTABLE_SECURITY_ID:
        reasons.append("No verified NSE security ID")
    if template.get("afterMarketOrder") is not True or template.get("amoTime") != "OPEN":
        reasons.append("AMO route invalid")
    if template.get("productType") != "CNC" or template.get("exchangeSegment") != "NSE_EQ":
        reasons.append("NSE equity delivery route invalid")
    if template.get("orderType") != "LIMIT":
        reasons.append("Only LIMIT AMO is supported by the retained broker route")
    if template.get("transactionType") != "BUY":
        reasons.append("BUY-only demonstration required")
    if not reasons:
        reasons.append("Verified live quote, session, risk, funds and owner opt-in were never checked")
    return False, tuple(reasons)


def card(symbol: str, alert_utc: int) -> str:
    """HTML Telegram card for explicitly requested Test Group-only AMO dry run."""
    d = envelope(symbol)
    ready, _ = safety_validation(d)
    if ready:
        raise AssertionError("Safety gate must NEVER permit demo order placement")
    when = datetime.fromtimestamp(int(alert_utc), IST).strftime("%d-%b-%Y %H:%M:%S IST")
    notional = d["quantity"] * d["price"]
    return "\n".join((
        "🧪 <b>RESULTBOT — EQUITY AMO PIPELINE TEST</b>",
        f"📈 Test symbol: <b>#{escape(symbol)}</b>",
        "✅ Stage 1: Verified TEST GROUP source",
        "✅ Stage 2: Excellent Results classification",
        "✅ Stage 3: AMO route template NSE_EQ / CNC / BUY / LIMIT",
        "✅ Stage 4: afterMarketOrder=true, amoTime=OPEN",
        f"🧮 Stage 5: TEST fixture ₹{d['price']:.2f} × {d['quantity']} = ₹{notional:.2f}",
        f"📊 Sample test budget: ₹{FIXTURE_BUDGET_INR:.2f}",
        "⛔ Stage 6: Broker submission intentionally BLOCKED",
        "⚠️ No verified NSE security ID or live quote — sample inputs only",
        "🏦 Dhan order: NOT SUBMITTED",
        "💰 Broker execution price: N/A",
        "🔐 No Dhan order API, trading mode switch or live account mutation",
        f"🕒 Test message: {when}",
    ))


def self_test() -> None:
    d = envelope("YESBANK")
    assert d["exchangeSegment"] == "NSE_EQ"
    assert d["productType"] == "CNC"
    assert d["transactionType"] == "BUY"
    assert d["orderType"] == "LIMIT"
    assert d["afterMarketOrder"] is True
    assert d["amoTime"] == "OPEN"
    assert d["quantity"] == 5
    assert d["securityId"] == NONROUTABLE_SECURITY_ID
    assert safety_validation(d)[0] is False
    assert "NOT SUBMITTED" in card("YESBANK", 1793991000)
    print("PASS: simulated NSE_EQ CNC LIMIT OPEN AMO envelope, fail-closed broker gate")


def main() -> None:
    ap = argparse.ArgumentParser(description="ResultBot Equity AMO NO-BROKER demonstration")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--symbol", default="YESBANK")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    sample = envelope(args.symbol.strip().upper())
    print("RESULTBOT EQUITY AMO: NON-TRADING PIPELINE PREVIEW")
    print("Symbol:", args.symbol.strip().upper())
    print("Simulated broker AMO request (NON-ROUTABLE FIXTURE):")
    print(json.dumps(sample, indent=2))
    print("\nBROKER SUBMISSION: BLOCKED — no Dhan request or live quote.")
    print("Post to verified ResultBot TEST GROUP to receive a Telegram preview:")
    print(f"{TRIGGER}#{args.symbol.strip().upper()} - 🏆🔥 Excellent Results")


if __name__ == "__main__":
    main()
