# ResultBot — REAL Dhan Equity AMO, Terminal YES / NO Control

**LIVE ORDER FEATURE:** The YES button can arm genuine Dhan broker order
placement. It is OFF by default. No browser/WebUI is required.

## Why a YES / NO menu?

The original ResultBot web dashboard is not operational. This release
installs a terminal switch (`amo_yes_no.sh`) which uses **whiptail** YES/NO
buttons when available and a simple **type YES or NO** fallback otherwise.
No separate HTTP service, port 8093 or panel login is needed.

YES:
- Checks the real Dhan account, available funds, registered static IP and
  VM's outbound IP; verifies that the original trading service is disabled.
- Arms **ONE** real Dhan Equity `NSE_EQ / CNC / BUY / LIMIT / OPEN` AMO
  with maximum ₹500 notional, for a new eligible owner-originated signal in
  the existing authorized ResultBot **Test Group**.
- The arm expires after ten minutes and disarms automatically once a
  signal is accepted for submission. Choosing YES **alone** does not place an order.
- An actual Dhan broker `POST /orders` may follow the next eligible
  Telegram message. Dhan's genuine `orderId` is recorded in durable
  local SQLite and placed in the existing Log Group when verified.

NO:
- Immediately blocks new AMO submissions (including already-reserved
  requests that have not entered the irreversible POST transition).
- Does **not** cancel a Dhan order already accepted or being submitted.

Safety: at most **one attempt per IST day** and one at a time; the
original `trading_bot.service` remains inactive and disabled. A stored
intent prevents silent duplicate POST requests after a timeout or restart.
The listener uses the existing Telegram account, Log Group outbox and
separately renewed private Dhan token.

## Install (once, on the ResultBot VM)

Use one pinned commit URL, never copy in-development main-branch files.
The published `resultbot/install_real_amo.sh` downloads
`equity_amo_live.py`, `excellent_watch.py`, `amo_yes_no.sh`,
compiles them, checks Dhan preflight with **GET only**, backs up the
existing listener, initializes the switch to OFF, and restarts
**only** the ResultBot listener.

If Dhan IP whitelisting or any other preflight fails, the original
ResultBot listener is left in place. The installer does NOT submit orders.

Then launch the terminal YES/NO menu at any time:

```bash
bash ~/resultbot-data/amo_yes_no.sh
```

It presents a YES and a NO button when `whiptail` is available.
The menu always defaults to **NO**. Without `whiptail`, type
uppercase **YES** to arm or anything else to disable.

For an actual order attempt, after choosing YES send this **new**
message *from the Telegram account running ResultBot* into the
authorized ResultBot Test Group:

```text
[RBLIVEAMO] #YESBANK - 🏆🔥 Excellent Results
```

`[RBLIVEAMO]` is a genuine order trigger, distinct from the older
`[RBAMO]` simulator. The Dhan order may be refused because of the
market calendar, symbol price, missing instrument mapping, static IP,
missing broker AMO permissions, or risk limits. If Dhan returns an
authentic order ID, it appears in the Real Equity AMO Telegram Log
Group card and in:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py status
```

A verified order ID does **not** mean the order was filled.

## Cancellation and risk

A background monitor attempts to cancel an accepted pending AMO after
120 seconds and checks the broker state. The owner can also request
cancellation explicitly:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py cancel --intent-id 1
```

Use the actual intent number from `status`. **Never** assume a
cancellation succeeded until Dhan reports `CANCELLED`. The Dhan
order may execute before cancellation, or the cancellation request can
be delayed or refused. Partial fills create actual holdings. No
stop-loss or protective target order is installed automatically.

Dhan requires a whitelisted static outbound IP for real placement and
cancellation. Reference: https://dhanhq.co/docs/v2/authentication/
Order placement and cancellation: https://dhanhq.co/docs/v2/orders/

**Do not run the old WebUI installer** from an earlier pinned commit:
the terminal-only release supersedes it. This is a separate one-shot
live-order feature, not a replacement for UditBot or the existing
research and balance collectors.
