# ResultBot — VM source snapshot and fresh installation

This release incorporates the 27-file source export from the working ResultBot VM
uploaded on 7 November 2026. The repository also retains the complete original
AuditBot baseline. `resultbot_runtime/` preserves the exported Python and shell
scripts, including the retro settings screen and the owner-defined order budget.

## One command on a NEW Debian/Ubuntu VM

Log in as a normal sudo-enabled user. Run in a browser SSH terminal; no apps are
required on your computer. Do not run this on an existing AmanBot/UditBot/ResultBot VM.

```bash
sudo apt-get update && sudo apt-get install -y git sudo && \
git clone --single-branch --branch release/resultbot-fresh-install-v1 \
  https://github.com/darvasboys-lab/resultbot.git "$HOME/uditbot" && \
bash "$HOME/uditbot/deploy/resultbot/install.sh"
```

The wizard asks for Telegram API ID/hash, phone/OTP (and 2-step password if enabled),
log and test group IDs, and the new Dhan account credentials. The source remains
the same verified `@earnings_pulse` channel. The Telegram account must have access
to the source, log and test groups. Dhan accepts either a current access token or
PIN plus TOTP setup key; supplying PIN/TOTP enables unattended daily renewal.
Secrets remain in private VM files, never GitHub. Without PIN/TOTP, token renewal
remains manual. Broker-side account/IP requirements still apply before orders.

Installation provisions both captured listener roles: `resultbot-listener.service`
collects raw excellent alerts, while the user service `resultbot-excellent-watch.service`
runs the Excellent parser, notifications, price tracking and existing AMO path.
The user renewal timer runs at 08:00 Asia/Kolkata. User lingering is enabled so
services survive logout and reboot. No WebUI is required or enabled.

The original `trading_bot.service` is installed but disabled and inactive because
the captured runtime checks that condition. The two ResultBot listeners start
automatically after authentication. **AMO starts OFF**, with TEST source and INR
500 as editable defaults. Run `bot setup` to enter your desired budget/source
and use its existing YES/NO switch to enable real AMO orders. No orders are sent
by the installer. No signal or order history is imported.

After installation:

```bash
source ~/.bashrc
menu
```

`bot setup`, `bot status`, `bot orders`, `bot funds`, `bot menu`, `bot logs`,
`livelogs`, `prevlogs`, `daylogs` are available. Log shortcuts now show the
Excellent/AMO user service rather than only the raw collection service.

## Resume interrupted setup

Run the existing installer again; do not clone again:

```bash
bash "$HOME/uditbot/deploy/resultbot/install.sh"
```

A local stage file distinguishes this install's partial state from an unrelated
existing VM. If the wizard has already saved `.env`, it reuses it and resumes
Telegram login. If entered values were wrong, correct the private `.env` on the
VM before resuming. Broker profile and Telegram access must verify before startup.

## Snapshot and precise limits

`vm_snapshot/` contains all 27 exported files, with SHA-256 entries in
`deploy/resultbot/VM_SOURCE_SHA256.json`. Source reconstruction preserves exported
content with normalized terminal-separator blank lines. Original VM filesystem
metadata and exact original file hashes were not in the export.

The export deliberately omitted inline systemd Environment values. New templates
reconstruct unbuffered logging, use portable user paths, and read the new `.env`.
Private credentials, sessions, databases, pending orders, historical data, caches,
and account-specific saved strategy JSON are not copied. Optional position-strategy
code is retained with its source defaults; it is not newly connected to live trading.
This is a software replica with fresh runtime state, not a complete disk image.

Offline tests cover source preservation, configuration file permissions, shell
syntax and installer contracts. They do not certify a fresh VM, live Telegram
authentication, broker availability, or real order fills. A fresh-VM installation
with real credentials remains the end-to-end verification step.
