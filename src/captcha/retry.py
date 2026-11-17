"""Async exponential backoff with jitter."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger("ticketmaster-bot")

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 5,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    jitter: bool = True,
    description: str = "operation",
    retry_on: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Run ``fn`` with exponential backoff until it succeeds or attempts are exhausted.

    Parameters
    ----------
    fn:
        Zero-argument async callable to retry.
    max_attempts:
        Total number of tries (including the first).
    base_delay_s:
        Initial delay before the second attempt.
    max_delay_s:
        Cap on the delay between attempts.
    jitter:
        Add random jitter (0–25 % of the computed delay) to avoid thundering herds.
    description:
        Label used in log messages.
    retry_on:
        Exception types that should trigger a retry. Others are re-raised immediately.

    Returns
    -------
    The successful return value of ``fn``.

    Raises
    ------
    The last exception raised by ``fn`` if all attempts fail.
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter()
        try:
            result = await fn()
            latency_ms = int((time.perf_counter() - start) * 1000)
            if attempt > 1:
                log.info(
                    "retry %s success on attempt %d/%d latency=%dms",
                    description,
                    attempt,
                    max_attempts,
                    latency_ms,
                )
            return result
        except retry_on as exc:
            last_exc = exc
            latency_ms = int((time.perf_counter() - start) * 1000)
            if attempt == max_attempts:
                log.warning(
                    "retry %s failed after %d attempts (last error: %s)",
                    description,
                    max_attempts,
                    exc,
                )
                raise

            # Exponential backoff: base * 2^(attempt-1)
            delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
            if jitter:
                delay *= 0.75 + random.random() * 0.5  # ±25 %

            log.warning(
                "retry %s attempt=%d/%d latency=%dms error=%s; sleeping %.2fs",
                description,
                attempt,
                max_attempts,
                latency_ms,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    # Unreachable, but satisfies type checkers
    assert last_exc is not None
    raise last_exc
