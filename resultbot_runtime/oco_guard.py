#!/usr/bin/env python3
"""ResultBot broker-hosted OCO protection. No BUY orders; durable no-retry submissions."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path.home()/'uditbot'))
import equity_amo_live as live
import dhan_readonly as dh
ROOT=Path.home()/'resultbot-data'
DB=ROOT/'oco_guard.sqlite3'
CONFIG=ROOT/'oco_guard.json'
LOG=logging.getLogger('resultbot.oco')
ACTIVE={'PENDING','TRANSIT','CONFIRM'}
DEAD={'TRADED','TRIGGERED','CANCELLED','EXPIRED','REJECTED','CLOSED'}

class GuardError(ValueError): pass

def number(value):
    if isinstance(value,bool): raise GuardError('Invalid numeric broker field')
    try: n=Decimal(str(value))
    except Exception: raise GuardError('Missing numeric broker field') from None
    if not n.is_finite(): raise GuardError('Non-finite numeric broker field')
    return n

def whole(value):
    n=number(value)
    if n<0 or n!=n.to_integral_value(): raise GuardError('Invalid share quantity')
    return int(n)

def api(method,path,token,payload=None):
    allowed={'GET':{'/orders','/trades','/positions','/forever/orders'},'POST':{'/forever/orders'}}
    if path not in allowed.get(method,set()): raise GuardError('Unsupported OCO endpoint')
    req=Request('https://api.dhan.co/v2'+path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,headers={'access-token':token,'Content-Type':'application/json','Accept':'application/json'})
    # POST never retries. Do not log responses/URLs containing credentials.
    try:
        with urlopen(req,timeout=8) as r: result=json.load(r)
    except HTTPError as e:
        raise GuardError('Dhan OCO HTTP '+str(e.code)) from None
    except Exception:
        raise GuardError('Dhan OCO request failed; no automatic POST retry') from None
    if method=='GET' and (not isinstance(result,list) or any(not isinstance(r,dict) for r in result)):
        raise GuardError('Unexpected '+path+' response; cannot verify coverage')
    if method=='POST' and not isinstance(result,dict): raise GuardError('Uncertain OCO response')
    return result

@contextmanager
def db():
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    if DB.is_symlink(): raise GuardError('Unexpected OCO state path')
    c=sqlite3.connect(DB,timeout=15,isolation_level=None); DB.chmod(0o600);c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS coverage(
        id INTEGER PRIMARY KEY, intent_id INTEGER NOT NULL, filled_through INTEGER NOT NULL,
        payload TEXT NOT NULL, anchor TEXT NOT NULL, tick TEXT NOT NULL,
        state TEXT NOT NULL, order_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        UNIQUE(intent_id,filled_through))''')
    c.execute("CREATE TABLE IF NOT EXISTS problems(intent_id INTEGER PRIMARY KEY, message TEXT NOT NULL)")
    try: yield c
    finally: c.close()

@contextmanager
def lock():
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (ROOT/'.oco_guard.lock').open('a') as f:
        import os
        os.fchmod(f.fileno(),0o600)
        fcntl.flock(f,fcntl.LOCK_EX)
        yield

def settings():
    if not CONFIG.exists(): return {'enabled':False}
    return json.loads(CONFIG.read_text())

def strategy():
    p=ROOT/'position_strategy.json'
    if not p.is_file(): raise GuardError('Saved strategy missing; defaults never authorize protection')
    s=json.loads(p.read_text())
    if s.get('down_mode')!='STOP_ONLY' or s.get('up_mode')!='TARGET_ONLY':
        raise GuardError('Only saved STOP_ONLY / TARGET_ONLY supported; staged strategy requires separate integration')
    sl,tp=number(s.get('stop_pct')),number(s.get('target_pct'))
    if not 0<sl<100 or tp<=0: raise GuardError('Invalid saved stop/target percentages')
    return sl,tp

def plan(item,anchor,quantity,tick,sl,tp,through):
    anchor,tick=number(anchor),number(tick)
    if anchor<=0 or not Decimal('.01')<=tick<=1 or quantity<=0: raise GuardError('Invalid fill/tick')
    stop=(anchor*(1-sl/100)/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
    target=(anchor*(1+tp/100)/tick).to_integral_value(rounding=ROUND_CEILING)*tick
    limit=stop-tick
    if not 0<limit<stop<anchor<target: raise GuardError('Invalid protective levels after tick rounding')
    tag='RBO-'+hashlib.sha256(f"{item['account']}:{item['order_id']}:{through}".encode()).hexdigest()[:24]
    return dict(dhanClientId=item['account'],correlationId=tag,orderFlag='OCO',transactionType='SELL',
        exchangeSegment='NSE_EQ',productType='CNC',orderType='LIMIT',validity='DAY',securityId=item['security_id'],
        quantity=quantity,quantity1=quantity,disclosedQuantity=0,price=float(limit),triggerPrice=float(stop),
        price1=float(target),triggerPrice1=float(target))

def verified_fill(item,orders,trades):
    matches=[r for r in orders if str(r.get('orderId'))==item['order_id']]
    if len(matches)!=1: raise GuardError('Owned BUY not uniquely present in today order book')
    r=matches[0]
    expected={'dhanClientId':item['account'],'correlationId':item['correlation'],'securityId':item['security_id'],
              'transactionType':'BUY','exchangeSegment':'NSE_EQ','productType':'CNC'}
    if any(str(r.get(k))!=str(v) for k,v in expected.items()): raise GuardError('BUY order binding mismatch')
    if r.get('afterMarketOrder') is not (item.get('entry_route','AMO')=='AMO'): raise GuardError('BUY route mismatch')
    qty=whole(r.get('filledQty')); requested=whole(r.get('quantity'))
    if not 0<qty<=requested or requested!=item['qty']: raise GuardError('No valid confirmed filled quantity')
    fills=[t for t in trades if str(t.get('orderId'))==item['order_id']]
    total=0; value=Decimal(0)
    for t in fills:
        for k in ('dhanClientId','securityId','transactionType','exchangeSegment','productType'):
            if str(t.get(k))!=str(expected[k]): raise GuardError('Trade binding mismatch')
        q=whole(t.get('tradedQuantity')); price=number(t.get('tradedPrice'))
        if q<=0 or price<=0: raise GuardError('Invalid broker execution')
        total+=q; value+=q*price
    if total!=qty: raise GuardError('Order/trade filled quantities not yet consistent')
    avg=value/qty
    if abs(avg-number(r.get('averageTradedPrice'))) > Decimal('.01'): raise GuardError('Order/trade prices disagree')
    return qty,avg

def owned_exposure(item,positions):
    found=[p for p in positions if str(p.get('securityId'))==item['security_id']
           and p.get('exchangeSegment')=='NSE_EQ' and p.get('productType')=='CNC']
    if len(found)!=1 or str(found[0].get('dhanClientId'))!=item['account']:
        raise GuardError('Current CNC position not uniquely verified')
    return whole(found[0].get('netQty'))

def record_state(c,ident,state,note,oid=None):
    if oid is None: c.execute('UPDATE coverage SET state=?,note=? WHERE id=?',(state,note,ident))
    else: c.execute('UPDATE coverage SET state=?,note=?,order_id=? WHERE id=?',(state,note,oid,ident))
    LOG.info('OCO record=%s state=%s %s',ident,state,note)

def verify_oco(payload,oid,rows,tick):
    rows=[r for r in rows if str(r.get('orderId'))==oid]
    if not rows: return False
    base={'dhanClientId':payload['dhanClientId'],'securityId':payload['securityId'],
          'exchangeSegment':'NSE_EQ','productType':'CNC','transactionType':'SELL'}
    for r in rows:
        if any(str(r.get(k))!=str(v) for k,v in base.items()): raise GuardError('OCO ownership/route mismatch')
    # Existing repository contract understands aggregate and two-leg broker books.
    from src.broker.dhan_client import verify_forever_oco_group
    result=verify_forever_oco_group(rows,oid,expected_security_id=payload['securityId'],
        expected_qty=payload['quantity'],expected_sl_trigger=payload['triggerPrice'],
        expected_target_price=payload['price1'],tick_size=float(tick),
        expected_account_id=payload['dhanClientId'],expected_segment='NSE_EQ',expected_product_type='CNC')
    return result.is_verified

def reconcile(c,row,forever):
    p=json.loads(row['payload']);oid=row['order_id']
    if not oid:
        ids={str(r.get('orderId')) for r in forever if r.get('correlationId')==p['correlationId']}
        if len(ids)!=1: return False
        oid=ids.pop()
    if not re.fullmatch(r'[0-9]{6,25}',oid): return False
    matching=[r for r in forever if str(r.get('orderId'))==oid]
    if verify_oco(p,oid,forever,row['tick']):
        if row['state']!='ACTIVE': record_state(c,row['id'],'ACTIVE','Both SELL legs broker-verified',oid)
        return True
    if matching and any(str(r.get('orderStatus','')).upper() in DEAD for r in matching):
        record_state(c,row['id'],'REVIEW','OCO triggered/ended: inspect linked exit; never recreate automatically',oid)
    elif row['state']=='ACTIVE':
        record_state(c,row['id'],'UNKNOWN','OCO protection no longer verified; inspect Dhan',oid)
    return False

def process(item,orders,trades,positions,forever,token):
    qty,avg=verified_fill(item,orders,trades)
    with db() as c:
        previous=list(c.execute('SELECT * FROM coverage WHERE intent_id=? ORDER BY id',(item['id'],)))
        for row in previous:
            if row['state']!='REVIEW': reconcile(c,row,forever)
        previous=list(c.execute('SELECT * FROM coverage WHERE intent_id=? ORDER BY id',(item['id'],)))
        if any(r['state']!='ACTIVE' for r in previous): return  # never duplicate an uncertain POST
        through=max((r['filled_through'] for r in previous),default=0)
        if qty<=through: return
        net=owned_exposure(item,positions)
        # Avoid overlapping ordinary SELL orders or foreign/unverified OCOs.
        for o in orders:
            if str(o.get('securityId'))==item['security_id'] and o.get('transactionType')=='SELL' and o.get('orderStatus') not in {'TRADED','CANCELLED','REJECTED','EXPIRED'}:
                raise GuardError('Existing pending SELL for this security; reconcile before additional OCO')
        known={r['order_id']:json.loads(r['payload']) for r in c.execute("SELECT * FROM coverage WHERE state='ACTIVE'")}
        seen=set(); reserved=0
        for f in forever:
            if str(f.get('securityId'))!=item['security_id'] or f.get('transactionType')!='SELL': continue
            if f.get('orderStatus') in DEAD: continue
            oid=str(f.get('orderId'))
            if oid not in known: raise GuardError('Existing unmanaged OCO/SELL; no overlapping OCO submitted')
            if oid not in seen: reserved+=known[oid]['quantity'];seen.add(oid)
        uncertain=list(c.execute("SELECT payload FROM coverage WHERE state!='ACTIVE'"))
        if any(json.loads(r[0])['securityId']==item['security_id'] for r in uncertain):
            raise GuardError('Unresolved protection for same security')
        delta=qty-through
        if net<reserved+delta: raise GuardError('Insufficient confirmed open quantity for OCO')
        if previous:
            anchor=number(previous[0]['anchor']); tick=number(previous[0]['tick'])
            old=json.loads(previous[0]['payload'])
            p=dict(old,quantity=delta,quantity1=delta)
            p['correlationId']='RBO-'+hashlib.sha256(f"{item['account']}:{item['order_id']}:{qty}".encode()).hexdigest()[:24]
        else:
            sec,tick=live._verified_instrument(item['symbol'])
            if sec!=item['security_id']: raise GuardError('Instrument master mismatch')
            anchor=avg; sl,tp=strategy();p=plan(item,anchor,delta,tick,sl,tp,qty)
        c.execute('BEGIN IMMEDIATE')
        cur=c.execute("INSERT OR IGNORE INTO coverage(intent_id,filled_through,payload,anchor,tick,state) VALUES(?,?,?,?,?,'SUBMITTING')",
                      (item['id'],qty,json.dumps(p),str(anchor),str(tick)))
        c.execute('COMMIT')
        if not cur.rowcount:return
        ident=cur.lastrowid
        try:
            answer=api('POST','/forever/orders',token,p)
            oid=str(answer.get('orderId') or '')
            if not re.fullmatch(r'[0-9]{6,25}',oid): raise GuardError('No verifiable OCO ID returned')
            record_state(c,ident,'UNKNOWN','OCO ID received; awaiting both-leg verification',oid)
            latest=api('GET','/forever/orders',token)
            row=c.execute('SELECT * FROM coverage WHERE id=?',(ident,)).fetchone()
            reconcile(c,row,latest)
        except Exception as exc:
            record_state(c,ident,'UNKNOWN','Submission/verification uncertain; no automatic retry: '+type(exc).__name__)


def cycle():
    cfg=settings()
    if not cfg.get('enabled'): return
    with lock():
        account,token=dh.load_credentials()
        if account!=cfg.get('account'): raise GuardError('OCO configured account changed')
        orders=api('GET','/orders',token)
        with live.managed_db() as c:
            c.row_factory=sqlite3.Row
            items=[dict(r) for r in c.execute("SELECT * FROM intents WHERE order_id!=''")
                   if r['account']==account and (r['created_ts']>=cfg['since'] or r['id'] in cfg['include_intents'])]
        candidates=[i for i in items if any(str(o.get('orderId'))==i['order_id'] and number(o.get('filledQty',0))>0 for o in orders)]
        with db() as c: records=list(c.execute('SELECT * FROM coverage'))
        if not candidates and not records:return
        forever=api('GET','/forever/orders',token)
        with db() as c:
            for r in records:
                if r['state']!='REVIEW': reconcile(c,r,forever)
        if not candidates:return
        trades=api('GET','/trades',token);positions=api('GET','/positions',token)
        for item in candidates:
            try:
                process(item,orders,trades,positions,forever,token)
                with db() as c: c.execute("DELETE FROM problems WHERE intent_id=?",(item['id'],))
                # Refresh before another symbol/intent to include newly created SELL coverage.
                forever=api('GET','/forever/orders',token)
            except (GuardError,dh.DhanReadError,live.LiveAmoError,ValueError,OSError) as exc:
                LOG.error('OCO NOT CONFIRMED intent=%s %s',item['id'],str(exc))
                with db() as c:
                    c.execute("INSERT OR REPLACE INTO problems VALUES(?,?)",(item['id'],str(exc)))


def main():
    import os
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    p=argparse.ArgumentParser();p.add_argument('command',choices=['enable','status','run','once'])
    p.add_argument('--include-intent',type=int,action='append',default=[]);a=p.parse_args()
    if a.command=='enable':
        strategy(); account,token=dh.load_credentials();dh.verified_profile(account,token)
        with lock():
            old=settings()
            if old.get('account') not in (None,account): raise GuardError('Existing OCO state belongs to another account')
            cfg={'enabled':True,'account':account,'since':old.get('since',int(time.time())),
                 'include_intents':sorted(set(old.get('include_intents',[])+a.include_intent))}
            tmp=CONFIG.with_suffix('.tmp');tmp.write_text(json.dumps(cfg));tmp.chmod(0o600);tmp.replace(CONFIG)
        print('Automatic OCO enabled using saved STOP_ONLY / TARGET_ONLY strategy; awaiting broker verification.')
    elif a.command=='status':
        with db() as c:
            rows=[]
            for r in c.execute('SELECT * FROM coverage'):
                payload=json.loads(r['payload'])
                rows.append(dict(intent_id=r['intent_id'],state=r['state'],oco_order_id=r['order_id'],
                    quantity=payload['quantity'],fill_anchor=r['anchor'],sl_trigger=payload['triggerPrice'],
                    sl_limit=payload['price'],target=payload['price1'],note=r['note']))
            print(json.dumps({'enabled':settings().get('enabled',False),'protection':rows,
                'unprotected_errors':[dict(r) for r in c.execute('SELECT * FROM problems')]},indent=2))
    elif a.command=='once': cycle()
    else:
        while True:
            try: cycle()
            except Exception as exc: LOG.error('OCO scan failed: %s',type(exc).__name__)
            time.sleep(2)

if __name__=='__main__': main()
