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
| Connections | A connection to one HEOS device controls the HEOS system; the spec recommends not connecting separately to every speaker and allows up to 32 simultaneous sockets [spec, 1, p. 5]. The driver keeps one connection only while the panel subscribes to events. |
| State updates | [spec][1, p. 8][3, not yet measured] The driver registers for HEOS change events while subscribed and handles `event/player_volume_changed` for the resolved player PID. |

## Transport and framing

Send an ASCII command terminated by `\r\n`, for example `heos://player/get_volume?pid=123\r\n`. A response is one JSON object per CRLF-delimited line. The `heos` object carries `command`, `result` (`success` or `fail`) and `message`; optional command data is in `payload` [spec, 1, p. 7]. The shared `ReceiverSession` owns TCP stream setup, reply deadlines, serialization and cleanup. Since later HEOS requests depend on the player-list reply, a logical operation holds a session transaction group across its ordered request/reply steps; the stream closes when the group ends unless a watch is subscribed.

For example, `player/get_volume` returns its level in the response message:

```json
{"heos":{"command":"player/get_volume","result":"success","message":"pid='123'&level='42'"}}
```

Skip replies whose message is `command under process`; the final response may arrive later [spec, 1, p. 7]. A shared session keyed by `(host, port)` serializes operations across driver instances. When the panel subscribes, the driver sends `heos://system/register_for_change_events?enable=on` and handles `event/player_volume_changed` only for the resolved player PID [spec, 1, p. 8; 3]. After a dropped connection, the next transaction reconnects and the driver registers for events again [spec, 1, p. 8]. HEOS does not send these events by default; this event path has not been measured on hardware.

[assumed] Volume and mute status are read independently; if one response times out, the available field is retained and the missing field remains `None`. Setters validate readback against the requested value, route differing state responses to subscribers, and return the last observed value if confirmation expires.

## Discovery

Search with the HEOS SSDP target, then use the response sender IP as the device host and fetch its UPnP description [spec, 1, p. 4]. The description's serial number is preferred for the receiver ID, with UDN as fallback; an IP fallback is used if neither is present [assumed]. The found receiver's config carries `host`, `ip` (the same host), `model`, `player` (the UPnP `friendlyName`) and, when the description has one, `serial` (the UPnP `serialNumber`) [assumed: the UPnP serial equals the HEOS player serial; if it doesn't, selection falls back to the player name]. A manual probe sends `heos://system/heart_beat`, checks for a successful JSON response, then calls `player/get_players` and resolves the target [spec, 1, p. 8]. A successful probe rewrites `player` to the resolved player's name and sets `serial` to its `serial` field, with `serial_for` recording the selector it was learned for, when the player reports one. Every operation re-learns `serial`/`serial_for` from the player it resolves, and clears both when that player has no serial. The `identity_keys` are `host` and `player`, so changing the selected player is a different receiver and resets its caps and late-night state (ADR 0014).

The CLI must initialize its player list after a connection is opened; the spec says player IDs become available only after player discovery [spec, 1, p. 5]. PIDs are resolved again for each operation and are never saved as identity because the spec does not promise they remain stable after reconfiguration [assumed].

## Commands

| Capability | Query | Set | Reply | Notes |
|---|---|---|---|---|
| Volume | `player/get_volume?pid=...` | `player/set_volume?pid=...&level=...` | `level` in `heos.message` | Player volume, 0–100 [spec, 1, p. 13]; setter returns a fresh query result |
| Mute | `player/get_mute?pid=...` | `player/set_mute?pid=...&state=on\|off` | `state` in `heos.message` | Player mute [spec, 1, p. 14]; setter returns a fresh query result |
| Power | — | — | — | Unsupported |
| Listening mode | — | — | — | Unsupported |

`player/get_players` supplies the player names, models, PIDs and, in later spec versions, serials and IPs [spec, 1, p. 10; spec, 2, fields as cited in review, page not checked]. Any field may be absent: the spec allows a player without an `ip`. The driver picks one player in this order [assumed implementation policy]:

1. the player whose `serial` equals the configured `serial`, but only when `serial_for` equals the current selector (the casefolded `player`, or `host` when no player is set). A serial learned for another selector, or a legacy serial with no `serial_for`, is ignored, so changing the selected player always re-resolves;
2. the player whose `name` equals the configured `player` (case-insensitive; a renamed player is not found by name). A configured name wins over the IP because any one HEOS device controls the whole system, so `host` may be a different player;
3. the player whose `ip` equals `host`, when no `player` is configured;
4. the only player, when neither matches.

Each step needs exactly one match; two matches at a step are an error that lists the names. A missing name is an error that lists the available players. PIDs are used only as command targets; model, name, serial and IP are retained in the found receiver's config.

## Volume scale

Raw range 0–100, one-unit steps, displayed as percent [spec, 1, p. 13]. Zero is raw 0. The default slider cap is 60 [assumed product default].

## Listening modes

Not supported. HEOS playback/group functions are not AVR surround listening modes [assumed].

`mode_state()` returns `current=None`, no selectable modes, and `complete=false`
without I/O [assumed]. The base `Mode {code}` label is used for any saved
unknown code [assumed].

## Quirks and gotchas

- HEOS volume is the HEOS player's volume. On a Denon/Marantz AVR with HEOS built in, this is **not necessarily the AVR's main-zone master volume** [assumed; not verified on hardware]. Use the Denon driver for AVR master-volume control.
- The HEOS CLI controls the whole system through a connection to one HEOS device; do not open a separate connection to each player [spec, 1, p. 5].
- Duplicate names or multiple candidates are errors rather than an arbitrary selection [assumed implementation policy].
- Configs saved before the `player`/`serial` keys existed carry `name` instead, which is no longer read. Run the search again to rewrite them; until then, a system with several players that have no `ip` field reports "Multiple HEOS players found".
- Configs with a `serial` but no `serial_for` (saved before this rule) ignore that serial. Name resolution then applies, so a renamed player needs a new search or probe to be found by serial again.

## Unknowns and open questions

- No real HEOS device has been tested. Confirm player IP and serial availability, response variations and whether HEOS-built-in AVR volume tracks main-zone volume.
- The v1.7 `get_players` example shows name, PID, group ID, model and version, but not IP or serial [spec, 1, p. 10]. Whether the UPnP `serialNumber` matches a player's `serial` is unverified.
- Confirm that pids can change after device reconfiguration; the spec describes them as discovered at connection time but does not state their persistence [spec, 1, p. 5; assumed volatility].
- [spec, not yet measured] Tester checklist: change volume with the remote while the panel is open; does the slider follow within 1 s?

## Sources

1. HEOS, [HEOS CLI Protocol Specification v1.7 (2016)](http://rn.dmglobal.com/euheos/HEOS_CLI_ProtocolSpecification.pdf): connection, TCP port and SSDP target (p. 4); socket and initialization guidance, including the 32-socket limit (p. 5); JSON format, CRLF framing and interim replies (p. 7); heartbeat and event registration (p. 8); player list (p. 10); volume (p. 13); mute (p. 14).
2. HEOS, [HEOS CLI Protocol Specification v1.14](https://www.marantz.com/on/demandware.static/-/Library-Sites-marantz_northamerica_shared/default/dw04da2b25/archive-downloads/heos_cli_protocolspecification-version-114.pdf): cited for player entries that may omit `ip` and for `serial` in `get_players`. The page numbers were not checked against the PDF.
3. [spec] [HEOS CLI Protocol Specification](https://rn.dmglobal.com/eu/en/online/HEOS_CLI_Protocol_Specification.pdf): change-event registration and player event messages; not measured for this project.
