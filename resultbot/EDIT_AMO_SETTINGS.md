# ResultBot — Edit REAL Equity AMO Settings (terminal menu)

The ResultBot owner can edit trading settings without using a web browser and
without interrupting the already-running Telegram listener or modifying an
existing Dhan order. The **YES/NO master switch remains exactly as it was**.

## Open the settings menu

```bash
bash ~/resultbot-data/amo_settings.sh
```

The terminal offers a menu (whiptail if installed; numbered text choices
otherwise):

1. **Alert source:** `TEST` (authenticated owner messages in the configured
   Test Group), `EARNINGS_PULSE` (new official Excellent Results alerts), or
   `BOTH`. Selecting BOTH while AMO is ON authorizes real orders from both.
2. **Per-order budget:** owner-entered positive whole INR amount (no arbitrary upper budget cap), default `500`.
   This is per AMO, not a daily/order-count limit. The chosen amount is
   preserved after switching AMO OFF and ON again.
3. **Master YES / NO:** opens the existing safe terminal toggle.
   YES persists until explicitly changed; NO stops future orders only.
4. **Current settings / order list:** displays broker order IDs, status and
   internal intent numbers without making any broker mutation.
0. **Exit**: no other changes.

Edits are confirmed by the owner and saved atomically in the same private
`~/resultbot-data/equity_amo_live.sqlite3` file used by the current
listener. New settings take effect for future Telegram messages without a
listener restart. Existing order intents retain their originally captured
budget, correlation ID, security ID and broker status.

Budget changes are saved for **future orders only**, even if an existing AMO is pending or submitting. Only source changes are briefly deferred while an order is RESERVED, PREPARING or SUBMITTING because those could affect an in-flight submission.

## Direct commands, if preferred

Read-only settings:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py settings --show
```

Change just the source, without changing YES/NO:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py settings --source TEST
```

Change just the per-order budget, without changing YES/NO:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_live.py settings --budget 600
```

A plain `arm` command and the existing `amo_yes_no.sh` now reuse your
saved source/budget, rather than resetting to BOTH and ₹500.

## Install the settings editor on an existing working ResultBot VM

**Do NOT rerun the full real-AMO installer just to edit settings.** That
installer intentionally turns AMO OFF and restarts the listener; doing so
while you have a working real-order setup would be unnecessarily disruptive.

Instead, run the pinned settings-only installer
`resultbot/install_amo_settings.sh` using the matching source directory
URL from a verified commit. It downloads and syntax checks only three
files, backs up the previous versions privately, and installs:
`equity_amo_live.py`, `amo_yes_no.sh`, `amo_settings.sh`.

It does **not** restart the listener, switch YES/NO, submit/cancel any Dhan
orders, or modify the token renewal timer. It checks the current state
before and after installation.

## Critical distinction

**Changing settings is not the same as cancelling an accepted Dhan order.**
NO blocks new submissions, but an already-accepted AMO can remain pending
or fill later. To cancel a specific ResultBot-owned Dhan order, use its
intent ID in the explicit `equity_amo_live.py cancel --intent-id ...` command,
and check for broker-confirmed `CANCELLED`.

The quantity for each new signal is `floor(chosen INR budget / verified Yahoo-derived LIMIT price)`, and Dhan broker funds must be sufficient. Real Dhan order validity and duplicate suppression are maintained, but these are not arbitrary user-budget or daily order-count limits. The bot never automatically submits a second order if an earlier broker POST outcome is ambiguous.
