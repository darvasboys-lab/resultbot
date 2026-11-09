# ResultBot Telegram Control Center (private owner pairing)

This is a **new, separate** service and Telegram bot. It does not reuse the
existing Telethon listener session, does not open an incoming network port and
does not provide an arbitrary Linux shell.

## Before installation

1. Open Telegram **@BotFather**, use `/newbot`, and create a dedicated bot.
2. Add it as an ordinary member of your **private terminal group**. It does
   not need administrator privileges. Leave BotFather **Privacy Mode ON**:
   commands and inline button taps are sufficient.
3. Keep the BotFather token private. Paste it **only in the VM terminal**
   at the installer prompt; never into a Telegram group, ticket or GitHub.

## Install on the existing ResultBot VM

Run as the ordinary `resultbot` VM user, not root.

```bash
(
set -e
DIR="$(mktemp -d "$HOME/resultbot-telegram-panel.XXXXXX")"
git clone --depth 1 --single-branch \
 --branch feature/resultbot-telegram-control-center-20261109 \
 https://github.com/darvasboys-lab/resultbot.git "$DIR"
"$HOME/uditbot/venv/bin/python" "$DIR/deploy/resultbot/telegram_remote.py" self-test
"$HOME/uditbot/venv/bin/python" "$DIR/deploy/resultbot/telegram_remote.py" install
systemctl --user is-active resultbot-telegram-control.service
)
```

When prompted, enter your new bot token using the hidden input. The installer
validates it with Telegram, refuses to take over a bot with an existing webhook,
and prints a private pairing command valid for 15 minutes.

**Pairing:** Send `/rb pair <printed-code>` to your new bot in a **private DM**.
Then, in your dedicated terminal group, send `/rb bind` from the same Telegram
account. The bot replies with the menu. Only that Telegram user ID **and**
this exact group ID can issue ResultBot commands. Anonymous-admin group posts
cannot be used to bind; post as your own Telegram account.

## Commands in bound group

- `/rb`, `/rb help`, `/rb status`, `/rb setup`
- `/rb orders`, `/rb funds`, `/rb oco`, `/rb logs`
- `/rb budget 1000` (enter your preferred positive whole-rupee amount)
- `/rb source both`, `/rb source test`, `/rb source earnings`
- `/rb live on`, `/rb live off`, `/rb amo on`, `/rb amo off`
- `/rb stop` (switch OFF new regular and AMO entries; not a broker cancel)

All state-changing commands need **a second click on CONFIRM** within 60
seconds. The broker-dependent LIVE/AMO checks remain enforced by the existing
ResultBot command implementation. Merely running `/rb` does not trade.

If another bot in the group handles slash commands, explicitly address the
command to this bot as `/rb@YourBotUsername status`.

## Troubleshooting

```bash
systemctl --user status resultbot-telegram-control.service --no-pager
journalctl --user -u resultbot-telegram-control.service -n 25 --no-pager
```

If a pairing code expired:

```bash
~/uditbot/venv/bin/python ~/resultbot-data/telegram_remote.py pair-code
```

A paired bot cannot be silently reassigned to a different group through
Telegram commands. To revoke access, disable the service and rotate the token
in BotFather; the private file `~/resultbot-data/.telegram_remote.json`
contains your token and the bound identifiers, mode 0600.

The Telegram menu is a group-visible chat interface. Use a one-person private
terminal group for sensitive balances and trade details.

## Design boundaries

- No `shell`, `exec`, `sudo`, or arbitrary command forwarding.
- No new Telegram userbot session; the original alert listener remains alone.
- Bot API long polling; no inbound firewall, public HTTP, or webhook.
- Existing LIVE, AMO, OCO and risk/quote checks are not replaced.
- A control command cannot cancel an existing Dhan order; the `stop` action
  only disables **new** entries.
- Read-only messages reflect saved data and the last guard checks; they do not
  by themselves prove the current live OCO status.
