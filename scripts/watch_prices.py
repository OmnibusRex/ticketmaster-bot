#!/usr/bin/env python3
"""Monitor displayed ticket offers without interacting with the sale flow."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BRL = re.compile(r"R\$\s*((?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)(?:,[0-9]{2})?)")
USD = re.compile(r"\$\s*((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{2})?)")
LIST = "[data-bdd='ticket-list'], [data-bdd='quick-picks-list']"
ROW = "[data-bdd='quick-pick-row']"
PRICE = "[data-bdd='ticket-price'], [data-bdd='offer-price']"
EMPTY = "[data-bdd='sold-out']"
UNAVAILABLE = '[aria-disabled="true"], [disabled], [data-available="false"]'

FAILURE_LIMIT = 3
DELIVERY_RETRIES = 2
CONTEXT_FIELDS = {
    "section": "[data-bdd='section'], [data-bdd='offer-section']",
    "ticket_type": "[data-bdd='ticket-type'], [data-bdd='offer-type']",
    "fees": "[data-bdd='fee-status'], [data-bdd='offer-fees']",
}


@dataclass(frozen=True)
class Offer:
    cents: int
    section: str = ""
    ticket_type: str = ""
    fees: str = ""

    def describe(self, currency: str) -> str:
        details = [f"{currency} {self.cents // 100}.{self.cents % 100:02d}"]
        for label, value in (
            ("Section", self.section),
            ("Ticket type", self.ticket_type),
            ("Fee information", self.fees),
        ):
            if value:
                details.append(f"{label}: {value}")
        return " | ".join(details)


@dataclass
class PendingAlert:
    message: str
    channels: set[str]
    retries_left: int = DELIVERY_RETRIES


class UnreadableOffers(Exception):
    """The page did not provide a trustworthy offer snapshot."""


def parse_price(text: str, currency: str) -> int | None:
    normalized = " ".join(text.split())
    if currency == "BRL":
        match = BRL.fullmatch(normalized)
        if match:
            amount = match[1].replace(".", "").replace(",", ".")
    else:
        match = USD.fullmatch(normalized)
        if match:
            amount = match[1].replace(",", "")
    if not match:
        return None
    whole, _, fraction = amount.partition(".")
    return int(whole) * 100 + int(fraction or "0")


def positive_cents(value: str) -> int:
    try:
        amount = Decimal(value)
        cents = amount * 100
        if amount.is_finite() and amount > 0 and cents == cents.to_integral_value():
            return int(cents)
    except (InvalidOperation, OverflowError):
        pass
    raise argparse.ArgumentTypeError("use a positive amount with up to two decimal places")


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("interval must be positive")
    return seconds


def configured_url(path: Path) -> str:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    events = config.get("events")
    event = events[0] if events else config.get("event", {})
    return event["url"]


async def read_offers(page, args) -> list[Offer]:
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    response = await page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
    if response is None or not response.ok:
        status = response.status if response else "no response"
        raise UnreadableOffers(f"navigation failed ({status})")
    if urlsplit(page.url).hostname != urlsplit(args.url).hostname:
        raise UnreadableOffers("page redirected to another host")

    empty = page.locator(args.empty_selector).and_(page.locator("css=:visible"))
    rows = (
        page.locator(args.list_selector)
        .and_(page.locator("css=:visible"))
        .locator(args.offer_selector)
    )
    rows = rows.and_(page.locator("css=:visible"))
    available = rows.and_(page.locator(f":not(:is({UNAVAILABLE}))"))
    ready = rows.or_(empty)
    try:
        await ready.first.wait_for(state="visible", timeout=12000)
    except PlaywrightTimeout:
        raise UnreadableOffers("no recognized offer rows or sold-out marker") from None

    if await empty.count() and not await rows.count():
        return []

    offers = []
    for row in await available.all():
        price = row.locator(args.price_selector).and_(page.locator("css=:visible"))
        try:
            text = await price.inner_text(timeout=3000)
        except PlaywrightTimeout:
            raise UnreadableOffers("an available row has no readable price") from None
        cents = parse_price(text, args.currency)
        if cents is None:
            raise UnreadableOffers(f"an offer has an ambiguous {args.currency} price")
        details = {}
        for name, default_selector in CONTEXT_FIELDS.items():
            selector = getattr(args, f"{name}_selector", default_selector)
            fields = row.locator(selector).and_(page.locator("css=:visible"))
            values = dict.fromkeys(
                " ".join(value.split()) for value in await fields.all_inner_texts()
            )
            details[name] = " / ".join(value for value in values if value)[:200]
        offers.append(Offer(cents, **details))
    return offers


async def check(page, args) -> tuple[datetime, Offer | None] | None:
    from playwright.async_api import Error as PlaywrightError

    try:
        offers = await read_offers(page, args)
    except (PlaywrightError, UnreadableOffers) as exc:
        print(f"Offer check failed: {exc}", file=sys.stderr, flush=True)
        return None

    observed_at = datetime.now(timezone.utc)
    cheapest = min(offers, key=lambda offer: offer.cents, default=None)
    if cheapest is None:
        print(f"{observed_at.isoformat()} | No available offers.", flush=True)
    else:
        print(
            f"{observed_at.isoformat()} | Lowest displayed offer: {cheapest.describe(args.currency)}",
            flush=True,
        )
    return observed_at, cheapest


def failed_channels(results: dict[str, bool]) -> set[str]:
    if not results:
        print("No notification channels configured.", flush=True)
    for channel, delivered in results.items():
        print(f"{channel}: {'accepted' if delivered else 'failed'}", flush=True)
    return {channel for channel, delivered in results.items() if not delivered}


async def monitor(page, args, notify) -> int:
    below_threshold = False
    pending = None
    last_valid = None
    failures = 0
    while True:
        reading = await check(page, args)
        if args.once:
            return 0 if reading is not None else 1

        if reading is None:
            failures += 1
            if failures == FAILURE_LIMIT:
                last = last_valid.isoformat() if last_valid else "none since startup"
                message = f"Price watcher has {failures} consecutive failed checks. Last valid check: {last}."
                print(message, file=sys.stderr, flush=True)
                await notify(message, event_type="watcher_degraded", level="warning")
        else:
            last_valid, cheapest = reading
            if failures >= FAILURE_LIMIT:
                message = f"Price watcher recovered at {last_valid.isoformat()}."
                print(message, flush=True)
                await notify(message, event_type="watcher_recovered")
            failures = 0

            if cheapest is None:
                pending = None
            elif cheapest.cents > args.max:
                below_threshold = False
                pending = None
            elif not below_threshold:
                below_threshold = True
                message = (
                    f"Price alert: {cheapest.describe(args.currency)} | "
                    f"Observed at {last_valid.isoformat()} | {args.url}"
                )
                print(message, flush=True)
                failed = failed_channels(await notify(message, event_type="price_alert"))
                if failed:
                    pending = PendingAlert(message, failed)
            elif pending is not None:
                results = await notify(
                    pending.message, event_type="price_alert", only_channels=pending.channels
                )
                pending.channels = failed_channels(results)
                pending.retries_left -= 1
                if not pending.channels or pending.retries_left == 0:
                    if pending.channels:
                        print(
                            "Price alert delivery retries exhausted.", file=sys.stderr, flush=True
                        )
                    pending = None

        await asyncio.sleep(args.interval)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Watch displayed ticket prices")
    parser.add_argument("--url")
    parser.add_argument("--max", type=positive_cents)
    parser.add_argument(
        "--once", action="store_true", help="read once without sending notifications"
    )
    parser.add_argument("--interval", type=positive_seconds, default=120)
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--accounts", help=argparse.SUPPRESS)
    parser.add_argument("--user-data-dir", default="sessions/price-watcher")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--list-selector", default=LIST)
    parser.add_argument("--offer-selector", default=ROW)
    parser.add_argument("--price-selector", default=PRICE)
    parser.add_argument("--empty-selector", default=EMPTY)
    for name, selector in CONTEXT_FIELDS.items():
        parser.add_argument(f"--{name.replace('_', '-')}-selector", default=selector)
    args = parser.parse_args()
    if args.max is None and not args.once:
        parser.error("--max is required unless --once is set")
    try:
        args.url = args.url or configured_url(args.config)
        url = urlsplit(args.url)
        if url.scheme not in ("http", "https") or "REPLACE" in args.url:
            raise ValueError("provide a real HTTP(S) event URL")
        host = url.hostname or ""
        if host == "ticketmaster.com.br" or host.endswith(".ticketmaster.com.br"):
            args.currency = "BRL"
        elif host == "ticketmaster.com" or host.endswith(".ticketmaster.com"):
            args.currency = "USD"
        else:
            raise ValueError("provide a Ticketmaster Brazil or US event URL")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        parser.error(str(exc))

    load_dotenv(_ROOT / ".env")
    from playwright.async_api import async_playwright

    from src.notify import notify

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=args.user_data_dir,
            headless=args.headless,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            return await monitor(page, args, notify)
        finally:
            await context.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
