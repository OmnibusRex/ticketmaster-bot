"""Per-account proxy assignment for Playwright contexts.

The submodule exposes :class:`src.proxy.manager.ProxyManager`, which turns
a configured list of proxy URLs and an assignment policy
(``sticky``, ``round_robin`` or ``failover``) into the small kwarg dict
Playwright's ``launch_persistent_context`` expects.

Also exports:

* :func:`src.proxy.check.check_proxy` — connectivity test
* :func:`src.proxy.fallback.resolve_with_fallback` — automatic retry
"""

from __future__ import annotations

from .check import check_proxy
from .fallback import resolve_with_fallback
from .manager import ProxyManager, parse_proxy_url

__all__ = [
    "ProxyManager",
    "check_proxy",
    "parse_proxy_url",
    "resolve_with_fallback",
]
