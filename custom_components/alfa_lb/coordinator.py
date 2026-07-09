"""DataUpdateCoordinator for Alfa Lebanon (add-on file transport)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AlfaApiError, AlfaAuthError, AlfaFileClient
from .const import DOMAIN, FILE_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AlfaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads the add-on session file every FILE_POLL_INTERVAL."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: AlfaFileClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=FILE_POLL_INTERVAL,
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        # Every failure (missing/stale/error/auth) degrades to `unavailable`.
        # We do NOT raise ConfigEntryAuthFailed: the add-on owns the auth UX and
        # fires its own notification; an HA reauth prompt would be misleading
        # (the integration holds no working credential path anymore).
        try:
            return await self.client.async_get_account_data()
        except (AlfaAuthError, AlfaApiError) as err:
            raise UpdateFailed(str(err)) from err
