#!/usr/bin/env python3
"""ResultBot standalone TEST-only strategy configuration: NO broker integration."""
import json, math, os, sys
from copy import deepcopy
from pathlib import Path

FILE = Path.home()/"resultbot-data"/"position_strategy.json"
DEFAULT = {
    "down_mode":"CUT",             # CUT, AVERAGE, STOP_ONLY
    "up_mode":"TAKE_PROFIT",       # TAKE_PROFIT, ADD_UP, TARGET_ONLY
    "cut_down":[[3,50],[5,75]],    # drop%, cumulative original quantity sold%
    "average_down":[3,6],         # drop% for each buy
    "take_profit":[[2,50],[4,75]], # rise%, cumulative original quantity sold%
    "add_up":[2,4],                # rise% for each buy
    "average_down_amounts_inr":[500,500],  # budget per averaging stage
    "add_up_amounts_inr":[500,500],        # budget per add-up stage
    "stop_pct":9, "target_pct":5, # absolute first-fill anchors
    "initial_budget_inr":500, "extra_budget_inr":500,
    "total_buy_cap_inr":2500, "execution_enabled":False
}

def valid(d):
    if d["down_mode"] not in ("CUT","AVERAGE","STOP_ONLY") or d["up_mode"] not in ("TAKE_PROFIT","ADD_UP","TARGET_ONLY"):
        raise ValueError("Unknown position-management mode")
    for k in ("stop_pct","target_pct","initial_budget_inr","extra_budget_inr","total_buy_cap_inr"):
        if not isinstance(d[k],(int,float)) or isinstance(d[k],bool) or not math.isfinite(d[k]) or d[k]<=0:
            raise ValueError(k+" must be a positive finite number")
    if d["stop_pct"]>=50 or d["target_pct"]>100 or d["initial_budget_inr"]>d["total_buy_cap_inr"]:
        raise ValueError("Invalid price exits or budget cap")
    for key in ("average_down","add_up"):
        xs=d[key]
        if len(xs)>5 or any(not isinstance(x,(int,float)) or not 0<x<100 for x in xs) or sorted(set(xs))!=xs:
            raise ValueError(key+": specify 0-5 ascending distinct percentages")
    for key in ("average_down","add_up"):
        amounts = d[key+"_amounts_inr"]
        if (not isinstance(amounts,list) or len(amounts)!=len(d[key]) or
            any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) or x<=0 for x in amounts)):
            raise ValueError(key+" budgets: one positive INR amount for each price trigger")
    for key in ("cut_down","take_profit"):
        xs=d[key]
        if len(xs)>5 or any(not isinstance(x,list) or len(x)!=2 for x in xs):
            raise ValueError(key+": specify up to 5 trigger:cumulative-exit pairs")
        if not all(0<x[0]<100 and 0<x[1]<100 for x in xs):
            raise ValueError(key+": triggers and exit fractions must be in 0..100")
        if [x[0] for x in xs]!=sorted(set(x[0] for x in xs)) or [x[1] for x in xs]!=sorted(set(x[1] for x in xs)):
            raise ValueError(key+": triggers and cumulative sold percentages must increase")
    down = d["average_down"] if d["down_mode"]=="AVERAGE" else [p for p,q in d["cut_down"]] if d["down_mode"]=="CUT" else []
    up = d["add_up"] if d["up_mode"]=="ADD_UP" else [p for p,q in d["take_profit"]] if d["up_mode"]=="TAKE_PROFIT" else []
    if down and max(down)>=d["stop_pct"]: raise ValueError("Final stop must be below all active downside triggers")
    if up and max(up)>=d["target_pct"]: raise ValueError("Final target must be above all active upside triggers")
    buying=d["initial_budget_inr"]
    if d["down_mode"]=="AVERAGE": buying+=sum(d["average_down_amounts_inr"])
    if d["up_mode"]=="ADD_UP": buying+=sum(d["add_up_amounts_inr"])
    if buying>d["total_buy_cap_inr"]:
        raise ValueError("Total buy cap cannot cover all configured additional buys")

