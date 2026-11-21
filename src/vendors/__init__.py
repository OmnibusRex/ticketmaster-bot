"""Vendor adapters for ticket-purchasing flows.

Each subpackage under :mod:`src.vendors` implements a concrete
:class:`~src.vendors.base.VendorAdapter`.

Importing this package registers every shipped adapter into
:mod:`src.registry.vendors`.
"""

from __future__ import annotations

from . import (
    ticketmaster,  # noqa: F401
    ticketmaster_sg,  # noqa: F401
    ticketmaster_br,  # noqa: F401
)
from .base import VendorAdapter

__all__ = ["VendorAdapter", "ticketmaster", "ticketmaster_sg", "ticketmaster_br"]
