"""Problem sensors: all backups, and one per app."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
    """Add the overall sensor, and one per app as Backuparr lists them."""
    coordinator = entry.runtime_data
    async_add_entities([AllBackupsSensor(coordinator)])
    async_track_apps(
        hass,
        entry,
        Platform.BINARY_SENSOR,
        async_add_entities,
        ("problem",),
        lambda app_id: [AppProblemSensor(coordinator, app_id)],
    )


class AllBackupsSensor(BackuparrEntity, BinarySensorEntity):
    """On when any app's latest backup failed."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "all_backups"

    def __init__(self, coordinator: BackuparrCoordinator) -> None:
        """One per install."""
        super().__init__(coordinator, "all")

    @property
    def is_on(self) -> bool:
        """An app that hasn't been backed up yet isn't a failure."""
        return self.coordinator.data.failed > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The counts from Backuparr's Overview."""
        data = self.coordinator.data
        return {"total": data.total, "succeeded": data.succeeded, "failed": data.failed, "running": data.running}


class AppProblemSensor(BackuparrAppEntity, BinarySensorEntity):
    """On when the app's latest backup failed; unknown before its first one."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: BackuparrCoordinator, app_id: str) -> None:
        """One per app."""
        super().__init__(coordinator, "problem", app_id)
        self._attr_translation_key = "self_problem" if is_self(app_id) else "app_problem"

    @property
    def is_on(self) -> bool | None:
        """The app's latest run."""
        ok = self.app.ok
        return None if ok is None else not ok

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When it last ran and last worked, and why it failed."""
        app = self.app
        return {
            "app_id": self.app_id,
            "last_run": app.last_run,
            "last_success": app.last_success,
            "error": app.error,
        }
