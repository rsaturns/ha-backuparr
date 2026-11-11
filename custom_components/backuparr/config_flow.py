"""Config flow: Backuparr's address, the token from its card, Verify SSL."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import probatio
from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    BackuparrClient,
    CannotConnect,
    CertificateError,
    InvalidAuth,
    InvalidUrl,
    NotEnabled,
    RateLimited,
    UnexpectedResponse,
    normalize_url,
)
from .const import DEFAULT_VERIFY_SSL, DOMAIN, LOGGER

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
# Example addresses for the hint (translations can't hold URLs).
_URL_EXAMPLES = {"proxy_url": "https://backuparr.example.com", "local_url": "http://192.168.1.10:8990"}


def _unique_id(url: str) -> str:
    """One entry per Backuparr address: scheme, host, port and path."""
    parsed = URL(url)
    return str(parsed.with_host(parsed.host.lower() if parsed.host else "")).rstrip("/")


class BackuparrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Backuparr install."""

    VERSION = 1

    async def _async_check(self, url: str, token: str, verify_ssl: bool) -> dict[str, str]:
        """Read the status once; the errors to show, or none."""
        try:
            client = BackuparrClient(async_get_clientsession(self.hass, verify_ssl), url, token)
            await client.async_get_status()
        except InvalidUrl:
            return {CONF_URL: "invalid_url"}
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except NotEnabled:
            return {"base": "not_enabled"}
        except RateLimited:
            return {"base": "rate_limited"}
        except CertificateError:
            return {"base": "certificate"}
        except CannotConnect:
            return {"base": "cannot_connect"}
        except UnexpectedResponse:
            return {"base": "unexpected_response"}
        except Exception:
            LOGGER.exception("Unexpected error reading Backuparr's status")
            return {"base": "unknown"}
        return {}

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address, token and Verify SSL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
            except InvalidUrl:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(_unique_id(url))
                self._abort_if_unique_id_configured()
                token = user_input[CONF_TOKEN].strip()
                errors = await self._async_check(url, token, user_input[CONF_VERIFY_SSL])
                if not errors:
                    return self.async_create_entry(
                        title=URL(url).host or "Backuparr",
                        data={CONF_URL: url, CONF_TOKEN: token, CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL]},
                    )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_URL): _URL_SELECTOR,
                probatio.Required(probatio.Secret(CONF_TOKEN)): _TOKEN_SELECTOR,
                probatio.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            description_placeholders=_URL_EXAMPLES,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Backuparr refused the token, or its integration was switched off."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            errors = await self._async_check(entry.data[CONF_URL], token, entry.data[CONF_VERIFY_SSL])
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(probatio.Secret(CONF_TOKEN)): _TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change Backuparr's address or Verify SSL; the token stays."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
            except InvalidUrl:
                errors[CONF_URL] = "invalid_url"
            else:
                unique_id = _unique_id(url)
                if any(
                    other.unique_id == unique_id and other.entry_id != entry.entry_id
                    for other in self._async_current_entries(include_ignore=False)
                ):
                    return self.async_abort(reason="already_configured")
                errors = await self._async_check(url, entry.data[CONF_TOKEN], user_input[CONF_VERIFY_SSL])
                if not errors:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=unique_id,
                        data_updates={CONF_URL: url, CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL]},
                    )

        schema = probatio.Schema(
            {
                probatio.Required(CONF_URL): _URL_SELECTOR,
                probatio.Required(CONF_VERIFY_SSL): bool,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or entry.data),
            description_placeholders=_URL_EXAMPLES,
            errors=errors,
        )
