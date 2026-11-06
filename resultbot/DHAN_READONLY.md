# ResultBot — DhanHQ read-only account connection

This standalone module connects the ResultBot VM to **DhanHQ API v2** for profile, account binding, configured IPs, **live verified funds**, and actual daily order/trade inspection.

## Security boundaries

- Absolutely **no order placement, modification, or cancellation**: allowlisted account-data GET requests only to `/profile`, `/ip/getIP`, `/fundlimit`, `/orders`, `/orders/{numeric order id}`, `/trades`. The existing separate TOTP-authentication and renewal commands remain unchanged.
- This file is independent from `trading_bot.service` and `resultbot-excellent-watch.service`. Importing or running it does not start trading, change Telegram sessions, or modify the Excellent research database.
- The only credential copy for ResultBot read-only diagnostics is `~/resultbot-data/.dhan_readonly.json`, written with mode `0600` under an `0700` directory after a matching `GET /profile` response. **Never** commit, upload, send, log, or paste this file or the token into chat.
- Do not silently reuse another VM's or another bot's token. Account ID must match the Dhan API profile exactly. Order and trade rows are also account-verified, and unrelated trades cannot be linked to an Excellent alert by symbol alone.
- No Automatic Telegram broker response yet: the order/trade queries are manual. A future verified order-correlation layer is required to send Dhan-verified execution status cards.
- Dhan access tokens have a **24-hour lifetime**. The optional isolated `resultbot-dhan-token-refresh.timer` renews automatically at **08:00 IST every day**, including weekends, and catches up after reboot when user lingering is enabled. The timer is not tied to `trading_bot.service` and never submits orders. A manual or automated refresh first verifies account identity; any failed renewal preserves the previous working token.

## Live trading balance — verified broker read, no AMO

DhanHQ v2 provides the official **GET /fundlimit** endpoint. ResultBot's
`funds` command validates its current token against `GET /profile`
before reading the fund response. Both replies must carry the **same**
Dhan client ID; the VM prints only a masked account identifier, numeric
values and token expiry. It never logs the access token.

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py funds
```

`balance` is an alias for `funds`.

The report includes available to trade (`availabelBalance` — this is
the field's **actual spelling** in DhanHQ v2), utilised funds, withdrawable
balance, start-of-day limit, collateral, receivables and blocked payouts.
If optional fields are absent, it prints **NOT PROVIDED** rather than zero.
If the required available-balance field is missing, negative, nonnumeric
or account-mismatched, it errors instead of reporting false balance.
A verified zero is shown as `INR 0.00` (not an error).

The values reflect a new Dhan funds API response retrieved at the printed
IST timestamp; they are **not** evidence of eligibility to place any
particular order or of broker AMO margin acceptance. No broker mutation
endpoints are called, and neither the research listener nor token-refresh
systemd timer is modified.

The official DhanHQ v2 specification:
https://dhanhq.co/docs/v2/funds/

## VM deployment

The production VM has a separate, existing `~/uditbot/venv/bin/python` environment. Download `resultbot/dhan_readonly.py` into `~/resultbot-data/dhan_readonly.py` from a **pinned GitHub commit**, compile and self-test before using it.

Then choose **one** secure authentication method in an interactive SSH terminal:

**Recommended when Dhan TOTP is enabled:**

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py setup-totp
```

The CLI asks you to type `GENERATE`, enter your numeric Dhan Client ID, and your **6-digit Dhan PIN**. At the last prompt you may enter EITHER:

- the current **6-digit TOTP code** shown in your authenticator app, OR
- your existing **16-character Base32 TOTP setup key** from Dhan (letters A-Z and digits 2-7, not a 16-digit decimal OTP).

Both are entered as **hidden input** in your VM terminal. If a setup key is entered, ResultBot computes the rotating 6-digit TOTP in memory using RFC 6238 HMAC-SHA1. It sends only that code to Dhan for authentication, **never the original setup key**. The key, 6-digit PIN and temporary TOTP code are not saved or logged. If you have a 16-digit numeric value containing 0/1/8/9, it is not a standard Base32 TOTP setup key; use the authenticator-generated 6-digit code instead.

