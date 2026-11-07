#!/usr/bin/env python3
"""ResultBot: offline Dhan Forever OCO price plan from a *confirmed* Equity BUY fill.

This module does NOT place, modify, cancel, or verify a live OCO. The returned
prices are suggestions pending broker holdings, DDPI, eligibility and existing
protection reconciliation. Never use order LIMIT price, alert CMP or Yahoo CMP
as a replacement for missing averageTradedPrice.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_CEILING
import re


class OcoPlanError(ValueError):
    """A confirmed and correctly bound BUY execution is not available."""


def _positive_decimal(value, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise OcoPlanError(label + " missing or invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise OcoPlanError(label + " is not numeric") from None
    if not result.is_finite() or result <= 0:
        raise OcoPlanError(label + " must be finite and positive")
    return result


def _positive_whole(value, label: str) -> int:
    if value is None or isinstance(value, bool):
        raise OcoPlanError(label + " missing")
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise OcoPlanError(label + " is invalid") from None
    if not n.is_finite() or n <= 0 or n != n.to_integral_value():
        raise OcoPlanError(label + " must be a positive whole share quantity")
    if n >= (1 << 63):
        raise OcoPlanError(label + " exceeds supported quantity")
    return int(n)


def _to_tick(price: Decimal, tick: Decimal, direction: str) -> Decimal:
    rounding = ROUND_FLOOR if direction == "floor" else ROUND_CEILING
    return (price / tick).to_integral_value(rounding=rounding) * tick


def _price_str(value: Decimal) -> str:
    return format(value, "f")


def build_oco_price_plan(
    *,
    intent: dict,
    order: dict,
    stop_loss_pct: float | int,
    target_pct: float | int,
    tick_size: float | str,
    stop_limit_buffer_ticks: int = 1,
) -> dict:
    """Compute protective SELL price levels using Dhan's executed BUY VWAP.

    Expected to receive an already verified ResultBot-owned order from
    equity_amo_live._verify_owned_order. Recheck core immutable binding here
    for safe standalone and offline use. Order must be *fully* TRADED; partial
    fills are intentionally not treated as a fully protectable holding.
    """
    if not isinstance(intent, dict) or not isinstance(order, dict):
        raise OcoPlanError("Invalid ownership/order records")

    required = {
        "dhanClientId": "account",
        "orderId": "order_id",
        "correlationId": "correlation",
        "securityId": "security_id",
    }
    for broker_key, intent_key in required.items():
        own = str(intent.get(intent_key) or "").strip()
        found = str(order.get(broker_key) or "").strip()
        if not own or not found or found != own:
            raise OcoPlanError("Unverified ResultBot order binding: " + broker_key)
    if not re.fullmatch(r"[0-9]{6,25}", str(order["orderId"])):
        raise OcoPlanError("Invalid Dhan order ID")
    if (
        str(order.get("transactionType") or "").upper() != "BUY"
        or str(order.get("exchangeSegment") or "").upper() != "NSE_EQ"
        or str(order.get("productType") or "").upper() != "CNC"
        or order.get("afterMarketOrder") is not True
    ):
        raise OcoPlanError("Order is not a ResultBot NSE_EQ CNC BUY AMO")

    status = str(order.get("orderStatus") or "").upper()
    if status != "TRADED":
        raise OcoPlanError(
            "Order not fully TRADED ({}); no executable OCO plan".format(
                status or "UNKNOWN"
            )
        )

    requested = _positive_whole(intent.get("qty"), "Original entry quantity")
    broker_order_qty = _positive_whole(order.get("quantity"), "Dhan order quantity")
    filled = _positive_whole(order.get("filledQty"), "Dhan filledQty")
    if filled != broker_order_qty or requested != broker_order_qty:
        raise OcoPlanError("Dhan filled quantity does not match the owned BUY order")

    execution = _positive_decimal(
        order.get("averageTradedPrice"), "Dhan averageTradedPrice"
    )
    sl = _positive_decimal(stop_loss_pct, "Stop-loss percentage")
    tp = _positive_decimal(target_pct, "Target percentage")
    if sl >= 50 or tp >= 100:
        raise OcoPlanError("Stop-loss must be below 50%; target below 100%")
    tick = _positive_decimal(tick_size, "Verified NSE instrument tick size")
    if tick < Decimal("0.01") or tick > Decimal("1"):
        raise OcoPlanError("Unverified NSE tick size")
    if isinstance(stop_limit_buffer_ticks, bool) or not isinstance(stop_limit_buffer_ticks, int):
        raise OcoPlanError("Stop-limit buffer must be a whole tick count")
    if not 1 <= stop_limit_buffer_ticks <= 20:
        raise OcoPlanError("Stop-limit buffer must be 1–20 ticks")

    sl_raw = execution * (Decimal(1) - sl / Decimal(100))
    tp_raw = execution * (Decimal(1) + tp / Decimal(100))
    sl_trigger = _to_tick(sl_raw, tick, "floor")
    sl_limit = sl_trigger - tick * stop_limit_buffer_ticks
    target_price = _to_tick(tp_raw, tick, "ceil")
    if sl_limit <= 0 or not (sl_limit < sl_trigger < execution < target_price):
        raise OcoPlanError("Derived OCO prices are not valid for a protective SELL")
    return {
        "plan_status": "PREVIEW_ONLY_NOT_PLACED",
        "execution_price_source": "DHAN_ORDER_AVERAGE_TRADED_PRICE",
        "entry_order_id": str(order["orderId"]),
        "security_id": str(order["securityId"]),
        "exchange_segment": "NSE_EQ",
        "product_type": "CNC",
        "entry_fill_status": "TRADED",
        "original_entry_filled_qty": filled,
        "proposed_oco_qty": filled,
        "confirmed_execution_vwap": _price_str(execution),
        "stop_loss_pct": _price_str(sl),
        "target_pct": _price_str(tp),
        "tick_size": _price_str(tick),
        "stop_loss_trigger": _price_str(sl_trigger),
        "stop_loss_sell_limit": _price_str(sl_limit),
        "target_sell_limit": _price_str(target_price),
        "target_trigger": _price_str(target_price),
        "stop_limit_buffer_ticks": stop_limit_buffer_ticks,
        "broker_oco_created": False,
        "holding_quantity_verified": False,
        "existing_oco_checked": False,
        "eligibility_verified": False,
        "warnings": [
            "Price anchor is this BUY order's confirmed execution VWAP, not Yahoo/Telegram/limit price",
            "OCO quantity is a candidate, NOT verified available holding quantity",
            "Verify portfolio holdings, DDPI, existing SELL/OCO orders and T+1 eligibility before placing",
            "Initial BUY fill does not prove Forever OCO is already active or supported today",
        ],
    }
