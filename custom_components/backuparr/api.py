"""A small client for Backuparr's read-only status endpoint.

Backuparr (3.0 and later) answers GET /api/ha/status with
"Authorization: Bearer <token>" while its Home Assistant integration is
switched on, and 404 while it's off. Everything it sends is checked here
before the entities see it: app ids end up in unique ids, so only plain
slugs are kept.

Backuparr serves HTTPS with its own self-signed certificate, which Home
Assistant can't check. Once the user trusts it (config flow), every request
is pinned to that certificate, checked before the token is sent. Backuparr
renews its certificate with the same key, so when the certificate changes
but its public key doesn't, the pin moves to the new one by itself; a new
key raises CertificateChanged, and the user is asked again (repairs.py).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Any

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from yarl import URL

from homeassistant.util.ssl import get_default_no_verify_context

from .const import REQUEST_TIMEOUT, STATUS_PATH

# Backuparr's app and instance ids, e.g. "radarr" or "sonarr-4k".
_APP_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class BackuparrError(Exception):
    """Backuparr couldn't be read."""


class CannotConnect(BackuparrError):
    """No answer, or not one from Backuparr."""


class CertificateError(CannotConnect):
    """Backuparr's TLS certificate couldn't be verified."""


class InvalidAuth(BackuparrError):
    """Backuparr refused the token."""


class NotEnabled(BackuparrError):
    """Backuparr answered 404: the integration is off, or Backuparr is too old."""


class RateLimited(BackuparrError):
    """Too many wrong tokens from this address; Backuparr waits a while."""


class UnexpectedResponse(BackuparrError):
    """Backuparr's answer wasn't the status it should be."""


class InvalidUrl(ValueError):
    """Not an http(s) address with a host."""


@dataclass(frozen=True, slots=True)
class PeerCertificate:
    """A server's certificate, as the pin needs it."""

    sha256: str  # of the certificate (DER), hex: what each request is pinned to
    public_key_sha256: str  # of its public key, hex: what survives a renewal

    @property
    def fingerprint(self) -> str:
        """As browsers and Backuparr's card show it: AB:CD:..."""
        return ":".join(self.sha256[i : i + 2] for i in range(0, len(self.sha256), 2)).upper()


class CertificateChanged(BackuparrError):
    """The pinned certificate was replaced by one with another key."""

    def __init__(self, certificate: PeerCertificate) -> None:
        """Keep the new certificate, to offer it for trust."""
        super().__init__(certificate.fingerprint)
        self.certificate = certificate


def describe_certificate(der: bytes) -> PeerCertificate:
    """The pins for a DER certificate."""
    cert = x509.load_der_x509_certificate(der)
    spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return PeerCertificate(hashlib.sha256(der).hexdigest(), hashlib.sha256(spki).hexdigest())


async def async_fetch_certificate(url: str) -> PeerCertificate:
    """The certificate an https:// address presents. Only the TLS handshake
    happens: nothing is sent, so no token can go to the wrong server."""
    parsed = URL(normalize_url(url))
    if parsed.scheme != "https" or not parsed.host:
        raise InvalidUrl
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            _, writer = await asyncio.open_connection(
                parsed.host, parsed.port, ssl=get_default_no_verify_context(), server_hostname=parsed.host
            )
    except (OSError, TimeoutError) as err:
        raise CannotConnect(type(err).__name__) from err
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError):
            async with asyncio.timeout(2):
                await writer.wait_closed()
    if not der:
        raise CannotConnect("no certificate")
    try:
        return describe_certificate(der)
    except ValueError as err:
        raise UnexpectedResponse("unreadable certificate") from err


@dataclass(frozen=True, slots=True)
class AppStatus:
    """One app's latest backup, as Backuparr's Overview shows it."""

    app_id: str
    name: str
    ok: bool | None
    last_run: datetime | None
    last_success: datetime | None
    error: str | None


@dataclass(frozen=True, slots=True)
class BackuparrStatus:
    """Everything /api/ha/status returns."""

    ok: bool
    total: int
    succeeded: int
    failed: int
    running: bool
    version: str | None
    apps: dict[str, AppStatus]


