from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.util import dt as dt_util

from . import SSCCoordinator, parsing
from .const import DOMAIN
from .entity import SSCEntity


async def async_setup_entry(hass, entry, async_add_entities):
    c: SSCCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HomeworkMissedFlag(c), CancelledToday(c), Offline(c)])


class HomeworkMissedFlag(SSCEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, c): super().__init__(c, "has_missed_homework", "Missed homework")

    @property
    def is_on(self): return parsing.homework_counts(self.coordinator.data)["missed"] > 0


class CancelledToday(SSCEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, c): super().__init__(c, "cancelled_today", "Lesson cancelled today")

    @property
    def is_on(self):
        return any(e["cancelled"] for e in parsing.today_lessons(self.coordinator.data, dt_util.now().date()))


class Offline(SSCEntity, BinarySensorEntity):
    """On while SSC can't be reached and the cached copy is being shown."""
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, c): super().__init__(c, "showing_cached_data", "Showing cached data")

    @property
    def is_on(self): return self.coordinator.stale
