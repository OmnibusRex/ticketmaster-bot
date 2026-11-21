"""Per-account proxy assignment for Playwright contexts."""

from __future__ import annotations

from .check import check_proxy
from .fallback import resolve_with_fallback
from .manager import ProxyManager, parse_proxy_url
from .session_retry import run_with_proxy_retry

__all__ = [
    "ProxyManager",
    "check_proxy",
    "parse_proxy_url",
    "resolve_with_fallback",
    "run_with_proxy_retry",
]
