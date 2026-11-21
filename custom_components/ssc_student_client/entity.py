from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SSCCoordinator
from .const import DOMAIN


class SSCEntity(CoordinatorEntity[SSCCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SSCCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.entry.entry_id)}, name="Stundl Student Client",
                                            manufacturer="SSC", entry_type=None)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