def load():
    d=deepcopy(DEFAULT)
    if FILE.exists():
        saved=json.loads(FILE.read_text())
        d.update({k:v for k,v in saved.items() if k in DEFAULT and k!="execution_enabled"})
    d["execution_enabled"]=False
    valid(d)
    return d

def ask(label, default):
    s=input(f"{label} [{default}]: ").strip()
    return s or str(default)

def edits(d):
    d["down_mode"]=ask("DOWN: CUT / AVERAGE / STOP_ONLY",d["down_mode"]).upper()
    d["up_mode"]=ask("UP: TAKE_PROFIT / ADD_UP / TARGET_ONLY",d["up_mode"]).upper()
    d["stop_pct"]=float(ask("Full exit stop % below FIRST fill",d["stop_pct"]))
    d["target_pct"]=float(ask("Full exit target % above FIRST fill",d["target_pct"]))
    for k in ("initial_budget_inr","extra_budget_inr","total_buy_cap_inr"):
        d[k]=float(ask(k.replace('_',' '),d[k]))
    key={"CUT":"cut_down","AVERAGE":"average_down","STOP_ONLY":None}[d["down_mode"]]
    if key:
        d[key]=read_levels(key,d[key])
        if key=="average_down":
            d["average_down_amounts_inr"]=(d["average_down_amounts_inr"]+[d["extra_budget_inr"]]*len(d[key]))[:len(d[key])]
    key={"TAKE_PROFIT":"take_profit","ADD_UP":"add_up","TARGET_ONLY":None}[d["up_mode"]]
    if key:
        d[key]=read_levels(key,d[key])
        if key=="add_up":
            d["add_up_amounts_inr"]=(d["add_up_amounts_inr"]+[d["extra_budget_inr"]]*len(d[key]))[:len(d[key])]
    d["execution_enabled"]=False
    valid(d)
    FILE.parent.mkdir(parents=True,exist_ok=True)
    FILE.parent.chmod(0o700)
    tmp=FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(d,indent=2)+'\n')
    tmp.chmod(0o600); tmp.replace(FILE)
    print("SAVED:",FILE,"(TEST CONFIG ONLY; NO ORDERS ENABLED)")
    return d

def read_levels(key,default):
    partial = key in ("cut_down","take_profit")
    old=','.join(f'{x}:{y}' for x,y in default) if partial else ','.join(map(str,default))
    s=ask(key+(' drop/rise%:cumulative original qty sold%, comma separated' if partial else ' trigger percentages, comma separated')+' (none for zero)',old or 'none')
    if s.lower() in ('none','off','0'): return []
    if partial: return [[float(a),float(b)] for a,b in (v.split(':') for v in s.split(','))]
    return [float(v) for v in s.split(',')]

def show(d,first):
    valid(d)
    print(f'\nRESULTBOT / EQUITY / EXCELLENT / TEST ONLY | First filled price ₹{first:.2f}')
    print('DOWN:',d['down_mode'],'| UP:',d['up_mode'])
    if d['down_mode']=='CUT':
        for p,q in d['cut_down']: print(f'  DROP -{p:g}% ₹{first*(1-p/100):.2f}: CUT to {q:g}% cumulative original shares sold')
    if d['down_mode']=='AVERAGE':
        for p,amount in zip(d['average_down'],d['average_down_amounts_inr']): print(f'  DROP -{p:g}% ₹{first*(1-p/100):.2f}: BUY extra up to ₹{amount:g}')
    if d['up_mode']=='TAKE_PROFIT':
        for p,q in d['take_profit']: print(f'  RISE +{p:g}% ₹{first*(1+p/100):.2f}: BOOK to {q:g}% cumulative original shares sold')
    if d['up_mode']=='ADD_UP':
        for p,amount in zip(d['add_up'],d['add_up_amounts_inr']): print(f'  RISE +{p:g}% ₹{first*(1+p/100):.2f}: BUY extra up to ₹{amount:g}')
    print(f'  STOP -{d["stop_pct"]:g}% ₹{first*(1-d["stop_pct"]/100):.2f}: close ALL remaining')
    print(f'  TARGET +{d["target_pct"]:g}% ₹{first*(1+d["target_pct"]/100):.2f}: close ALL remaining')
    buying=d["initial_budget_inr"]
    if d["down_mode"]=="AVERAGE": buying+=sum(d["average_down_amounts_inr"])
    if d["up_mode"]=="ADD_UP": buying+=sum(d["add_up_amounts_inr"])
    print(f'Planned maximum buying ₹{buying:g} / Ceiling ₹{d["total_buy_cap_inr"]:g}')
    print('Cumulative exits use ORIGINAL filled share count; never % of remaining shares.')
    print('No live orders, no broker API calls, no Dhan services changed.')

