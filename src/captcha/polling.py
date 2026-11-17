"""Robust async polling for captcha solver APIs (CapSolver, 2Captcha, etc.)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("ticketmaster-bot")


class PollingError(Exception):
    """Raised when polling exhausts its budget without a successful result."""


async def poll_until(
    check: Callable[[], Awaitable[Any | None]],
    *,
    timeout_s: float = 120.0,
    interval_s: float = 3.0,
    jitter_s: float = 0.5,
    description: str = "task",
) -> Any:
    """Poll ``check()`` until it returns a non-None value or the timeout is hit.

    Parameters
    ----------
    check:
        Async callable that returns a result when ready, or ``None`` while
        still processing. May raise on transient errors (they are logged and
        treated as "still waiting").
    timeout_s:
        Hard wall-clock limit for the entire poll.
    interval_s:
        Base sleep between attempts.
    jitter_s:
        Random extra delay (0..jitter_s) added to each interval.
    description:
        Human-readable label used in logs (e.g. "capsolver task 61138bb6").

    Returns
    -------
    The first non-None value returned by ``check()``.

    Raises
    ------
    PollingError
        If the timeout is reached.
    """
    deadline = time.monotonic() + timeout_s
    attempt = 0

    while True:
        attempt += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PollingError(
                f"Polling {description} timed out after {timeout_s:.1f}s "
                f"({attempt} attempts)"
            )

        try:
            result = await check()
        except Exception as exc:
            log.warning(
                "poll %s attempt=%d error=%s (continuing)",
                description,
                attempt,
                exc,
            )
            result = None

        if result is not None:
            latency_ms = int((time.monotonic() - (deadline - timeout_s)) * 1000)
            log.info(
                "poll %s success attempt=%d latency=%dms",
                description,
                attempt,
                latency_ms,
            )
            return result

        # Sleep with jitter, but never past the deadline
        sleep_for = min(interval_s + random.uniform(0, jitter_s), remaining)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
