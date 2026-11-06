# ResultBot — Excellent Results ONLY (read-only research)

Source: **Earnings Pulse** (`@earnings_pulse`, Telegram numeric channel ID `2457353254`).

This separate service **does not** use Dhan, submit orders, read the ResultBot strategy file, or alter `trading_bot.service`. It does not process Great, Good, OK, Weak, or unlabeled messages.

## Key methodology
- Accept exact `Excellent Results` rating with **one** stock hashtag. If multiple hashtags are found, leave out and review manually. Verify Telegram channel entity numeric ID before reading.
- Store original Telegram timestamp, message ID, source ID, text, and symbol; deduplicate on `(source_channel_id,message_id)`.
- Query Yahoo Finance **NSE `.NS` 1-minute** chart only for Excellent alert symbols in the previous five days. Require returned ticker and INR currency to match, positive OHLC, valid minute timestamps; never substitute a ticker silently.
- `first_price` is the **close of the first completed 1-minute candle after the Telegram alert's minute**, not an executable entry price, Telegram-time tick, opening price, or previous-day close.
- Track closest available completed minute bar at/after first observation +5, +15, +30 and +60 min **in the same IST calendar session**, with at most two minutes lateness. Keep `N/A` for missing data.
- Observed high/low is based on retrieved high/low for bars within the first 60 minutes and the same IST day, relative to the first observed close. **It is not an intrabar trade sequence or maximum drawdown.** Missing candles can understate actual extremes.
- After-hours alerts can first receive a verified price at the next market session. Research prices are potentially delayed, incomplete or unavailable; Yahoo is unofficial, subject to throttling and limited 1m history. No guaranteed returns, fills, or real-time trade suitability.
- The listener polls research prices 09:16–15:34 IST on weekdays as a *clock heuristic*; exchange holidays and halts may have no data. No exchange calendar is claimed.

## Local use on VM
```
cd ~/uditbot
venv/bin/python ~/resultbot-data/excellent_watch.py self-test
venv/bin/python ~/resultbot-data/excellent_watch.py backfill --limit 2000
venv/bin/python ~/resultbot-data/excellent_watch.py prices
venv/bin/python ~/resultbot-data/excellent_watch.py report --limit 25
venv/bin/python ~/resultbot-data/excellent_watch.py listen
```

The collector checks **existing ResultBot login credentials in this order**: (1) `TG_SESSION_STRING` from `~/uditbot/.env`, (2) original `~/uditbot/data/tg_userbot.session` (read-only conversion to an in-memory StringSession), and (3) an already-authorized independent research session. It never asks for another OTP or modifies `.env`. If none is authorized, it stops and reports an error. Sharing the original authentication requires `trading_bot.service` verifiably inactive; an active or unknown state blocks reuse and the `listen` watchdog disconnects if trading starts. Never run simultaneous Telegram listeners using the same authentication, and never commit or share sessions, tokens or `.env`. Stop the collector before intentionally starting the trading bot.

Runtime database: `~/resultbot-data/excellent_watch.sqlite3` (does not overwrite pre-existing `alerts.sqlite3`). It contains historical message text and price bars; it is excluded from GitHub by being outside the repository.

Service (after successful interactive login): copy the `.service` file to `~/.config/systemd/user/resultbot-excellent-watch.service`, run `systemctl --user daemon-reload` and `systemctl --user enable --now resultbot-excellent-watch.service`. Use `journalctl --user -u resultbot-excellent-watch -n 50 --no-pager` for logs. Running across logout/reboot may require user lingering to be enabled by the VM owner.

## Excellent-only Telegram Log Group cards
When a new Excellent alert is captured by the running `listen` service, the collector may send an **Excellent alert received** card to your existing joined Telegram Log Group. It reads `RESULTBOT_LOG_GROUP_ID` (if set) or falls back to `LOG_GROUP_ID` from `~/uditbot/.env`. The destination must resolve to exactly one joined signed-ID Telegram group, never the signal-source channel. Missing or invalid configuration disables outgoing cards without interrupting research.

**Historical backfills, startup catch-up imports, Great/Good/Weak and other messages never generate cards or 'blocked' notices.** New Excellent live messages are durably queued once by Telegram message ID. Delivery retries are bounded; ambiguous network acknowledgements can very rarely duplicate a message, so do not claim end-to-end exactly once. Unsent notices older than 24 hours expire instead of replaying a huge backlog.

Each card states **Dhan order (ResultBot research): NOT SUBMITTED** and **Dhan execution price: N/A**. These are truthful research-collector statements, *not* a Dhan rejection and *not* an observation about another bot or manually placed order. This module does not read Dhan order status or trade fills. A separate, properly correlated broker-status integration will be required before sending real execution/price or rejection cards. NEVER display Yahoo research prices as broker execution fills.

To check locally without sending any Telegram messages:

```
~/uditbot/venv/bin/python ~/resultbot-data/excellent_watch.py log-status
```

## Test Group: safe end-to-end cross-check

The existing `TEST_GROUP_ID` in `~/uditbot/.env` is used automatically; `RESULTBOT_TEST_GROUP_ID` can override it. Both require an already joined Telegram **group**, a signed negative chat ID, and an identity distinct from the Log Group and Earnings Pulse source. If the group is not resolved, the research listener continues without the test input.

In the **Telegram Test Group only**, send **exactly** these two messages as separate posts:

```text
[RBTEST] #ROLEXRINGS - 🏆🔥 Excellent Results
```

```text
[RBTEST] #ROLEXRINGS - 🌟 Great Results
```

Expected: the first produces **one** Telegram Log Group card starting `🧪 RESULTBOT TEST — EXCELLENT ONLY`, with Dhan order `NOT SUBMITTED` and execution price `N/A`. The second produces **no** Log Group notification. Neither creates a real `alerts` row, one-minute research candle, Dhan order or trade. The first message is deduplicated by test-chat/message ID; other messages without the exact `[RBTEST]` prefix do nothing. In addition to live updates, the listener polls the most recent 40 Test Group messages every 30 seconds as a fallback for same-account posts. It accepts only explicitly marked messages whose Telegram timestamp is within the last 15 minutes, so old test history is not replayed. The first scan reports `[TEST SCAN]` with scanned/marked/new counts; thereafter `TEST EXCELLENT` appears when an eligible test is newly queued.

Check local delivery with:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/excellent_watch.py log-status
journalctl --user -u resultbot-excellent-watch.service -n 20 --no-pager
```

Look for `TEST_EXCELLENT_ALERT / SENT : 1` (or higher if tests were repeated). Telegram transport failures may produce `PENDING`; a configured destination alone does not prove delivery. The test is isolated from real market research and does not imply that any Dhan integration exists.

## Restrictions
- No broker integration, trading, profit guarantee, historical minute coverage guarantee, or application-level Yahoo API SLA.
- No `set -e` installation snippet; validate files before installing; never enable `trading_bot.service` as part of research.