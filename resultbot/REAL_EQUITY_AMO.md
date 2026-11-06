# ResultBot — REAL Equity AMO ON/OFF (owner-armed, one-shot)

**DRAFT FEATURE — real-money order functionality.** Only deploy after the
offline GitHub checks succeed AND the VM's Dhan read-only preflight verifies
profile, broker funds, and whitelisted static outbound IP. Installing it never
places an order, and its switch is OFF until the authenticated owner turns ON.

## How it differs from the AMO preview

- **REAL** `POST https://api.dhan.co/v2/orders` on an authorized new
  Telegram signal, using the existing Dhan auto-renewed token.
- The result must contain an actual Dhan `orderId`; ResultBot verifies that
  ID against `GET /orders/{id}` and shows it in the private WebUI and the
  Telegram Log Group. A genuine order ID is **not** proof of a fill.
- If submission times out or returns an ambiguous answer, it never retries.
  It stores `UNKNOWN` and queries `GET /orders/external/{correlationId}`
  to recover a genuine ID if Dhan actually accepted it.
- The **Cancel at Dhan** button uses the real
  `DELETE /v2/orders/{order-id}` only for a ResultBot-recorded, broker-
  verified order. The listener attempts cancellation after 120 seconds,
  and polls Dhan for status. A cancel request is not proof of cancellation.
- This module **does not enable trading_bot.service**. No F&O, no live
  margin or market order. Only NSE equity BUY CNC LIMIT AMO to OPEN.

## Owner safety boundaries

- Authenticated **localhost-only** password + CSRF dashboard on
  `127.0.0.1:8093`. Use an SSH port-forward from your own computer.
  Do not publicly expose port 8093, and do not attach it to the existing
  unauthenticated/public port-80 dashboard.
- OFF by default; explicit 10-minute arming of the **next NEW** allowed
  signal; one attempt per IST day; ₹500 default maximum notional,
  absolute cap ₹1000; source either authorized Test Group or official
  Earnings Pulse. Armed state is automatically consumed before any broker
  network action. A saved immutable intent prevents retry after crashes.
- The Test Group real-order command is accepted **only from the Telegram
  account running the listener** and only with the exact prefix
  `[RBLIVEAMO] `. It is separate from `[RBAMO]` preview.
  Example: `[RBLIVEAMO] #YESBANK - 🏆🔥 Excellent Results`.
- Real source mode permits a newly observed, strict Excellent-only message
  from the verified `@earnings_pulse` channel.
- Preflight before ON: account-bound Dhan profile, current available funds,
  registered static IP matching VM egress, original trading service inactive
  and disabled, and closed-market time.
- Before POST: verified NSE_EQ security ID from Dhan instrument master,
  most recent **completed** provider-labeled Yahoo NSE 1-minute price
  within 72 hours, up-to-₹1000 total buying notional, extra ₹100 funds
  buffer, owner OFF generation, message freshness and session gate.
  Yahoo's data is **not exchange-grade trading data**; a delayed closing
  quote is no guarantee of the next market-open price or execution.
- OFF *prevents new submissions* until a POST transition starts. It does
  **not** cancel an already placed AMO.
- Real CANCEL may fail or arrive too late. A partial fill is still a
  genuine stock holding. No automated protective stop/target is installed
  for a filled AMO: the owner must manage the resulting holding manually.
- Never manually retry a signal if broker outcome is UNKNOWN without
  reconciling in Dhan Order Book.

## Installation on the ResultBot VM

The final pinned commit of this feature provides
`resultbot/install_real_amo.sh` and exactly three runtime Python files:
`equity_amo_live.py`, `amo_panel.py`, `excellent_watch.py`.
The installer:
1. Requires original `trading_bot.service` **inactive and disabled** and
   the current ResultBot listener active.
2. Downloads pinned source files to a private temporary directory.
3. Compiles all three and backs up the existing listener.
4. Runs a fully OFF-by-default self-test and live **GET-only** Dhan
   account/registered-IP/funds preflight.
5. Only after preflight passes, enables the localhost-only owner panel
   and restarts the ResultBot Telegram listener. Existing token refresh
   and the original trading service are not changed.
6. If preflight fails, restores the old listener; no real orders attempted.

Open your private browser tunnel with a cloud/SSH forward from your computer
to the VM: local port 8093 -> VM `127.0.0.1:8093`. Login:
- Username: `resultbot`
- Password: read the private VM file `~/resultbot-data/.amo_panel_key`
  **locally**, never paste it into GitHub or ChatGPT.

Then visit `http://127.0.0.1:8093`, keep Test Group mode selected,
choose maximum notional (₹500 by default), type
`PLACE REAL DHAN AMO` and press **ON — ARM ONE REAL AMO**.
The switch is consumed after the next permitted message or expires after
10 minutes; no order is created merely by turning ON.

For a real, broker-bound Telegram test, from the logged-in owner's Telegram
account, send as a **new message in the authorized ResultBot Test Group**:

```text
[RBLIVEAMO] #YESBANK - 🏆🔥 Excellent Results
```

A genuine order attempt is then possible if **all** preflight, instrument,
quote and risk checks pass and Dhan accepts the AMO request. Check the
private WebUI for the order ID and your Log Group for
`RESULTBOT — REAL EQUITY AMO`. Some broker orders may be rejected by Dhan
and no order ID may be issued; ResultBot must report that accurately.

Official DhanHQ docs:
https://dhanhq.co/docs/v2/orders/
https://dhanhq.co/docs/v2/authentication/
https://dhanhq.co/docs/v2/funds/
