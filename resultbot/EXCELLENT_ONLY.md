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

`backfill` and `listen` use an **independent Telethon session** `~/resultbot-data/excellent_telegram.session` to avoid locking the main bot's session file. The first run may ask for Telegram phone, OTP and 2FA; do this in the VM terminal before running as a systemd user service. This session contains login credentials and must never be committed or pasted into chat. Do not run `backfill` while `listen` uses that same session.

Runtime database: `~/resultbot-data/excellent_watch.sqlite3` (does not overwrite pre-existing `alerts.sqlite3`). It contains historical message text and price bars; it is excluded from GitHub by being outside the repository.

Service (after successful interactive login): copy the `.service` file to `~/.config/systemd/user/resultbot-excellent-watch.service`, run `systemctl --user daemon-reload` and `systemctl --user enable --now resultbot-excellent-watch.service`. Use `journalctl --user -u resultbot-excellent-watch -n 50 --no-pager` for logs. Running across logout/reboot may require user lingering to be enabled by the VM owner.

## Restrictions
- No broker integration, trading, profit guarantee, historical minute coverage guarantee, or application-level Yahoo API SLA.
- No `set -e` installation snippet; validate files before installing; never enable `trading_bot.service` as part of research.