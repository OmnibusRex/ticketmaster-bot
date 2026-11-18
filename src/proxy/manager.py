"""Per-account proxy assignment for Playwright launches.

:class:`ProxyManager` is the single entry point. Given a configured
:class:`~src.utils.config_loader.ProxyConfig` (a list of proxy URLs and
an assignment policy), :meth:`ProxyManager.resolve` returns the small
kwarg dict Playwright's ``launch_persistent_context`` expects::

    {"server": "http://host:port", "username": "u", "password": "p"}

The ``username``/``password`` keys are emitted **only** when the
underlying URL carries credentials, matching Playwright's own
``ProxySettings`` type where both fields are optional.

Policies:

* ``sticky`` — every call for a given ``account_name`` returns the same
  proxy. Mapping is deterministic across processes.
* ``round_robin`` — each call advances a counter.
* ``failover`` — skips proxies that have been marked as failed recently.
  Falls back to round-robin when all proxies are in a failed state.

Callers can report success/failure with :meth:`mark_success` and
:meth:`mark_failure` so the manager can avoid bad proxies.

If the proxy list is empty *or* the ``enabled`` flag is false, every
:meth:`resolve` returns ``None``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from ..utils.config_loader import ProxyConfig

log = logging.getLogger("ticketmaster-bot")

VALID_POLICIES: frozenset[str] = frozenset({"sticky", "round_robin", "failover"})

# How long a failed proxy stays avoided (seconds).
_FAIL_COOLDOWN_S = 300.0
# After this many failures the proxy is avoided until cooldown expires.
_FAIL_THRESHOLD = 2


def parse_proxy_url(url: str) -> dict[str, str]:
    """Parse a proxy URL into Playwright's ``proxy=`` kwarg shape."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("proxy URL must be a non-empty string")
    trimmed = url.strip()
    if "://" not in trimmed:
        raise ValueError(
            f"proxy URL {trimmed!r} is missing a scheme (e.g. 'http://', 'https://', 'socks5://')"
        )
    parsed = urlparse(trimmed)
    if not parsed.scheme:
        raise ValueError(
            f"proxy URL {url!r} is missing a scheme (e.g. 'http://', 'https://', 'socks5://')"
        )
    if not parsed.hostname:
        raise ValueError(f"proxy URL {url!r} is missing a host")

    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{parsed.hostname}:{parsed.port}"
    server = f"{parsed.scheme}://{netloc}"
    if parsed.path and parsed.path != "/":
        server += parsed.path

    out: dict[str, str] = {"server": server}
    if parsed.username:
        out["username"] = unquote(parsed.username)
    if parsed.password:
        out["password"] = unquote(parsed.password)
    return out


class ProxyManager:
    """Resolve a Playwright ``proxy=`` kwarg dict for a given account.

    Supports sticky, round-robin, and failover (skip recently failed)
    policies. Thread-safe.
    """

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._parsed: list[dict[str, str]] = [parse_proxy_url(u) for u in config.urls]
        if config.policy not in VALID_POLICIES:
            raise ValueError(
                f"proxy.policy must be one of {sorted(VALID_POLICIES)}, got {config.policy!r}"
            )
        self._policy: str = config.policy
        self._lock = threading.Lock()
        self._round_robin_index: int = 0
        self._sticky_assignments: dict[str, int] = {}
        self._next_sticky_index: int = 0

        # Failover state: index -> (fail_count, last_fail_monotonic)
        self._failures: dict[int, tuple[int, float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._parsed) and self._config.enabled

    @property
    def policy(self) -> str:
        return self._policy

    @property
    def proxy_count(self) -> int:
        return len(self._parsed)

    def _is_available(self, index: int, now: float) -> bool:
        """Return True if the proxy at ``index`` is not in cooldown."""
        info = self._failures.get(index)
        if info is None:
            return True
        count, last = info
        if count < _FAIL_THRESHOLD:
            return True
        return (now - last) >= _FAIL_COOLDOWN_S

    def resolve(self, account_name: str | None) -> dict[str, str] | None:
        """Return the proxy kwargs for ``account_name`` or ``None``."""
        if not self.enabled or not self._parsed:
            return None

        with self._lock:
            n = len(self._parsed)
            now = time.monotonic()

            if self._policy == "sticky":
                key = account_name or ""
                if key not in self._sticky_assignments:
                    # Prefer an available proxy for new sticky assignments.
                    chosen = None
                    for i in range(n):
                        idx = (self._next_sticky_index + i) % n
                        if self._is_available(idx, now):
                            chosen = idx
                            break
                    if chosen is None:
                        chosen = self._next_sticky_index % n
                    self._sticky_assignments[key] = chosen
                    self._next_sticky_index += 1
                index = self._sticky_assignments[key]

            elif self._policy == "failover":
                # Prefer available proxies; fall back to any if all are cooling down.
                index = None
                for i in range(n):
                    idx = (self._round_robin_index + i) % n
                    if self._is_available(idx, now):
                        index = idx
                        break
                if index is None:
                    index = self._round_robin_index % n
                self._round_robin_index = (index + 1) % n

            else:  # round_robin
                index = self._round_robin_index % n
                self._round_robin_index += 1

            return dict(self._parsed[index])

    def mark_failure(self, proxy_kwargs: dict[str, str] | None) -> None:
        """Record a failure for the proxy that produced ``proxy_kwargs``.

        After ``_FAIL_THRESHOLD`` failures the proxy is avoided for
        ``_FAIL_COOLDOWN_S`` seconds (failover / sticky new assignments).
        """
        if not proxy_kwargs:
            return
        with self._lock:
            index = self._find_index(proxy_kwargs)
            if index is None:
                return
            count, _ = self._failures.get(index, (0, 0.0))
            self._failures[index] = (count + 1, time.monotonic())
            log.debug("proxy[%d] failure count=%d", index, count + 1)

    def mark_success(self, proxy_kwargs: dict[str, str] | None) -> None:
        """Clear failure state for a proxy that just worked."""
        if not proxy_kwargs:
            return
        with self._lock:
            index = self._find_index(proxy_kwargs)
            if index is None:
                return
            if index in self._failures:
                del self._failures[index]
                log.debug("proxy[%d] success — failure state cleared", index)

    def _find_index(self, proxy_kwargs: dict[str, str]) -> int | None:
        """Return the index of a proxy matching the given kwargs, or None."""
        server = proxy_kwargs.get("server")
        username = proxy_kwargs.get("username")
        for i, p in enumerate(self._parsed):
            if p.get("server") == server and p.get("username") == username:
                return i
        return None


__all__ = ["VALID_POLICIES", "ProxyManager", "parse_proxy_url"]
