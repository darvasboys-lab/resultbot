# ResultBot ↔ DhanHQ — verified read-only Telegram bridge

This addition **connects** the independently authorized ResultBot Dhan account to its
existing `@earnings_pulse` Excellent-only listener and Log Group.

## What it does

1. Uses the existing private `~/resultbot-data/.dhan_readonly.json` token.
   The daily Dhan TOTP refresh service updates this file atomically at 08:00 IST.
   Each broker query reads the token fresh — no trading service restart.
2. Validates `GET /profile`, `GET /orders` and `GET /trades`; each returned
   record must match the verified Dhan client ID.
3. Queues a **one-time connection card** to the existing Log Group outbox.
   The card reports account-wide current-day order/trade counts, **not**
   ResultBot-generated orders.
4. Optionally links an existing, owner-specified broker BUY order ID to an
   explicitly identified REAL `@earnings_pulse` Excellent alert by Telegram
   message ID. It validates source/channel, symbol, NSE equity segment, BUY
   direction, order creation time, verified account, and broker order ID.
5. For each manually linked order, polls the read-only Dhan books every 45 sec
   using the **existing** Telegram listener (not a second session), and
   queues status-change cards with filled quantity and Dhan-confirmed average
   executed price when available.
6. Never includes the full broker Order ID in Telegram notifications.
   Never pretends that an unrelated account trade belongs to ResultBot.
   No orders are placed, modified or cancelled by this module.

## VM installation

Install **three** sibling files from one pinned GitHub commit into
`~/resultbot-data/`:
`excellent_watch.py`, `dhan_readonly.py` (already installed) and
`dhan_bridge.py`.
Before replacing files, `python -m py_compile`, `dhan_bridge.py self-test`,
and `dhan_bridge.py connect` should pass. Restart only
`systemctl --user restart resultbot-excellent-watch.service` after these checks.
Never run or enable `trading_bot.service` as part of this installation.

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_bridge.py connect
~/uditbot/venv/bin/python ~/resultbot-data/dhan_bridge.py recent-alerts
~/uditbot/venv/bin/python ~/resultbot-data/dhan_bridge.py links
```

The `connect` command verifies Dhan's profile and daily order/trade lists
and queues **one** connection card for the existing verified Log Group.
The listener's existing durable `log_outbox` handles delivery/retries.
Running `connect` again will not send duplicate connection cards.

For **optional manual broker order attribution**:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_bridge.py link-order --telegram-id 15924 --order-id 112111182198
~/uditbot/venv/bin/python ~/resultbot-data/dhan_bridge.py poll
```

The IDs above are **examples**. Use the actual Telegram message ID from
`recent-alerts` and the actual Dhan order ID from Dhan Order Book. Only
**real** Excellent alerts are accepted, never Test Group posts or Great/Good.
An order must be verified as BUY, `NSE_EQ`, same stock, created no earlier
than that message, and on the same verified account. If a condition is
missing, ResultBot fails closed. Manual linking does **not** establish
that ResultBot placed the order; cards explicitly label it as
*manually linked and broker-verified*.

The Dhan order/trade APIs return the **current day's** order book. If a
previous-day order is no longer returned, ResultBot does not assume
rejection or fill. A future trading implementation must record its own
immutable order ID at submission and reconcile actual broker actions.

No automatic symbol-only link is allowed, including when the same stock
appears in both Earnings Pulse and the Dhan order book.

## Safety and troubleshooting

- `trading_bot.service` remains **inactive and disabled**.
- No additional Telegram login/session; existing `excellent_watch.py listen`
  handles the 45-second broker status polling.
- The broker API is **GET-only** here. Token generation is handled by the
  separately installed Dhan authentication/refresh service.
- No broker credentials, TOTP setup key, private Telegram session or `.env`
  is copied into GitHub.
- On broker errors, the original Excellent collector continues working and
  logs only the exception **type** to avoid leaking secrets.
- If `dhan_bridge.py connect` fails due to token/profile/orderbook error,
  do not restart the listener; fix the broker connection first.
- The positive `TRADED` status should be treated as fully completed only
  when `filledQty == quantity` and a positive average execution price
  is available. Partial fills are reported as such.
- On token renewal, the next broker poll sees the new token automatically.

Official documentation: https://dhanhq.co/docs/v2/authentication/ and
https://dhanhq.co/docs/v2/orders/
