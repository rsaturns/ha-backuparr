"""Shared entity pieces: the device, and adding/removing per-app entities."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import AppStatus
from .const import DOMAIN, SELF_ID
from .coordinator import BackuparrConfigEntry, BackuparrCoordinator


class BackuparrEntity(CoordinatorEntity[BackuparrCoordinator]):
    """An entity on the install's one device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BackuparrCoordinator, key: str) -> None:
        """Name the entity by its key (and app, for per-app ones)."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Backuparr",
            manufacturer="Backuparr",
            sw_version=coordinator.data.version,
            configuration_url=entry.data[CONF_URL],
            entry_type=DeviceEntryType.SERVICE,
        )


class BackuparrAppEntity(BackuparrEntity):
    """An entity for one app (or Backuparr's own backup)."""

    def __init__(self, coordinator: BackuparrCoordinator, key: str, app_id: str) -> None:
        """Remember the app the entity follows."""
        super().__init__(coordinator, f"{key}_{app_id}")
        self.app_id = app_id
        self._attr_translation_placeholders = {"app": self.app.name}

    @property
    def app(self) -> AppStatus:
        """The app's latest status (the last one seen, if it was removed)."""
        return self.coordinator.data.apps.get(self.app_id) or self._last_app

    @property
    def available(self) -> bool:
        """Unavailable once Backuparr stops listing the app."""
        return super().available and self.app_id in self.coordinator.data.apps

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.app_id in self.coordinator.data.apps:
            self._last_app = self.coordinator.data.apps[self.app_id]
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Keep a copy of the status in case the app disappears."""
        self._last_app = self.coordinator.data.apps[self.app_id]
        await super().async_added_to_hass()


def is_self(app_id: str) -> bool:
    """Backuparr's own backup, rather than an app's."""
    return app_id == SELF_ID


@callback
def async_track_apps(
    hass: HomeAssistant,
    entry: BackuparrConfigEntry,
    platform: str,
    async_add_entities: AddConfigEntryEntitiesCallback,
    keys: Iterable[str],
    make: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities for apps Backuparr starts listing, remove them for apps it drops.

    keys are the unique-id keys this platform makes per app ("problem", ...),
    so entities of apps that went while Home Assistant was off go too.
    """
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    prefixes = tuple(f"{entry.entry_id}_{key}_" for key in keys)
    known: set[str] = set()

    @callback
    def _sync() -> None:
        if not coordinator.last_update_success:
            return
        current = set(coordinator.data.apps)
        if new := current - known:
            known.update(new)
            async_add_entities([entity for app_id in sorted(new) for entity in make(app_id)])
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg_entry.domain != platform:
                continue
            for prefix in prefixes:
                app_id = reg_entry.unique_id.removeprefix(prefix)
                if app_id != reg_entry.unique_id and app_id not in current:
                    registry.async_remove(reg_entry.entity_id)
                    known.discard(app_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
