"""The Backuparr integration: whether each app's latest backup worked."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import BackuparrConfigEntry, BackuparrCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: BackuparrConfigEntry) -> bool:
    """Set up one Backuparr install."""
    coordinator = BackuparrCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _update_version() -> None:
        """Show Backuparr's version on the device after it's updated."""
        registry = dr.async_get(hass)
        device = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
        if device and coordinator.last_update_success and device.sw_version != coordinator.data.version:
            registry.async_update_device(device.id, sw_version=coordinator.data.version)

    entry.async_on_unload(coordinator.async_add_listener(_update_version))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BackuparrConfigEntry) -> bool:
    """Unload one Backuparr install."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
