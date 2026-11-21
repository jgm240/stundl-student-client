from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import untis_api
from .const import (CONF_KEY, CONF_MODE, CONF_SCAN_MINUTES, CONF_SCHOOL, CONF_SERVER, CONF_URL, CONF_USER, DEFAULT_SCAN_DIRECT,
                    DEFAULT_SCAN_MINUTES, DOMAIN, MODE_DIRECT, MODE_PARENT)
from .parsing import parse_link

CONF_QR = "qr"


class SSCConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["direct", "parent"])

    # ---- autonomous: Home Assistant talks to WebUntis itself
    async def async_step_direct(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            cfg = untis_api.parse_qr(user_input.get(CONF_QR, "")) if user_input.get(CONF_QR) else {
                k: (user_input.get(k) or "").strip() for k in (CONF_SERVER, CONF_SCHOOL, CONF_USER, CONF_KEY)}
            if not cfg or not all(cfg.values()):
                errors["base"] = "invalid_qr"
            else:
                session = async_get_clientsession(self.hass)

                async def post(url, body):
                    async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        resp.raise_for_status()
                        return await resp.json(content_type=None)
                try:
                    await untis_api.call(post, cfg, "getUserData2017")
                except untis_api.AuthError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    errors["base"] = "cannot_connect"
                if not errors:
                    data = {CONF_MODE: MODE_DIRECT, **cfg}
                    if self.source == config_entries.SOURCE_REAUTH:
                        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                        self.hass.config_entries.async_update_entry(entry, data=data)
                        await self.hass.config_entries.async_reload(entry.entry_id)
                        return self.async_abort(reason="reauth_successful")
                    await self.async_set_unique_id(f"direct:{cfg[CONF_SERVER]}:{cfg[CONF_SCHOOL]}:{cfg[CONF_USER]}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=f"Stundl Student Client ({cfg[CONF_USER]})", data=data)
        return self.async_show_form(step_id="direct", errors=errors, data_schema=vol.Schema({
            vol.Optional(CONF_QR): str, vol.Optional(CONF_SERVER): str, vol.Optional(CONF_SCHOOL): str,
            vol.Optional(CONF_USER): str, vol.Optional(CONF_KEY): str}))

    async def async_step_reauth(self, entry_data):
        return await self.async_step_direct()

    # ---- the SSC Parent link on your Mac
    async def async_step_parent(self, user_input=None):
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
                    return self.async_create_entry(title="Stundl Student Client", data={CONF_MODE: MODE_PARENT, CONF_URL: base})
        return self.async_show_form(step_id="parent", data_schema=vol.Schema({vol.Required(CONF_URL): str}), errors=errors)

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
        direct = self._entry.data.get(CONF_MODE) == MODE_DIRECT
        current = self._entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_DIRECT if direct else DEFAULT_SCAN_MINUTES)
        return self.async_show_form(step_id="init", data_schema=vol.Schema(
            {vol.Required(CONF_SCAN_MINUTES, default=current): vol.All(vol.Coerce(int), vol.Range(min=5 if direct else 1, max=240))}))
