"""Setup, entities, polling and diagnostics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.backuparr.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_VERIFY_SSL, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import BEARER, STATUS_URL, TOKEN, URL


async def _poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker, **answer: Any
) -> None:
    """Change Backuparr's answer and let the 5-minute poll read it."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, **answer)
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities(hass: HomeAssistant, setup_entry: MockConfigEntry, entity_registry: er.EntityRegistry) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED

    overall = hass.states.get("binary_sensor.backuparr_all_backups")
    assert overall.state == STATE_ON
    assert overall.attributes["device_class"] == "problem"
    assert overall.attributes["total"] == 4
    assert overall.attributes["failed"] == 1

    assert hass.states.get("binary_sensor.backuparr_radarr").state == STATE_OFF
    sonarr = hass.states.get("binary_sensor.backuparr_sonarr_4k")
    assert sonarr.state == STATE_ON
    assert sonarr.attributes["friendly_name"] == "Backuparr Sonarr (4K)"
    assert sonarr.attributes["app_id"] == "sonarr-4k"
    assert sonarr.attributes["error"] == "couldn't reach the app"
    assert sonarr.attributes["last_success"].isoformat() == "2026-11-09T03:02:31+00:00"
    # Not backed up yet: unknown, not a problem.
    assert hass.states.get("binary_sensor.backuparr_pi_hole").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.backuparr_own_backup").state == STATE_OFF

    assert hass.states.get("sensor.backuparr_failed_backups").state == "1"

    # The timestamps are there but off by default.
    last = entity_registry.async_get("sensor.backuparr_radarr_last_backup")
    assert last is not None
    assert last.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("sensor.backuparr_radarr_last_backup") is None
    assert entity_registry.async_get("sensor.backuparr_own_last_backup") is not None


async def test_device(hass: HomeAssistant, setup_entry: MockConfigEntry, device_registry: dr.DeviceRegistry) -> None:
    device = device_registry.async_get_device_by_identifier((DOMAIN, setup_entry.entry_id), setup_entry.entry_id)
    assert device.name == "Backuparr"
    assert device.sw_version == "3.0.0"
    assert device.configuration_url == URL
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_last_backup_sensor(
    hass: HomeAssistant, setup_entry: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    for entity_id in ("sensor.backuparr_sonarr_4k_last_backup", "sensor.backuparr_pi_hole_last_backup"):
        entity_registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    sonarr = hass.states.get("sensor.backuparr_sonarr_4k_last_backup")
    assert sonarr.state == "2026-11-09T03:02:31+00:00"
    assert sonarr.attributes["device_class"] == "timestamp"
    assert hass.states.get("sensor.backuparr_pi_hole_last_backup").state == STATE_UNKNOWN


async def test_failure_and_recovery(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    status: dict[str, Any],
) -> None:
    status["apps"]["sonarr-4k"].update(ok=True, error=None, last_success="2026-11-10T04:00:00+00:00")
    status.update(failed=0, succeeded=3)
    await _poll(hass, freezer, aioclient_mock, json=status)
    assert hass.states.get("binary_sensor.backuparr_sonarr_4k").state == STATE_OFF
    assert hass.states.get("binary_sensor.backuparr_sonarr_4k").attributes["error"] is None
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == STATE_OFF
    assert hass.states.get("sensor.backuparr_failed_backups").state == "0"

    status["apps"]["radarr"].update(ok=False, error="the backup didn't finish")
    status.update(failed=1, succeeded=2)
    await _poll(hass, freezer, aioclient_mock, json=status)
    assert hass.states.get("binary_sensor.backuparr_radarr").state == STATE_ON
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == STATE_ON


async def test_backuparr_down(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    status: dict[str, Any],
) -> None:
    await _poll(hass, freezer, aioclient_mock, status=502)
    for entity_id in ("binary_sensor.backuparr_all_backups", "binary_sensor.backuparr_radarr"):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert setup_entry.state is ConfigEntryState.LOADED

    await _poll(hass, freezer, aioclient_mock, json=status)
    assert hass.states.get("binary_sensor.backuparr_radarr").state == STATE_OFF


async def test_apps_added_and_removed(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    entity_registry: er.EntityRegistry,
    status: dict[str, Any],
) -> None:
    status["apps"]["lidarr"] = {
        "name": "Lidarr",
        "ok": True,
        "last_run": "2026-11-10T03:05:00+00:00",
        "last_success": "2026-11-10T03:05:00+00:00",
        "error": None,
    }
    del status["apps"]["sonarr-4k"]
    await _poll(hass, freezer, aioclient_mock, json=status)

    assert hass.states.get("binary_sensor.backuparr_lidarr").state == STATE_OFF
    assert entity_registry.async_get("sensor.backuparr_lidarr_last_backup") is not None
    assert hass.states.get("binary_sensor.backuparr_sonarr_4k") is None
    assert entity_registry.async_get("binary_sensor.backuparr_sonarr_4k") is None
    assert entity_registry.async_get("sensor.backuparr_sonarr_4k_last_backup") is None

    # Back again: a fresh entity with the same id.
    status["apps"]["sonarr-4k"] = {"name": "Sonarr (4K)", "ok": True, "last_run": None, "last_success": None}
    await _poll(hass, freezer, aioclient_mock, json=status)
    assert hass.states.get("binary_sensor.backuparr_sonarr_4k").state == STATE_OFF


async def test_app_removed_while_home_assistant_was_off(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    status: dict[str, Any],
) -> None:
    entry.add_to_hass(hass)
    stale = entity_registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_problem_tdarr", config_entry=entry
    )
    aioclient_mock.get(STATUS_URL, json=status)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get(stale.entity_id) is None


@pytest.mark.parametrize("answer", [{"status": 401, "headers": BEARER}, {"status": 404}])
async def test_token_refused_starts_reauth(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    answer: dict[str, Any],
) -> None:
    await _poll(hass, freezer, aioclient_mock, **answer)
    flows = hass.config_entries.flow.async_progress()
    assert [(flow["handler"], flow["context"]["source"]) for flow in flows] == [(DOMAIN, SOURCE_REAUTH)]
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        ({"status": 401, "headers": BEARER}, ConfigEntryState.SETUP_ERROR),
        ({"status": 502}, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_fails(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    answer: dict[str, Any],
    state: ConfigEntryState,
) -> None:
    aioclient_mock.get(STATUS_URL, **answer)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state


async def test_verify_ssl_is_passed_on(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, status: dict[str, Any]
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=URL, data={**entry.data, CONF_VERIFY_SSL: False})
    aioclient_mock.get(STATUS_URL, json=status)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.backuparr.coordinator.async_get_clientsession", wraps=async_get_clientsession
    ) as session:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert session.call_args.args[1] is False


async def test_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics(
    hass: HomeAssistant, setup_entry: MockConfigEntry, hass_client: ClientSessionGenerator
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)
    text = str(diagnostics)
    assert TOKEN not in text
    assert "backuparr.local" not in text
    assert diagnostics["entry"]["data"] == {"url": "**REDACTED**", CONF_TOKEN: "**REDACTED**", "verify_ssl": True}
    assert diagnostics["status"]["apps"]["sonarr-4k"]["error"] == "couldn't reach the app"
