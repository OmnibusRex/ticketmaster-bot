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
