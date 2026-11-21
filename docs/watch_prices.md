# Price watcher

`scripts/watch_prices.py` polls an event page and notifies when a visible
price is at or below a threshold.

## Usage

```bash
python scripts/watch_prices.py --url https://www.ticketmaster.com/event/XXX --max 250
python scripts/watch_prices.py --max 180 --interval 90
python scripts/watch_prices.py --url https://www.ticketmaster.com.br/event/YYY --max 300 --no-headless
```

## Behaviour

- Opens the page with the persistent profile (`browser.user_data_dir`).
- Extracts currency-like numbers from the page text.
- Logs the cheapest price each round.
- Sends a Telegram/Discord message (if configured in `.env`) when the
  cheapest price is `<= --max`.

## Notes

- First run works better after a profile warm-up
  (`python scripts/warmup_profile.py`).
- Price parsing is best-effort (looks for `$` / `R$` patterns).
- For a real purchase, still use the main bot with a strategy and
  `max_price`.
