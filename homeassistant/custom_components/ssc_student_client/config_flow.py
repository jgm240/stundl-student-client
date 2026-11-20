from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SCAN_MINUTES, CONF_URL, DEFAULT_SCAN_MINUTES, DOMAIN
from .parsing import parse_link


class SSCConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            parsed = parse_link(user_input[CONF_URL])
            if not parsed:
                errors["base"] = "invalid_link"
            else:
                base, short_id = parsed
                try:
                    session = async_get_clientsession(self.hass)
                    async with session.get(base + "data.json", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        ok = resp.status == 200 and isinstance(await resp.json(content_type=None), dict)
                    if not ok:
                        errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "cannot_connect"
                if not errors:
                    await self.async_set_unique_id(short_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title="Stundl Student Client", data={CONF_URL: base})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required(CONF_URL): str}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return SSCOptionsFlow(entry)


class SSCOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self._entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        return self.async_show_form(step_id="init", data_schema=vol.Schema(
            {vol.Required(CONF_SCAN_MINUTES, default=current): vol.All(vol.Coerce(int), vol.Range(min=1, max=60))}))
