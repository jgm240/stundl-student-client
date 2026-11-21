"""Stundl Student Client (SSC) for Home Assistant.

Fetches the read-only Parent-mode data from your Mac by itself, keeps a cached copy (so sensors keep working while the
Mac is off) and exposes sensors, a calendar and problem indicators. No credentials are involved - only the secret
Parent link, which Home Assistant stores in its own config.
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

from .const import CONF_SCAN_MINUTES, CONF_URL, DEFAULT_SCAN_MINUTES, DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor", "calendar"]


class SSCCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=minutes))
        self.entry = entry
        self.stale = False
        self.fetched_at: float | None = None
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")

    async def _async_update_data(self) -> dict:
        url = self.entry.data[CONF_URL] + "data.json"
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"SSC answered HTTP {resp.status}")
                data = await resp.json(content_type=None)
            if not isinstance(data, dict):
                raise UpdateFailed("unexpected data")
        except Exception as err:  # Mac asleep / off, wrong link, network
            cached = await self._store.async_load()
            if cached:
                self.stale, self.fetched_at = True, cached.get("t")
                return cached["data"]
            raise UpdateFailed(f"Can't reach SSC and nothing is cached yet: {err}") from err
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
