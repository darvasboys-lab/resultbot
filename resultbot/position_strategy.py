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


def selection_json():
    """Return persisted staged position plan. Never present it as LIVE."""
    d=load()
    return {
        "down_mode":d["down_mode"],
        "up_mode":d["up_mode"],
        "stop_pct":d["stop_pct"],
        "target_pct":d["target_pct"],
        "execution_enabled":False,
        "execution_status":"TEST_ONLY_NOT_CONNECTED_TO_DHAN",
        "live_amo_strategy":"ENTRY_ONLY",
        "cut_down":d["cut_down"], "average_down":d["average_down"],
        "average_down_amounts_inr":d["average_down_amounts_inr"],
        "take_profit":d["take_profit"], "add_up":d["add_up"],
        "add_up_amounts_inr":d["add_up_amounts_inr"],
        "initial_budget_inr":d["initial_budget_inr"],
        "total_buy_cap_inr":d["total_buy_cap_inr"]
    }



def _stage_number(value, label, maximum=None):
    """Convert numbers from a UI draft; reject NaN, bool and empty entries."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + " must be a number")
    number=float(value)
    if not math.isfinite(number) or number<=0:
        raise ValueError(label + " must be positive and finite")
    if maximum is not None and number>maximum:
        raise ValueError(label + " is outside the supported percentage range")
    return number


def apply_side_configuration(request):
    """Atomically save one fully reviewed side of a TEST-only position plan.

    The UI never partially saves an unfinished stage. All stages refer to the
    FIRST fill price; each sale fraction refers to ORIGINAL filled shares.
    """
    if not isinstance(request, dict) or set(request) != {
            "side", "mode", "stages", "terminal_pct"}:
        raise ValueError("Invalid strategy configuration request")
    side=request["side"]
    mode=request["mode"]
    stages=request["stages"]
    if side not in ("down", "up"):
        raise ValueError("Choose falling or rising price")
    allowed_down={"CUT":"cut_down","AVERAGE":"average_down","STOP_ONLY":None}
    allowed_up={"TAKE_PROFIT":"take_profit","ADD_UP":"add_up","TARGET_ONLY":None}
    allowed=allowed_down if side=="down" else allowed_up
    if not isinstance(mode, str) or mode not in allowed:
        raise ValueError("Unknown strategy mode")
    if not isinstance(stages, list) or len(stages)>5:
        raise ValueError("Choose 0 to 5 price stages")
    if allowed[mode] is None and stages:
        raise ValueError("Hold-only mode does not accept intermediate stages")
    terminal=_stage_number(request["terminal_pct"], "Final stop/target", 100)
    if side=="down" and terminal>=50:
        raise ValueError("Final stop must be below 50%")
    d=load()
    previous_trigger=0.0
    cumulative_sold=0.0
    triggers=[]
    amounts=[]
    for i, stage in enumerate(stages,1):
        if not isinstance(stage, dict) or set(stage)!={"trigger_pct", ("buy_inr" if mode in ("AVERAGE","ADD_UP") else "sell_pct")}:
            raise ValueError("Invalid fields in stage "+str(i))
        trigger=_stage_number(stage["trigger_pct"], "Stage "+str(i)+" trigger", 100)
        if trigger <= previous_trigger:
            raise ValueError("Stage percentages must increase")
        if trigger >= terminal:
            raise ValueError("Final stop/target must be beyond every stage")
        previous_trigger=trigger
        if mode in ("AVERAGE", "ADD_UP"):
            amount=stage["buy_inr"]
            if type(amount) is not int or not (0<amount<=2**63-1):
                raise ValueError("Buy amount must be a positive whole-rupee number")
            amounts.append(amount)
            triggers.append(trigger)
        else:
            percent=_stage_number(stage["sell_pct"], "Stage "+str(i)+" sale %", 100)
            cumulative_sold+=percent
            if cumulative_sold >=100-1e-9:
                raise ValueError("Partial exits must leave shares for the final stop/target")
            triggers.append([trigger, round(cumulative_sold, 6)])
    mode_field = "down_mode" if side=="down" else "up_mode"
    terminal_field = "stop_pct" if side=="down" else "target_pct"
    d[mode_field]=mode
    d[terminal_field]=terminal
    active=allowed[mode]
    if active:
        d[active]=triggers
        if active in ("average_down","add_up"):
            d[active+"_amounts_inr"]=amounts

    # This is an informational planned-buy envelope, not a user budget cap.
    planned=d["initial_budget_inr"]
    if d["down_mode"]=="AVERAGE":
        planned+=sum(d["average_down_amounts_inr"])
    if d["up_mode"]=="ADD_UP":
        planned+=sum(d["add_up_amounts_inr"])
    d["total_buy_cap_inr"]=max(d["total_buy_cap_inr"],planned)
    d["execution_enabled"]=False
    valid(d)

    import tempfile
    FILE.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    FILE.parent.chmod(0o700)
    fd,tmp=tempfile.mkstemp(prefix=".strategy-reviewed-",suffix=".json",dir=FILE.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(d,stream,indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return selection_json()




def set_oco_percentages(stop_pct, target_pct):
    """Save only final SL/target percentages; does not touch broker orders.

    The reference price is not stored here: it is obtained later from Dhan's
    confirmed averageTradedPrice for the original equity BUY order.
    """
    d=load()
    for key, val in (("stop_pct",stop_pct),("target_pct",target_pct)):
        if isinstance(val,bool):
            raise ValueError("Invalid "+key)
        try: n=float(val)
        except (TypeError,ValueError):
            raise ValueError("Invalid "+key) from None
        if not math.isfinite(n) or n<=0:
            raise ValueError(key+" must be positive and finite")
        d[key]=n
    d["execution_enabled"]=False
    valid(d)
    import tempfile
    FILE.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    FILE.parent.chmod(0o700)
    fd,tmp=tempfile.mkstemp(prefix=".oco-settings-",suffix=".json",dir=FILE.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(d,stream,indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return selection_json()

def choose_modes(down=None, up=None):
    """Update just the selected modes; never enable broker execution."""
    d=load()
    if down is None and up is None:
        raise ValueError("Choose at least one side")
    if down is not None:
        if down not in ("CUT","AVERAGE","STOP_ONLY"):
            raise ValueError("Unknown downside strategy")
        if d["down_mode"] != down:
            d["down_mode"]=down
            key={"CUT":"cut_down","AVERAGE":"average_down","STOP_ONLY":None}[down]
            if key:
                d[key]=deepcopy(DEFAULT[key])
                if key=="average_down":
                    d["average_down_amounts_inr"]=deepcopy(DEFAULT["average_down_amounts_inr"])
    if up is not None:
        if up not in ("TAKE_PROFIT","ADD_UP","TARGET_ONLY"):
            raise ValueError("Unknown upside strategy")
        if d["up_mode"] != up:
            d["up_mode"]=up
            key={"TAKE_PROFIT":"take_profit","ADD_UP":"add_up","TARGET_ONLY":None}[up]
            if key:
                d[key]=deepcopy(DEFAULT[key])
                if key=="add_up":
                    d["add_up_amounts_inr"]=deepcopy(DEFAULT["add_up_amounts_inr"])
    d["execution_enabled"]=False
    valid(d)
    FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    FILE.parent.chmod(0o700)
    import tempfile
    fd, name=tempfile.mkstemp(prefix=".strategy-",suffix=".json",dir=FILE.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(d,f,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name,0o600)
        os.replace(name,FILE)
    finally:
        if os.path.exists(name): os.unlink(name)
    return selection_json()


if __name__=='__main__':
    command=sys.argv[1] if len(sys.argv)>1 else 'show'
    if command=='self-test':test()
    elif command=='settings-json':
        print(json.dumps(selection_json(),indent=2))
    elif command=='set-oco':
        import argparse
        p=argparse.ArgumentParser(description="Save final OCO SL/target as TEST-only strategy configuration")
        p.add_argument("--sl-pct",type=float,required=True)
        p.add_argument("--target-pct",type=float,required=True)
        args=p.parse_args(sys.argv[2:])
        try: print(json.dumps(set_oco_percentages(args.sl_pct,args.target_pct),indent=2))
        except ValueError as exc:
            print("OCO SETTINGS NOT SAVED: "+str(exc),file=sys.stderr)
            raise SystemExit(2) from None
    elif command=='apply-side-json':
        if len(sys.argv)!=3:
            raise SystemExit("Usage: apply-side-json '<JSON>'")
        try:
            print(json.dumps(apply_side_configuration(json.loads(sys.argv[2])),indent=2))
        except (ValueError,KeyError,TypeError) as exc:
            print("STRATEGY NOT SAVED: "+str(exc),file=sys.stderr)
            raise SystemExit(2) from None
    elif command=='choose':
        import argparse
        p=argparse.ArgumentParser(description="Save TEST-ONLY staged position modes")
        p.add_argument("--down",choices=["CUT","AVERAGE","STOP_ONLY"])
        p.add_argument("--up",choices=["TAKE_PROFIT","ADD_UP","TARGET_ONLY"])
        opt=p.parse_args(sys.argv[2:])
        try:
            print(json.dumps(choose_modes(opt.down,opt.up),indent=2))
        except (ValueError, KeyError) as exc:
            print("STRATEGY NOT SAVED: "+str(exc),file=sys.stderr)
            raise SystemExit(2) from None
    elif command=='easy':easy(load())
    elif command in ('show','configure'):
        d=load()
        if command=='configure':d=edits(d)
        first=float(sys.argv[2]) if len(sys.argv)>2 and command=='show' else 100
        if not math.isfinite(first) or first<=0:raise SystemExit('First fill must be positive')
        show(d,first)
    else:raise SystemExit('Usage: resultbot strategy [easy | show [first_fill_price] | configure | settings-json | apply-side-json JSON | choose --down CODE | choose --up CODE | self-test]')
