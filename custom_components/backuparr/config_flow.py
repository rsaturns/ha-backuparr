"""Config flow: Backuparr's address, the token from its card, Verify SSL.

Backuparr serves HTTPS with its own self-signed certificate, which Home
Assistant can't check. Then, with Verify SSL on, the flow shows that
certificate's fingerprint to compare with the one on Backuparr's card, and
pins it once trusted (api.py), instead of turning certificate checks off."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import probatio
from yarl import URL

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    BackuparrClient,
    BackuparrError,
    CannotConnect,
    CertificateChanged,
    CertificateError,
    InvalidAuth,
    InvalidUrl,
    NotEnabled,
    PeerCertificate,
    RateLimited,
    UnexpectedResponse,
    async_fetch_certificate,
    normalize_url,
)
from .const import DEFAULT_VERIFY_SSL, DOMAIN, LOGGER
from .pin import pin_data, pin_from, without_pin

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
# Example addresses for the hint (translations can't hold URLs).
_URL_EXAMPLES = {"local_url": "https://192.168.1.10:8991", "proxy_url": "https://backuparr.example.com"}


def _unique_id(url: str) -> str:
    """One entry per Backuparr address: scheme, host, port and path."""
    parsed = URL(url)
    return str(parsed.with_host(parsed.host.lower() if parsed.host else "")).rstrip("/")


class BackuparrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Backuparr install."""

    VERSION = 1

    def __init__(self) -> None:
        """Nothing waiting to be trusted yet."""
        self._pending: dict[str, Any] = {}

    async def _async_check(
        self, url: str, token: str, verify_ssl: bool, pin: PeerCertificate | None = None
    ) -> dict[str, str]:
        """Read the status once; the errors to show, or none."""
        try:
            client = BackuparrClient(async_get_clientsession(self.hass, verify_ssl), url, token, pin=pin)
            await client.async_get_status()
        except InvalidUrl:
            return {CONF_URL: "invalid_url"}
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except NotEnabled:
            return {"base": "not_enabled"}
        except RateLimited:
            return {"base": "rate_limited"}
        except CertificateChanged:
            return {"base": "certificate_changed"}
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

    async def _async_check_or_offer_trust(
        self, url: str, token: str, verify_ssl: bool, pin: PeerCertificate | None, done: dict[str, Any]
    ) -> tuple[dict[str, str], ConfigFlowResult | None]:
        """Check the address; when only its certificate can't be checked,
        go on to the trust step instead of showing an error. `done` says what
        to do once the certificate is trusted."""
        errors = await self._async_check(url, token, verify_ssl, pin)
        if errors != {"base": "certificate"} or not verify_ssl or not url.startswith("https://"):
            return errors, None
        try:
            certificate = await async_fetch_certificate(url)
        except BackuparrError, InvalidUrl:
            return errors, None
        self._pending = {"url": url, "token": token, "certificate": certificate, **done}
        return {}, await self.async_step_trust_certificate()

    async def async_step_trust_certificate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the certificate's fingerprint; trust it on Submit."""
        pending = self._pending
        certificate: PeerCertificate = pending["certificate"]
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_check(pending["url"], pending["token"], True, certificate)
            if not errors:
                data = {
                    CONF_URL: pending["url"],
                    CONF_TOKEN: pending["token"],
                    CONF_VERIFY_SSL: True,
                    **pin_data(certificate),
                }
                if pending["action"] == "create":
                    return self.async_create_entry(title=URL(pending["url"]).host or "Backuparr", data=data)
                entry = self._get_reconfigure_entry()
                return self.async_update_reload_and_abort(
                    entry, unique_id=_unique_id(pending["url"]), data={**without_pin(entry.data), **data}
                )
        return self.async_show_form(
            step_id="trust_certificate",
            description_placeholders={"url": pending["url"], "fingerprint": certificate.fingerprint},
            errors=errors,
        )

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
                verify_ssl = user_input[CONF_VERIFY_SSL]
                errors, trust = await self._async_check_or_offer_trust(
                    url, token, verify_ssl, None, {"action": "create"}
                )
                if trust:
                    return trust
                if not errors:
                    return self.async_create_entry(
                        title=URL(url).host or "Backuparr",
                        data={CONF_URL: url, CONF_TOKEN: token, CONF_VERIFY_SSL: verify_ssl},
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
            errors = await self._async_check(
                entry.data[CONF_URL], token, entry.data[CONF_VERIFY_SSL], pin_from(entry.data)
            )
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
        entry: ConfigEntry = self._get_reconfigure_entry()
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
                verify_ssl = user_input[CONF_VERIFY_SSL]
                # A trusted certificate belongs to the address it was trusted for.
                pin = pin_from(entry.data) if url == entry.data[CONF_URL] and verify_ssl else None
                errors, trust = await self._async_check_or_offer_trust(
                    url, entry.data[CONF_TOKEN], verify_ssl, pin, {"action": "reconfigure"}
                )
                if trust:
                    return trust
                if not errors:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=unique_id,
                        data={**without_pin(entry.data), CONF_URL: url, CONF_VERIFY_SSL: verify_ssl, **pin_data(pin)},
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
