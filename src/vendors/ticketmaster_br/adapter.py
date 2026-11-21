"""Concrete VendorAdapter for Ticketmaster Brazil."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import VendorAdapter
from ..ticketmaster import auth, cart, checkout, core, navigator, queue

if TYPE_CHECKING:
    from ...utils.config_loader import AccountConfig, BotConfig


class TicketmasterBRAdapter(VendorAdapter):
    """Brazil adapter.

    Reuses the US step modules for now. Selectors and locale defaults
    are overridden via config/selectors/ticketmaster_br.yaml when present.
    """

    name = "ticketmaster_br"

    def __init__(self) -> None:
        self.auth = auth
        self.cart = cart
        self.checkout = checkout
        self.core = core
        self.navigator = navigator
        self.queue = queue

    def build_runner(
        self, config: BotConfig, account: AccountConfig | None = None
    ) -> core.BotRunner:
        return core.BotRunner(config, account=account)


__all__ = ["TicketmasterBRAdapter"]
