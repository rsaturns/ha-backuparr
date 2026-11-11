"""Diagnostics, with Backuparr's address and token left out."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import BackuparrConfigEntry

# The title and unique id are made from the address.
TO_REDACT = {CONF_URL, CONF_TOKEN, "title", "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BackuparrConfigEntry) -> dict[str, Any]:
    """The config entry and the last status Backuparr sent."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "status": asdict(coordinator.data) if coordinator.data else None,
    }
