"""Constants for the Solariot integration."""

from __future__ import annotations

import os
from datetime import timedelta

DOMAIN = "solariot"

CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"

# The server address is fixed. Users have exactly one Solariot to talk to, and
# asking them to type a URL only invites typos and http:// pastes that then fail
# with a connection error instead of an obvious one.
#
# SOLARIOT_BASE_URL exists for exactly one concrete reason, not as general
# configurability: the development lab (tools/ha-lab) runs Home Assistant in a
# container against a backend on the host, and without an override that setup
# becomes untestable. It is deliberately an environment variable rather than a
# field in the config flow, so it stays invisible to everyone it is not for.
DEFAULT_BASE_URL = "https://solariot.net"
BASE_URL = os.environ.get("SOLARIOT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

# The poll is the FALLBACK, not the primary path: the coordinator also runs an
# SSE task that pushes updates as the inverter reports them. This interval only
# governs how stale things get while that stream is down, so it can be gentle —
# the server meters 60 requests/minute per key across all devices.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

# Reconnect backoff for the push stream. The server closes a stream deliberately
# (deploy, revoked key, expired key), so a disconnect is ordinary and must not
# escalate into hammering.
SSE_BACKOFF_START = 5
SSE_BACKOFF_MAX = 300

# The server allows 3 concurrent streams per key. Devices past that are served
# by the polling fallback.
MAX_STREAMS = 3

API_PREFIX = "/api/v1/integration"
STREAM_PATH = "/api/v1/stream/luxpower"