Dhan's documented token endpoint `POST https://auth.dhan.co/app/generateAccessToken` uses the account ID, PIN and 6-digit TOTP as HTTPS query parameters; the connector does not log or follow redirects containing these values. A resulting 24-hour access token is saved only after `GET /profile` confirms the account. **This is the only POST: strictly for authentication. All order APIs are GET-only.** Never paste credentials, QR codes, secrets, or tokens into ChatGPT, shell command arguments, GitHub, or log files.

**Alternative when you already generated the Access Token in Dhan Web:**

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py setup
```

This accepts the long Dhan API access token (not the 6-digit TOTP code). Both methods validate `GET https://api.dhan.co/v2/profile` and account identity before saving credentials. Dhan currently supports up to five active access tokens per account; if limit reached, use Dhan Web to manage active tokens instead of repeatedly retrying login. Never share PIN, TOTP, authenticator seed, or token in chat.

**Important:** The Dhan `Setup TOTP` button enables an authenticator. It is not itself an API access token. You still need the current authenticator code plus your Dhan PIN to use `setup-totp`.

After successful verification:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py check
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py orders
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py trades
```

Optional: `... dhan_readonly.py order --id 123456789012` to check a **known broker order ID**, not a symbol match.

## Automatic daily renewal — same behavior as the original bot

The original copied bot already supports `DHAN_PIN` and `DHAN_TOTP_SECRET` in `~/uditbot/.env`. ResultBot now **reuses these existing private credentials** where the `DHAN_CLIENT_ID` matches its separately verified read-only Dhan account. No TOTP secret needs to be re-entered if both are already present.

If the copied `.env` does not include the credentials, ResultBot asks **once** whether to save its own PIN and TOTP setup key to `~/resultbot-data/.dhan_autorenew.json` (mode `0600`), then renews unattended. This local file contains sensitive values in plain text with Unix permission controls, so protect the VM account and disk. The one-off 6-digit TOTP code entered during an earlier successful login cannot be recovered or reused for future renewals. No secrets enter GitHub.

Install with the pinned `resultbot/install_dhan_autorenew.sh` from the same GitHub commit as the Dhan connector. The installer:

1. Verifies the read-only connector's offline self-test.
2. Checks for eligible `DHAN_PIN` and `DHAN_TOTP_SECRET` from your existing, private `.env`. If unavailable, prompts in the VM terminal to save new private secrets once.
3. Performs a **live broker-verified renewal** immediately; if it fails, it does not enable a timer or replace the previous access token.
4. Installs and enables an **isolated systemd user timer**, daily 08:00 IST, `Persistent=true` for missed runs. A 90-second guard prevents duplicate token generation if the timer catches up immediately after installation.
5. Does **not** start the trading bot, change its STOP state, alter its original Dhan refresh timer or restart the Telegram listener.

Inspect the result using:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/dhan_readonly.py autorenew-status
systemctl --user is-active resultbot-dhan-token-refresh.timer
systemctl --user list-timers --all | grep resultbot-dhan-token-refresh
systemctl --user status resultbot-dhan-token-refresh.service --no-pager
```

For unattended execution even after the SSH session logs out and VM reboots, enable systemd **user lingering** as the VM account owner:

```bash
sudo loginctl enable-linger "$(id -un)"
```

The service uses `refresh-scheduled` and is intentionally never enabled as a trading service. Unlike SapphireBot's full `daily_health_check` (which also checks funds and instrument metadata), this **ResultBot-only timer performs token renewal and account verification**. It does not call the original bot's broader health check or require any trading engine.

## Interpretation

- `PENDING` or `TRANSIT`: Dhan has not confirmed a fill.
- `PART_TRADED`: use `filledQty` and a positive broker `averageTradedPrice` to display verified partial execution.
- `TRADED`: fully executed only when `filledQty`, order quantity and a positive broker average price agree. Otherwise the price remains `N/A` or unverified.
- `REJECTED`, `CANCELLED`, `EXPIRED`: no automatic buy retry or replacement orders.
- Orders and trades endpoints only show Dhan's documented **current-day** books; historical trades require separate API handling.
- This is not a signal-to-order attribution system. A matching symbol is insufficient evidence that ResultBot executed a signal.

Official references: https://dhanhq.co/docs/v2/authentication/ and https://dhanhq.co/docs/v2/orders/
