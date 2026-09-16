# Solariot for Home Assistant

[![hacs][hacs-badge]][hacs-url]
[![Validate](https://github.com/phunb3101/hass-solariot/actions/workflows/validate.yml/badge.svg)](https://github.com/phunb3101/hass-solariot/actions/workflows/validate.yml)

Reads your own solar system — inverter, battery, grid — from a Solariot portal
into Home Assistant. **Read-only**: it registers no switch, number or button
platform, and the credential it uses is refused by the server on every write.

## Install

**HACS** → ⋮ → Custom repositories → add
`https://github.com/phunb3101/hass-solariot` as an *Integration* → install →
restart Home Assistant.

**Manually**: copy `custom_components/solariot` into your Home Assistant
`config/custom_components/` and restart.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration

## Get a key

Portal → avatar menu → **Khoá API / API keys** → give it a label → Create.

The full key is shown **once**. The server stores only a digest and cannot read
it back, so copy it before closing the dialog. Each account may hold 3 active
keys; a key never expires unless you give it an expiry date.

## Set it up

Settings → Devices & Services → **Add Integration** → Solariot.

There is one field: the `slk_…` key. The server is fixed at
**https://solariot.net** — there is exactly one Solariot to talk to, and a
typed-in URL only buys typos that then fail as a connection error instead of an
obvious one.

A wrong key is rejected here, in words — the integration will not create a
silent, empty configuration. A valid key on an account with no devices yet says
so in different words, because that is a different problem.

### Pointing it somewhere else (development only)

Set the environment variable `SOLARIOT_BASE_URL` on the Home Assistant process
before it starts, e.g. `SOLARIOT_BASE_URL=http://host.docker.internal:4000` when
Home Assistant is in a container and the server runs on the host (`localhost`
there is the container itself). This exists for the repo's own bench and is
deliberately not a field in the setup dialog.

## What you get

One Home Assistant device per inverter, with sensors for PV / load / battery /
grid power, battery SOC and SOH, voltages, temperatures, and the daily and
lifetime energy counters. Entities are only created for values your inverter
actually reports — brands differ, and the server omits what it does not have
rather than sending nulls.

The energy counters carry `state_class: total_increasing`, so they can be used
directly in Home Assistant's **Energy** dashboard.

### Sign conventions

| Sensor | Positive | Negative |
|---|---|---|
| Battery power | discharging | charging |
| Grid power | importing | exporting |

## How it updates

A REST call seeds the device list and a first reading; after that the
integration follows a server-sent event stream, so values arrive as the inverter
reports them rather than on a poll. If the stream drops — a deploy, a network
blip, a revoked key — it falls back to polling every 30 s and keeps trying to
reconnect. Entities go unavailable per device, so one quiet inverter does not
take the others down.

Revoking a key in the portal closes its stream immediately; the integration will
then report an authentication failure rather than sitting on stale data.
