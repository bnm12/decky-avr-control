# Denon HEOS CLI (`heos`)

> **Source tags:** **[measured]** seen on real hardware · **[spec]** from a vendor document · **[community]** from an open-source library or forum · **[assumed]** our guess, untested.

## Summary

| | |
|---|---|
| Driver | `py_modules/avr_control/drivers/heos.py` |
| Brands / models | Denon HEOS speakers, soundbars and HEOS-built-in products [spec, 1, pp. 4–5] |
| Verified on | None (simulator only) |
| Transport | TCP 1255; ASCII commands and JSON responses, CRLF-delimited [spec, 1, pp. 4, 7] |
| Discovery | SSDP `urn:schemas-denon-com:device:ACT-Denon:1`, then UPnP description; receiver ID is `heos:<serial>` or `heos:<UDN>` [spec, 1, p. 4; assumed stable identity policy] |
| Connections | A connection to one HEOS device controls the HEOS system; the spec recommends not connecting separately to every speaker and allows up to 32 simultaneous sockets [spec, 1, p. 5] |

## Transport and framing

Send an ASCII command terminated by `\r\n`, for example `heos://player/get_volume?pid=123\r\n`. A response is one JSON object per CRLF-delimited line. The `heos` object carries `command`, `result` (`success` or `fail`) and `message`; optional command data is in `payload` [spec, 1, p. 7].

For example, `player/get_volume` returns its level in the response message:

```json
{"heos":{"command":"player/get_volume","result":"success","message":"pid='123'&level='42'"}}
```

Skip replies whose message is `command under process`; the final response may arrive later [spec, 1, p. 7]. The driver opens one short-lived connection per operation and serializes operations by `(host, port)` with a shared `asyncio.Lock` so requests and replies cannot overlap [assumed implementation choice]. It does not register for change events; HEOS does not send them by default [spec, 1, pp. 5, 8].

## Discovery

Search with the HEOS SSDP target, then use the response sender IP as the device host and fetch its UPnP description [spec, 1, p. 4]. The description's serial number is preferred for the receiver ID, with UDN as fallback; an IP fallback is used if neither is present [assumed]. A manual probe sends `heos://system/heart_beat`, checks for a successful JSON response, then calls `player/get_players` and resolves the target [spec, 1, p. 8].

The CLI must initialize its player list after a connection is opened; the spec says player IDs become available only after player discovery [spec, 1, p. 5]. PIDs are resolved again for each operation and are never saved as identity because the spec does not promise they remain stable after reconfiguration [assumed].

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | `player/get_volume?pid=...` | `player/set_volume?pid=...&level=...` | `level` in `heos.message` | Player volume, 0–100 [spec, 1, p. 13]; setter returns a fresh query result |
| Mute | `player/get_mute?pid=...` | `player/set_mute?pid=...&state=on\|off` | `state` in `heos.message` | Player mute [spec, 1, p. 14]; setter returns a fresh query result |
| Power | — | — | — | Unsupported |
| Listening mode | — | — | — | Unsupported |

`player/get_players` supplies the player names, models and PIDs used to resolve each operation [spec, 1, p. 10]. The driver prefers a player whose reported IP matches `host`, then the configured player name, then the only available player. If more than one remains, it reports the available names [assumed implementation policy]. PIDs are used only as command targets; model, name and IP are retained in the found receiver's config.

## Volume scale

Raw range 0–100, one-unit steps, displayed as percent [spec, 1, p. 13]. Zero is raw 0. The default slider cap is 60 [assumed product default].

## Listening modes

Not supported. HEOS playback/group functions are not AVR surround listening modes [assumed].

## Quirks and gotchas

- HEOS volume is the HEOS player's volume. On a Denon/Marantz AVR with HEOS built in, this is **not necessarily the AVR's main-zone master volume** [assumed; not verified on hardware]. Use the Denon driver for AVR master-volume control.
- The HEOS CLI controls the whole system through a connection to one HEOS device; do not open a separate connection to each player [spec, 1, p. 5].
- A configured player name is used only when no player IP matches the host. Duplicate names or multiple candidates are errors rather than an arbitrary selection [assumed implementation policy].

## Unknowns and open questions

- No real HEOS device has been tested. Confirm player IP availability, response variations and whether HEOS-built-in AVR volume tracks main-zone volume.
- The v1.7 `get_players` example shows name, PID, group ID, model and version, but not IP or serial [spec, 1, p. 10]. The driver uses IP when a device reports it and otherwise relies on the configured name or a single-player system.
- Confirm that pids can change after device reconfiguration; the spec describes them as discovered at connection time but does not state their persistence [spec, 1, p. 5; assumed volatility].

## Sources

1. HEOS, [HEOS CLI Protocol Specification v1.7 (2016)](http://rn.dmglobal.com/euheos/HEOS_CLI_ProtocolSpecification.pdf): connection, TCP port and SSDP target (p. 4); socket and initialization guidance, including the 32-socket limit (p. 5); JSON format, CRLF framing and interim replies (p. 7); heartbeat and event registration (p. 8); player list (p. 10); volume (p. 13); mute (p. 14).
