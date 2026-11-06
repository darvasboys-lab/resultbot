#!/usr/bin/env python3
"""Private ResultBot Equity AMO owner panel. Requires SSH forwarding."""
from __future__ import annotations
import base64
import hmac
import html
import os
import secrets
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
try:
    from . import equity_amo_live as amo
except ImportError:
    import equity_amo_live as amo

HOST = "127.0.0.1"
PORT = 8093
SECRET = amo.ROOT / ".amo_panel_key"
CSRF = amo.ROOT / ".amo_csrf"

def read_secret(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Unsafe panel secret path")
    if not path.exists():
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w") as file:
            file.write(secrets.token_urlsafe(26))
    if stat.S_IMODE(path.stat().st_mode)!=0o600:
        raise ValueError("Panel secret must have mode 0600")
    return path.read_text().strip()

class Panel(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,status,body):
        raw=body.encode("utf-8")
        self.send_response(status)
        for k,v in {
            "Content-Type":"text/html; charset=utf-8",
            "Content-Length":str(len(raw)),
            "Cache-Control":"no-store",
            "X-Frame-Options":"DENY",
            "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        }.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(raw)
    def authorized(self):
        if self.headers.get("Host") not in (f"localhost:{PORT}",f"127.0.0.1:{PORT}"):
            self.send(403,"Invalid host"); return False
        raw=self.headers.get("Authorization","")
        try:
            prefix,code=raw.split(" ",1)
            credentials=base64.b64decode(code,validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            prefix=""; credentials=""
        if prefix!="Basic" or not hmac.compare_digest(credentials,"resultbot:"+self.server.password):
            self.send_response(401)
            self.send_header("WWW-Authenticate",'Basic realm="ResultBot local AMO control"')
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            return False
        return True
    def do_GET(self):
        if not self.authorized(): return
        if self.path!="/": self.send(404,"Not found"); return
        state=amo.status()
        rows=[]
        for x in state["orders"]:
            oid=html.escape(x["order_id"] or "UNCONFIRMED")
            c=""
            if x["state"] in ("ACKED","UNKNOWN","CANCEL_REQUESTED") and x["order_id"]:
                c=(f'<form method="post" action="/cancel"><input name="csrf" type="hidden" value="{self.server.csrf}">'
                   f'<input type="hidden" name="intent" value="{x["id"]}"><button>Cancel at Dhan</button></form>')
            rows.append(f'<tr><td>{x["id"]}</td><td>{html.escape(x["symbol"])}</td><td>{html.escape(x["state"])}</td>'
                        f'<td>{oid}</td><td>{html.escape(x["broker_status"])}</td><td>{c}</td></tr>')
        which="ARMED — ONE REAL ORDER" if state["effective_on"] else "OFF"
        page=f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ResultBot Real Equity AMO</title><style>
body{{font:16px system-ui,sans-serif;max-width:1050px;margin:20px auto;padding:16px;background:#f0f3f6;color:#202e39}}
section{{background:#fff;border-radius:12px;padding:22px;margin:16px 0;border:1px solid #dde3e8}}
button{{padding:12px 18px;background:#145f9b;color:#fff;border:0;border-radius:6px;cursor:pointer;margin:5px}}
input,select{{padding:9px;margin:6px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}
.warn{{background:#fff5ed;padding:12px;border-left:4px solid #a83d28}} </style></head><body>
<h1>ResultBot — Real Dhan Equity AMO</h1><section><h2>Trading switch: {which}</h2>
<p class="warn"><b>REAL ORDER:</b> ON arms ONE NSE_EQ CNC LIMIT BUY AMO for a NEW eligible alert (10-minute limit).
OFF stops future orders but never cancels an existing one. Cancellation may fail; an AMO may fill at market open.
<b>There is no automatic stop-loss/target if the AMO fills.</b>
You must manage any resulting holding yourself.
One attempt per IST day. Maximum ₹1000 notional, default ₹500.</p>
<form method="POST" action="/arm"><input name="csrf" type="hidden" value="{self.server.csrf}">
<label>Source: <select name="source"><option value="TEST">Verified Test Group (RBLIVEAMO)</option>
<option value="EARNINGS_PULSE">New real Earnings Pulse Excellent signals</option></select></label>
<label>Maximum notional ₹<input type="number" min="100" max="1000" step="1" value="{state["budget"]}" name="budget"></label>
<label>Confirmation: type PLACE REAL DHAN AMO <input name="confirm" autocomplete="off" required></label>
<button>ON — ARM ONE REAL AMO</button></form>
<form method="POST" action="/off"><input name="csrf" type="hidden" value="{self.server.csrf}">
<button style="background:#b12626">OFF — BLOCK NEW ORDERS</button></form>
<p>Test Group live trigger (after ON): <code>[RBLIVEAMO] #YESBANK - 🏆🔥 Excellent Results</code></p>
<p>Order IDs below are reported by Dhan. An ID is an acknowledgement, not proof of a fill.</p></section>
<section><h2>Actual Dhan orders</h2><div style="overflow-x:auto"><table>
<tr><th>Intent</th><th>Stock</th><th>State</th><th>Broker ID</th><th>Dhan Status</th><th>Cancel</th></tr>
{''.join(rows) or '<tr><td colspan="6">No attempted orders</td></tr>'}
</table></div></section></body></html>"""
        self.send(200,page)
    def do_POST(self):
        if not self.authorized():return
        if self.path not in ("/arm","/off","/cancel"):
            self.send(404,"Not found"); return
        if self.headers.get("Origin") not in (f"http://localhost:{PORT}",f"http://127.0.0.1:{PORT}"):
            self.send(403,"Origin blocked");return
        if self.headers.get("Content-Type","").split(";")[0]!="application/x-www-form-urlencoded":
            self.send(415,"Unsupported form");return
        try:
            length=int(self.headers.get("Content-Length","-1"))
            if not 0<=length<=2048: raise ValueError("Invalid form")
            form=parse_qs(self.rfile.read(length).decode("utf-8"),strict_parsing=True)
            if not hmac.compare_digest(form.get("csrf",[""])[0],self.server.csrf):
                raise ValueError("Invalid CSRF")
            if self.path=="/arm":
                if form.get("confirm",[""])[0]!="PLACE REAL DHAN AMO":
                    raise ValueError("Explicit REAL order authorization required")
                amo.arm(source=form["source"][0],budget=int(form["budget"][0]))
                reply="ON: next NEW eligible alert may place ONE real Dhan AMO."
            elif self.path=="/off":
                amo.disarm()
                reply="OFF: future orders blocked. Existing Dhan orders remain active."
            else:
                reply="Dhan cancellation request: "+amo.request_cancel(int(form["intent"][0]))
        except (ValueError,IndexError,KeyError,amo.LiveAmoError) as exc:
            self.send(400,"<b>Blocked:</b> "+html.escape(str(exc)));return
        self.send(200,f'<h2>{html.escape(reply)}</h2><a href="/">Return to control</a>')

def main():
    panel=ThreadingHTTPServer((HOST,PORT),Panel)
    panel.password=read_secret(SECRET)
    panel.csrf=read_secret(CSRF)
    print("ResultBot Equity AMO owner panel bound to localhost only; OFF by default",flush=True)
    panel.serve_forever()

if __name__=="__main__":
    main()
