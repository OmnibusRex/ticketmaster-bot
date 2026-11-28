from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.watch_prices import (
    EMPTY,
    LIST,
    PRICE,
    ROW,
    monitor,
    parse_price,
    positive_cents,
    positive_seconds,
    read_offers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "watch_prices"


def arguments(currency):
    return SimpleNamespace(
        url="https://fixture.invalid/event",
        list_selector=LIST,
        offer_selector=ROW,
        price_selector=PRICE,
        empty_selector=EMPTY,
        currency=currency,
        interval=0,
        once=False,
        max=30000,
    )


@pytest.mark.parametrize(
    ("text", "currency", "expected"),
    [
        ("R$ 1.250,00", "BRL", 125000),
        ("$1,250.00", "USD", 125000),
        ("$300.00", "USD", 30000),
        ("R$ 12.345,67", "BRL", 1234567),
        ("R 42", "BRL", None),
        ("5x R$ 50,00", "BRL", None),
        ("R$ 25,00", "USD", None),
        ("$50.000", "USD", None),
    ],
)
def test_parse_price(text, currency, expected):
    assert parse_price(text, currency) == expected


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "1.001"])
def test_invalid_threshold(value):
    with pytest.raises(argparse.ArgumentTypeError):
        positive_cents(value)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_invalid_interval(value):
    with pytest.raises(argparse.ArgumentTypeError):
        positive_seconds(value)


@pytest.mark.parametrize(
    ("market", "currency", "expected"),
    [("br", "BRL", [125000]), ("us", "USD", [125000, 30000])],
)
async def test_reads_only_visible_available_offer_prices(
    chromium_context, market, currency, expected
):
    page = await chromium_context.new_page()
    args = arguments(currency)
    html = (FIXTURES / f"{market}.html").read_text()

    async def serve(route):
        await route.fulfill(content_type="text/html", body=html)

    await page.route(args.url, serve)
    assert [offer.cents for offer in await read_offers(page, args)] == expected


async def test_alert_rearms_only_after_valid_price_above_threshold(chromium_context):
    page = await chromium_context.new_page()
    args = arguments("USD")
    html = (FIXTURES / "us.html").read_text()
    checks = iter(
        [
            html,
            html,
            None,
            '<p data-bdd="sold-out">Sold out</p>',
            html,
            html.replace("$300.00", "$301.00"),
            html,
        ]
    )
    attempted = []
    alerts = []

    async def serve(route):
        body = next(checks)
        attempted.append(body)
        await route.fulfill(status=200 if body else 503, content_type="text/html", body=body or "")

    async def record(message, *, event_type):
        alerts.append((message, event_type, len(attempted)))
        if len(alerts) == 2:
            raise asyncio.CancelledError
        return {"telegram": True}

    await page.route(args.url, serve)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(monitor(page, args, record), timeout=15)
    assert [(event, index) for _, event, index in alerts] == [
        ("price_alert", 1),
        ("price_alert", 7),
    ]


async def test_waits_for_offers_rendered_after_navigation(chromium_context):
    page = await chromium_context.new_page()
    args = arguments("BRL")
    html = """
    <main></main>
    <script>
    setTimeout(() => {
        document.querySelector('main').innerHTML = `
        <ul data-bdd="ticket-list"><li data-bdd="quick-pick-row">
        <span data-bdd="offer-price">R$ 350,00</span></li></ul>`;
    }, 300);
    </script>
    """

    async def serve(route):
        await route.fulfill(content_type="text/html", body=html)

    await page.route(args.url, serve)
    assert [offer.cents for offer in await read_offers(page, args)] == [35000]


@pytest.mark.parametrize("status", [200, 503])
async def test_once_reads_one_page_and_sends_nothing(chromium_context, capsys, status):
    page = await chromium_context.new_page()
    args = arguments("USD")
    args.once = True
    args.max = None
    visits = []
    notifications = []
    html = (FIXTURES / "us.html").read_text()

    async def serve(route):
        visits.append(route.request.url)
        await route.fulfill(status=status, content_type="text/html", body=html)

    async def record(*args, **kwargs):
        notifications.append(args)

    await page.route(args.url, serve)
    assert await monitor(page, args, record) == (0 if status == 200 else 1)
    assert visits == [args.url]
    assert notifications == []
    captured = capsys.readouterr()
    if status == 200:
        assert "USD 300.00" in captured.out
    else:
        assert "navigation failed (503)" in captured.err


async def test_retries_only_failed_channel_and_reports_outage(chromium_context, capsys):
    page = await chromium_context.new_page()
    args = arguments("USD")
    html = (
        (FIXTURES / "us.html")
        .read_text()
        .replace(
            '<span data-bdd="ticket-price">$300.00</span>',
            """
        <span data-bdd="ticket-price">$300.00</span>
        <span data-bdd="offer-section">Lower Bowl</span>
        <span data-bdd="offer-type">Adult</span>
        <span data-bdd="offer-fees">Fees included</span>
        """,
        )
    )
    sold_out = '<p data-bdd="sold-out">Sold out</p>'
    checks = iter([html, html, html, html, None, None, None, None, sold_out])
    calls = []

    async def serve(route):
        body = next(checks)
        await route.fulfill(status=200 if body else 503, content_type="text/html", body=body or "")

    async def record(message, *, event_type, only_channels=None, level="info"):
        calls.append((event_type, only_channels, message, level))
        if event_type == "watcher_recovered":
            raise asyncio.CancelledError
        if only_channels:
            return {"telegram": False}
        return {"discord": True, "telegram": False}

    await page.route(args.url, serve)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(monitor(page, args, record), timeout=15)
    assert [(kind, channels) for kind, channels, _, _ in calls] == [
        ("price_alert", None),
        ("price_alert", {"telegram"}),
        ("price_alert", {"telegram"}),
        ("watcher_degraded", None),
        ("watcher_recovered", None),
    ]
    assert (
        "Section: Lower Bowl | Ticket type: Adult | Fee information: Fees included" in calls[0][2]
    )
    assert "Last valid check: " in calls[3][2]
    assert calls[3][3] == "warning"
    assert "delivery retries exhausted" in capsys.readouterr().err