def test():
    c=deepcopy(DEFAULT); valid(c)
    for down in ('CUT','AVERAGE','STOP_ONLY'):
        for up in ('TAKE_PROFIT','ADD_UP','TARGET_ONLY'):
            d=deepcopy(c); d['down_mode']=down; d['up_mode']=up; valid(d)
    for key in ('cut_down','take_profit'):
        d=deepcopy(c);d[key]=[[3,75],[4,50]]
        try:valid(d)
        except ValueError: pass
        else:raise AssertionError('Non-increasing cumulative exits accepted')
    d=deepcopy(c); d["up_mode"]="ADD_UP"; d["add_up_amounts_inr"]=[500,20000]
    try:valid(d)
    except ValueError: pass
    else:raise AssertionError("Stage budget cap bypassed")
    d=deepcopy(c); d["down_mode"]="AVERAGE"; d["average_down_amounts_inr"]=[500]
    try:valid(d)
    except ValueError: pass
    else:raise AssertionError("Stage budget mismatch accepted")
    print('PASS: 9 mode combinations + per-stage budget validation')


# Numbered menu: preserves existing JSON and never enables execution.
def _choice(title, choices, current):
    print("\n"+title)
    for number, (code, description) in enumerate(choices, 1):
        print(f"  {number}. {description}" + ("  [CURRENT]" if code == current else ""))
    default = next(i for i, (code, _) in enumerate(choices, 1) if code == current)
    raw = input(f"Choose 1-{len(choices)} [Enter={default}]: ").strip()
    n = int(raw) if raw else default
    if not 1 <= n <= len(choices):
        raise ValueError("Selection must be a displayed number")
    return choices[n-1][0]

def _yes(question):
    return input(question + " [y/N]: ").strip().lower() in ("y", "yes")

def _number(question, default):
    raw = input(f"{question} [{default:g}]: ").strip()
    return float(raw) if raw else float(default)

def _count(question, default):
    n = int(_number(question, default))
    if not 0 <= n <= 5:
        raise ValueError("Enter a number of stages from 0 to 5")
    return n

def _custom_levels(d, side):
    """Each stage: percentage trigger FIRST, then buy rupees or cumulative sold shares."""
    if side == "down":
        key = {"CUT":"cut_down","AVERAGE":"average_down","STOP_ONLY":None}[d["down_mode"]]
        direction = "FALL"
    else:
        key = {"TAKE_PROFIT":"take_profit","ADD_UP":"add_up","TARGET_ONLY":None}[d["up_mode"]]
        direction = "RISE"
    if key is None:
        print("No intermediate stages on this side.")
        return
    previous = d[key]
    old_amounts = d.get(key+"_amounts_inr",[])
    count = _count(f"How many {direction} stages?", len(previous))
    stages, amounts = [], []
    for i in range(count):
        print(f"\n--- {direction} STAGE {i+1} of {count} ---")
        if i < len(previous):
            old_trigger = previous[i][0] if key in ("cut_down","take_profit") else previous[i]
        elif stages:
            old_trigger = (stages[-1][0] if key in ("cut_down","take_profit") else stages[-1])+2
        else:
            old_trigger = 2
        trigger = _number(f"1. At what % should price {direction.lower()} from FIRST buy?", old_trigger)
        if key in ("cut_down","take_profit"):
            old_fraction = previous[i][1] if i<len(previous) else min(99,50+25*i)
            fraction = _number("2. By now, what TOTAL % of ORIGINAL shares should be sold?", old_fraction)
            stages.append([trigger, fraction])
        else:
            old_amount = old_amounts[i] if i<len(old_amounts) else d["extra_budget_inr"]
            amount = _number("2. How many rupees (INR) to BUY at this stage?", old_amount)
            stages.append(trigger)
            amounts.append(amount)
    d[key] = stages
    if key in ("average_down","add_up"):
        d[key+"_amounts_inr"] = amounts

