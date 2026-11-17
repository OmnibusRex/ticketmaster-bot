"""Entry point for the Ticketmaster bot.

The argparse surface lives in :mod:`src.cli`. This module is responsible
for wiring the parsed namespace into config loading, vendor resolution,
account selection, and the runner launch path.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Allow `python src/main.py` and `python -m src.main` both to work
_here = Path(__file__).resolve().parent
if str(_here.parent) not in sys.path:
    sys.path.insert(0, str(_here.parent))

from src.cli import parse_args, parse_events_flag, parse_set_overrides  # noqa: E402
from src.registry import vendors as vendor_registry  # noqa: E402
from src.utils.config_loader import config_to_yaml, load_config  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402

#: Default vendor adapter name when no host-specific match is found.
DEFAULT_VENDOR = "ticketmaster"

#: Mapping from event-URL host suffix to vendor adapter name.
#: ticketmaster.com.br falls through to the default US adapter (no dedicated BR adapter yet).
_HOST_VENDOR_MAP: tuple[tuple[str, str], ...] = (
    ("ticketmaster.sg", "ticketmaster_sg"),
    # ticketmaster.com.br → DEFAULT_VENDOR (US adapter used as best-effort)
)


def detect_vendor_for_url(url: str) -> str:
    """Return the vendor adapter name implied by ``url``'s host."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return DEFAULT_VENDOR
    for suffix, vendor in _HOST_VENDOR_MAP:
        if host == suffix or host.endswith("." + suffix):
            return vendor
    return DEFAULT_VENDOR


def _resolve_vendor_adapter(name: str) -> Any:
    """Look up the vendor adapter class for ``name`` or raise ValueError."""
    import src.vendors  # noqa: F401  (registers all VendorAdapters)

    try:
        return vendor_registry.get(name)
    except Exception as exc:
        available = ", ".join(sorted(vendor_registry.all())) or "<none>"
        raise ValueError(f"Unknown vendor {name!r}. Registered vendors: {available}") from exc


async def _check_proxies(config, log) -> None:
    """If proxies are enabled, test the first one and log the public IP."""
    if not config.proxy.enabled or not config.proxy.urls:
        return

    from src.proxy.check import check_proxy

    proxy_url = config.proxy.urls[0]
    safe = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
    log.info("Testing proxy connectivity: %s", safe)
    result = await check_proxy(proxy_url)
    if result["ok"]:
        log.info("Proxy OK — public IP: %s (via %s)", result["ip"], result["endpoint"])
    else:
        log.warning("Proxy check failed: %s", result["error"])
        log.warning("Continuing anyway — the browser may still work if the proxy is intermittent.")


def _readiness_warnings(config, log) -> None:
    """Emit warnings for common missing pieces before launch."""
    # Placeholder event URL
    url = config.events[0].url if config.events else ""
    if "REPLACE_WITH" in url or not url.startswith("http"):
        log.warning("Event URL looks like a placeholder: %s", url)

    # Accounts
    if not config.accounts:
        log.warning("No accounts configured. Set accounts.yaml or TM_EMAIL / TM_PASSWORD.")

    # Captcha keys when auto_solve is on
    if config.captcha.auto_solve:
        if "capsolver" in config.captcha.providers and not os.getenv("CAPSOLVER_API_KEY"):
            log.warning("captcha.auto_solve is on and capsolver is listed, but CAPSOLVER_API_KEY is unset.")
        if "openai_vlm" in config.captcha.providers and not os.getenv("OPENAI_API_KEY"):
            log.warning("captcha.auto_solve is on and openai_vlm is listed, but OPENAI_API_KEY is unset.")

    # Brazil note
    host = (urlparse(url).hostname or "").lower()
    if "ticketmaster.com.br" in host:
        log.info("ticketmaster.com.br detected — using US adapter as best-effort (no dedicated BR adapter).")


async def main_async(args: argparse.Namespace | None = None) -> int:
    if args is None:
        args = parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    bootstrap_log = logging.getLogger("ticketmaster-bot")

    adapter_cls: Any | None = None
    if args.vendor is not None:
        try:
            adapter_cls = _resolve_vendor_adapter(args.vendor)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        overrides = parse_set_overrides(args.set_overrides)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    event_urls = parse_events_flag(args.events)
    if event_urls:
        overrides["events"] = [{"url": url} for url in event_urls]

    try:
        config = load_config(
            args.config,
            args.accounts,
            profile=args.profile,
            overrides=overrides,
        )
    except Exception:
        bootstrap_log.exception("Failed to load config")
        return 2

    if args.headless is not None:
        config.browser.headless = args.headless
    if args.auto_purchase is not None:
        config.checkout.auto_purchase = args.auto_purchase

    if adapter_cls is None:
        vendor_name = detect_vendor_for_url(config.events[0].url)
        try:
            adapter_cls = _resolve_vendor_adapter(vendor_name)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        resolved_vendor = vendor_name
    else:
        resolved_vendor = args.vendor

    if args.explain:
        sys.stdout.write(config_to_yaml(config))
        sys.stdout.flush()
        return 0

    log = setup_logger(
        level=config.logging.level,
        log_file=config.logging.file,
        format=config.logging.format,
    )
    log.info("=" * 60)
    log.info("Ticketmaster Bot starting")
    log.info("Vendor: %s", resolved_vendor)
    for i, evt in enumerate(config.events, start=1):
        log.info("Event %d: %s", i, evt.url)
    log.info(
        "Strategy: %s | Quantity: %d | Auto-purchase: %s",
        config.tickets.strategy,
        config.tickets.quantity,
        config.checkout.auto_purchase,
    )
    if args.parallel or args.max_parallel is not None or args.stagger_seconds is not None:
        log.info(
            "Parallel: %s | max_parallel: %s | stagger_seconds: %s",
            args.parallel,
            args.max_parallel,
            args.stagger_seconds,
        )
    log.info("=" * 60)

    _readiness_warnings(config, log)
    await _check_proxies(config, log)

    account = None
    if args.account_name:
        for a in config.accounts:
            if a.name == args.account_name:
                account = a
                break
        if account is None:
            log.error("No account named %r found", args.account_name)
            return 2

    if args.dry_run:
        log.info("Dry run: config validated successfully. Exiting before launch.")
        return 0

    if args.parallel:
        from src.orchestrator.parallel import ParallelCoordinator

        if not config.accounts:
            log.error("--parallel requires at least one account in accounts.yaml")
            return 2
        if args.account_name and account is not None:
            parallel_accounts = [account]
        else:
            parallel_accounts = list(config.accounts)
        max_parallel = args.max_parallel if args.max_parallel is not None else 3
        stagger_seconds = args.stagger_seconds if args.stagger_seconds is not None else 5.0

        adapter_cls()

        def _factory(acct, cfg, _stop):  # noqa: ANN001
            return adapter_cls().build_runner(cfg, account=acct)

        coordinator = ParallelCoordinator(
            accounts=parallel_accounts,
            config=config,
            max_parallel=max_parallel,
            stagger_seconds=stagger_seconds,
            runner_factory=_factory,
        )
        try:
            success = await coordinator.run()
        except KeyboardInterrupt:
            log.warning("Interrupted by user")
            return 130
        except Exception:
            log.exception("Parallel coordinator crashed")
            return 1
        return 0 if success else 1

    adapter = adapter_cls()
    runner = adapter.build_runner(config, account=account)
    try:
        success = await runner.run()
    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        return 130
    except Exception:
        log.exception("Bot crashed")
        return 1
    return 0 if success else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
