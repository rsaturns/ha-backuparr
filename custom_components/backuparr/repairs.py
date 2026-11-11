"""Repair: Backuparr's certificate changed to one with another key.

Its own renewals keep the key and are followed without asking (api.py). A
new key means a new install without its own backup, your own certificate
replaced, or someone pretending to be Backuparr, so the user compares the
fingerprint with the one on Backuparr's card before it's trusted. Until
then, the token isn't sent."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .api import PeerCertificate
from .pin import pin_data


class CertificateChangedFlow(RepairsFlow):
    """Trust the new certificate."""

    def __init__(self, data: dict[str, Any]) -> None:
        """The entry and the certificate it now presents."""
        self.entry_id: str = data["entry_id"]
        self.certificate = PeerCertificate(data["sha256"], data["public_key_sha256"])

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Show the new fingerprint."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Trust it, and reload the entry to use it."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return self.async_abort(reason="entry_removed")
        if user_input is not None:
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, **pin_data(self.certificate)})
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"title": entry.title, "fingerprint": self.certificate.fingerprint},
        )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    """The flow for a certificate_changed_<entry> repair."""
    assert data is not None
    return CertificateChangedFlow(data)
