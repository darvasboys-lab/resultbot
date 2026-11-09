# Confirmed-fill OCO protection

Connects the saved STOP_ONLY / TARGET_ONLY strategy to real Dhan Forever SELL OCO submission. Read `stop_pct` and `target_pct` from the VM's `position_strategy.json`; never substitutes code defaults. The supplied owner configuration is SL 9%, target 5%. Entry sizing still uses the independent saved entry budget (INR 25); the strategy preview's INR 500 is not substituted.

## Behavior

An independent user service polls the broker order book every two seconds plus request-processing time. It binds account, entry order ID, correlation, instrument, segment, product and route to the ResultBot intent. Filled quantity and execution average must agree with the trade book. Each newly filled increment gets one linked SELL OCO for that increment; later increments retain the first confirmed fill's price anchor and original rounded levels. No exit is submitted for the unfilled remainder.

Stop trigger is rounded down to the instrument tick, stop SELL limit is one tick below the trigger, target is rounded up to the tick. This is a broker-hosted conditional LIMIT OCO. Dhan monitors the prices after accepting it; a triggered LIMIT order does not guarantee a fill, particularly on gaps. Broker eligibility and delivery authorization requirements still apply.

Before submission, verify today's CNC net position covers existing managed protection plus the new quantity, and reject overlapping unknown protection or pending ordinary SELL orders. Persist SUBMITTING before sending. Any timeout, malformed response or missing confirmation becomes UNKNOWN; never automatically POST again. If the forever book provides correlationId, recover the corresponding order ID. Otherwise leave UNKNOWN for review. ACTIVE requires both legs verified in the broker book, not just a POST response.

The watcher continues independently of `bot live-off`, so stopping new buys does not stop protection for their fills. Strategy stages/averaging are not activated; switching to unsupported modes produces a visible unprotected error. The legacy preview field `execution_enabled` remains unchanged; the separate `oco_guard.json` controls this integration. Do not edit that JSON to resolve UNKNOWN submissions.

## Installation and activation

Install as the normal VM owner:

    ~/uditbot/venv/bin/python /path/to/checkout/deploy/resultbot/install_oco_guard.py

Installer backs up and hash-checks the known entry/readonly modules, adds the guard, fixes the individual-order CLI to select an account-verified row from `/orders`, installs a user systemd unit, and resumes the entry listener if it was running. No broker orders are submitted by installation. Runtime data and credentials stay on the VM.

Explicit activation on the VM:

    ~/uditbot/venv/bin/python ~/resultbot-data/oco_guard.py enable --include-intent 3
    systemctl --user enable --now resultbot-oco-guard.service

This authorizes existing YESBANK intent 3 and entries reserved after activation. Old RAMKY intents are not automatically adopted. The initial YESBANK fill will be reverified against current position and existing exits; the pasted historical fill alone never authorizes selling a holding already closed.

Status and logs:

    ~/uditbot/venv/bin/python ~/resultbot-data/oco_guard.py status
    journalctl --user -u resultbot-oco-guard.service -n 60 --no-pager

## Limits

This is not an execution-time guarantee. The watcher polls, rather than uses a broker push connection. Network/API delays can defer protection; failures appear in service logs and status. The current version does not cancel or resize active protection after manual position changes, does not retrospectively adopt historical holdings across trading days, and does not implement live staged averaging/partial profit strategies. Review/cancel broker exits when manually closing holdings. Triggered/ended OCO records are marked REVIEW and never recreated blindly.

Offline regression coverage includes the supplied 16.98 fill, no-fill/mismatched-account cases, partial fills, overlapping exits, missing strategy, duplicate/restarted runs, unknown outcomes and both-leg verification. No real broker OCO or end-to-end VM test was performed in development.

API sources: https://dhanhq.co/docs/v2/forever/ and https://dhanhq.co/docs/v2/orders/ and https://dhanhq.co/docs/v2/portfolio/
