"""Fixtures: a Backuparr status answer and a set-up config entry."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.backuparr.const import DOMAIN
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

URL = "http://backuparr.local:8990"
STATUS_URL = f"{URL}/api/ha/status"
TOKEN = "t0ken-for-tests-0123456789abcdef"
# Backuparr's 401s name the scheme.
BEARER = {"WWW-Authenticate": "Bearer"}

STATUS: dict[str, Any] = {
    "ok": False,
    "total": 4,
    "succeeded": 2,
    "failed": 1,
    "running": False,
    "version": "3.0.0",
    "apps": {
        "backuparr": {
            "name": "Backuparr",
            "ok": True,
            "last_run": "2026-11-10T03:00:05+00:00",
            "last_success": "2026-11-10T03:00:05+00:00",
            "error": None,
        },
        "radarr": {
            "name": "Radarr",
            "ok": True,
            "last_run": "2026-11-10T03:01:12+00:00",
            "last_success": "2026-11-10T03:01:12+00:00",
            "error": None,
        },
        "sonarr-4k": {
            "name": "Sonarr (4K)",
            "ok": False,
            "last_run": "2026-11-10T03:02:40+00:00",
            "last_success": "2026-11-09T03:02:31+00:00",
            "error": "couldn't reach the app",
        },
        "pihole": {"name": "Pi-hole", "ok": None, "last_run": None, "last_success": None, "error": None},
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def status() -> dict[str, Any]:
    """A fresh copy of the status answer to change in a test."""
    return copy.deepcopy(STATUS)


@pytest.fixture
def entry() -> MockConfigEntry:
    """A config entry like the flow creates."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="backuparr.local",
        unique_id=URL,
        data={CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, status: dict[str, Any]
) -> MockConfigEntry:
    """The entry, set up against the status answer."""
    aioclient_mock.get(STATUS_URL, json=status)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
