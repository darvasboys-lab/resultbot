"""Pure interactive strategy wizard state. No broker, filesystem or network access."""
from __future__ import annotations

DOWN = {"CUT": ("cut_down", "SELL"), "AVERAGE": ("average_down", "BUY"),
        "STOP_ONLY": (None, "HOLD")}
UP = {"TAKE_PROFIT": ("take_profit", "SELL"), "ADD_UP": ("add_up", "BUY"),
      "TARGET_ONLY": (None, "HOLD")}


def levels_of(plan, side, mode):
    key, action=(DOWN if side=="down" else UP)[mode]
    if key is None:
        return []
    if plan.get("down_mode" if side=="down" else "up_mode") != mode:
        return []
    data=plan.get(key, [])
    values=plan.get(key+"_amounts_inr", [])
    result=[]
    previous=0
    for i, level in enumerate(data):
        if action=="BUY":
            result.append({"trigger_pct":level,
                           "buy_inr":int(values[i]) if i<len(values) else 500})
        else:
            cumulative=level[1]
            result.append({"trigger_pct":level[0],
                           "sell_pct":round(cumulative-previous, 6)})
            previous=cumulative
    return result


def question(kind, prompt, hint, default, index=None):
    return {"kind":kind, "prompt":prompt, "hint":hint,
            "default":str(default), "index":index}


def begin(plan, side, mode):
    choices=DOWN if side=="down" else UP
    if mode not in choices:
        raise ValueError("Unknown strategy")
    terminal_key="stop_pct" if side=="down" else "target_pct"
    exit_label="FINAL STOP LOSS" if side=="down" else "FINAL TARGET"
    direction="FALL" if side=="down" else "RISE"
    current=levels_of(plan,side,mode)
    terminal=plan.get(terminal_key, 9 if side=="down" else 5)
    if choices[mode][0] is None:
        questions=[question("exit",exit_label+" — what % from FIRST buy?",
                            "Used for full exit of remaining shares",terminal)]
    else:
        questions=[question("count","How many "+direction+" stages? (0–5)",
                            "Each stage will ask PRICE % and amount or shares to sell",
                            len(current) if current else 1)]
    return {"side":side,"mode":mode,"prior":current,"direction":direction,
            "terminal":terminal,"questions":questions,"position":0,
            "stages":[],"terminal_pct":None,"input":questions[0]["default"]}


def _clean_number(raw, kind):
    value=str(raw).strip()
    if not value:
        raise ValueError("Please enter a number")
    if kind in ("count", "buy"):
        if not value.isascii() or not value.isdecimal():
            raise ValueError("Enter a whole number")
        n=int(value)
        if kind=="count" and not 0<=n<=5:
            raise ValueError("Stage count must be 0 to 5")
        if kind=="buy" and n<=0:
            raise ValueError("Amount must be greater than zero")
        return n
    try: n=float(value)
    except ValueError: raise ValueError("Enter a valid number") from None
    import math
    if not math.isfinite(n) or n<=0:
        raise ValueError("Percentage must be positive")
    if kind=="exit":
        if n>=50: # caller adjusts for rising target
            pass
    if n>=100:
        raise ValueError("Percentage must be below 100")
    return n


def _expand(wizard, count):
    side,mode=wizard["side"],wizard["mode"]
    buying=(DOWN if side=="down" else UP)[mode][1]=="BUY"
    label="FALL" if side=="down" else "RISE"
    old=wizard["prior"]
    steps=[]
    for i in range(count):
        saved=old[i] if i<len(old) else {}
        trigger=saved.get("trigger_pct", (i+1)*2)
        steps.append(question("trigger",
            "Stage {}: at what % {} from FIRST buy?".format(i+1,label),
            "Stage percentages must increase",trigger,i))
        if buying:
            steps.append(question("buy",
                "Stage {}: how much extra to BUY (₹)?".format(i+1),
                "Whole-rupee investment for this stage",saved.get("buy_inr",500),i))
        else:
            steps.append(question("sell",
                "Stage {}: sell what % of ORIGINAL shares?".format(i+1),
                "Percent sold AT THIS STAGE (not cumulative)",
                saved.get("sell_pct",25),i))
    exit_label="STOP LOSS" if side=="down" else "TARGET"
    steps.append(question("exit",
        "FINAL {}: at what % {} from FIRST buy?".format(exit_label,label),
        "Final full exit must be beyond the last stage",
        wizard["terminal"]))
    wizard["questions"].extend(steps)


