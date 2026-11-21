"""Session-level retry with proxy rotation.

Wraps a runner so that if it fails with a proxy-related error the
manager marks the proxy as failed and the caller can relaunch.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from .fallback import resolve_with_fallback
from .manager import ProxyManager

log = logging.getLogger("ticketmaster-bot")


async def run_with_proxy_retry(
    runner_factory: Callable[..., Any],
    manager: ProxyManager | None,
    account_name: str | None,
    *,
    max_launches: int = 2,
) -> bool:
    """Launch the runner up to ``max_launches`` times.

    On failure, mark the current proxy as failed and try the next one.
    Returns True if any launch succeeds.
    """
    last_proxy = None
    for attempt in range(1, max_launches + 1):
        proxy = None
        if manager is not None and manager.enabled:
            proxy = await resolve_with_fallback(manager, account_name)
            last_proxy = proxy

        log.info("session attempt %d/%d", attempt, max_launches)
        runner = runner_factory()
        # If the runner exposes a way to inject proxy, use it; otherwise
        # the factory is expected to have already bound the config.
        try:
            success = await runner.run()
            if success:
                if manager is not None and proxy is not None:
                    manager.mark_success(proxy)
                return True
            if manager is not None and proxy is not None:
                manager.mark_failure(proxy)
        except Exception:
            log.exception("session attempt %d crashed", attempt)
            if manager is not None and proxy is not None:
                manager.mark_failure(proxy)

    return False


__all__ = ["run_with_proxy_retry"]
