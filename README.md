# eldobot

A private Discord bot for managing an Eldorado seller profile through the Seller API.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill it in
```

1. **Eldorado credential:** [Dashboard > Seller API](https://www.eldorado.gg/dashboard/seller-api), generate a credential, and put the Client ID and secret in `.env`.
2. **Discord bot:** create an application at <https://discord.com/developers/applications>, add a bot, copy its token into `.env`, and invite it to your server with the `bot` and `applications.commands` scopes.
3. **Discord IDs:** turn on Developer Mode in Discord, then right-click to copy your server ID, your own user ID, and the channel for notifications.

## Run

```sh
.venv/bin/python -m eldobot                    # start the bot (keep it running to get alerts)
.venv/bin/python -m eldobot.tools.check_auth   # verify the Eldorado credential (read-only)
.venv/bin/python -m unittest discover -s tests -t .
```

## Alerts

The Seller API has no webhooks, so the bot polls Eldorado once a minute and posts to
`DISCORD_NOTIFY_CHANNEL_ID` (or DMs you if that is unset):

- new orders, and every later state change (delivered, disputed, cancelled, completed)
- new boosting requests
- any other Eldorado notification (reviews, warranty claims, account notices)

Every alert pings you except new boosting requests, which post silently. The first run records what
already exists without announcing it. What has been announced is kept in `eldobot.db`.

## Commands

Only the Discord user in `DISCORD_OWNER_ID` can run commands, and replies are visible only to
you. Offer and order arguments autocomplete from your live listings. Every command that
changes something asks for confirmation first.

| Command | What it does |
|---|---|
| `/status` | Checks the Eldorado connection and unread notification count |
| `/online`, `/offline` | Switches your seller status |
| `/items list\|pause\|resume\|price\|quantity\|delete` | Manages item offers |
| `/accounts list\|pause\|resume\|price\|delete` | Manages account offers |
| `/orders list` | Lists recent sales, optionally by state |
| `/orders deliver\|cancel\|extend\|fix-details` | Marks delivered, cancels, extends delivery time, or asks the buyer to correct delivery details |
| `/boost requests` | Lists boosting requests you received |
| `/boost offer\|withdraw` | Sends or deletes an offer on a boosting request |
| `/boost subscriptions` | Shows the boosting categories you receive requests for |
| `/notifications read` | Marks all Eldorado notifications as read |

Prices are in USD.

## Notes

- Eldorado has no test environment. Every write acts on live offers and orders.
- The API cannot send chat messages. Eldorado still expects you to message the buyer yourself.
- Creating offers is not in the bot: it needs Eldorado's game and attribute IDs, so use the site.
- The endpoint reference is at <https://www.eldorado.gg/swagger/seller/index.html> (log in first).
