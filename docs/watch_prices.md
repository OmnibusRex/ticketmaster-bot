# Price watcher

The watcher reads visible ticket offers from a Ticketmaster Brazil or US event
page. It monitors one URL at a time.

```bash
python scripts/watch_prices.py --url 'https://www.ticketmaster.com.br/event/EXAMPLE' --max 300
python scripts/watch_prices.py --max 180 --interval 90
```

With no URL argument, the watcher reads the first event from `--config`
(default `config/config.yaml`). It uses a separate Chromium profile in
`sessions/price-watcher`. Set `--user-data-dir` to use a different directory
for concurrent watchers. Install Playwright Chromium before running it.

The threshold is expressed in BRL for Brazil or USD for the US. Use a dot
for decimal input, for example `--max 299.90`. The watcher compares the
displayed offer price, which includes fees only when the page includes them
in that price. It ignores fees and installments in separate fields.

The default selectors recognize rows inside the ticket list and dedicated
ticket-price elements. The watcher also recognizes the configured sold-out
marker. If the event uses different markup, pass CSS selectors with
`--list-selector`, `--offer-selector`, `--price-selector` and
`--empty-selector`. The offer selector is relative to the list, and the
price selector is relative to an available row. The price element must
contain a single amount. Verify these selectors against the event layout
before relying on monitoring. Unrecognized markup and navigation failures
are reported as failed checks.

The first reading at or below the threshold triggers an alert. A later
valid price above the threshold rearms it. Failed checks and rounds with
no available offers leave that state unchanged. The default pause is 120
seconds after each check.

Alerts appear in the terminal. To also send them through Telegram, set
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the environment or in the
repository's `.env`. For Discord, set `DISCORD_WEBHOOK_URL`. The watcher
reports acceptance or failure for each configured channel.

## One-time check

```bash
python scripts/watch_prices.py --url 'https://www.ticketmaster.com.br/event/EXAMPLE' --once
```

Replace the example URL with the event to check. This mode makes one
check and prints the result without sending notifications or requiring
`--max`. It exits with status 0 after a valid check, including a recognized
sold-out page, or 1 if the watcher cannot read the page.

## Offer details and notification recovery

When present on the same offer row, the alert includes the section, ticket
type and explicitly displayed fee information. Set
`--section-selector`, `--ticket-type-selector` or `--fees-selector` to match
the page layout. Missing fee information stays unspecified.

When a price alert fails on a channel, the watcher retries that channel
on up to two subsequent valid checks while an offer remains within the
threshold. The message retains its original observation timestamp. A valid
check with no offers or a price above the threshold clears pending
deliveries. The retry state lives in memory and resets on restart.

After three consecutive failed checks the watcher issues a warning with
the UTC time of the last valid check. It sends one recovery notice when
the next valid reading arrives, including a recognized sold-out page.
