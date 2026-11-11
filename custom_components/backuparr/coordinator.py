"""Polls Backuparr's status for every entity of one install."""

from __future__ import annotations

from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BackuparrClient,
    BackuparrStatus,
    CannotConnect,
    CertificateError,
    InvalidAuth,
    NotEnabled,
    RateLimited,
    UnexpectedResponse,
)
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL

type BackuparrConfigEntry = ConfigEntry[BackuparrCoordinator]


class BackuparrCoordinator(DataUpdateCoordinator[BackuparrStatus]):
    """Reads /api/ha/status every 5 minutes."""

    config_entry: BackuparrConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BackuparrConfigEntry) -> None:
        """Set up the client from the config entry."""
        self.client = BackuparrClient(
            async_get_clientsession(hass, entry.data[CONF_VERIFY_SSL]),
            entry.data[CONF_URL],
            entry.data[CONF_TOKEN],
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    @override
    async def _async_update_data(self) -> BackuparrStatus:
        try:
            return await self.client.async_get_status()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from err
        except NotEnabled as err:
            # Switching the integration off in Backuparr removes the token,
            # so turning it back on needs the new one.
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="not_enabled") from err
        except RateLimited as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="rate_limited") from err
        except CertificateError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="certificate") from err
        except CannotConnect as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect") from err
        except UnexpectedResponse as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="unexpected_response") from err
