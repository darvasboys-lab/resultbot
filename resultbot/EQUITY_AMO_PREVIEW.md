# ResultBot — Equity AMO support and safe end-to-end pipeline test

## Actual Equity AMO capability retained from the cloned trading bot

The copied repository has an **existing real Equity AMO branch** in:
- `src/main.py`: selects Equity AMO only for eligible closed-market sessions and
  with opt-in `EQUITY_AMO_ENABLED=true`.
- `src/broker/dhan_client.py`: Dhan `POST /v2/orders` placement route
  for BUY `NSE_EQ`, `CNC`, `LIMIT`, `afterMarketOrder=true`,
  `amoTime=OPEN` when permitted by trading mode, emergency STOP,
  market calendar, account reconciliation and risk safeguards.
- `tests/test_equity_amo.py`: verifies eligibility, broker request envelope
  and protection-watcher behavior with mocked Dhan HTTP.

**Important:** The currently running standalone `resultbot-excellent-watch.service`
does **not** call `src/main.py`, and `trading_bot.service` remains inactive
and disabled. Therefore the retained broker AMO route is **not yet connected to
live Earnings Pulse alerts**. Do not claim live automated AMO acceptance from
this setup.

## Safe Telegram simulation: exercise the route without a Dhan order

The optional `resultbot/equity_amo_preview.py` is **pure offline code**:
no Dhan credentials, no requests, no `POST /orders`, no real security mapping,
no live market quote, no order creation.

It builds an illustrative **non-routable** Dhan AMO envelope:
`NSE_EQ / CNC / BUY / LIMIT / DAY / afterMarketOrder=true / amoTime=OPEN`.

Default sample inputs are an **invented price of ₹100 and simulated ₹500 budget**.
Its security ID is intentionally `NO_VERIFIED_SECURITY_ID`, so it cannot be
mistaken for an executable broker order. It demonstrates the shape and sizing,
then always **blocks** submission. A mock is not a real AMO, broker ack or fill.

Run locally:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_preview.py --self-test
~/uditbot/venv/bin/python ~/resultbot-data/equity_amo_preview.py --symbol YESBANK
```

To test **Telegram → AMO template → safety block → existing Log Group**, send
exactly this message into your verified **ResultBot TEST GROUP only**:

```text
[RBAMO] #YESBANK - 🏆🔥 Excellent Results
```

Expected **two** deduplicated Test Group cards:

1. Your existing `TEST_EXCELLENT_ALERT` research-only acknowledgement.
2. `EQUITY_AMO_DRY_RUN` reporting the AMO route, a **sample fixture price**,
   indicative sample quantity, fail-closed Dhan submission, `NOT SUBMITTED`,
   and broker execution price `N/A`.

The Test Group history poll can recover the message from the preceding
30 minutes after a restart, but the outbox deduplicates by source/group ID and
Telegram message ID. Ordinary Excellent test posts, Great/Good/Weak messages,
and the real Earnings Pulse source do **not** trigger an AMO dry run.

After testing on the VM, check:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/excellent_watch.py log-status
```

Expected: `EQUITY_AMO_DRY_RUN / SENT : 1` after Log Group delivery.
Test messages do not enter the actual Excellent alert research database.

## Live AMO remains OFF

- The new research collector does not enable `EQUITY_AMO_ENABLED` or
  the original trading bot.
- The existing Dhan connector `dhan_readonly.py` and `dhan_bridge.py`
  still use broker **GET** APIs only (token refresh is an authentication POST).
- When/if live trade execution is a separately approved goal, the collector
  needs an audited signal-to-order handoff, verified market instrument ID,
  quote reference, risk sizing, STOP state, funds, durable idempotency,
  protected exit plan and order reconciliation. Never enable trading just to
  exercise the AMO demo.

Dhan's documented AMO fields:
https://dhanhq.co/docs/v2/orders/
