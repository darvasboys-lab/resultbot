# ResultBot — Real Dhan Equity AMO: persistent YES / NO switch

The AMO switch is a **master enable/disable control**, not an order-count selector.
The existing ResultBot Telegram listener and separate Dhan token service stay in place.
No browser/WebUI required. No live order is submitted during installation.

## YES — AMO ON until you choose NO

Once the owner selects **YES** in `amo_yes_no.sh`:
- The switch is durably **ON** across multiple alerts, runtime restarts and days.
- **Every distinct, new, eligible Excellent Results alert** from the official
  monitored Earnings Pulse channel may create its own genuine Dhan Equity AMO.
- The owner's authenticated Telegram account may deliberately trigger a real
  order by posting a **new ordinary Excellent Results** message in the
  configured ResultBot Test Group (for example `#YESBANK - Excellent Results`).
  The `[RBLIVEAMO]` prefix is optional. Older `[RBAMO]` simulation and
  `[RBTEST]` messages remain non-trading even when AMO is ON.
- No artificial one-order limit, maximum number per day, one-shot disarm,
  or ten-minute expiry. It stays ON until you explicitly select **NO**
  (or a critical safety gate disables entries).
- Each order has its **own** durable intent, idempotency correlation ID,
  verified security ID and broker order ID. A duplicated Telegram message
  cannot trigger another order.
- The current **per-order** default budget remains ₹500 (adjustable via the
  authenticated owner's CLI to ₹100–₹1000); this is a *sizing safeguard*,
  not a cap on how many orders you can place.
- Funds checks include outstanding ResultBot AMO commitments, and invalid
  account, token, instrument mapping, market session or static outbound IP
  will block that particular order.

## NO — new AMO submissions OFF

Selecting **NO** blocks future orders. An intent that has not crossed the
irreversible broker-submission transition must not be submitted. Orders
already accepted by Dhan are **not** cancelled by switching OFF.

There is **no automatic 120-second cancellation**. Pending AMOs remain
pending until *you* request cancellation or Dhan reaches a terminal state.
The broker-order monitor continues while OFF, to report changes.

To see the switch state at any time:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py status-short
```

To see all ResultBot-owned AMOs, broker IDs, states, and intent IDs:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py status
```

To cancel one specific resultbot-owned order, take its **intent ID** from
the status output (NOT a broker order number):

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py cancel --intent-id 1
```

Replace `1` with the real intent ID. Do not assume a cancellation succeeded
until the broker confirms `CANCELLED`. Cancellation might fail or arrive
after an AMO has partially or fully filled. A filled stock holding is real;
the ResultBot AMO adapter does **not** attach protective stop/target orders.
Manage any holding separately in your broker account.

## Install the terminal switch (no WebUI)

The pinned installer `resultbot/install_real_amo.sh` downloads the
`equity_amo_live.py`, `excellent_watch.py`, and `amo_yes_no.sh` files
from the same commit, compiles them, performs **read-only Dhan profile /
funds / IP-whitelist preflight**, and backs up the existing listener before
installing. It leaves the switch OFF by default and restarts only the
ResultBot Telegram listener. On a failed preflight, the original listener
is preserved.

Then run:

```bash
bash ~/resultbot-data/amo_yes_no.sh
```

Select **YES** to keep real AMO on, or **NO** to switch it off.

If `whiptail` exists, the terminal shows YES / NO buttons. Otherwise
type `YES` or `NO`. You can repeat this operation whenever you wish.

Example manual trigger (owner-only, Test Group):

```text
#YESBANK - 🏆🔥 Excellent Results
```

The same ON switch enables naturally arriving, unprefixed official
Earnings Pulse Excellent alerts. Only newly received strict Excellent
signals are eligible. The separate Test Group receipt means only that
Telegram delivered the message: a genuine Dhan order ID is shown only
on a distinct verified broker card and in the AMO status database.
Old/replayed, unauthorized, duplicate, or non-Excellent messages
cannot submit AMOs.

## When Dhan `GET /fundlimit` returns HTTP 500

A broker HTTP 500 means that **fresh live funds have not been verified**.
This is not proof of invalid account credentials or an accepted AMO.
The default installer retries only the idempotent `GET /fundlimit` up to
three times on HTTP 500/502/503/504. It never retries order placement,
never uses a remembered balance, and remains OFF if the broker stays unavailable.

When the broker funds endpoint is down, the owner can still install the
corrected Telegram listener **with AMO kept OFF** using the explicit
`--code-only` argument to `install_real_amo.sh`. This skips only the
installer's read-only broker preflight; it does **not** bypass the
fresh funds and IP checks required by the owner YES switch and by every
order submission. It will restart only the ResultBot Telegram listener.

Verify broker recovery with:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py preflight
```

If it still fails, do not turn AMO ON and do not send more real trading
signals. Keep AMO OFF and let Dhan's funds API recover or contact Dhan
support if failures persist.

## Safety, brokerage verification and transparency

- Each real request uses `NSE_EQ / CNC / BUY / LIMIT / DAY`,
  `afterMarketOrder=true`, `amoTime=OPEN`.
- Verified Dhan account, read-only balance, static-IP allowlist,
  official NSE security ID and a bounded price reference are checked
  again before each order.
- A timeout after `POST /orders` results in an **UNKNOWN** order outcome.
  ResultBot must reconcile using Dhan's correlation/order lookup, not
  resubmit automatically. No fabricated broker IDs or trade fills.
- A genuine Dhan order ID is an acknowledgment, not proof of execution.
  Order-status polling and the Telegram Log Group continue even when
  AMO is switched OFF.
- The default risk sizing is intentionally separate from the operator's
  YES/NO **number-of-orders** decision.

Official broker docs:
https://dhanhq.co/docs/v2/orders/
https://dhanhq.co/docs/v2/funds/

**Important:** The earlier pinned one-shot / 120-second-auto-cancel
installer is superseded. Do not deploy an older revision of this feature.
