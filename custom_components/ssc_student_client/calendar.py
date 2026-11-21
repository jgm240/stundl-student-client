from __future__ import annotations

from datetime import date, datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from . import SSCCoordinator, parsing
from .const import DOMAIN
from .entity import SSCEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SSCCalendar(hass.data[DOMAIN][entry.entry_id])])


def _localize(v):
    return v if (isinstance(v, date) and not isinstance(v, datetime)) else v.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


def _event(e) -> CalendarEvent:
    return CalendarEvent(start=_localize(e["start"]), end=_localize(e["end"]), summary=e["summary"],
                         description=e["description"] or None, location=e["location"] or None)


class SSCCalendar(SSCEntity, CalendarEntity):
    _attr_icon = "mdi:school"

    def __init__(self, c: SSCCoordinator) -> None:
        super().__init__(c, "calendar", "Timetable")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now().replace(tzinfo=None)
        cur = parsing.current_lesson(self.coordinator.data, now)
        nxt = cur or parsing.next_lesson(self.coordinator.data, now)
        if not nxt:
            return None
        return CalendarEvent(start=_localize(datetime.fromisoformat(nxt["start"])), end=_localize(datetime.fromisoformat(nxt["end"])),
                             summary=nxt["subject"] or "Lesson", location=nxt.get("room") or None)

    async def async_get_events(self, hass, start_date, end_date) -> list[CalendarEvent]:
        s, e = start_date.replace(tzinfo=None), end_date.replace(tzinfo=None)
        return [_event(x) for x in parsing.events(self.coordinator.data, s, e)]