def normalize_url(url: str) -> str:
    """Backuparr's base address, without a trailing slash."""
    try:
        parsed = URL(url.strip())
    except ValueError as err:
        raise InvalidUrl from err
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise InvalidUrl
    return str(parsed.with_query(None).with_fragment(None)).rstrip("/")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Backuparr sends UTC with an offset; a time without one is useless here.
    return when if when.tzinfo is not None else None


def _text(value: Any, limit: int = 255) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def parse_status(data: Any) -> BackuparrStatus:
    """Check and convert a /api/ha/status answer."""
    if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
        raise UnexpectedResponse("no apps in the answer")
    apps: dict[str, AppStatus] = {}
    for app_id, app in data["apps"].items():
        if not isinstance(app_id, str) or not _APP_ID.match(app_id) or not isinstance(app, dict):
            continue
        ok = app.get("ok")
        apps[app_id] = AppStatus(
            app_id=app_id,
            name=_text(app.get("name"), 64) or app_id,
            ok=ok if isinstance(ok, bool) else None,
            last_run=_time(app.get("last_run")),
            last_success=_time(app.get("last_success")),
            error=_text(app.get("error")) if ok is False else None,
        )
    return BackuparrStatus(
        ok=data.get("ok") is True,
        total=_count(data.get("total")),
        succeeded=_count(data.get("succeeded")),
        failed=_count(data.get("failed")),
        running=data.get("running") is True,
        version=_text(data.get("version"), 32),
        apps=apps,
    )


class BackuparrClient:
    """Reads one Backuparr install's status."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        pin: PeerCertificate | None = None,
        on_renewed: Callable[[PeerCertificate], None] | None = None,
    ) -> None:
        """Keep the session, base address, token and certificate pin."""
        self._session = session
        self._base = normalize_url(url)
        self._url = self._base + STATUS_PATH
        self._token = token
        self.pin = pin if self._base.startswith("https://") else None
        self._on_renewed = on_renewed

    async def async_get_status(self) -> BackuparrStatus:
        """GET /api/ha/status, following a renewal of the pinned certificate."""
        try:
            return await self._async_get_status()
        except aiohttp.ServerFingerprintMismatch:
            pass
        if self.pin is None:  # can't happen: only a pinned request is fingerprinted
            raise CannotConnect("certificate mismatch without a pin")
        peer = await async_fetch_certificate(self._base)
        if peer.public_key_sha256 != self.pin.public_key_sha256:
            raise CertificateChanged(peer)
        self.pin = peer
        if self._on_renewed:
            self._on_renewed(peer)
        try:
            return await self._async_get_status()
        except aiohttp.ServerFingerprintMismatch as err:  # changed again in between
            raise CannotConnect("certificate changed twice") from err

    async def _async_get_status(self) -> BackuparrStatus:
        extra: dict[str, Any] = {}
        if self.pin is not None:
            # Checked after the TLS handshake, before the request (and token) goes out.
            extra["ssl"] = aiohttp.Fingerprint(bytes.fromhex(self.pin.sha256))
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                # No redirects: the token goes to the address the user gave,
                # never anywhere else.
                async with self._session.get(
                    self._url,
                    headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                    allow_redirects=False,
                    **extra,
                ) as response:
                    if response.status == 401:
                        # Backuparr names the scheme; another app's login
                        # page doesn't, and the token isn't the problem there.
                        if "bearer" not in response.headers.get("WWW-Authenticate", "").lower():
                            raise UnexpectedResponse("HTTP 401 without a Bearer challenge")
                        raise InvalidAuth
                    if response.status == 404:
                        raise NotEnabled
                    if response.status == 429:
                        raise RateLimited
                    if response.status != 200:
                        raise CannotConnect(f"HTTP {response.status}")
                    try:
                        data = await response.json(content_type=None)
                    except ValueError as err:
                        raise UnexpectedResponse("not JSON") from err
        except aiohttp.ServerFingerprintMismatch:
            raise
        except aiohttp.ClientConnectorCertificateError as err:
            raise CertificateError(str(err.certificate_error)) from err
        except aiohttp.ClientSSLError as err:
            raise CertificateError("TLS handshake failed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(type(err).__name__) from err
        return parse_status(data)
