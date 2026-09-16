"""Thin HTTP client for the Solariot integration API.

Dependency-free on purpose: it uses the aiohttp session Home Assistant already
owns, matching the project's own "third-party clients are plain HTTP" rule.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_PREFIX, STREAM_PATH

_LOGGER = logging.getLogger(__name__)


class SolariotAuthError(Exception):
    """The key was refused. Wrong, revoked, expired, or the account is disabled.

    The server answers all four identically and on purpose — telling them apart
    would confirm which keys exist — so this exception cannot say which it was.
    """


class SolariotConnectionError(Exception):
    """The server could not be reached or did not answer usefully."""


class SolariotApi:
    """Read-only client. There is deliberately no write method on it."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._key = api_key

    @property
    def headers(self) -> dict[str, str]:
        # Authorization, NOT x-api-key: that header belongs to the gateway and
        # operator credentials on the server, and the two must not be confusable.
        return {"Authorization": f"Bearer {self._key}"}

    @property
    def stream_url(self) -> str:
        return f"{self._base}{STREAM_PATH}"

    async def _get(self, path: str) -> Any:
        url = f"{self._base}{API_PREFIX}{path}"
        try:
            async with self._session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status in (401, 403):
                    raise SolariotAuthError
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    raise SolariotConnectionError(f"HTTP {resp.status} from {path}")
                return await resp.json()
        except aiohttp.ClientError as err:
            raise SolariotConnectionError(str(err)) from err
        except TimeoutError as err:
            raise SolariotConnectionError("timeout") from err

    async def async_get_me(self) -> dict[str, Any]:
        """Identity + device count. Used by the config flow to check the key.

        deviceCount is what lets the flow tell "wrong key" from "right key, no
        devices yet" — two situations that need different words in front of a
        person.
        """
        data = await self._get("/me")
        if data is None:
            raise SolariotConnectionError("no answer from /me")
        return data

    async def async_get_devices(self) -> list[dict[str, Any]]:
        data = await self._get("/devices")
        return (data or {}).get("devices", [])

    async def async_get_latest(self, device_sn: str) -> dict[str, Any] | None:
        """Latest reading for one device.

        None means the server refused to acknowledge the device at all — which
        is the same answer it gives for a device belonging to someone else, so
        the caller must not treat it as "temporarily missing".
        """
        return await self._get(f"/devices/{device_sn}/latest")
