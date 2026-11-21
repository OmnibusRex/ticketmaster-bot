#!/usr/bin/env python3
"""Watch Ticketmaster event prices and notify when they drop.

Opens the event page periodically, extracts visible prices, and
notifies (log + optional Telegram/Discord) when any price is at or
below the configured maximum.

Usage:
    python scripts/watch_prices.py --url https://www.ticketmaster.com/event/XXX --max 250
    python scripts/watch_prices.py --max 180 --interval 90
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here.parent) not in sys.path:
    sys.path.insert(0, str(_here.parent))

from src.utils.config_loader import load_config  # noqa: E402

PRICE_RE = re.compile(r"[\$R]\$?\s*([0-9]{1,4}(?:[.,][0-9]{2})?)")


def _parse_prices(text: str) -> list[float]:
    out: list[float] = []
    for m in PRICE_RE.finditer(text):
        raw = m.group(1).replace(",", ".")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


async def scrape_prices(url: str, user_data: str, headless: bool) -> list[float]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            body = await page.inner_text("body")
        finally:
            await context.close()
    return _parse_prices(body)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Watch ticket prices")
    parser.add_argument("--url", default=None)
    parser.add_argument("--max", type=float, required=True, help="Alert when price <= this")
    parser.add_argument("--interval", type=int, default=120, help="Seconds between checks")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--accounts", default="config/accounts.yaml")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    args = parser.parse_args()

    try:
        config = load_config(args.config, args.accounts)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    url = args.url
    if not url and config.events:
        url = config.events[0].url
    if not url or "REPLACE" in url:
        print("Provide --url or set a real event URL in config.", file=sys.stderr)
        return 2

    user_data = config.browser.user_data_dir or "sessions/default"
    Path(user_data).mkdir(parents=True, exist_ok=True)

    from src.notify import notify

    print(f"Watching {url}")
    print(f"Alert when price <= {args.max}")
    print(f"Interval: {args.interval}s")

    seen_alert = False
    while True:
        try:
            prices = await scrape_prices(url, user_data, args.headless)
        except Exception as exc:
            print(f"scrape error: {exc}")
            await asyncio.sleep(args.interval)
            continue

        if not prices:
            print("No prices found this round.")
        else:
            cheapest = min(prices)
            print(f"Prices: {sorted(prices)[:8]} ... cheapest={cheapest}")
            if cheapest <= args.max:
                msg = f"Price alert! {cheapest} <= {args.max} on {url}"
                print(msg)
                await notify(msg)
                seen_alert = True
            else:
                print(f"No match (cheapest {cheapest} > {args.max})")

        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
