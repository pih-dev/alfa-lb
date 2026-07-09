"""The Alfa Lebanon integration (add-on file transport)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import AlfaApiError, AlfaAuthError, AlfaFileClient
from .const import CONF_MOBILE, DOMAIN, SESSION_FILE
from .coordinator import AlfaCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # No network session: the add-on does all portal I/O. We read its file.
    client = AlfaFileClient(hass, SESSION_FILE, entry.data[CONF_MOBILE])

    # If the add-on has not produced a fresh file yet, retry setup rather than
    # fail hard — the entry loads once the add-on writes latest.json.
    try:
        await client.async_validate()
    except (AlfaApiError, AlfaAuthError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = AlfaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
