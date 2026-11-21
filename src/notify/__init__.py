"""Optional outbound notifications (Telegram / Discord webhooks).

No external dependency beyond httpx (already used by the captcha stack).
Configure via environment variables; silently no-ops when unset.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("ticketmaster-bot")


async def notify(message: str, *, level: str = "info") -> None:
    """Send a message to configured webhooks. Best-effort, never raises."""
    tasks = []
    tg = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if tg and chat:
        tasks.append(_telegram(tg, chat, message))

    discord = os.getenv("DISCORD_WEBHOOK_URL")
    if discord:
        tasks.append(_discord(discord, message))

    if not tasks:
        return

    import asyncio

    await asyncio.gather(*tasks, return_exceptions=True)


async def _telegram(token: str, chat_id: str, text: str) -> None:
    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text[:4000]})
    except Exception as exc:
        log.debug("telegram notify failed: %s", exc)


async def _discord(webhook: str, text: str) -> None:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook, json={"content": text[:2000]})
    except Exception as exc:
        log.debug("discord notify failed: %s", exc)


__all__ = ["notify"]
