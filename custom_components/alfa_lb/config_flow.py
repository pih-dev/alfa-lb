"""Config flow for Alfa Lebanon (add-on file transport).

Validation just confirms the add-on's session file exists, is fresh, and parses.
Credentials live in the add-on, not here — but we keep the mobile/password fields
so existing entries (which store both) load unchanged; password is unused.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .api import AlfaApiError, AlfaAuthError, AlfaFileClient
from .const import CONF_MOBILE, CONF_PASSWORD, DOMAIN, SESSION_FILE

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MOBILE): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


class AlfaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alfa Lebanon."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            mobile = user_input[CONF_MOBILE].strip()
            client = AlfaFileClient(self.hass, SESSION_FILE, mobile)
            try:
                data = await client.async_validate()
            except (AlfaApiError, AlfaAuthError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                unique = data.get("MobileNumberValue") or mobile
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Alfa {unique}",
                    data={
                        CONF_MOBILE: mobile,
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
