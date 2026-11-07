#!/usr/bin/env python3
"""ResultBot live intraday LIMIT buy-buffer settings and OFFLINE price calculator.

Separate from Equity AMO. This module never contacts Dhan or places orders.
A limit BUY sets the maximum acceptable price, not a promised fill price.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

ROOT = Path.home() / "resultbot-data"
CONFIG = ROOT / "intraday_buy_settings.json"
DEFAULT_BUFFER_PCT = Decimal("1")
PRESET_PCTS = ("1", "2", "3", "4")
MAX_STORABLE_BUDGET = (1 << 63) - 1


class BuyBufferError(ValueError):
    pass


def number(value, label):
    if value is None or isinstance(value, bool):
        raise BuyBufferError(label + " is missing")
    try:
        n = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise BuyBufferError(label + " must be numeric") from None
    if not n.is_finite():
        raise BuyBufferError(label + " must be finite")
    return n


def percentage(value):
    p = number(value, "Buy buffer %")
    if p < 0 or p >= 100:
        raise BuyBufferError("Buy buffer must be 0% or greater and below 100%")
    # No arbitrary 1-4% restriction; owner may choose a custom buffer.
    return p


def settings():
    saved = {}
    if CONFIG.exists():
        if CONFIG.is_symlink():
            raise BuyBufferError("Refusing symlinked intraday settings")
        try:
            saved = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BuyBufferError("Cannot read saved intraday settings") from exc
        if not isinstance(saved, dict):
            raise BuyBufferError("Invalid intraday settings document")
    current = percentage(saved.get("buy_buffer_pct", DEFAULT_BUFFER_PCT))
    return {
        "buy_buffer_pct": format(current, "f"),
        "buffer_presets_pct": list(PRESET_PCTS),
        "trading_scope": "INTRADAY_ONLY",
        "planned_broker_product_type": "INTRADAY",
        "entry_order_type": "LIMIT",
        "live_intraday_execution_connected": False,
        "existing_equity_amo_unchanged": True,
    }


def save_buffer_pct(value):
    p = percentage(value)
    if CONFIG.is_symlink():
        raise BuyBufferError("Refusing symlinked intraday settings")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    ROOT.chmod(0o700)
    fd, tmp = tempfile.mkstemp(prefix=".intraday-buffer-", suffix=".json", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version":1, "buy_buffer_pct":format(p, "f"),
                       "trading_scope":"INTRADAY_ONLY"}, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return settings()


def calculate_buy_limit(*, quote_price, budget_inr, tick_size,
                        buffer_pct=None, reference_kind="BEST_ASK"):
    """Pure calculation. The caller must verify market quote freshness separately.

    Uses verified exchange tick rounding, and sizes shares against the *inflated
    limit*, rather than the lower quote price; actual fills may cost less.
    """
    if reference_kind not in ("BEST_ASK", "LIVE_LTP"):
        raise BuyBufferError("Reference must be live best ask or live LTP")
    quote = number(quote_price, "Market price")
    tick = number(tick_size, "Tick size")
    budget = number(budget_inr, "Investment budget")
    pct = percentage(settings()["buy_buffer_pct"] if buffer_pct is None else buffer_pct)
    if quote <= 0 or tick <= 0 or budget <= 0:
        raise BuyBufferError("Market price, tick size and budget must be positive")
    if budget != budget.to_integral_value() or budget > MAX_STORABLE_BUDGET:
        raise BuyBufferError("Budget must be a positive whole-rupee amount")
    theoretical = quote * (Decimal("1") + pct / Decimal("100"))
    ceiling = (theoretical / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    quantity = int((budget / ceiling).to_integral_value(rounding=ROUND_FLOOR))
    return {
        "status": "OFFLINE_PREVIEW_NOT_SUBMITTED",
        "strategy": "LIVE_INTRADAY_PLANNED",
        "product_type": "INTRADAY",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "after_market_order": False,
        "reference_kind": reference_kind,
        "reference_price": format(quote, "f"),
        "buy_buffer_pct": format(pct, "f"),
        "buy_limit_price": format(ceiling, "f"),
        "verified_tick_input": format(tick, "f"),
        "per_order_budget_inr": int(budget),
        "whole_shares_affordable_at_limit": quantity,
        "maximum_entry_notional_inr": format(ceiling * quantity, "f"),
        "can_fit_one_share": quantity > 0,
        "live_quote_freshness_verified": False,
        "broker_order_submitted": False,
        "warning": "Limit BUY may fill below limit, partially fill, or not fill. "
                   "Quote freshness, broker price bands and exchange eligibility "
                   "must be checked by a future live intraday executor.",
    }


def main():
    p = argparse.ArgumentParser(description="ResultBot intraday buy buffer, no broker orders")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("settings-json")
    edit = sub.add_parser("set-buffer")
    edit.add_argument("percent", help="Owner-selected percent; 1 2 3 4 or custom")
    demo = sub.add_parser("preview")
    demo.add_argument("--price", required=True)
    demo.add_argument("--budget", required=True)
    demo.add_argument("--tick", default="0.05")
    demo.add_argument("--buffer", default=None)
    demo.add_argument("--reference", choices=("BEST_ASK","LIVE_LTP"), default="BEST_ASK")
    args = p.parse_args()
    try:
        if args.command == "settings-json":
            value = settings()
        elif args.command == "set-buffer":
            value = save_buffer_pct(args.percent)
        else:
            value = calculate_buy_limit(
                quote_price=args.price, budget_inr=args.budget,
                tick_size=args.tick, buffer_pct=args.buffer,
                reference_kind=args.reference
            )
    except (BuyBufferError, OSError) as exc:
        p.exit(2, "INTRADAY SETTINGS NOT SAVED: "+str(exc)+"\n")
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
