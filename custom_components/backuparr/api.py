"""A small client for Backuparr's read-only status endpoint.

Backuparr (3.0 and later) answers GET /api/ha/status with
"Authorization: Bearer <token>" while its Home Assistant integration is
switched on, and 404 while it's off. Everything it sends is checked here
before the entities see it: app ids end up in unique ids, so only plain
slugs are kept.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

import aiohttp
from yarl import URL

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

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        """Keep the session, base address and token."""
        self._session = session
        self._url = normalize_url(url) + STATUS_PATH
        self._token = token

    async def async_get_status(self) -> BackuparrStatus:
        """GET /api/ha/status."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                # No redirects: the token goes to the address the user gave,
                # never anywhere else.
                async with self._session.get(
                    self._url,
                    headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                    allow_redirects=False,
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
        except aiohttp.ClientConnectorCertificateError as err:
            raise CertificateError(str(err.certificate_error)) from err
        except aiohttp.ClientSSLError as err:
            raise CertificateError("TLS handshake failed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(type(err).__name__) from err
        return parse_status(data)
