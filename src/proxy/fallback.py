"""Automatic proxy fallback.

Tries proxies in order (respecting the manager's policy) until one
passes a connectivity check, then returns it. Failed proxies are
marked so subsequent resolves avoid them.
"""

from __future__ import annotations

import logging
from typing import Any

from .check import check_proxy
from .manager import ProxyManager

log = logging.getLogger("ticketmaster-bot")


async def resolve_with_fallback(
    manager: ProxyManager,
    account_name: str | None,
    *,
    max_attempts: int | None = None,
    check_timeout_s: float = 12.0,
) -> dict[str, str] | None:
    """Return a working proxy kwargs dict, or None.

    Tries up to ``max_attempts`` (default: number of configured proxies)
    different proxies. Each candidate is connectivity-checked. Failures
    are recorded on the manager so later calls prefer healthier proxies.

    Returns the first proxy that passes the check, or the last one tried
    if none succeed (so the caller can still attempt a launch).
    """
    if not manager.enabled:
        return None

    attempts = max_attempts if max_attempts is not None else max(manager.proxy_count, 1)
    last: dict[str, str] | None = None

    for i in range(attempts):
        proxy = manager.resolve(account_name)
        if proxy is None:
            return None
        last = proxy

        # Rebuild a URL for the checker.
        server = proxy.get("server", "")
        user = proxy.get("username")
        password = proxy.get("password", "")
        if user:
            scheme, rest = server.split("://", 1)
            url = f"{scheme}://{user}:{password}@{rest}"
        else:
            url = server

        log.info("proxy fallback attempt %d/%d: %s", i + 1, attempts, rest if user else server)
        result = await check_proxy(url, timeout_s=check_timeout_s)
        if result.get("ok"):
            manager.mark_success(proxy)
            log.info("proxy OK — public IP %s", result.get("ip"))
            return proxy

        manager.mark_failure(proxy)
        log.warning("proxy failed check: %s", result.get("error"))

    log.warning("all proxy attempts failed; returning last candidate")
    return last


__all__ = ["resolve_with_fallback"]
