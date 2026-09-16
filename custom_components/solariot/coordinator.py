"""Coordinator: seed over REST, then follow the push stream.

Shape is Home Assistant's standard one for a push source with a pull fallback —
a DataUpdateCoordinator so every entity shares one data source and one failure
mode, plus a background task that feeds it via async_set_updated_data.

The fallback is not decoration. The server closes streams on purpose (a deploy,
a revoked key, an expired key), and a home connection drops on its own, so an
SSE-only design would leave entities frozen on whatever they last heard.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SolariotApi, SolariotAuthError, SolariotConnectionError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_STREAMS,
    SSE_BACKOFF_MAX,
    SSE_BACKOFF_START,
)

_LOGGER = logging.getLogger(__name__)


class SolariotCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds {device_sn: {"device": {...}, "metrics": {...}}} for every device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SolariotApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.api = api
        self.devices: list[dict[str, Any]] = []
        self._stream_task: asyncio.Task | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """The seed, and the fallback poll."""
        try:
            devices = await self.api.async_get_devices()
        except SolariotAuthError as err:
            # Re-raised as ConfigEntryAuthFailed by HA's own handling of
            # UpdateFailed subclasses would be nicer, but this keeps the
            # dependency surface small: the message is what a user sees.
            raise UpdateFailed("API key bị từ chối") from err
        except SolariotConnectionError as err:
            raise UpdateFailed(str(err)) from err

        self.devices = devices
        out: dict[str, Any] = {}
        for dev in devices:
            sn = dev.get("deviceSn")
            if not sn:
                continue
            try:
                latest = await self.api.async_get_latest(sn)
            except (SolariotAuthError, SolariotConnectionError) as err:
                # One unreachable device must not blank the others: keep what we
                # had for it and carry on.
                _LOGGER.debug("latest failed for %s: %s", sn, err)
                latest = (self.data or {}).get(sn, {}).get("latest")
            out[sn] = {"device": dev, "latest": latest or {}}
        return out

    # ── push ────────────────────────────────────────────────────────────────

    def start_stream(self) -> None:
        if self._stream_task is None or self._stream_task.done():
            self._stream_task = self.config_entry.async_create_background_task(
                self.hass, self._stream_loop(), name=f"{DOMAIN}_stream"
            )

    async def async_shutdown(self) -> None:
        if self._stream_task:
            self._stream_task.cancel()
            self._stream_task = None
        await super().async_shutdown()

    async def _stream_loop(self) -> None:
        """One stream per device, up to the server's per-key ceiling.

        Measured against the real server, NOT assumed: a stream opened for
        deviceSn=X carries X plus the BMS packs on the same account — it does
        NOT carry the account's other inverters. Subscribing once and hoping to
        hear about everything leaves every other inverter silently on the
        polling path, which is exactly what happened the first time.

        Each event is still routed by its own data.deviceSn rather than by the
        subscription, because a stream does carry more than the device asked
        for and attributing all of it to X would paint the BMS pack's numbers
        onto the inverter.

        The ceiling is the server's (3 per key). Devices beyond it are covered
        by the polling fallback — correct, just less immediate.
        """
        backoff = SSE_BACKOFF_START
        while True:
            try:
                sns = [d["deviceSn"] for d in self.devices if d.get("deviceSn")][:MAX_STREAMS]
                if not sns:
                    await asyncio.sleep(backoff)
                    continue
                await asyncio.gather(*(self._stream_once(sn) for sn in sns))
                backoff = SSE_BACKOFF_START
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 — the stream must never die for good
                _LOGGER.debug("stream restarting after %s", err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, SSE_BACKOFF_MAX)

    async def _stream_once(self, subscribe_sn: str) -> None:
        session = async_get_session(self.hass)
        url = f"{self.api.stream_url}?deviceSn={subscribe_sn}"
        async with session.get(
            url,
            headers={**self.api.headers, "Accept": "text/event-stream"},
            timeout=aiohttp.ClientTimeout(total=None, sock_read=None),
        ) as resp:
            if resp.status != 200:
                raise SolariotConnectionError(f"stream HTTP {resp.status}")
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                # ": hb" keepalive comments keep proxies from cutting a quiet
                # stream; they are not events.
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                self._apply_event(event)

    def _apply_event(self, event: dict[str, Any]) -> None:
        """Fold one pushed reading into the coordinator's data.

        Envelope is {"type": ..., "data": {...}}; the reading sits at
        data.latest.metrics, and data.deviceSn says WHICH device it belongs to.
        Routing on that field rather than on the stream's subscription is what
        keeps a shared stream from cross-contaminating devices.
        """
        body = event.get("data")
        if not isinstance(body, dict):
            return
        device_sn = body.get("deviceSn")
        if not device_sn:
            # "connected" and other control frames carry no device.
            return
        known = self.data or {}
        if device_sn not in known:
            # A sibling the account owns but this integration does not track —
            # a BMS pack, say. Ignore rather than invent an entry for it.
            return
        latest = body.get("latest")
        metrics = (latest or {}).get("metrics") if isinstance(latest, dict) else None
        if not isinstance(metrics, dict):
            return

        data = dict(known)
        entry = dict(data.get(device_sn) or {})
        prev = dict(entry.get("latest") or {})
        merged = dict(prev.get("metrics") or {})
        # A field absent from an update means "not reported this frame", not
        # zero — the same rule the backend applies when it merges readings.
        # Replacing the dict wholesale would blank every metric this frame left
        # out.
        merged.update({k: v for k, v in metrics.items() if v is not None})
        prev["metrics"] = merged
        entry["latest"] = prev
        data[device_sn] = entry
        self.async_set_updated_data(data)


def async_get_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(hass)
