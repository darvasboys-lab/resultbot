# ResultBot — DhanHQ read-only account connection

This standalone module connects the ResultBot VM to **DhanHQ API v2** for profile, account binding, configured IPs, and actual daily order/trade inspection.

## Security boundaries

- Absolutely **no order placement, modification, cancellation or token renewal**: allowlisted GET requests only to `/profile`, `/ip/getIP`, `/orders`, `/orders/{numeric order id}`, `/trades`.
- This file is independent from `trading_bot.service` and `resultbot-excellent-watch.service`. Importing or running it does not start trading, change Telegram sessions, or modify the Excellent research database.
- The only credential copy for ResultBot read-only diagnostics is `~/resultbot-data/.dhan_readonly.json`, written with mode `0600` under an `0700` directory after a matching `GET /profile` response. **Never** commit, upload, send, log, or paste this file or the token into chat.
- Do not silently reuse another VM's or another bot's token. Account ID must match the Dhan API profile exactly. Order and trade rows are also account-verified, and unrelated trades cannot be linked to an Excellent alert by symbol alone.
- No Automatic Telegram broker response yet: the order/trade queries are manual. A future verified order-correlation layer is required to send Dhan-verified execution status cards.
- Access tokens generally have a **24-hour lifetime**. When expired, generate a new token in the Dhan Web interface and run `setup` again. This module never renews tokens automatically.

## VM deployment

The production VM has a separate, existing `~/uditbot/venv/bin/python` environment. Download `resultbot/dhan_readonly.py` into `~/resultbot-data/dhan_readonly.py` from a **pinned GitHub commit**, compile and self-test before using it.

Then run, interactively in a secure SSH terminal:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py setup
```

Enter your Dhan Client ID, followed by the access token when prompted. The token input is hidden. The script verifies `GET https://api.dhan.co/v2/profile` and account identity before saving the credentials. Invalid credentials are not persisted.

After successful verification:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py check
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py orders
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py trades
```

Optional: `... dhan_readonly.py order --id 123456789012` to check a **known broker order ID**, not a symbol match.

## Interpretation

- `PENDING` or `TRANSIT`: Dhan has not confirmed a fill.
- `PART_TRADED`: use `filledQty` and a positive broker `averageTradedPrice` to display verified partial execution.
- `TRADED`: fully executed only when `filledQty`, order quantity and a positive broker average price agree. Otherwise the price remains `N/A` or unverified.
- `REJECTED`, `CANCELLED`, `EXPIRED`: no automatic buy retry or replacement orders.
- Orders and trades endpoints only show Dhan's documented **current-day** books; historical trades require separate API handling.
- This is not a signal-to-order attribution system. A matching symbol is insufficient evidence that ResultBot executed a signal.

Official references: https://dhanhq.co/docs/v2/authentication/ and https://dhanhq.co/docs/v2/orders/