def easy(d):
    """Human-friendly configuration wizard; config only, not order execution."""
    d = deepcopy(d)
    print("\n====== RESULTBOT: EASY STRATEGY SETUP ======")
    print("Choose a number. Press Enter to keep your current selection.")
    print("No broker orders will be sent.")
    old_down, old_up = d["down_mode"], d["up_mode"]
    d["down_mode"] = _choice("WHEN STOCK PRICE FALLS:", [
        ("CUT","Cut losses gradually: sell part of the position"),
        ("AVERAGE","Average down: buy extra shares"),
        ("STOP_ONLY","Hold: no partial actions; final stop only"),
    ], d["down_mode"])
    d["up_mode"] = _choice("WHEN STOCK PRICE RISES:", [
        ("TAKE_PROFIT","Book profits gradually: sell part of the position"),
        ("ADD_UP","Add up: buy extra shares"),
        ("TARGET_ONLY","Hold: sell everything at final target"),
    ], d["up_mode"])
    presets = {
        "cut_down": [[3,50],[5,75]],
        "average_down": [3,6],
        "take_profit": [[2,50],[4,75]],
        "add_up": [2,4],
    }
    if d["down_mode"] != old_down:
        name = {"CUT":"cut_down","AVERAGE":"average_down","STOP_ONLY":None}[d["down_mode"]]
        if name: d[name] = deepcopy(presets[name])
    if d["up_mode"] != old_up:
        name = {"TAKE_PROFIT":"take_profit","ADD_UP":"add_up","TARGET_ONLY":None}[d["up_mode"]]
        if name: d[name] = deepcopy(presets[name])
    print("\nCURRENT LEVELS (first buy price used as reference):")
    show(d, 100)
    print("\nNOW SET EACH STAGE: first PRICE %, next HOW MUCH.")
    _custom_levels(d,"down")
    _custom_levels(d,"up")
    if _yes("Change final stop-loss or target percentages?"):
        d["stop_pct"] = _number("  Final stop below first buy (%)", d["stop_pct"])
        d["target_pct"] = _number("  Final target above first buy (%)", d["target_pct"])
    if _yes("Change first-buy budget, default NEW-stage buy budget, or total ceiling?"):
        d["initial_budget_inr"] = _number("  First buy budget (INR)", d["initial_budget_inr"])
        d["extra_budget_inr"] = _number("  Each additional buy budget (INR)", d["extra_budget_inr"])
        d["total_buy_cap_inr"] = _number("  Maximum total planned buying (INR)", d["total_buy_cap_inr"])
    d["execution_enabled"] = False
    valid(d)
    print("\n===== REVIEW BEFORE SAVING =====")
    show(d, 100)
    if not _yes("\nSave this TEST-only strategy?"):
        print("Not saved; previous settings retained.")
        return
    FILE.parent.mkdir(parents=True,exist_ok=True)
    FILE.parent.chmod(0o700)
    tmp=FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d,indent=2)+"\n")
    tmp.chmod(0o600)
    tmp.replace(FILE)
    print("\nSAVED:",FILE,"| EXECUTION DISABLED")

if __name__=='__main__':
    command=sys.argv[1] if len(sys.argv)>1 else 'show'
    if command=='self-test':test()
    elif command=='easy':easy(load())
    elif command in ('show','configure'):
        d=load()
        if command=='configure':d=edits(d)
        first=float(sys.argv[2]) if len(sys.argv)>2 and command=='show' else 100
        if not math.isfinite(first) or first<=0:raise SystemExit('First fill must be positive')
        show(d,first)
    else:raise SystemExit('Usage: resultbot strategy [easy | show [first_fill_price] | configure | self-test]')
