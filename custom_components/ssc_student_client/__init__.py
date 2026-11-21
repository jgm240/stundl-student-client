"""Stundl Student Client (SSC) for Home Assistant.

Two sources, same sensors and calendar:
  * direct (autonomous): Home Assistant talks to WebUntis itself with the Untis Mobile secret - no Mac, no browser.
  * parent: fetches the read-only Parent-mode data from your Mac.
Either way a cached copy keeps the sensors working while the source is unreachable.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.exceptions import ConfigEntryAuthFailed

from . import untis_api
from .const import (CONF_KEY, CONF_MODE, CONF_SCAN_MINUTES, CONF_SCHOOL, CONF_SERVER, CONF_URL, CONF_USER, DEFAULT_SCAN_DIRECT,
                    DEFAULT_SCAN_MINUTES, DOMAIN, MODE_DIRECT)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor", "calendar"]


class SSCCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        direct = entry.data.get(CONF_MODE) == MODE_DIRECT
        minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_DIRECT if direct else DEFAULT_SCAN_MINUTES)
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=minutes))
        self.entry = entry
        self.stale = False
        self.fetched_at: float | None = None
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")

    async def _fetch(self) -> dict:
        session = async_get_clientsession(self.hass)
        if self.entry.data.get(CONF_MODE) == MODE_DIRECT:
            async def post(url, body):
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            cfg = {k: self.entry.data[k] for k in (CONF_SERVER, CONF_SCHOOL, CONF_USER, CONF_KEY)}
            try:
                return await untis_api.fetch_all(post, cfg)
            except untis_api.AuthError as err:  # secret revoked / clock off by more than ~30 s
                raise ConfigEntryAuthFailed(str(err)) from err
        async with session.get(self.entry.data[CONF_URL] + "data.json", timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                raise UpdateFailed(f"SSC answered HTTP {resp.status}")
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise UpdateFailed("unexpected data")
        return data

    async def _async_update_data(self) -> dict:
        try:
            data = await self._fetch()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:  # source unreachable (Mac off / no internet / school server down)
            cached = await self._store.async_load()
            if cached:
                self.stale, self.fetched_at = True, cached.get("t")
                return cached["data"]
            raise UpdateFailed(f"Can't fetch the data and nothing is cached yet: {err}") from err
        self.stale = False
        self.fetched_at = data.get("synced_at")
        await self._store.async_save({"t": self.fetched_at, "data": data})
        return data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SSCCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