def advance(wizard,raw):
    """Validate one answer, update the in-memory draft, then advance.

    Returns True only when all questions are complete; raises on bad input.
    """
    q=wizard["questions"][wizard["position"]]
    kind=q["kind"]
    n=_clean_number(raw, kind)
    if kind=="count":
        _expand(wizard,n)
    elif kind in ("trigger", "buy", "sell"):
        index=q["index"]
        while len(wizard["stages"])<=index:
            wizard["stages"].append({})
        entry=wizard["stages"][index]
        if kind=="trigger":
            previous=wizard["stages"][index-1]["trigger_pct"] if index else 0
            if n<=previous:
                raise ValueError("Trigger must be greater than the previous stage")
            entry["trigger_pct"]=n
        elif kind=="buy":
            if n>2**63-1:
                raise ValueError("Amount is too large to store")
            entry["buy_inr"]=n
        elif kind=="sell":
            sold=sum(row.get("sell_pct",0) for row in wizard["stages"][:index])
            if sold+n>=100-1e-9:
                raise ValueError("Partial sells must leave shares for final exit")
            entry["sell_pct"]=n
    else:
        last=wizard["stages"][-1]["trigger_pct"] if wizard["stages"] else 0
        if n<=last:
            raise ValueError("Final stop/target must be beyond every stage")
        if wizard["side"]=="down" and n>=50:
            raise ValueError("Final stop must be below 50%")
        wizard["terminal_pct"]=n
    wizard["position"]+=1
    if wizard["position"]<len(wizard["questions"]):
        wizard["input"]=wizard["questions"][wizard["position"]]["default"]
    else:
        wizard["input"]=""
    return wizard["position"]>=len(wizard["questions"])


def request(wizard):
    if wizard["position"]!=len(wizard["questions"]) or wizard["terminal_pct"] is None:
        raise ValueError("Plan not complete")
    return {"side":wizard["side"],"mode":wizard["mode"],
            "stages":wizard["stages"],"terminal_pct":wizard["terminal_pct"]}


def stage_summary(wizard):
    side,mode=wizard["side"],wizard["mode"]
    key,action=(DOWN if side=="down" else UP)[mode]
    sign="-" if side=="down" else "+"
    rows=[]
    for i,item in enumerate(wizard["stages"],1):
        if action=="BUY":
            detail="Buy ₹{:,.0f}".format(item["buy_inr"])
        else:
            detail="Sell {:g}% of original".format(item["sell_pct"])
        rows.append(("S"+str(i),"{}{:g}%".format(sign,item["trigger_pct"]),detail))
    exit_name="FULL STOP" if side=="down" else "FULL TARGET"
    rows.append(("END",exit_name,"{}{:g}%: sell remainder".format(sign,wizard["terminal_pct"])))
    return rows


def saved_summary(plan, side):
    mode=plan.get("down_mode" if side=="down" else "up_mode","?")
    sign="-" if side=="down" else "+"
    if mode not in (DOWN if side=="down" else UP):
        return "Unavailable"
    existing=levels_of(plan,side,mode)
    items=[]
    for stage in existing:
        if "buy_inr" in stage:
            val="₹{:,.0f}".format(stage["buy_inr"])
        else:
            val="{:g}% shares".format(stage["sell_pct"])
        items.append("{}{:g}% {}".format(sign,stage["trigger_pct"],val))
    return " | ".join(items) if items else "No intermediate stages"
