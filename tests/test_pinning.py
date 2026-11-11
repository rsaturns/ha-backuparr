"""Trusting Backuparr's self-signed certificate, against a real local HTTPS
server: the pin is checked before the token goes out, a renewal with the
same key is followed, and a new key asks the user again."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import ssl
from typing import Any

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.backuparr.api import (
    BackuparrClient,
    CertificateChanged,
    PeerCertificate,
    async_fetch_certificate,
    describe_certificate,
)
from custom_components.backuparr.const import CONF_CERT_SHA256, CONF_KEY_SHA256, DOMAIN
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import STATUS, TOKEN

# A real HTTPS server on 127.0.0.1: the TLS handshake is what's under test.
pytestmark = pytest.mark.usefixtures("socket_enabled")


def _certificate(key: ec.EllipticCurvePrivateKey, serial: int) -> bytes:
    """A self-signed certificate like Backuparr's, as PEM."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Backuparr")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


@dataclass
class FakeBackuparr:
    """Backuparr's HTTPS port: answers /api/ha/status and logs who asked."""

    folder: Path
    key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    context: ssl.SSLContext = field(default_factory=lambda: ssl.create_default_context(ssl.Purpose.CLIENT_AUTH))
    tokens: list[str] = field(default_factory=list)
    url: str = ""
    serial: int = 1

    def serve(self) -> PeerCertificate:
        """Write the key and a new certificate, and serve them from now on."""
        self.serial += 1
        cert_pem = _certificate(self.key, self.serial)
        (self.folder / "cert.pem").write_bytes(cert_pem)
        (self.folder / "key.pem").write_bytes(
            self.key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        self.context.load_cert_chain(self.folder / "cert.pem", self.folder / "key.pem")
        der = x509.load_pem_x509_certificate(cert_pem).public_bytes(serialization.Encoding.DER)
        return describe_certificate(der)

    def renew(self) -> PeerCertificate:
        """Backuparr's own renewal: a new certificate, the same key."""
        return self.serve()

    def replace_key(self) -> PeerCertificate:
        """A new install, or another server: a new key."""
        self.key = ec.generate_private_key(ec.SECP256R1())
        return self.serve()

    async def handle(self, request: web.Request) -> web.Response:
        self.tokens.append(request.headers.get("Authorization", ""))
        # No keep-alive: each request makes a new TLS handshake, as after a while.
        return web.json_response(STATUS, headers={"Connection": "close"})


@pytest.fixture
async def backuparr(hass: HomeAssistant, tmp_path: Path) -> AsyncIterator[FakeBackuparr]:
    # The client drops the connection right after the handshake when the pin
    # doesn't match - as it should. The fake server's side logs that as a
    # reset handshake; that's the server's noise, not a failure.
    loop = hass.loop
    handler = loop.get_exception_handler()

    def quiet(loop_: Any, context: dict[str, Any]) -> None:
        if isinstance(context.get("exception"), (ConnectionResetError, ssl.SSLError)):
            return
        if handler:
            handler(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(quiet)
    fake = FakeBackuparr(tmp_path)
    fake.current = fake.serve()  # type: ignore[attr-defined]
    app = web.Application()
    app.router.add_get("/api/ha/status", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=fake.context)
    await site.start()
    fake.url = f"https://127.0.0.1:{runner.addresses[0][1]}"
    yield fake
    await runner.cleanup()
    loop.set_exception_handler(handler)


async def test_fetch_certificate_sends_nothing(backuparr: FakeBackuparr) -> None:
    peer = await async_fetch_certificate(backuparr.url)
    assert peer == backuparr.current  # type: ignore[attr-defined]
    assert len(peer.fingerprint.split(":")) == 32
    assert backuparr.tokens == []


async def test_a_pinned_request_works(backuparr: FakeBackuparr) -> None:
    async with aiohttp.ClientSession() as session:
        client = BackuparrClient(session, backuparr.url, TOKEN, pin=backuparr.current)  # type: ignore[attr-defined]
        assert (await client.async_get_status()).failed == 1
    assert backuparr.tokens == [f"Bearer {TOKEN}"]


async def test_a_renewal_with_the_same_key_is_followed(backuparr: FakeBackuparr) -> None:
    old = backuparr.current  # type: ignore[attr-defined]
    new = backuparr.renew()
    assert new.sha256 != old.sha256 and new.public_key_sha256 == old.public_key_sha256
    renewed: list[PeerCertificate] = []
    async with aiohttp.ClientSession() as session:
        client = BackuparrClient(session, backuparr.url, TOKEN, pin=old, on_renewed=renewed.append)
        assert (await client.async_get_status()).failed == 1
    assert renewed == [new] and client.pin == new
    assert backuparr.tokens == [f"Bearer {TOKEN}"]


async def test_a_new_key_is_refused_before_the_token_is_sent(backuparr: FakeBackuparr) -> None:
    old = backuparr.current  # type: ignore[attr-defined]
    new = backuparr.replace_key()
    async with aiohttp.ClientSession() as session:
        client = BackuparrClient(session, backuparr.url, TOKEN, pin=old)
        with pytest.raises(CertificateChanged) as err:
            await client.async_get_status()
    assert err.value.certificate == new
    assert backuparr.tokens == []


async def _flow(hass: HomeAssistant, url: str, verify_ssl: bool = True) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: url, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: verify_ssl}
    )


