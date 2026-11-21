#!/usr/bin/env python3
"""Warm up a persistent Chromium profile before a real run.

Opens the browser (non-headless), visits the Ticketmaster home or event
URL, accepts cookies if present, and waits so you can log in manually.
The session is saved in the configured user_data_dir.

Usage:
    python scripts/warmup_profile.py
    python scripts/warmup_profile.py --url https://www.ticketmaster.com.br/
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here.parent) not in sys.path:
    sys.path.insert(0, str(_here.parent))

from src.utils.config_loader import load_config  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Warm up a Chromium profile")
    parser.add_argument("--url", default=None, help="URL to open (default: first event or TM home)")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--accounts", default="config/accounts.yaml")
    parser.add_argument("--wait", type=int, default=120, help="Seconds to keep the browser open")
    args = parser.parse_args()

    try:
        config = load_config(args.config, args.accounts)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 2

    url = args.url
    if not url and config.events:
        url = config.events[0].url
    if not url or "REPLACE" in url:
        url = "https://www.ticketmaster.com/"

    user_data = config.browser.user_data_dir or "sessions/default"
    Path(user_data).mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    print(f"Opening {url}")
    print(f"Profile: {user_data}")
    print(f"Browser will stay open for {args.wait}s — log in and accept cookies if needed.")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=False,
            locale=config.browser.locale,
            timezone_id=config.browser.timezone,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"Navigation warning: {exc}")

        # Try common cookie accept buttons.
        for sel in (
            "#onetrust-accept-btn-handler",
            "button:has-text('Aceitar')",
            "button:has-text('Accept')",
            "button:has-text('Aceito')",
        ):
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    print(f"Clicked cookie button: {sel}")
                    break
            except Exception:
                pass

        print("Waiting... close the browser or wait for the timer.")
        try:
            await asyncio.sleep(args.wait)
        except asyncio.CancelledError:
            pass
        await context.close()

    print("Profile warm-up finished. Session saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
