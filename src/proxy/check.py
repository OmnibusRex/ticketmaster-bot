"""Proxy connectivity checker.

Used at bot startup to verify that a configured proxy can reach the
internet and to log the public IP that will be presented to Ticketmaster.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .manager import parse_proxy_url

log = logging.getLogger("ticketmaster-bot")

# Lightweight IP-echo services (in order of preference).
_IP_ENDPOINTS = (
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


async def check_proxy(
    proxy_url: str,
    *,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Test a proxy URL and return the observed public IP.

    Parameters
    ----------
    proxy_url:
        Full proxy URL (``http://user:pass@host:port`` or ``socks5://...``).
    timeout_s:
        Request timeout.

    Returns
    -------
    dict with keys ``ok`` (bool), ``ip`` (str | None), ``error`` (str | None),
    and ``endpoint`` (str | None).

    Notes
    -----
    Uses ``httpx`` with the proxy. SOCKS proxies require the optional
    ``socksio`` extra (``pip install httpx[socks]``).
    """
    result: dict[str, Any] = {"ok": False, "ip": None, "error": None, "endpoint": None}

    try:
        parsed = parse_proxy_url(proxy_url)
    except ValueError as exc:
        result["error"] = str(exc)
        return result

    # Rebuild a URL that httpx understands.
    server = parsed["server"]
    if "username" in parsed:
        # httpx wants credentials in the proxy URL.
        scheme, rest = server.split("://", 1)
        user = parsed["username"]
        password = parsed.get("password", "")
        proxy_for_httpx = f"{scheme}://{user}:{password}@{rest}"
    else:
        proxy_for_httpx = server

    timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0))

    for endpoint in _IP_ENDPOINTS:
        try:
            async with httpx.AsyncClient(
                proxies=proxy_for_httpx,
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.get(endpoint)
                resp.raise_for_status()
                text = resp.text.strip()
                # api.ipify returns JSON; the others return plain IP.
                if text.startswith("{"):
                    import json

                    data = json.loads(text)
                    ip = data.get("ip") or data.get("query")
                else:
                    ip = text.split()[0] if text else None

                if ip and "." in ip or (ip and ":" in ip):  # basic IPv4/IPv6 sanity
                    result.update({"ok": True, "ip": ip, "endpoint": endpoint})
                    return result
        except Exception as exc:
            log.debug("proxy check failed for %s via %s: %s", endpoint, proxy_for_httpx, exc)
            result["error"] = str(exc)
            continue

    if not result["error"]:
        result["error"] = "all IP endpoints failed"
    return result


__all__ = ["check_proxy"]