async def test_setup_asks_to_trust_a_self_signed_certificate(hass: HomeAssistant, backuparr: FakeBackuparr) -> None:
    result = await _flow(hass, backuparr.url)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "trust_certificate"
    assert result["description_placeholders"]["fingerprint"] == backuparr.current.fingerprint  # type: ignore[attr-defined]
    # Nothing was sent to the server while its certificate wasn't trusted.
    assert backuparr.tokens == []

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CERT_SHA256] == backuparr.current.sha256  # type: ignore[attr-defined]
    assert result["data"][CONF_KEY_SHA256] == backuparr.current.public_key_sha256  # type: ignore[attr-defined]
    assert result["data"][CONF_VERIFY_SSL] is True
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == "on"


async def test_trust_step_fails_if_the_certificate_changes_meanwhile(
    hass: HomeAssistant, backuparr: FakeBackuparr
) -> None:
    result = await _flow(hass, backuparr.url)
    backuparr.replace_key()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "certificate_changed"}
    assert backuparr.tokens == []


async def test_verify_ssl_off_skips_the_pin(hass: HomeAssistant, backuparr: FakeBackuparr) -> None:
    result = await _flow(hass, backuparr.url, verify_ssl=False)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_CERT_SHA256 not in result["data"]


async def _pinned_entry(hass: HomeAssistant, backuparr: FakeBackuparr) -> MockConfigEntry:
    pin = backuparr.current  # type: ignore[attr-defined]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="127.0.0.1",
        unique_id=backuparr.url,
        data={
            CONF_URL: backuparr.url,
            CONF_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_CERT_SHA256: pin.sha256,
            CONF_KEY_SHA256: pin.public_key_sha256,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_a_renewal_updates_the_saved_pin(hass: HomeAssistant, backuparr: FakeBackuparr) -> None:
    entry = await _pinned_entry(hass, backuparr)
    new = backuparr.renew()
    await _refresh(hass, entry)
    assert entry.data[CONF_CERT_SHA256] == new.sha256
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == "on"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"certificate_changed_{entry.entry_id}") is None


async def test_a_new_key_raises_a_repair_that_trusts_it(hass: HomeAssistant, backuparr: FakeBackuparr) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = await _pinned_entry(hass, backuparr)
    sent_before = len(backuparr.tokens)
    new = backuparr.replace_key()
    await _refresh(hass, entry)
    assert len(backuparr.tokens) == sent_before
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == STATE_UNAVAILABLE
    issue_id = f"certificate_changed_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["fingerprint"] == new.fingerprint

    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["fingerprint"] == new.fingerprint
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.data[CONF_CERT_SHA256] == new.sha256
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.backuparr_all_backups").state == "on"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_reconfigure_keeps_the_pin_for_the_same_address_and_drops_it_without_checks(
    hass: HomeAssistant, backuparr: FakeBackuparr
) -> None:
    entry = await _pinned_entry(hass, backuparr)
    pin = backuparr.current  # type: ignore[attr-defined]
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: backuparr.url + "/", CONF_VERIFY_SSL: True}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CERT_SHA256] == pin.sha256

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: backuparr.url, CONF_VERIFY_SSL: False}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert CONF_CERT_SHA256 not in entry.data and CONF_KEY_SHA256 not in entry.data


async def test_reconfigure_to_a_new_address_asks_to_trust_its_certificate(
    hass: HomeAssistant, backuparr: FakeBackuparr
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="old",
        unique_id="https://old.invalid",
        data={
            CONF_URL: "https://old.invalid",
            CONF_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_CERT_SHA256: "00" * 32,
            CONF_KEY_SHA256: "11" * 32,
        },
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: backuparr.url, CONF_VERIFY_SSL: True}
    )
    assert result["step_id"] == "trust_certificate"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == backuparr.url
    assert entry.data[CONF_CERT_SHA256] == backuparr.current.sha256  # type: ignore[attr-defined]
    assert entry.unique_id == backuparr.url
