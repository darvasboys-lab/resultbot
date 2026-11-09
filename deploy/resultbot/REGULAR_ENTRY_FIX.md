# Regular market-hours equity entry fix

The uploaded VM module required AMO ON and an after-market window for all entries. This patch adds an independent regular-session switch and persists the route on each intent. Regular orders submit `afterMarketOrder: false`, without `amoTime`; AMOs retain their original payload. Both remain CNC LIMIT orders, not leveraged INTRADAY product orders.

## Existing VM installation

Run `~/uditbot/venv/bin/python /path/to/checkout/deploy/resultbot/install_regular_entry.py` as the normal VM owner. The installer checks the uploaded module's exact hash, saves a private code/SQLite backup, stops the execution listener, replaces only the execution module and adds launcher commands, checks syntax/imports, and resumes the listener if it was running. It preserves credentials, source, budget, AMO state, strategy previews, settings UI and existing orders. No broker order is submitted by installation; LIVE initially defaults OFF. Reinstalling preserves its current switch.

- `bot live-on`: enables new regular-session signals using saved source/budget; broker checks are read-only.
- `bot live-off`: blocks new regular-session submissions; existing broker orders remain.
- `bot amo-off`: blocks new AMOs, leaving LIVE unchanged.
- `bot stop`: blocks both routes; does not cancel existing orders.
- `bot status`: displays LIVE and AMO independently.

The pre-existing settings menu is preserved. Its AMO OFF action remains AMO-only; it does not disable regular entries. Use `bot live-off` for regular entries or `bot stop` for both. AMO ON continues through the existing AMO arm command/menu.

## Price and session behavior

Regular entries require the existing verified exchange calendar and 09:15 <= IST time < 15:30, with a Yahoo metadata price no older than two seconds at final submission preparation. Missing/stale prices block the order with a reason; no previous close is substituted. The existing `intraday_buy_settings.json` buying buffer is used if present; otherwise the prior executor's 0.25% buffer is retained. Quantity is whole shares affordable at the tick-rounded limit, using the owner's saved budget. No new budget cap.

Old intents migrate to AMO, retaining IDs, amounts and history. The installer marks interrupted SUBMITTING work UNKNOWN for broker reconciliation, and prevents replay of interrupted RESERVED/PREPARING work. New activation does not replay old alerts. A fresh distinct alert is needed. Immediate/partial fills, rejections, cancellations and expired orders are tracked using the reserved route. Transport ambiguity is never an automatic retry.

This is an entry-path fix. It does not connect offline averaging/SL/OCO strategy planners to live execution, and it does not guarantee fills. Existing CNC product behavior is retained. No actual account or live VM smoke test has been performed from the development environment.

## Validation

Offline execution tests mock broker calls, credentials and Telegram side effects. Coverage includes both payloads, independent switches, migration, preserved budget, deduplication, stale alerts/quotes, market-boundary changes, immediate fills, unknown outcomes, and route-aware reconciliation. Existing fresh-installer tests also run; original captured VM files remain unchanged in vm_snapshot.

Dhan API contract: https://dhanhq.co/docs/v2/orders/
