"""The status parser and the client's error mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.backuparr.api import (
    BackuparrClient,
    CannotConnect,
    InvalidAuth,
    InvalidUrl,
    NotEnabled,
    RateLimited,
    UnexpectedResponse,
    normalize_url,
    parse_status,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import BEARER, STATUS_URL, TOKEN, URL


def test_parse_status(status: dict[str, Any]) -> None:
    parsed = parse_status(status)
    assert (parsed.ok, parsed.total, parsed.succeeded, parsed.failed, parsed.running) == (False, 4, 2, 1, False)
    assert parsed.version == "3.0.0"
    assert list(parsed.apps) == ["backuparr", "radarr", "sonarr-4k", "pihole"]
    sonarr = parsed.apps["sonarr-4k"]
    assert sonarr.name == "Sonarr (4K)"
    assert sonarr.ok is False
    assert sonarr.error == "couldn't reach the app"
    assert sonarr.last_run == datetime(2026, 11, 10, 3, 2, 40, tzinfo=UTC)
    assert parsed.apps["pihole"].ok is None
    assert parsed.apps["pihole"].last_success is None


def test_parse_status_drops_bad_values(status: dict[str, Any]) -> None:
    """Ids that aren't slugs, wrong types and times without a zone are left out."""
    status["apps"]["../etc"] = status["apps"]["radarr"]
    status["apps"]["Radarr Big"] = status["apps"]["radarr"]
    status["apps"]["lidarr"] = "not a dict"
    status["apps"]["radarr"] = {"name": 5, "ok": "yes", "last_run": "2026-11-10T03:01:12", "error": "x"}
    status["failed"] = True
    status["total"] = -3
    parsed = parse_status(status)
    assert set(parsed.apps) == {"backuparr", "radarr", "sonarr-4k", "pihole"}
    radarr = parsed.apps["radarr"]
    assert (radarr.name, radarr.ok, radarr.last_run, radarr.error) == ("radarr", None, None, None)
    assert (parsed.failed, parsed.total) == (0, 0)


@pytest.mark.parametrize("data", [None, [], {"ok": True}, {"apps": []}])
def test_parse_status_not_a_status(data: Any) -> None:
    with pytest.raises(UnexpectedResponse):
        parse_status(data)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://backuparr.local:8990/", "http://backuparr.local:8990"),
        (" https://example.com/backuparr/ ", "https://example.com/backuparr"),
        ("http://10.0.0.2:8990?x=1#y", "http://10.0.0.2:8990"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize("url", ["backuparr.local:8990", "ftp://host", "http://", ""])
def test_normalize_url_refuses(url: str) -> None:
    with pytest.raises(InvalidUrl):
        normalize_url(url)


async def test_client_sends_the_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: dict[str, Any]
) -> None:
    aioclient_mock.get(STATUS_URL, json=status)
    client = BackuparrClient(async_get_clientsession(hass), URL + "/", TOKEN)
    assert (await client.async_get_status()).failed == 1
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == STATUS_URL
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401, "headers": BEARER}, InvalidAuth),
        # Another app's login: not Backuparr, so not the token's fault.
        ({"status": 401}, UnexpectedResponse),
        ({"status": 404}, NotEnabled),
        ({"status": 429}, RateLimited),
        ({"status": 500}, CannotConnect),
        # A redirect isn't followed, so the token never goes elsewhere.
        ({"status": 302, "headers": {"Location": "https://elsewhere.example/"}}, CannotConnect),
        ({"text": "<html>login</html>"}, UnexpectedResponse),
        ({"exc": aiohttp.ClientConnectionError()}, CannotConnect),
        ({"exc": TimeoutError()}, CannotConnect),
    ],
)
async def test_client_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs: dict[str, Any], error: type[Exception]
) -> None:
    aioclient_mock.get(STATUS_URL, **kwargs)
    client = BackuparrClient(async_get_clientsession(hass), URL, TOKEN)
    with pytest.raises(error):
        await client.async_get_status()
