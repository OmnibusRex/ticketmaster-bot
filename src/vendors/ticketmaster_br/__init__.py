"""Ticketmaster Brazil (ticketmaster.com.br) adapter.

Best-effort adapter that reuses the US flow modules with BR-specific
selectors and locale defaults. Full recon of the BR site is still
recommended; this gets the bot pointed at the correct host and
gives a place to override selectors later.
"""

from __future__ import annotations

from .adapter import TicketmasterBRAdapter

__all__ = ["TicketmasterBRAdapter"]
