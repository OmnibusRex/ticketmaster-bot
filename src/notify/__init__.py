"""Optional Telegram and Discord notifications from environment settings."""

from __future__ import annotations

import asyncio
import logging
import os

from src.notifiers import DiscordNotifier, Notifier, NotifyEvent, TelegramNotifier

log = logging.getLogger("ticketmaster-bot")


async def notify(
    message: str, *, level: str = "info", event_type: str = "bot_status"
) -> dict[str, bool]:
    """Return whether each configured channel accepted the notification."""
    channels: dict[str, Notifier] = {}
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        channels["telegram"] = TelegramNotifier(token, chat)
    elif token or chat:
        log.warning("Telegram requires both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if webhook:
        channels["discord"] = DiscordNotifier(webhook)

    event = NotifyEvent(event_type, "Ticketmaster Bot", message[:1900], severity=level)
    results = await asyncio.gather(
        *(channel.notify(event) for channel in channels.values()), return_exceptions=True
    )
    delivered = {}
    for channel, result in zip(channels, results, strict=True):
        delivered[channel] = result is None
        if result is None:
            log.info("Notification accepted by %s", channel)
        else:
            log.warning("Notification failed on %s (%s)", channel, type(result).__name__)
    return delivered


__all__ = ["notify"]
