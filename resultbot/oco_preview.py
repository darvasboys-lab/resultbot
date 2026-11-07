#!/usr/bin/env python3
"""Read-only ResultBot OCO preview for a fully filled, owned Equity BUY AMO.

Does not create, modify or cancel a Dhan Forever order.
"""
from __future__ import annotations
import argparse
import json
import sys

try:
    from . import equity_amo_live as live
    from . import position_strategy as ps
    from . import oco_price_plan as oco
except ImportError:
    import equity_amo_live as live
    import position_strategy as ps
    import oco_price_plan as oco


def preview_confirmed_intent(intent_id: int) -> dict:
    if isinstance(intent_id, bool) or not isinstance(intent_id,int) or intent_id<=0:
        raise oco.OcoPlanError("Enter a positive ResultBot intent ID")
    intent=live._intent(intent_id)
    if intent["state"] not in ("TERMINAL","ACKED","CANCEL_REQUESTED"):
        raise oco.OcoPlanError(
            "Intent is not broker-confirmed: "+str(intent["state"]))
    if not intent.get("order_id"):
        raise oco.OcoPlanError("No verified broker order ID")
    account, token=live.dh.load_credentials()
    if account!=intent["account"]:
        raise oco.OcoPlanError("Dhan account changed since entry")
    live.dh.verified_profile(account,token)
    verified=live._verify_owned_order(intent,token)  # Read-only Dhan GET
    sec_id,tick=live._verified_instrument(intent["symbol"])
    if sec_id!=intent["security_id"]:
        raise oco.OcoPlanError("Current security ID does not match the entry")
    settings=ps.load()
    plan=oco.build_oco_price_plan(
        intent=intent, order=verified,
        stop_loss_pct=settings["stop_pct"],
        target_pct=settings["target_pct"],
        tick_size=tick,
    )
    plan["resultbot_intent_id"]=intent_id
    plan["symbol"]=intent["symbol"]
    plan["levels_from_saved_strategy"]=True
    plan["requires_current_net_holding_reconciliation"]=True
    return plan


def example(execution_price,quantity,tick,sl,tp):
    """Clearly marked synthetic example for viewing tick rounding only."""
    own={"account":"DEMO_ACCOUNT","order_id":"123456789",
         "correlation":"DEMO_OCO_ONLY","security_id":"111","qty":quantity}
    order={"dhanClientId":"DEMO_ACCOUNT","orderId":"123456789",
           "correlationId":"DEMO_OCO_ONLY","securityId":"111",
           "transactionType":"BUY","exchangeSegment":"NSE_EQ","productType":"CNC",
           "afterMarketOrder":True,"orderStatus":"TRADED","quantity":quantity,
           "filledQty":quantity,"averageTradedPrice":execution_price}
    result=oco.build_oco_price_plan(intent=own,order=order,
             stop_loss_pct=sl,target_pct=tp,tick_size=tick)
    result["EXAMPLE_DATA_ONLY"]=True
    result["DHAN_BROKER_CONTACTED"]=False
    return result


def main():
    p=argparse.ArgumentParser(
        description="ResultBot: OCO price preview from verified BUY executed VWAP; NEVER places OCO")
    selector=p.add_mutually_exclusive_group(required=True)
    selector.add_argument("--intent-id",type=int,help="ResultBot intent ID, uses Dhan GET only")
    selector.add_argument("--example-price",type=str,help="Synthetic fill price, no Dhan")
    p.add_argument("--example-qty",type=int,default=5)
    p.add_argument("--tick",default="0.05",help="Synthetic example tick, not used for live broker reads")
    p.add_argument("--sl-pct",type=str,default="3",help="Synthetic example SL percentage")
    p.add_argument("--target-pct",type=str,default="6",help="Synthetic example target percentage")
    args=p.parse_args()
    try:
        if args.intent_id is not None:
            data=preview_confirmed_intent(args.intent_id)
        else:
            data=example(args.example_price,args.example_qty,args.tick,args.sl_pct,args.target_pct)
    except (oco.OcoPlanError,live.LiveAmoError,live.dh.DhanReadError,
            ValueError,KeyError,ImportError,OSError) as exc:
        print("OCO PREVIEW BLOCKED: "+str(exc),file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(data,indent=2))


if __name__=="__main__":
    main()
