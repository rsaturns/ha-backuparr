"""Failed backups count, and each app's last successful backup."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BackuparrConfigEntry, BackuparrCoordinator
from .entity import BackuparrAppEntity, BackuparrEntity, async_track_apps, is_self

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BackuparrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the count, and a timestamp per app as Backuparr lists them."""
    coordinator = entry.runtime_data
    async_add_entities([FailedBackupsSensor(coordinator)])
    async_track_apps(
        hass,
        entry,
        Platform.SENSOR,
        async_add_entities,
        ("last_backup",),
        lambda app_id: [LastBackupSensor(coordinator, app_id)],
    )


class FailedBackupsSensor(BackuparrEntity, SensorEntity):
    """How many apps' latest backups failed."""

    _attr_translation_key = "failed_backups"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BackuparrCoordinator) -> None:
        """One per install."""
        super().__init__(coordinator, "failed")

    @property
    def native_value(self) -> int:
        """Backuparr's failed count."""
        return self.coordinator.data.failed


class LastBackupSensor(BackuparrAppEntity, SensorEntity):
    """When the app's latest successful backup finished. Off by default."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: BackuparrCoordinator, app_id: str) -> None:
        """One per app."""
        super().__init__(coordinator, "last_backup", app_id)
        self._attr_translation_key = "self_last_backup" if is_self(app_id) else "last_backup"

    @property
    def native_value(self) -> datetime | None:
        """None until the app's first successful backup."""
        return self.app.last_success
