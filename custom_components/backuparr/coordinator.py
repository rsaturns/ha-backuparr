"""Polls Backuparr's status for every entity of one install."""

from __future__ import annotations

from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BackuparrClient,
    BackuparrStatus,
    CannotConnect,
    CertificateChanged,
    CertificateError,
    InvalidAuth,
    NotEnabled,
    PeerCertificate,
    RateLimited,
    UnexpectedResponse,
)
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL
from .pin import pin_data, pin_from

type BackuparrConfigEntry = ConfigEntry[BackuparrCoordinator]


def certificate_issue_id(entry: ConfigEntry) -> str:
    """The repair raised when this entry's Backuparr shows a new certificate."""
    return f"certificate_changed_{entry.entry_id}"


class BackuparrCoordinator(DataUpdateCoordinator[BackuparrStatus]):
    """Reads /api/ha/status every 5 minutes."""

    config_entry: BackuparrConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BackuparrConfigEntry) -> None:
        """Set up the client from the config entry."""
        self.client = BackuparrClient(
            async_get_clientsession(hass, entry.data[CONF_VERIFY_SSL]),
            entry.data[CONF_URL],
            entry.data[CONF_TOKEN],
            pin=pin_from(entry.data),
            on_renewed=self._renewed,
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    @callback
    def _renewed(self, pin: PeerCertificate) -> None:
        """Backuparr renewed its certificate with the same key: keep the new one."""
        LOGGER.info("Backuparr renewed its certificate (SHA-256 %s); the key is unchanged", pin.fingerprint)
        self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, **pin_data(pin)})

    @override
    async def _async_update_data(self) -> BackuparrStatus:
        issue_id = certificate_issue_id(self.config_entry)
        try:
            status = await self.client.async_get_status()
        except CertificateChanged as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                is_persistent=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="certificate_changed",
                translation_placeholders={"title": self.config_entry.title, "fingerprint": err.certificate.fingerprint},
                data={
                    "entry_id": self.config_entry.entry_id,
                    "sha256": err.certificate.sha256,
                    "public_key_sha256": err.certificate.public_key_sha256,
                },
            )
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="certificate_changed") from err
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
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        return status
