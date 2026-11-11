"""Setup, reauthentication and reconfigure."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.backuparr.api import CertificateError
from custom_components.backuparr.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BEARER, STATUS_URL, TOKEN, URL

USER_INPUT = {CONF_URL: URL + "/", CONF_TOKEN: f" {TOKEN} ", CONF_VERIFY_SSL: True}


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_user_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: dict[str, Any]) -> None:
    aioclient_mock.get(STATUS_URL, json=status)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["description_placeholders"]["proxy_url"].startswith("https://")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "backuparr.local"
    # Address normalised, token trimmed.
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == URL


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401, "headers": BEARER}, "invalid_auth"),
        ({"status": 404}, "not_enabled"),
        ({"status": 429}, "rate_limited"),
        ({"status": 502}, "cannot_connect"),
        ({"text": "<html></html>"}, "unexpected_response"),
        ({"exc": CertificateError("self-signed")}, "certificate"),
        ({"exc": RuntimeError("boom")}, "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: dict[str, Any],
    kwargs: dict[str, Any],
    error: str,
) -> None:
    aioclient_mock.get(STATUS_URL, **kwargs)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, json=status)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_url(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_URL: "backuparr.local:8990"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_user_flow_already_configured(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_URL: "http://BACKUPARR.local:8990/"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, setup_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, status: dict[str, Any]
) -> None:
    result = await setup_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["url"] == URL

    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, status=401, headers=BEARER)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "old"})
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(STATUS_URL, json=status)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "new-token "})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_TOKEN] == "new-token"
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == "Bearer new-token"


async def test_reconfigure(
    hass: HomeAssistant, setup_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, status: dict[str, Any]
) -> None:
    new_url = "https://backups.example.com"
    aioclient_mock.get(f"{new_url}/api/ha/status", json=status)
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: new_url + "/", CONF_VERIFY_SSL: False}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data == {CONF_URL: new_url, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: False}
    assert setup_entry.unique_id == new_url
    assert setup_entry.title == "backuparr.local"


async def test_reconfigure_to_another_entry(
    hass: HomeAssistant, setup_entry: MockConfigEntry, status: dict[str, Any]
) -> None:
    other = MockConfigEntry(domain=DOMAIN, unique_id="http://other:8990", data=dict(setup_entry.data))
    other.add_to_hass(hass)
    result = await setup_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://other:8990", CONF_VERIFY_SSL: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_errors(
    hass: HomeAssistant, setup_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    result = await setup_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "nope", CONF_VERIFY_SSL: True}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}

    aioclient_mock.get("http://moved:8990/api/ha/status", status=401, headers=BEARER)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://moved:8990", CONF_VERIFY_SSL: True}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert setup_entry.data[CONF_URL] == URL
