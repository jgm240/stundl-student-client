from __future__ import annotations

from datetime import date, datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.util import dt as dt_util

from . import SSCCoordinator
from .const import DOMAIN
from .entity import SSCEntity
from . import parsing


async def async_setup_entry(hass, entry, async_add_entities):
    c: SSCCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NextLesson(c), LessonsToday(c), HomeworkOpen(c), HomeworkMissed(c), HomeworkSoon(c), NextExam(c),
                        PlanToday(c), LastSync(c)])


def _now() -> datetime:
    return dt_util.now().replace(tzinfo=None)


class NextLesson(SSCEntity, SensorEntity):
    _attr_icon = "mdi:school"

    def __init__(self, c): super().__init__(c, "next_lesson", "Next lesson")

    @property
    def native_value(self):
        e = parsing.next_lesson(self.coordinator.data, _now())
        return f"{e['start'][11:16]} {e['subject']}".strip() if e else "None"

    @property
    def extra_state_attributes(self):
        e = parsing.next_lesson(self.coordinator.data, _now()) or {}
        return {**{k: e.get(k) for k in ("subject", "start", "end", "room", "teacher", "info")}, "stale": self.coordinator.stale}


class LessonsToday(SSCEntity, SensorEntity):
    _attr_icon = "mdi:calendar-today"

    def __init__(self, c): super().__init__(c, "lessons_today", "Lessons today")

    @property
    def native_value(self):
        return sum(1 for e in parsing.today_lessons(self.coordinator.data, _now().date()) if not e["cancelled"])

    @property
    def extra_state_attributes(self):
        return {"lessons": [dict(time=f"{e['start'][11:16]}-{e['end'][11:16]}", subject=e["subject"], room=e["room"],
                                 cancelled=e["cancelled"]) for e in parsing.today_lessons(self.coordinator.data, _now().date())]}


class HomeworkOpen(SSCEntity, SensorEntity):
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, c): super().__init__(c, "homework_open", "Homework open")

    @property
    def native_value(self):
        return len(parsing.open_homework(self.coordinator.data))

    @property
    def extra_state_attributes(self):
        return {"homework": [dict(subject=h["subject"], due=h["due"], text=h["text"], status=h["status"])
                             for h in parsing.open_homework(self.coordinator.data)]}


class HomeworkMissed(SSCEntity, SensorEntity):
    _attr_icon = "mdi:book-alert"

    def __init__(self, c): super().__init__(c, "homework_missed", "Homework missed")

    @property
    def native_value(self): return parsing.homework_counts(self.coordinator.data)["missed"]


class HomeworkSoon(SSCEntity, SensorEntity):
    _attr_icon = "mdi:book-clock"

    def __init__(self, c): super().__init__(c, "homework_soon", "Homework due soon")

    @property
    def native_value(self): return parsing.homework_counts(self.coordinator.data)["soon"]


class NextExam(SSCEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:file-document-edit"

    def __init__(self, c): super().__init__(c, "next_exam", "Next exam")

    @property
    def native_value(self):
        e = parsing.next_exam(self.coordinator.data, _now().date())
        return date.fromisoformat(e["date"]) if e else None

    @property
    def extra_state_attributes(self):
        e = parsing.next_exam(self.coordinator.data, _now().date()) or {}
        return {"subject": e.get("subject"), "text": e.get("text")}


class PlanToday(SSCEntity, SensorEntity):
    _attr_icon = "mdi:notebook-edit"

    def __init__(self, c): super().__init__(c, "plan_today", "Study plan today")

    @property
    def native_value(self): return len(parsing.plan_for(self.coordinator.data, _now().date()))

    @property
    def extra_state_attributes(self):
        return {"items": [dict(title=p["title"], subject=p["subject"], minutes=p["minutes"], done=p["done"])
                          for p in parsing.plan_for(self.coordinator.data, _now().date())]}


class LastSync(SSCEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:sync"

    def __init__(self, c): super().__init__(c, "last_sync", "Last data update")

    @property
    def native_value(self):
        ts = self.coordinator.data.get("synced_at")
        return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
