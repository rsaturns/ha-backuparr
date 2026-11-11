"""The certificate pin in a config entry's data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api import PeerCertificate
from .const import CONF_CERT_SHA256, CONF_KEY_SHA256


def pin_from(data: Mapping[str, Any]) -> PeerCertificate | None:
    """The pinned certificate, or None when the address needs no pin."""
    cert, key = data.get(CONF_CERT_SHA256), data.get(CONF_KEY_SHA256)
    return PeerCertificate(cert, key) if cert and key else None


def pin_data(pin: PeerCertificate | None) -> dict[str, str]:
    """The data keys for a pin; none without one."""
    return {CONF_CERT_SHA256: pin.sha256, CONF_KEY_SHA256: pin.public_key_sha256} if pin else {}


def without_pin(data: Mapping[str, Any]) -> dict[str, Any]:
    """The entry's data with any pin dropped."""
    return {k: v for k, v in data.items() if k not in (CONF_CERT_SHA256, CONF_KEY_SHA256)}
